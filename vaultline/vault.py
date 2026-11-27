"""KeePass database access with sync-safe saving.

How saves stay safe with Google Drive / KeePassium:

1. Before writing, the file on disk is re-read and hashed. If it changed since
   we loaded it (the phone synced an update), it is decrypted again and our
   changes are re-applied on top of the newer copy, so the phone's changes are kept.
2. The new database is built in memory and decrypted again as a check. Nothing
   touches the disk unless that round trip succeeds.
3. The current file is copied to a local backup folder (outside the synced
   folder), keeping the last few versions.
4. The new bytes go to a temporary file in the same folder, are flushed to disk,
   and then swapped in with one atomic rename (os.replace). Drive never sees a
   half-written .kdbx. A brief sharing lock by the sync client is retried.

Custom icons are stored the way KeePassXC stores them (Meta/CustomIcons + the
entry's CustomIconUUID), so KeePassium and KeePassXC show them too.

This class is not thread-safe; the controller runs it on one worker thread.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import time
import uuid as uuidlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyotp
from lxml import etree
from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError, HeaderChecksumError, PayloadChecksumError

from . import otp as otplib

STANDARD_FIELDS = ("Title", "UserName", "Password", "URL", "Notes")

# Cards use the field names KeePassXC's Bitwarden importer creates, so imported cards just work:
CARD_NUMBER_KEY = "card_number"
CARD_CODE_KEY = "card_code"
CARD_MONTH_KEY = "card_expMonth"
CARD_YEAR_KEY = "card_expYear"
CARD_HOLDER_KEY = "card_cardholderName"
CARD_BRAND_KEY = "card_brand"
CARD_PHONE_KEY = "Phone"      # the bank's number (usually toll-free); the Bitwarden import uses this name
CARD_LOCAL_PHONE_KEY = "Phone (local)"
CARD_SLOT_KEYS = (CARD_NUMBER_KEY, CARD_CODE_KEY, CARD_MONTH_KEY, CARD_YEAR_KEY, CARD_HOLDER_KEY, CARD_BRAND_KEY,
                  CARD_PHONE_KEY, CARD_LOCAL_PHONE_KEY)
# A card's website is the normal URL field. Imports can leave it in a custom "Website" field.
CARD_WEBSITE_ALIAS = "Website"
_PHONE_ALIAS = re.compile(r"^Phone_[0-9a-f]{5}$")
# Older layouts, still read (and converted when the entry is saved from the card form):
CARD_TAG = "Card"
LEGACY_CODE_KEY = "Card code"
LEGACY_EXPIRY_KEY = "Expiry"
# Bitwarden imports can leave the card's password in a field like "Password_c724b".
_PASSWORD_ALIAS = re.compile(r"^Password_[0-9a-f]{5}$")


_TOLL_FREE = re.compile(r"^(?:\+?1)?(?:800|888|877|866|855|844|833)\d{4,7}$|^0?80[08]\d{4,8}$")


def is_toll_free(number: str) -> bool:
    """1-800 style (North America, also 1800-xxxx in Central America) and 0800/0808 numbers."""
    digits = re.sub(r"[^\d+]", "", number or "")
    return bool(_TOLL_FREE.match(digits))


def plan_phones(phone: str, local: str, extras: list[tuple[str, str]]):
    """Decide how a card's phone fields should end up.

    extras: leftover imported fields like ("Phone_59f63", "1-800 ..."). Returns
    (descriptions, new_phone, new_local, keys_to_drop). Descriptions carry no values.
    """
    phone, local = phone.strip(), local.strip()
    desc, drop = [], []
    left = []
    for key, value in extras:
        value = value.strip()
        if not value:
            continue
        if value in (phone, local):
            desc.append(f"remove {key} (same number as another phone field)")
            drop.append(key)
        else:
            left.append((key, value))
    if len(left) == 1 and not local:
        key, value = left[0]
        if not phone:
            desc.append(f"move {key} into Phone")
            phone = value
            drop.append(key)
        elif is_toll_free(value) and not is_toll_free(phone):
            desc.append(f"move {key} (toll-free) into Phone and the old Phone into Local phone")
            phone, local = value, phone
            drop.append(key)
        elif is_toll_free(phone) and not is_toll_free(value):
            desc.append(f"move {key} into Local phone")
            local = value
            drop.append(key)
        # otherwise: can't tell which is toll-free, so leave both alone
    return desc, phone, local, drop


def parse_expiry(text: str) -> tuple[str, str] | None:
    """'12/29', '12/2029', '1229', '12-29' -> ('12', '29'); None if it doesn't look like one."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*[/\-. ]?\s*(\d{2}|\d{4})\s*", text or "")
    if not m:
        return None
    mm, yy = int(m.group(1)), m.group(2)[-2:]
    return (f"{mm:02d}", yy) if 1 <= mm <= 12 else None


