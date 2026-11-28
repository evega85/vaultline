"""Several databases open at once: the main one plus attached ones (a shared vault).

Attached databases are listed in the main one as KeePassXC "AutoOpen" entries (top-level folder
"AutoOpen"; URL = the .kdbx path, Password = its master password, UserName = a key file if any), so
KeePassXC opens them too. Vaultline shows each attached database as a top-level folder named after
its entry; its folders sit under that one and its entries' folder paths start with that name.

Nothing here touches pykeepass: it works on Snapshots and change objects. The controller owns the
Vault objects, merges their snapshots with `merge` and sends each change to its database with `route`.
"""
from __future__ import annotations

import hashlib
import os
import socket
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote, urlparse

from .vault import (ChangeMasterPassword, CopyEntries, CopyFolder, CreateFolder, DeleteEntries, DeleteFolder, EntryEdit, GroupView, IconUpdate,
                    ImportEntries, MergeDuplicate, MoveEntries, MoveFolder, NormalizeCards, RenameFolder, Snapshot, UpdateEntry,
                    VaultError)

AUTOOPEN_GROUP = "AutoOpen"
MAIN = ""  # key of the main database
MOVES = "~moves"  # route() key for CrossMoves, run after the other changes


@dataclass(frozen=True)
class CrossMove:
    """Entries or one folder going to another database: copied there (new ids), checked, then the
    originals go to the source's recycle bin."""
    src: str
    dest: str
    entries: tuple[str, ...] = ()
    folder: str | None = None
    group_uuid: str | None = None  # destination folder, as the destination knows it (None = top)
    new_group: str = ""            # or a folder path to create there
    keep: bool = False             # a copy: the originals stay where they are


@dataclass(frozen=True)
class AutoOpenItem:
    """One database to attach, read from an AutoOpen entry. `key` is its normalised path."""
    key: str
    path: str
    name: str
    password: str
    keyfile: str
    entry_uuid: str
    managed: bool = False  # this database keeps it in sync (sharing.py); its entries aren't listed


@dataclass(frozen=True)
class VaultInfo:
    """An attached database as the UI sees it (no password)."""
    key: str
    name: str
    path: str
    root_uuid: str = ""   # "" while it isn't open
    error: str = ""       # why it isn't open, if it failed
    entry_uuid: str = ""  # its AutoOpen entry in the main database (deleting that detaches it)
    managed: bool = False # kept in sync from the main database: share entries with it
    vault_id: str = ""    # its own id (sharing.py), "" until known


def path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def top_id(key: str) -> str:
    """The id an attached database's top folder has in the merged view. Not its root folder's own
    uuid: databases made from the same template (or a copied file) share that with the main one."""
    return hashlib.md5(("vaultline-attached:" + key).encode()).hexdigest()


def _device_allowed(rule: str, host: str) -> bool:
    """KeePassXC's IfDevice: comma-separated computer names; '!name' excludes one."""
    names = [d.strip() for d in rule.split(",") if d.strip()]
    if not names:
        return True
    host = host.casefold()
    if any(n.startswith("!") and n[1:].strip().casefold() == host for n in names):
        return False
    wanted = [n.casefold() for n in names if not n.startswith("!")]
    return not wanted or host in wanted


def resolve_path(text: str, main_path: str) -> str:
    """An AutoOpen URL or key file path as a local path: {DB_DIR}, file:// and kdbx:// handled,
    relative paths taken from the main database's folder."""
    text = (text or "").strip()
    if not text:
        return ""
    db_dir = str(Path(main_path).parent)
    text = text.replace("{DB_DIR}", db_dir).replace("{db_dir}", db_dir)
    low = text.lower()
    if low.startswith("file:"):
        u = urlparse(text)
        p = unquote(u.path)
        if u.netloc:  # file://server/share
            p = f"//{u.netloc}{p}"
        elif len(p) > 2 and p[0] == "/" and p[2] == ":":  # file:///C:/x
            p = p[1:]
        text = p
    elif low.startswith("kdbx://"):
        text = text[7:]
    text = os.path.expandvars(text)
    if not os.path.isabs(text):
        text = os.path.join(db_dir, text)
    return os.path.normpath(text)


def autoopen_items(snap: Snapshot, main_path: str, host: str | None = None) -> list[AutoOpenItem]:
    """The databases the main one asks to open, in KeePassXC's way: entries directly in the top-level
    folder "AutoOpen", skipping expired ones, empty URLs, other computers (IfDevice), the main
    database itself and repeats."""
    host = host if host is not None else socket.gethostname()
    group = next((g for g in snap.groups if g.path == (AUTOOPEN_GROUP,)), None)
    if group is None:
        return []
    main_key = path_key(main_path)
    out, seen = [], {main_key}
    for e in snap.entries:
        if e.group_uuid != group.uuid or not e.url.strip() or e.autotype.expired:
            continue
        rule = next((f.value for f in e.custom if f.key == "IfDevice"), "")
        if not _device_allowed(rule, host):
            continue
        path = resolve_path(e.url, main_path)
        key = path_key(path)
        if key in seen:
            continue
        seen.add(key)
        keyfile = resolve_path(e.username, main_path) if e.username.strip() else ""
        name = e.title.strip() or Path(path).stem
        out.append(AutoOpenItem(key, path, name, e.password, keyfile, e.uuid,
                                managed=e.data.get("Vaultline.Manage") == "1"))
    return out


