"""Read a Bitwarden .json export (plain or password protected) into entries for a new database.

Decryption follows Bitwarden's export format, as KeePassXC's BitwardenReader does: the export password
is stretched with PBKDF2-SHA256 (kdfType 0) or Argon2id (kdfType 1, salt hashed first), expanded with
HKDF into an encryption key and a MAC key; the MAC is checked before anything is decrypted
(AES-256-CBC). Field names follow KeePassXC's importer (card_*, KP2A_URL_n, KPEX_PASSKEY_*), except
identities, which use Vaultline's split profile fields (identity.py).

Nothing here logs or raises with a value in it. No Qt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import identity as idlib, otp as otplib


class BitwardenError(ValueError):
    """The file can't be imported; the message says why (never with a value in it)."""


@dataclass
class ImportItem:
    title: str
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    otp: str = ""                                    # otpauth URI, or ""
    folder: str = ""                                 # "Work/Clients", "" = top level
    fields: list[tuple[str, str, bool]] = field(default_factory=list)  # (key, value, protected)
    tags: list[str] = field(default_factory=list)
    created: datetime | None = None
    modified: datetime | None = None

    def __repr__(self) -> str:  # never show values in tracebacks or test output
        return f"ImportItem(title={self.title!r}, folder={self.folder!r}, fields={len(self.fields)})"