def expiry_from_parts(month: str, year: str) -> tuple[str, str] | None:
    month, year = (month or "").strip(), (year or "").strip()
    if not (month.isdigit() and 1 <= int(month) <= 12 and year.isdigit() and len(year) in (2, 4)):
        return None
    return f"{int(month):02d}", year[-2:]


OTP_FIELDS = ("otp", *otplib.LEGACY_FIELDS)
# Fields other apps keep for themselves (browser integration, passkeys): preserved, never shown or edited.
_INTERNAL_PREFIXES = ("KeePassXC-Browser", "KPH: ", "KPXC_", "KPEX_")


def is_internal_field(key: str) -> bool:
    return key in STANDARD_FIELDS or key in OTP_FIELDS or key.startswith(_INTERNAL_PREFIXES)


class VaultError(Exception):
    pass


class WrongCredentials(VaultError):
    pass


class FileUnreadable(VaultError):
    """The file could not be decrypted as a whole, e.g. mid-sync or damaged."""


# ---- read-only views (safe to hand to the UI thread) --------------------------
@dataclass(frozen=True)
class CustomField:
    key: str
    value: str
    protected: bool = False


def create_vault(path: str | Path, password: str) -> None:
    """Write a new, empty KDBX 4 database (AES-256, Argon2d) that KeePassXC and KeePassium open.

    Built in memory from pykeepass's blank template (new KDF salt and seeds on save, new root-folder
    UUID so it's not shared with every other database made this way), checked by decrypting it again,
    then written next to the target and renamed into place. Never replaces an existing file."""
    from pykeepass.pykeepass import BLANK_DATABASE_LOCATION, BLANK_DATABASE_PASSWORD

    target = Path(path)
    if not password:
        raise VaultError("Choose a master password.")
    if target.exists():
        raise VaultError("A file with that name already exists. Choose another name.")
    kp = PyKeePass(BLANK_DATABASE_LOCATION, BLANK_DATABASE_PASSWORD)
    kp.password, kp.keyfile = password, None
    kp.root_group.uuid = uuidlib.uuid4()
    now = datetime.now(timezone.utc)
    kp.root_group.ctime = kp.root_group.mtime = kp.root_group.atime = now  # not the template's 2020
    kp.database_name = target.stem
    buf = io.BytesIO()
    kp.save(buf)
    data = buf.getvalue()
    check = PyKeePass(io.BytesIO(data), password)
    if check.root_group.uuid != kp.root_group.uuid:
        raise VaultError("The new database could not be verified; nothing was written.")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.vaultline-tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.rename(tmp, target)  # fails on Windows if the name was taken meanwhile: never overwrites
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise VaultError(f"Could not create the database: {exc}") from exc