def display_names(main: Snapshot, names: dict[str, str]) -> dict[str, str]:
    """Names for the attached databases' folders, unique among each other and the main database's
    top-level folders (a clash gets " (vault)", then a number)."""
    taken = {g.name.casefold() for g in main.groups if g.depth == 0}
    out = {}
    for key, name in names.items():
        name = (name or "Shared").replace("/", "-").strip() or "Shared"
        cand, n = name, 1
        while cand.casefold() in taken:
            n += 1
            cand = f"{name} (vault)" if n == 2 else f"{name} (vault {n - 1})"
        taken.add(cand.casefold())
        out[key] = cand
    return out


def merge(main: Snapshot, attached: dict[str, tuple[str, Snapshot]], infos: list[VaultInfo]) -> Snapshot:
    """One snapshot over all open databases. `attached`: key -> (display name, snapshot)."""
    entries, groups, binned = list(main.entries), list(main.groups), list(main.binned)
    icons = dict(main.icons)
    for key, (name, snap) in attached.items():
        top = top_id(key)

        def up(u, root=snap.root_uuid, top=top):
            return top if u == root else u
        groups.append(GroupView(top, name, (name,), 0, main.root_uuid, key))
        for g in snap.groups:
            groups.append(replace(g, path=(name, *g.path), depth=g.depth + 1, vault=key, parent_uuid=up(g.parent_uuid)))
        for lst, src in ((entries, snap.entries), (binned, snap.binned)):
            for e in src:  # copies: the database's own snapshot is merged again on every change
                lst.append(replace(e, vault=key, group=f"{name} / {e.group}" if e.group else name,
                                   group_uuid=up(e.group_uuid)))
        icons.update(snap.icons)
    groups.sort(key=lambda gv: tuple(p.casefold() for p in gv.path))
    entries.sort(key=lambda v: (v.title.casefold(), v.username.casefold()))
    return Snapshot(entries, groups, icons, main.root_uuid, binned, list(infos))


_TWICE = "*"  # owner of an id found in more than one database


