"""Sharing: the main database is the master copy, and entries or whole folders are "shared with" vaults
the user manages (attached databases whose AutoOpen entry says so). Each shared entry has a linked
copy in every vault it's shared with; `sync` keeps them in step.

- Changes made in the main database go out to the copies.
- Changes made to a copy (someone else edited it, or deleted it) are never applied on their own: they
  come back as Conflicts for the user to decide (Use theirs / Keep mine, or Put back / Stop sharing).
- Unsharing, or deleting the entry in the main database, sends the copy to that vault's recycle bin.
- Entries someone else adds to a managed vault are added to the main database (in a folder named after
  that vault) and linked, so the main database has everything.

Hidden data, in KeePass's CustomData (KeePassXC keeps it when it saves):
  entry/folder  Vaultline.ShareWith     JSON list of vault ids
  entry         Vaultline.NotShareWith  vault ids its folder shares with but it shouldn't
  entry         Vaultline.Links         JSON {vault id: {"copy": uuid hex, "sum": content digest at last sync}}
  Meta          Vaultline.VaultId       a managed vault's own id
  AutoOpen entry Vaultline.Manage       "1": this database keeps that vault in sync

Values never leave the vaults: conflicts carry titles and field names only.
"""
from __future__ import annotations

import hashlib
import json
import uuid as uuidlib
from dataclasses import dataclass, field

from .vault import (DeleteEntries, Transfer, Vault, VaultError, _b64_uuid_to_hex, _sha, own_data, set_data)

KEY_SHARE = "Vaultline.ShareWith"
KEY_NOT = "Vaultline.NotShareWith"
KEY_LINKS = "Vaultline.Links"
KEY_VAULT_ID = "Vaultline.VaultId"
KEY_MANAGE = "Vaultline.Manage"
MAX_ROUNDS = 4


def parse_ids(text: str | None) -> list[str]:
    try:
        val = json.loads(text or "[]")
    except ValueError:
        return []
    return [str(x) for x in val] if isinstance(val, list) else []


def parse_links(text: str | None) -> dict[str, dict]:
    try:
        val = json.loads(text or "{}")
    except ValueError:
        return {}
    if not isinstance(val, dict):
        return {}
    return {str(k): v for k, v in val.items() if isinstance(v, dict) and v.get("copy")}


def effective_shares(entry_data: dict, ancestor_data: list[dict]) -> set[str]:
    """Vault ids an entry is shared with: its own list plus its folders', minus its exclusions."""
    out = set(parse_ids(entry_data.get(KEY_SHARE)))
    for d in ancestor_data:
        out |= set(parse_ids(d.get(KEY_SHARE)))
    return out - set(parse_ids(entry_data.get(KEY_NOT)))


# ---- reading a database --------------------------------------------------------------
def _fields(el) -> dict[str, tuple[str, bool]]:
    out = {}
    for s in el.findall("String"):
        k = s.findtext("Key")
        if k:
            v = s.find("Value")
            out[k] = ((v.text if v is not None else "") or "", v is not None and v.get("Protected") == "True")
    return out


def content_digest(el, binaries: list) -> tuple[str, dict[str, str]]:
    """(digest of what a person sees and edits: fields with protection, attachments, tags;
    per-field fingerprints to name what differs). Ids, times, history, icons and CustomData left out."""
    h = hashlib.sha256()
    per = {}
    for k, (v, p) in sorted(_fields(el).items()):
        f = hashlib.sha256(repr((k, v, p)).encode()).hexdigest()
        per[k] = f
        h.update(f.encode())
    for b in el.findall("Binary"):
        ref = b.find("Value").get("Ref") if b.find("Value") is not None else None
        data = binaries[int(ref)] if ref is not None and int(ref) < len(binaries) else b""
        f = hashlib.sha256(repr((b.findtext("Key"), _sha(data))).encode()).hexdigest()
        per[f"attachment:{b.findtext('Key')}"] = f
        h.update(f.encode())
    tags = el.findtext("Tags") or ""
    per["tags"] = hashlib.sha256(tags.encode()).hexdigest()
    h.update(per["tags"].encode())
    return h.hexdigest(), per