@dataclass(eq=False)
class EntryView:
    uuid: str
    title: str
    username: str
    password: str
    url: str
    notes: str
    otp_text: str  # stored otp value as-is, for the edit dialog
    group: str  # display path, "" for top level
    group_uuid: str = ""
    custom: tuple[CustomField, ...] = ()
    attachments: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    icon_uuid: str | None = None
    created: datetime | None = None
    modified: datetime | None = None
    totp: pyotp.TOTP | None = field(default=None, repr=False)
    password_alias: str = ""  # custom field standing in for an empty Password (Bitwarden import)
    references: tuple[str, ...] = ()  # "username"/"password" when stored as a {REF:...} to another entry

    def get_field(self, key: str) -> str:
        return next((f.value for f in self.custom if f.key == key), "")

    def has_field(self, key: str) -> bool:
        return any(f.key == key for f in self.custom)

    @property
    def _legacy_card(self) -> bool:
        return any(t.casefold() == CARD_TAG.casefold() for t in self.tags) or self.has_field(LEGACY_CODE_KEY)

    @property
    def is_card(self) -> bool:
        return self.has_field(CARD_NUMBER_KEY) or self._legacy_card

    @property
    def card_number(self) -> str:
        if self.has_field(CARD_NUMBER_KEY):
            return self.get_field(CARD_NUMBER_KEY)
        return self.username if self._legacy_card else ""

    @property
    def card_code(self) -> str:
        return self.get_field(CARD_CODE_KEY) or self.get_field(LEGACY_CODE_KEY)

    @property
    def card_expiry(self) -> tuple[str, str] | None:
        return expiry_from_parts(self.get_field(CARD_MONTH_KEY), self.get_field(CARD_YEAR_KEY)) or \
            parse_expiry(self.get_field(LEGACY_EXPIRY_KEY))

    @property
    def card_holder(self) -> str:
        return self.get_field(CARD_HOLDER_KEY)

    @property
    def card_brand(self) -> str:
        return self.get_field(CARD_BRAND_KEY)

    @property
    def card_phone(self) -> str:
        return self.get_field(CARD_PHONE_KEY)

    @property
    def card_local_phone(self) -> str:
        return self.get_field(CARD_LOCAL_PHONE_KEY)

    @property
    def card_last4(self) -> str:
        digits = "".join(c for c in self.card_number if c.isdigit())
        return digits[-4:] if len(digits) >= 4 else ""

    def matches(self, terms: list[str]) -> bool:
        parts = [self.title, self.username, self.url, self.group, " ".join(self.tags)]
        digits = ""
        if self.is_card:
            parts += [self.card_brand, self.card_holder]
            digits = "".join(c for c in self.card_number if c.isdigit())
        hay = "\n".join(parts).lower()
        for t in terms:
            if t in hay:
                continue
            # card numbers match with or without spaces/dashes
            td = t.replace("-", "")
            if digits and td.isdigit() and td in digits:
                continue
            return False
        return True


def card_fixes(v: EntryView) -> list[str]:
    """What NormalizeCards would change on this entry, described without any values."""
    if not v.is_card:
        return []
    out = []
    by_key = {f.key: f for f in v.custom}
    if CARD_NUMBER_KEY not in by_key and v.card_number:
        out.append("move card number from username to card_number")
    if LEGACY_CODE_KEY in by_key and CARD_CODE_KEY not in by_key:
        out.append("move 'Card code' to card_code")
    month, year = v.get_field(CARD_MONTH_KEY), v.get_field(CARD_YEAR_KEY)
    if not expiry_from_parts(month, year) and parse_expiry(v.get_field(LEGACY_EXPIRY_KEY)):
        out.append("split 'Expiry' into card_expMonth / card_expYear")
    elif expiry_from_parts(month, year) and month.strip() != expiry_from_parts(month, year)[0]:
        out.append("pad expiry month to two digits")
    num = by_key.get(CARD_NUMBER_KEY)
    if (num and not num.protected) or (CARD_NUMBER_KEY not in by_key and v.card_number):
        out.append("hide card number")
    code = by_key.get(CARD_CODE_KEY)
    if code and not code.protected:
        out.append("hide card code")
    if v.password_alias:
        out.append(f"move {v.password_alias} into Password")
    web = by_key.get(CARD_WEBSITE_ALIAS)
    if web and web.value.strip():
        if not v.url.strip():
            out.append("move Website into URL")
        elif web.value.strip() == v.url.strip():
            out.append("remove Website (same as URL)")
    extras = [(f.key, f.value) for f in v.custom if _PHONE_ALIAS.match(f.key)]
    out += plan_phones(v.card_phone, v.card_local_phone, extras)[0]
    return out


@dataclass(frozen=True)
class GroupView:
    uuid: str
    name: str
    path: tuple[str, ...]
    depth: int
    parent_uuid: str = ""  # the root's uuid for top-level folders

    @property
    def display(self) -> str:
        return " / ".join(self.path)


@dataclass(eq=False)
class Snapshot:
    entries: list[EntryView]
    groups: list[GroupView]          # excludes the root and the recycle bin
    icons: dict[str, bytes]          # icon uuid -> image bytes (usually PNG)
    root_uuid: str