class Router:
    """Which database each entry and folder belongs to, from a merged snapshot. Changes going in are
    rewritten to each database's own ids (an attached top folder's id becomes its root's uuid)."""

    def __init__(self, merged: Snapshot | None):
        self.owner: dict[str, str] = {}
        self.roots: dict[str, str] = {}   # attached top-folder id -> key
        self.real: dict[str, str] = {}    # attached top-folder id -> that database's root uuid
        self.names: dict[str, str] = {}   # display name (casefolded) -> key
        if merged is None:
            return
        tops = {v.key: v for v in merged.vaults if v.root_uuid and not v.managed}  # managed: not listed
        for key, info in tops.items():
            top = top_id(key)
            self.roots[top] = key
            self.real[top] = info.root_uuid
            self.names[info.name.casefold()] = key
        seen: dict[str, str] = {merged.root_uuid: MAIN}
        for uid, key in ((g.uuid, g.vault) for g in merged.groups):
            seen[uid] = key if seen.get(uid, key) == key else _TWICE
        for uid, key in ((e.uuid, e.vault) for e in (*merged.entries, *merged.binned)):
            seen[uid] = key if seen.get(uid, key) == key else _TWICE
        for info in tops.values():  # an attached root uuid is never used directly
            seen[info.root_uuid] = _TWICE if info.root_uuid != merged.root_uuid else MAIN
        self.owner = seen

    def of(self, uuid: str | None) -> str:
        key = self.owner.get(uuid or "", MAIN)
        if key == _TWICE:
            raise VaultError("That entry or folder has the same id in two databases, so Vaultline can't tell "
                             "which one to change. Edit it in KeePassXC.")
        return key

    def _gid(self, uuid: str | None) -> str | None:
        """A folder id as its own database knows it."""
        return self.real.get(uuid or "", uuid)

    def path(self, path: str) -> tuple[str, str]:
        """A folder path typed from the top ("Shared/Work") -> (key, path inside that database)."""
        parts = [p.strip() for p in path.replace("\\", "/").split("/") if p.strip()]
        if parts and parts[0].casefold() in self.names:
            return self.names[parts[0].casefold()], "/".join(parts[1:])
        return MAIN, "/".join(parts)

    def split(self, change) -> list[tuple[str, object]]:
        """The change as (database key, change for that database) pairs."""
        if isinstance(change, EntryEdit):
            if change.new_group.strip():
                dest, inner = self.path(change.new_group)
                change = replace(change, new_group=inner, group_uuid=None if inner else self._root_of(dest))
            else:
                dest = self.of(change.group_uuid) if change.group_uuid else None
                change = replace(change, group_uuid=self._gid(change.group_uuid))
            if change.uuid and change.uuid in self.owner:
                key = self.of(change.uuid)
                if dest is not None and dest != key:  # save the fields where it is, then move it
                    move = CrossMove(key, dest, (change.uuid,), None,
                                     change.group_uuid if not change.new_group else None, change.new_group)
                    return [(key, replace(change, group_uuid=None, new_group="")), (MOVES, move)]
            else:
                key = dest or MAIN
            return [(key, change)]
        if isinstance(change, CopyEntries):
            if change.new_group.strip():
                dest, inner = self.path(change.new_group)
                gid = None if inner else self._root_of(dest)
            else:
                dest, inner, gid = self.of(change.group_uuid), "", self._gid(change.group_uuid)
            by_src: dict[str, list[str]] = {}
            for u in change.uuids:
                by_src.setdefault(self.of(u), []).append(u)
            return [(MOVES, CrossMove(src, dest, tuple(us), None, gid, inner, keep=True)) for src, us in by_src.items()]
        if isinstance(change, CopyFolder):
            if change.uuid in self.roots:
                raise VaultError("That folder is a whole vault; copy the folders inside it instead.")
            key = self.of(change.uuid)
            if change.parent_uuid is None:
                return [(MOVES, CrossMove(key, key, (), change.uuid, self._root_of(key), keep=True))]
            return [(MOVES, CrossMove(key, self.of(change.parent_uuid), (), change.uuid,
                                      self._gid(change.parent_uuid), keep=True))]
        if isinstance(change, MoveEntries):
            if change.new_group.strip():
                dest, inner = self.path(change.new_group)
                change = replace(change, new_group=inner, group_uuid=None if inner else self._root_of(dest))
            else:
                dest = self.of(change.group_uuid)
                change = replace(change, group_uuid=self._gid(change.group_uuid))
            by: dict[str, list[str]] = {}
            for u in change.uuids:
                by.setdefault(self.of(u), []).append(u)
            out = []
            for src, us in by.items():
                if src == dest:
                    out.append((dest, replace(change, uuids=us)))
                else:
                    gid = change.group_uuid if not change.new_group else None
                    out.append((MOVES, CrossMove(src, dest, tuple(us), None, gid, change.new_group)))
            return out
        if isinstance(change, CreateFolder):
            key, inner = self.path(change.path)
            if not inner:
                raise VaultError("Folder name is empty")
            return [(key, CreateFolder(inner))]
        if isinstance(change, (RenameFolder, DeleteFolder)):
            if change.uuid in self.roots:
                raise VaultError("That folder is the shared vault itself; detach it in Settings instead.")
            return [(self.of(change.uuid), change)]
        if isinstance(change, MoveFolder):
            if change.uuid in self.roots:
                raise VaultError("That folder is the shared vault itself and can't be moved.")
            key = self.of(change.uuid)
            if change.parent_uuid is None:  # "top level" means the top of its own database
                return [(key, replace(change, parent_uuid=self._root_of(key)))]
            dest = self.of(change.parent_uuid)
            if dest != key:
                return [(MOVES, CrossMove(key, dest, (), change.uuid, self._gid(change.parent_uuid)))]
            return [(key, replace(change, parent_uuid=self._gid(change.parent_uuid)))]
        if isinstance(change, (DeleteEntries, NormalizeCards)):
            by: dict[str, list[str]] = {}
            for u in change.uuids:
                by.setdefault(self.of(u), []).append(u)
            return [(k, type(change)(us)) for k, us in by.items()]
        if isinstance(change, UpdateEntry):
            return [(self.of(change.uuid), change)]
        if isinstance(change, MergeDuplicate):
            key = self.of(change.keep)
            if self.of(change.drop) != key:
                raise VaultError("Those two are in different vaults: move them into the same one first.")
            return [(key, change)]
        if isinstance(change, IconUpdate):
            byi: dict[str, dict[str, bytes]] = {}
            for u, png in change.icons.items():
                byi.setdefault(self.of(u), {})[u] = png
            return [(k, IconUpdate(d)) for k, d in byi.items()]
        if isinstance(change, (ImportEntries, ChangeMasterPassword)):
            return [(MAIN, change)]
        if hasattr(change, "apply_to") and hasattr(change, "uuid"):  # sharing.SetData and the like
            return [(self.of(change.uuid), change)]
        raise TypeError(f"Unknown change {change!r}")

    def _root_of(self, key: str) -> str | None:
        """A database's root uuid (None for the main one: its changes treat None as the top)."""
        if key == MAIN:
            return None
        return next((self.real[t] for t, k in self.roots.items() if k == key), None)

    def route(self, changes: list) -> dict[str, list]:
        """Group changes by database, keeping their order within each."""
        out: dict[str, list] = {}
        for ch in changes:
            for key, part in self.split(ch):
                out.setdefault(key, []).append(part)
        return out