@dataclass
class Item:
    uuid: str
    title: str
    path: tuple[str, ...]
    in_bin: bool
    digest: str
    fields: dict[str, str]
    data: dict[str, str] = field(default_factory=dict)
    ancestors: list[dict] = field(default_factory=list)  # folders' own data, nearest first


def read(v: Vault) -> dict[str, Item]:
    """Every entry (not history) of an open database, with its folder path and digest."""
    kp = v._kp
    recycle = kp.recyclebin_group
    bin_uuid = recycle.uuid if recycle is not None else None
    binaries = kp.binaries
    out = {}
    for e in kp.entries:
        names, in_bin, anc = [], False, []
        g = e.group
        while g is not None and not g.is_root_group:
            if bin_uuid is not None and g.uuid == bin_uuid:
                in_bin = True
            names.insert(0, g.name or "")
            anc.append(own_data(g._element))
            g = g.parentgroup
        digest, per = content_digest(e._element, binaries)
        out[e.uuid.hex] = Item(e.uuid.hex, e.title or "", tuple(names), in_bin, digest, per,
                               own_data(e._element), anc)
    return out


def vault_id(v: Vault) -> str | None:
    return own_data(v._meta()).get(KEY_VAULT_ID)


# ---- changes (applied inside Vault.save) -----------------------------------------------
@dataclass
class SetData:
    """Set Vaultline CustomData on an entry (or folder with group=True); None removes it."""
    uuid: str
    key: str
    value: str | None
    group: bool = False

    def apply_to(self, v: Vault) -> None:
        el = (v._group(self.uuid) if self.group else v._entry(self.uuid))._element
        set_data(el, self.key, self.value)


@dataclass
class SetVaultId:
    value: str

    def apply_to(self, v: Vault) -> None:
        if not own_data(v._meta()).get(KEY_VAULT_ID):
            set_data(v._meta(), KEY_VAULT_ID, self.value)


@dataclass
class ReplaceContent:
    """Make an entry's fields, attachments and tags those of `item` (an export from another database),
    keeping its own id, folder, icon, Auto-Type and CustomData. The old version goes to its history."""
    uuid: str
    item: object  # vault.TransferItem

    def apply_to(self, v: Vault) -> None:
        from lxml import etree
        e = v._entry(self.uuid)
        e.save_history()
        el = e._element
        src = etree.fromstring(self.item.xml)
        for child in [c for c in el if c.tag in ("String", "Binary", "Tags")]:
            el.remove(child)
        times = el.find("Times")
        tags = src.find("Tags")
        if tags is not None and times is not None:
            times.addprevious(tags)
        anchor = times
        refs: dict[int, int] = {}
        for child in [c for c in src if c.tag in ("String", "Binary")]:
            if child.tag == "Binary":
                val = child.find("Value")
                old = int(val.get("Ref"))
                if old not in refs:
                    refs[old] = v._kp.add_binary(self.item.binaries.get(old, b""))
                val.set("Ref", str(refs[old]))
            if anchor is not None:
                anchor.addnext(child)
            else:
                el.append(child)
            anchor = child
        e.touch(modify=True)
        v.last_touched.append(e.uuid.hex)


@dataclass
class LinkImported:
    """After a Transfer in the same save: link each new entry to the copy it came from."""
    vault: str
    sums: dict[str, str]  # copy uuid -> digest

    def apply_to(self, v: Vault) -> None:
        for old, new in v.last_transfer.items():
            if old in self.sums:
                el = v._entry(new)._element
                set_data(el, KEY_LINKS, json.dumps({self.vault: {"copy": old, "sum": self.sums[old]}}))
                set_data(el, KEY_SHARE, json.dumps([self.vault]))


# ---- planning ------------------------------------------------------------------------
@dataclass(frozen=True)
class Conflict:
    main_uuid: str
    vault: str          # vault id
    copy_uuid: str
    kind: str           # "changed" (edited there) or "deleted" (removed there) or "error"
    title: str
    fields: tuple[str, ...] = ()  # names of what differs (never values)
    message: str = ""


@dataclass
class Plan:
    creates: list = field(default_factory=list)   # (vault, main uuid, path)
    pushes: list = field(default_factory=list)    # (vault, main uuid, copy uuid)
    pulls: list = field(default_factory=list)     # (vault, copy uuid, main uuid)
    bins: list = field(default_factory=list)      # (vault, copy uuid)
    imports: list = field(default_factory=list)   # (vault, copy uuid, path)
    links: dict = field(default_factory=dict)     # main uuid -> new links dict (only changed ones)
    excludes: list = field(default_factory=list)  # (main uuid, vault): stop sharing this one there
    conflicts: list = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.creates or self.pushes or self.pulls or self.bins or self.imports or self.links
                    or self.excludes)