# ---- changes ------------------------------------------------------------------
@dataclass
class EntryEdit:
    uuid: str | None  # None creates a new entry
    title: str
    username: str
    password: str
    url: str
    notes: str
    otp: str | None  # otpauth URI, "" to remove, None to leave the OTP fields untouched
    group_uuid: str | None = None  # destination folder; None keeps it (or top level for new)
    new_group: str = ""            # "Work/Clients": create (if needed) and move there
    custom: list[CustomField] | None = None  # None leaves custom fields untouched
    card: bool = False             # saved from the card form (card fields arrive in `custom`)


@dataclass
class IconUpdate:
    """Set custom icons: entry uuid -> PNG bytes."""
    icons: dict[str, bytes]


@dataclass
class UpdateEntry:
    """Change non-secret fields of one entry (used by the command line)."""
    uuid: str
    title: str | None = None
    username: str | None = None
    url: str | None = None
    tags_add: list[str] = field(default_factory=list)
    tags_remove: list[str] = field(default_factory=list)
    card_brand: str | None = None    # non-secret card details ("" clears)
    card_holder: str | None = None
    card_phone: str | None = None
    card_local_phone: str | None = None


@dataclass
class MoveEntries:
    uuids: list[str]
    group_uuid: str | None = None  # existing folder (root uuid = top level)
    new_group: str = ""            # or a folder path to create, e.g. "Work/Clients"


@dataclass
class DeleteEntries:
    """Move entries to the recycle bin."""
    uuids: list[str]


@dataclass
class CreateFolder:
    path: str                      # "Work/Clients"; existing parts are reused


@dataclass
class RenameFolder:
    uuid: str
    name: str


@dataclass
class MoveFolder:
    uuid: str
    parent_uuid: str | None = None  # None = top level


@dataclass
class DeleteFolder:
    """Move a folder and everything in it to the recycle bin."""
    uuid: str


@dataclass
class NormalizeCards:
    """Bring card entries to the standard layout (see card_fixes). Values never leave the vault."""
    uuids: list[str]


@dataclass
class ChangeMasterPassword:
    new_password: str


# ---- helpers --------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _string_items(entry) -> list[tuple[str, str, bool]]:
    out = []
    for s in entry._element.findall("String"):
        key, val = s.find("Key"), s.find("Value")
        if key is not None and key.text:
            text = (val.text if val is not None else "") or ""
            prot = val is not None and val.get("Protected") == "True"
            out.append((key.text, text, prot))
    return out


def _string_fields(entry) -> dict[str, str]:
    return {k: v for k, v, _ in _string_items(entry)}


def _remove_field(entry, key: str) -> None:
    for s in entry._element.findall("String"):
        k = s.find("Key")
        if k is not None and k.text == key:
            entry._element.remove(s)