# ---- reading and decrypting -------------------------------------------------------------------------------
def is_encrypted(path: str | Path) -> bool:
    """Whether the export needs its password (False for a plain export or anything unreadable)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("encrypted")) and "kdfType" in data


def _hkdf_expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
    out, block, n = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([n]), hashlib.sha256).digest()
        out += block
        n += 1
    return out[:length]


def _stretch(password: str, data: dict) -> bytes:
    salt = str(data.get("salt") or "").encode("utf-8")
    kdf = data.get("kdfType")
    iterations = int(data.get("kdfIterations") or 0)
    if kdf == 0:
        if iterations <= 0:
            raise BitwardenError("The export's key settings are damaged (no PBKDF2 iterations).")
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 32)
    if kdf == 1:
        from argon2.low_level import Type, hash_secret_raw
        memory = int(data.get("kdfMemory") or 0) * 1024
        parallelism = int(data.get("kdfParallelism") or 0)
        if iterations <= 0 or memory <= 0 or parallelism <= 0:
            raise BitwardenError("The export's key settings are damaged (Argon2 settings missing).")
        return hash_secret_raw(password.encode("utf-8"), hashlib.sha256(salt).digest(), time_cost=iterations,
                               memory_cost=memory, parallelism=parallelism, hash_len=32, type=Type.ID)
    raise BitwardenError("This export uses a key setting Vaultline doesn't know (only PBKDF2 and Argon2id).")


def _cipher_parts(text: str) -> tuple[bytes, bytes, bytes]:
    """Bitwarden's "2.iv|data|mac" (all base64) -> (iv, data, mac)."""
    kind, _, rest = str(text or "").partition(".")
    parts = rest.split("|")
    if kind != "2" or len(parts) != 3:
        raise BitwardenError("The export's encrypted data isn't in the expected format.")
    try:
        return tuple(base64.b64decode(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        raise BitwardenError("The export's encrypted data is damaged.") from None


def _open(text: str, enc: bytes, mac: bytes) -> bytes:
    from Cryptodome.Cipher import AES
    iv, data, tag = _cipher_parts(text)
    if not hmac.compare_digest(hmac.new(mac, iv + data, hashlib.sha256).digest(), tag):
        raise BitwardenError("Wrong export password.")
    plain = AES.new(enc, AES.MODE_CBC, iv).decrypt(data)
    pad = plain[-1] if plain else 0
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        raise BitwardenError("Wrong export password.")
    return plain[:-pad]


def decrypt(data: dict, password: str) -> dict:
    """A password-protected export -> the plain export (the same shape Bitwarden writes unencrypted)."""
    if not password:
        raise BitwardenError("This export is password protected. Enter the password you set when exporting.")
    key = _stretch(password, data)
    enc, mac = _hkdf_expand(key, b"enc"), _hkdf_expand(key, b"mac")
    _open(data.get("encKeyValidation_DO_NOT_EDIT", ""), enc, mac)  # wrong password stops here
    try:
        out = json.loads(_open(data.get("data", ""), enc, mac).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BitwardenError("The export decrypted, but what's inside isn't readable.") from None
    if not isinstance(out, dict):
        raise BitwardenError("The export decrypted, but what's inside isn't a Bitwarden export.")
    return out


def load(path: str | Path, password: str = "") -> dict:
    """Read an export file, decrypting it if it's password protected."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise BitwardenError(f"Couldn't read the file ({exc.strerror or 'error'}).") from None
    except ValueError:
        raise BitwardenError("That file isn't a Bitwarden .json export.") from None
    if not isinstance(data, dict):
        raise BitwardenError("That file isn't a Bitwarden .json export.")
    if data.get("encrypted"):
        if "kdfType" not in data:
            raise BitwardenError("This is an 'account restricted' export, which only Bitwarden can open. "
                                 "Export again and choose 'Password protected'.")
        data = decrypt(data, password)
    if "items" not in data:
        raise BitwardenError("That file isn't a Bitwarden .json export (no items in it).")
    return data


# ---- mapping ---------------------------------------------------------------------------------------------
_RESERVED = {"Title", "UserName", "Password", "URL", "Notes", "otp"}
_FOLDER_SAFE = re.compile(r"[\r\n\t]+")


def _when(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def _b64url_from_hex(text: str) -> str:
    try:
        raw = bytes.fromhex(text.replace("-", ""))
    except ValueError:
        return text
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _passkey_fields(cred: dict) -> list[tuple[str, str, bool]]:
    out = []
    if cred.get("credentialId"):
        out.append(("KPEX_PASSKEY_CREDENTIAL_ID", _b64url_from_hex(str(cred["credentialId"])), True))
    if cred.get("keyValue"):
        raw = base64.urlsafe_b64decode(str(cred["keyValue"]) + "=" * (-len(str(cred["keyValue"])) % 4))
        pem = "-----BEGIN PRIVATE KEY-----" + base64.b64encode(raw).decode() + "-----END PRIVATE KEY-----"
        out.append(("KPEX_PASSKEY_PRIVATE_KEY_PEM", pem, True))
    out.append(("KPEX_PASSKEY_USERNAME", str(cred.get("userName") or ""), False))
    out.append(("KPEX_PASSKEY_RELYING_PARTY", str(cred.get("rpId") or ""), False))
    if cred.get("userHandle"):
        out.append(("KPEX_PASSKEY_USER_HANDLE", str(cred["userHandle"]), True))
    return out


def _item(raw: dict, folders: dict[str, str]) -> ImportItem:
    s = lambda v: "" if v is None else str(v)  # noqa: E731
    it = ImportItem(title=s(raw.get("name")) or "(untitled)", notes=s(raw.get("notes")),
                    created=_when(raw.get("creationDate")), modified=_when(raw.get("revisionDate")))
    folder_id = raw.get("folderId") or next(iter(raw.get("collectionIds") or []), None)
    it.folder = _FOLDER_SAFE.sub(" ", folders.get(folder_id, "")).strip("/ ")
    if raw.get("favorite"):
        it.tags.append("Favorite")
    taken: set[str] = set()

    def add(key: str, value: str, protected: bool = False) -> None:
        if value is None or value == "":
            return
        name, n = key, 1
        while name in taken or name in _RESERVED:
            n += 1
            name = f"{key} ({n})"
        taken.add(name)
        it.fields.append((name, str(value), protected))

    login = raw.get("login") or {}
    if login:
        it.username, it.password = s(login.get("username")), s(login.get("password"))
        totp = s(login.get("totp")).strip()
        if totp:
            try:
                it.otp = otplib.normalize(totp, it.title, it.username)
            except otplib.OtpError:
                add("TOTP (not imported)", totp, True)  # e.g. Steam codes: kept, not used
        uris = [s((u or {}).get("uri")).strip() for u in login.get("uris") or []]
        uris = [u for u in uris if u]
        if uris:
            it.url = uris[0]
            for n, extra in enumerate(uris[1:], 1):
                add(f"KP2A_URL_{n}", extra)
        creds = login.get("fido2Credentials") or []
        for cred in creds[:1]:  # KeePassXC keeps one passkey per entry
            for key, value, prot in _passkey_fields(cred):
                add(key, value, prot)
        if creds:
            it.tags.append("Passkey")
    card = raw.get("card") or {}
    if card:
        for key, prot in (("number", True), ("code", True), ("expMonth", False), ("expYear", False),
                          ("cardholderName", False), ("brand", False)):
            add(f"card_{key}", s(card.get(key)).strip(), prot)
    ident = raw.get("identity") or {}
    if ident:
        first = " ".join(p for p in (s(ident.get("firstName")), s(ident.get("middleName"))) if p.strip())
        street = ", ".join(p for p in (s(ident.get(k)) for k in ("address1", "address2", "address3")) if p.strip())
        for key, value in ((idlib.FIRST, first), (idlib.LAST, s(ident.get("lastName"))),
                           (idlib.EMAIL, s(ident.get("email"))), (idlib.PHONE, s(ident.get("phone"))),
                           (idlib.ADDRESS, street), (idlib.CITY, s(ident.get("city"))),
                           (idlib.STATE, s(ident.get("state"))), (idlib.POSTAL, s(ident.get("postalCode"))),
                           (idlib.COUNTRY, s(ident.get("country")))):
            add(key, value.strip(), key in idlib.PROTECTED)
        add("identity_title", s(ident.get("title")).strip())
        add("identity_company", s(ident.get("company")).strip())
        for key in ("ssn", "passportNumber", "licenseNumber"):
            add(f"identity_{key}", s(ident.get(key)).strip(), True)
        if not it.username and s(ident.get("username")).strip():
            it.username = s(ident.get("username")).strip()
        else:
            add("identity_username", s(ident.get("username")).strip())
    ssh = raw.get("sshKey") or {}
    if ssh:
        add("ssh_privateKey", s(ssh.get("privateKey")), True)
        add("ssh_publicKey", s(ssh.get("publicKey")))
        add("ssh_keyFingerprint", s(ssh.get("keyFingerprint")))
    for f in raw.get("fields") or []:
        f = f or {}
        if f.get("type") == 3:  # linked to another field of the same item: nothing to store
            continue
        add(s(f.get("name")).strip() or "Field", s(f.get("value")), f.get("type") == 1)
    return it


def items(export: dict) -> list[ImportItem]:
    """The export's items as entries to create (folders by path; organisation collections too)."""
    folders = {}
    for f in (export.get("folders") or []) + (export.get("collections") or []):
        if isinstance(f, dict) and f.get("id"):
            folders[f["id"]] = str(f.get("name") or "")
    return [_item(r, folders) for r in export.get("items") or [] if isinstance(r, dict)]