def _differs(a: dict[str, str], b: dict[str, str]) -> tuple[str, ...]:
    names = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    return tuple("one-time code" if k == "otp" else k.lower() if k in ("Password", "UserName", "URL", "Notes",
                                                                     "Title") else k for k in names)


def plan(main: dict[str, Item], copies: dict[str, dict[str, Item]], names: dict[str, str],
         decisions: dict[tuple[str, str], str]) -> Plan:
    """What to do. `copies`: open managed vaults (id -> their entries); `names`: id -> display name;
    `decisions`: (main uuid, vault id) -> theirs / mine / restore / unshare."""
    p = Plan()
    linked: dict[str, set[str]] = {vid: set() for vid in copies}
    for m in main.values():
        links = parse_links(m.data.get(KEY_LINKS))
        targets = set() if m.in_bin else effective_shares(m.data, m.ancestors) & set(copies)
        new_links = dict(links)
        for vid, link in links.items():
            if vid not in copies:
                continue  # that vault isn't open now: leave the link alone
            c = copies[vid].get(link["copy"])
            linked[vid].add(link["copy"])
            if vid not in targets:
                if c is not None and not c.in_bin:
                    p.bins.append((vid, link["copy"]))
                new_links.pop(vid)
                continue
            if c is None or c.in_bin:
                d = decisions.get((m.uuid, vid))
                if d == "restore":
                    new_links.pop(vid)
                    p.creates.append((vid, m.uuid, m.path))
                elif d == "unshare":
                    new_links.pop(vid)
                    p.excludes.append((m.uuid, vid))
                else:
                    p.conflicts.append(Conflict(m.uuid, vid, link["copy"], "deleted", m.title))
                continue
            base = link.get("sum", "")
            if c.digest == m.digest:
                if base != m.digest:
                    new_links[vid] = {"copy": link["copy"], "sum": m.digest}
            elif c.digest == base:
                p.pushes.append((vid, m.uuid, link["copy"]))
            else:  # changed there (perhaps here too)
                d = decisions.get((m.uuid, vid))
                if d == "theirs":
                    p.pulls.append((vid, link["copy"], m.uuid))
                elif d == "mine":
                    p.pushes.append((vid, m.uuid, link["copy"]))
                else:
                    p.conflicts.append(Conflict(m.uuid, vid, link["copy"], "changed", m.title,
                                                _differs(m.fields, c.fields)))
        for vid in sorted(targets - set(links)):
            p.creates.append((vid, m.uuid, m.path))
        if new_links != links:
            p.links[m.uuid] = new_links
    for vid, items in copies.items():
        for c in items.values():
            if c.in_bin or c.uuid in linked[vid] or c.path[:1] == ("AutoOpen",):
                continue
            p.imports.append((vid, c.uuid, (names.get(vid, "Shared"), *c.path)))
    return p


# ---- running ---------------------------------------------------------------------------
def ensure_ids(vaults: dict[str, Vault]) -> dict[str, str]:
    """Give managed vaults their own id (Meta CustomData) if they have none. key -> id."""
    out = {}
    for key, v in vaults.items():
        vid = vault_id(v)
        if not vid:
            v.save([SetVaultId(uuidlib.uuid4().hex)])
            vid = vault_id(v)
        out[key] = vid
    return out