def _b64_uuid_to_hex(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return uuidlib.UUID(bytes=base64.b64decode(text)).hex
    except (ValueError, TypeError):
        return None


def _hex_to_b64_uuid(hexstr: str) -> str:
    return base64.b64encode(uuidlib.UUID(hex=hexstr).bytes).decode()


class Vault:
    def __init__(self, path: str, password: str | None, keyfile: str | None, backup_dir: Path,
                 backups_to_keep: int = 10):
        self.path = Path(path)
        self._password = password or None
        self._keyfile = keyfile or None
        self._backup_dir = backup_dir
        self._backups_to_keep = backups_to_keep
        self._kp: PyKeePass | None = None
        self._sha: str | None = None
        self.last_touched: list[str] = []  # entry uuids changed by the last save()
        self.last_report: list[dict] = []  # value-free notes from the last save() (card fixes)

    # ---- reading -------------------------------------------------------
    def _read_disk(self) -> bytes:
        last_err = None
        for _ in range(20):  # sync clients can hold the file for a moment
            try:
                return self.path.read_bytes()
            except PermissionError as exc:
                last_err = exc
                time.sleep(0.25)
            except FileNotFoundError:
                # Some sync clients delete-then-create; give it a moment.
                last_err = FileNotFoundError(f"Database not found: {self.path}")
                time.sleep(0.25)
        raise VaultError(str(last_err))

    def _parse(self, data: bytes, password: str | None = None) -> PyKeePass:
        try:
            return PyKeePass(io.BytesIO(data), password=password if password is not None else self._password,
                             keyfile=self._keyfile)
        except CredentialsError:
            raise WrongCredentials("Wrong master password or key file") from None
        except (HeaderChecksumError, PayloadChecksumError):
            raise FileUnreadable("Database file is damaged or still being written") from None
        except Exception as exc:  # construct parsing errors, bad keyfile, etc.
            raise FileUnreadable(f"Could not open database: {exc}") from None

    def open(self) -> Snapshot:
        data = self._read_disk()
        self._kp = self._parse(data)
        self._sha = _sha(data)
        return self.snapshot()

    def reload_if_changed(self) -> Snapshot | None:
        """Re-read the file if its contents changed. Returns None if unchanged."""
        data = self._read_disk()
        sha = _sha(data)
        if sha == self._sha:
            return None
        self._kp = self._parse(data)  # raises if mid-sync / corrupt; caller retries later
        self._sha = sha
        return self.snapshot()

    def _meta(self):
        return self._kp.tree.getroot().find("Meta")

    def _custom_icons(self) -> dict[str, bytes]:
        icons = {}
        meta = self._meta()
        holder = meta.find("CustomIcons") if meta is not None else None
        if holder is None:
            return icons
        for icon in holder.findall("Icon"):
            uid = _b64_uuid_to_hex(icon.findtext("UUID"))
            data = icon.findtext("Data")
            if uid and data:
                try:
                    icons[uid] = base64.b64decode(data)
                except ValueError:
                    pass
        return icons

    def snapshot(self) -> Snapshot:
        kp = self._kp
        assert kp is not None
        root = kp.root_group
        recycle = kp.recyclebin_group
        bin_uuid = recycle.uuid if recycle is not None else None

        def path_of(g) -> tuple[tuple[str, ...], bool]:
            names, in_bin = [], False
            while g is not None and not g.is_root_group:
                if bin_uuid is not None and g.uuid == bin_uuid:
                    in_bin = True
                names.insert(0, g.name or "")
                g = g.parentgroup
            return tuple(names), in_bin

        groups = []
        for g in kp.groups:
            if g.is_root_group:
                continue
            path, in_bin = path_of(g)
            if not in_bin:
                parent = g.parentgroup
                groups.append(GroupView(g.uuid.hex, g.name or "", path, len(path) - 1,
                                        parent.uuid.hex if parent is not None else ""))
        groups.sort(key=lambda gv: tuple(p.casefold() for p in gv.path))

        views = []
        for e in kp.entries:
            path, in_bin = path_of(e.group)
            if in_bin:
                continue
            views.append(self._entry_view(e, path))
        views.sort(key=lambda v: (v.title.casefold(), v.username.casefold()))
        return Snapshot(views, groups, self._custom_icons(), root.uuid.hex)

    def _entry_view(self, e, path: tuple[str, ...]) -> EntryView:
        items = _string_items(e)
        fields = {k: v for k, v, _ in items}
        custom = tuple(CustomField(k, v, p) for k, v, p in items if not is_internal_field(k))
        try:
            attachments = tuple(a.filename for a in e.attachments if a.filename)
        except Exception:
            attachments = ()
        password = self._deref(e, "password", fields.get("Password", ""))
        alias = ""
        if not password:
            aliases = [f for f in custom if _PASSWORD_ALIAS.match(f.key) and f.value]
            if len(aliases) == 1:
                alias, password = aliases[0].key, aliases[0].value
        refs = tuple(name for name, key in (("username", "UserName"), ("password", "Password"))
                     if "{REF:" in fields.get(key, "").upper())
        return EntryView(
            references=refs,
            password_alias=alias,
            uuid=e.uuid.hex,
            title=fields.get("Title", ""),
            username=self._deref(e, "username", fields.get("UserName", "")),
            password=password,
            url=fields.get("URL", ""),
            notes=fields.get("Notes", ""),
            otp_text=fields.get("otp") or fields.get("TimeOtp-Secret-Base32") or fields.get("TOTP Seed") or "",
            group=" / ".join(path),
            group_uuid=e.group.uuid.hex,
            custom=custom,
            attachments=attachments,
            tags=tuple(e.tags or ()),
            icon_uuid=_b64_uuid_to_hex(e._element.findtext("CustomIconUUID")),
            created=e.ctime,
            modified=e.mtime,
            totp=otplib.from_fields(fields),
        )

    @staticmethod
    def _deref(entry, attr: str, raw: str) -> str:
        if "{REF:" not in raw:
            return raw
        try:
            return entry.deref(attr) or ""
        except Exception:
            return raw

    # ---- writing -------------------------------------------------------
    def save_entry(self, edit: EntryEdit) -> tuple[Snapshot, bool]:
        return self.save([edit])

    def save(self, changes: list) -> tuple[Snapshot, bool]:
        """Apply changes and write the file. Returns (snapshot, merged_external)."""
        merged = False
        for _ in range(4):
            self.last_touched = []
            self.last_report = []
            disk = self._read_disk()
            disk_sha = _sha(disk)
            if disk_sha != self._sha:
                self._kp = self._parse(disk)
                self._sha = disk_sha
                merged = True

            new_password = None
            for change in changes:
                if isinstance(change, ChangeMasterPassword):
                    new_password = change.new_password
                    self._kp.password = new_password
                else:
                    self._apply(change)
            buf = io.BytesIO()
            self._kp.save(buf)
            data = buf.getvalue()
            # never write something we cannot read back (with the new password, if changed)
            verified = self._parse(data, password=new_password)

            # Did a sync land while we were encrypting? Then start over on top of it.
            if _sha(self._read_disk()) != disk_sha:
                self._kp = None
                self._sha = None
                continue

            self._backup(disk)
            self._atomic_write(data)
            self._kp = verified
            self._sha = _sha(data)
            if new_password is not None:
                self._password = new_password
            return self.snapshot(), merged
        raise VaultError("The database file keeps changing on disk; try again in a moment")

    def _apply(self, change) -> None:
        kp = self._kp
        if isinstance(change, EntryEdit):
            self._apply_edit(change)
        elif isinstance(change, IconUpdate):
            self._apply_icons(change)
        elif isinstance(change, UpdateEntry):
            e = self._entry(change.uuid)
            e.save_history()
            if change.title is not None:
                e.title = change.title
            if change.username is not None:
                e.username = change.username
            if change.url is not None:
                e.url = change.url
            for key, value in ((CARD_BRAND_KEY, change.card_brand), (CARD_HOLDER_KEY, change.card_holder),
                               (CARD_PHONE_KEY, change.card_phone),
                               (CARD_LOCAL_PHONE_KEY, change.card_local_phone)):
                if value is not None:
                    _remove_field(e, key)
                    if value:
                        e._set_string_field(key, value, False)
            if change.tags_add or change.tags_remove:
                tags = [t for t in (e.tags or []) if t not in change.tags_remove]
                tags += [t for t in change.tags_add if t not in tags]
                e.tags = tags
            e.touch(modify=True)
            self.last_touched.append(e.uuid.hex)
        elif isinstance(change, MoveEntries):
            dest = self._ensure_group_path(change.new_group) if change.new_group.strip() \
                else self._group(change.group_uuid)
            for u in change.uuids:
                e = self._entry(u)
                if e.group.uuid != dest.uuid:
                    kp.move_entry(e, dest)
                    e.touch(modify=True)
        elif isinstance(change, DeleteEntries):
            for u in change.uuids:
                kp.trash_entry(self._entry(u))
        elif isinstance(change, CreateFolder):
            if not change.path.strip(" /"):
                raise VaultError("Folder name is empty")
            self._ensure_group_path(change.path)
        elif isinstance(change, RenameFolder):
            g = self._group(change.uuid)
            name = change.name.strip()
            if not name or "/" in name:
                raise VaultError("Folder names can't be empty or contain /")
            g.name = name
            g.touch(modify=True)
        elif isinstance(change, MoveFolder):
            g = self._group(change.uuid)
            parent = self._group(change.parent_uuid) if change.parent_uuid else kp.root_group
            p = parent
            while p is not None:  # refuse to move a folder inside itself
                if p.uuid == g.uuid:
                    raise VaultError("Can't move a folder into itself")
                p = p.parentgroup
            kp.move_group(g, parent)
        elif isinstance(change, DeleteFolder):
            kp.trash_group(self._group(change.uuid))
        elif isinstance(change, NormalizeCards):
            for u in change.uuids:
                self._normalize_card(self._entry(u))
        else:
            raise TypeError(f"Unknown change {change!r}")

    def _normalize_card(self, e) -> None:
        view = self._entry_view(e, ())
        fixes = card_fixes(view)
        if not fixes:
            return
        e.save_history()
        items = {k: (v, p) for k, v, p in _string_items(e)}

        def put(key, value, protected):
            _remove_field(e, key)
            e._set_string_field(key, value, protected)

        number = items.get(CARD_NUMBER_KEY, (view.card_number, True))[0]
        if number:
            put(CARD_NUMBER_KEY, number, True)
        if CARD_NUMBER_KEY not in items and view.card_number and view.username == view.card_number:
            e.username = ""  # older layout kept the number in the username
        code = items.get(CARD_CODE_KEY, items.get(LEGACY_CODE_KEY, ("", True)))[0]
        if code:
            put(CARD_CODE_KEY, code, True)
        _remove_field(e, LEGACY_CODE_KEY)
        exp = view.card_expiry
        if exp:
            year_raw = items.get(CARD_YEAR_KEY, ("",))[0].strip()
            put(CARD_MONTH_KEY, exp[0], False)
            put(CARD_YEAR_KEY, exp[1] if len(year_raw) == 2 else "20" + exp[1], False)
            if parse_expiry(items.get(LEGACY_EXPIRY_KEY, ("",))[0]):
                _remove_field(e, LEGACY_EXPIRY_KEY)
        if view.password_alias:
            e.password = view.password
            _remove_field(e, view.password_alias)
        web = items.get(CARD_WEBSITE_ALIAS, ("",))[0].strip()
        if web:
            if not (e.url or "").strip():
                e.url = web
                _remove_field(e, CARD_WEBSITE_ALIAS)
            elif web == e.url.strip():
                _remove_field(e, CARD_WEBSITE_ALIAS)
        old_phone = items.get(CARD_PHONE_KEY, ("",))[0].strip()
        old_local = items.get(CARD_LOCAL_PHONE_KEY, ("",))[0].strip()
        extras = [(k, v) for k, (v, _p) in items.items() if _PHONE_ALIAS.match(k)]
        _d, phone, local, drop = plan_phones(old_phone, old_local, extras)
        if phone != old_phone:
            put(CARD_PHONE_KEY, phone, False)
        if local != old_local:
            put(CARD_LOCAL_PHONE_KEY, local, False)
        for k in drop:
            _remove_field(e, k)
        if CARD_TAG in (e.tags or []):
            e.tags = [t for t in e.tags if t != CARD_TAG]
        e.touch(modify=True)
        self.last_touched.append(e.uuid.hex)
        self.last_report.append({"id": e.uuid.hex, "title": view.title, "fixes": fixes})

    def _entry(self, hexuuid: str):
        try:
            e = self._kp.find_entries(uuid=uuidlib.UUID(hex=hexuuid), first=True)
        except ValueError:
            e = None
        if e is None:
            raise VaultError(f"No entry with id {hexuuid}")
        return e

    def _group(self, hexuuid: str | None):
        if not hexuuid:
            return self._kp.root_group
        try:
            g = self._kp.find_groups(uuid=uuidlib.UUID(hex=hexuuid), first=True)
        except ValueError:
            g = None
        if g is None:
            raise VaultError(f"No folder with id {hexuuid}")
        recycle = self._kp.recyclebin_group
        if recycle is not None and g.uuid == recycle.uuid:
            raise VaultError("That's the recycle bin")
        return g

    def _find_group(self, hexuuid: str | None):
        if not hexuuid:
            return None
        return self._kp.find_groups(uuid=uuidlib.UUID(hex=hexuuid), first=True)

    def _ensure_group_path(self, path: str):
        g = self._kp.root_group
        for name in [p.strip() for p in path.replace("\\", "/").split("/") if p.strip()]:
            child = next((c for c in g.subgroups if (c.name or "") == name), None)
            g = child if child is not None else self._kp.add_group(g, name)
        return g

    def _apply_edit(self, edit: EntryEdit) -> None:
        kp = self._kp
        assert kp is not None
        if edit.new_group.strip():
            dest = self._ensure_group_path(edit.new_group)
        else:
            dest = self._find_group(edit.group_uuid)

        entry = None
        if edit.uuid:
            entry = kp.find_entries(uuid=uuidlib.UUID(hex=edit.uuid), first=True)
        if entry is None:
            entry = kp.add_entry(dest or kp.root_group, edit.title, edit.username, edit.password,
                                 url=edit.url or None, notes=edit.notes or None, force_creation=True)
        else:
            entry.save_history()
            entry.title = edit.title
            entry.username = edit.username
            entry.password = edit.password
            entry.url = edit.url
            entry.notes = edit.notes
            if dest is not None and dest.uuid != entry.group.uuid:
                kp.move_entry(entry, dest)

        if edit.otp is not None:
            old_otp = _string_fields(entry).get("otp")
            if edit.otp:
                if edit.otp != old_otp:
                    _remove_field(entry, "otp")
                    entry._set_string_field("otp", edit.otp, True)
            else:
                for key in OTP_FIELDS:
                    _remove_field(entry, key)

        if edit.custom is not None:
            wanted = {f.key: f for f in edit.custom if f.key and not is_internal_field(f.key)}
            for key, _v, _p in _string_items(entry):
                if not is_internal_field(key) and key not in wanted:
                    _remove_field(entry, key)
            current = {k: (v, p) for k, v, p in _string_items(entry)}
            for f in wanted.values():
                if current.get(f.key) != (f.value, f.protected):
                    _remove_field(entry, f.key)
                    entry._set_string_field(f.key, f.value, f.protected)
        entry.touch(modify=True)
        self.last_touched.append(entry.uuid.hex)

    def _ensure_icon(self, png: bytes) -> str:
        """Add an icon to Meta/CustomIcons (reusing an identical one). Returns its uuid hex."""
        for uid, data in self._custom_icons().items():
            if data == png:
                return uid
        meta = self._meta()
        holder = meta.find("CustomIcons")
        if holder is None:
            holder = etree.SubElement(meta, "CustomIcons")
        icon = etree.SubElement(holder, "Icon")
        uid = uuidlib.uuid4()
        etree.SubElement(icon, "UUID").text = base64.b64encode(uid.bytes).decode()
        etree.SubElement(icon, "Data").text = base64.b64encode(png).decode()
        return uid.hex

    def _apply_icons(self, update: IconUpdate) -> None:
        kp = self._kp
        for entry_uuid, png in update.icons.items():
            entry = kp.find_entries(uuid=uuidlib.UUID(hex=entry_uuid), first=True)
            if entry is None or not png:
                continue
            icon_hex = self._ensure_icon(png)
            el = entry._element.find("CustomIconUUID")
            if el is None:
                el = etree.Element("CustomIconUUID")
                icon_id = entry._element.find("IconID")
                if icon_id is not None:
                    icon_id.addnext(el)
                else:
                    entry._element.append(el)
            new_text = _hex_to_b64_uuid(icon_hex)
            if el.text != new_text:
                el.text = new_text
                entry.touch(modify=True)
        self._drop_unused_icons()

    def _drop_unused_icons(self) -> None:
        meta = self._meta()
        holder = meta.find("CustomIcons") if meta is not None else None
        if holder is None:
            return
        used = {t for t in self._kp.tree.getroot().xpath("//CustomIconUUID/text()")}
        for icon in list(holder.findall("Icon")):
            if icon.findtext("UUID") not in used:
                holder.remove(icon)

    def _backup(self, data: bytes) -> None:
        try:
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (self._backup_dir / f"{self.path.stem}.{stamp}.kdbx").write_bytes(data)
            old = sorted(self._backup_dir.glob(f"{self.path.stem}.*.kdbx"))
            for p in old[: max(0, len(old) - self._backups_to_keep)]:
                p.unlink(missing_ok=True)
        except OSError:
            pass  # a failed backup should not block saving

    def _atomic_write(self, data: bytes) -> None:
        tmp = self.path.with_name(f".{self.path.name}.vaultline-tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        last_err = None
        for _ in range(40):  # up to ~10 s while the sync client holds the file
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError as exc:
                last_err = exc
                time.sleep(0.25)
        try:
            tmp.unlink()
        except OSError:
            pass
        raise VaultError(f"Could not replace the database file (locked by another program?): {last_err}")

    def check_password(self, password: str) -> bool:
        return bool(self._password) and password == self._password

    def suspend(self) -> None:
        """PIN lock: drop the decrypted database, keep only the credentials to reopen it."""
        self._kp = None
        self._sha = None

    def close(self) -> None:
        self._kp = None
        self._sha = None
        self._password = None
        self._keyfile = None