def sync(main: Vault, managed: dict[str, tuple[str, Vault]],
         decisions: dict[tuple[str, str], str]) -> tuple[set[str], list[Conflict], list[dict]]:
    """Bring the managed vaults (id -> (name, Vault)) and the main database in step. Returns (vault ids
    saved, "" for the main one; conflicts waiting for the user; value-free report)."""
    saved: set[str] = set()
    report: list[dict] = []
    names = {vid: name for vid, (name, _v) in managed.items()}
    decisions = dict(decisions)
    conflicts: list[Conflict] = []
    for v in (main, *(v for _n, v in managed.values())):
        v.reload_if_changed()  # someone may have changed a file since it was last read
    for _round in range(MAX_ROUNDS):
        mstate = read(main)
        cstate = {vid: read(v) for vid, (_n, v) in managed.items()}
        p = plan(mstate, cstate, names, decisions)
        conflicts = p.conflicts
        if p.empty():
            break
        links = {u: dict(ls) for u, ls in p.links.items()}

        def link_of(u):
            if u not in links:
                links[u] = parse_links(mstate[u].data.get(KEY_LINKS))
            return links[u]

        errors = []
        # 1) the managed vaults: new copies, updates, removals
        per_vault: dict[str, list] = {}
        for vid, mu, path in p.creates:
            try:
                per_vault.setdefault(vid, []).append(("create", mu, main.export([mu]), path))
            except VaultError as exc:
                errors.append(Conflict(mu, vid, "", "error", mstate[mu].title, message=str(exc)))
        for vid, mu, cu in p.pushes:
            try:
                per_vault.setdefault(vid, []).append(("push", mu, main.export([mu])[0], cu))
            except VaultError as exc:
                errors.append(Conflict(mu, vid, cu, "error", mstate[mu].title, message=str(exc)))
        for vid, cu in p.bins:
            per_vault.setdefault(vid, []).append(("bin", None, None, cu))
        for vid, work in per_vault.items():
            v = managed[vid][1]
            changes = []
            for kind, mu, item, extra in work:
                if kind == "create":
                    changes.append(Transfer(item, None, "/".join(extra)))
                elif kind == "push":
                    changes.append(ReplaceContent(extra, item))
                else:
                    changes.append(DeleteEntries([extra]))
            v.save(changes)
            saved.add(vid)
            made = dict(v.last_transfer)  # main uuid -> new copy uuid
            for kind, mu, _item, extra in work:
                if kind == "create" and mu in made:
                    link_of(mu)[vid] = {"copy": made[mu], "sum": mstate[mu].digest}
                    report.append({"shared": mstate[mu].title, "with": names[vid]})
                elif kind == "push":
                    link_of(mu)[vid] = {"copy": extra, "sum": mstate[mu].digest}
                    report.append({"updated": mstate[mu].title, "in": names[vid]})
        # 2) the main database: answers taken from the copies, imports, link bookkeeping
        mchanges: list = []
        for vid, cu, mu in p.pulls:
            v = managed[vid][1]
            mchanges.append(ReplaceContent(mu, v.export([cu])[0]))
            link_of(mu)[vid] = {"copy": cu, "sum": cstate[vid][cu].digest}
            report.append({"took": mstate[mu].title, "from": names[vid]})
        for mu, vid in p.excludes:
            nots = set(parse_ids(mstate[mu].data.get(KEY_NOT))) | {vid}
            own = set(parse_ids(mstate[mu].data.get(KEY_SHARE))) - {vid}
            mchanges.append(SetData(mu, KEY_NOT, json.dumps(sorted(nots))))
            mchanges.append(SetData(mu, KEY_SHARE, json.dumps(sorted(own)) if own else None))
        for mu, ls in links.items():
            mchanges.append(SetData(mu, KEY_LINKS, json.dumps(ls, sort_keys=True) if ls else None))
        by_vault: dict[str, list] = {}
        for vid, cu, path in p.imports:
            by_vault.setdefault(vid, []).append((cu, path))
        for vid, items in by_vault.items():
            v = managed[vid][1]
            for cu, path in items:
                mchanges.append(Transfer(v.export([cu]), None, "/".join(path)))
                mchanges.append(LinkImported(vid, {cu: cstate[vid][cu].digest}))
                report.append({"added": cstate[vid][cu].title, "from": names[vid]})
        if mchanges:
            main.save(mchanges)
            saved.add("")
        for (mu, vid) in [(c.main_uuid, c.vault) for c in p.conflicts]:
            decisions.pop((mu, vid), None)
        for k in [k for k in decisions if k in {(mu, vid) for vid, cu, mu in p.pulls}
                  or k in {(mu, vid) for vid, mu, cu in p.pushes} or k in set(p.excludes)
                  or k in {(mu, vid) for vid, mu, _ in p.creates}]:
            decisions.pop(k, None)
        conflicts = p.conflicts + errors
        if errors:
            break
    return saved, conflicts, report
