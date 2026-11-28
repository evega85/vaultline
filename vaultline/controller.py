"""Runs vault work on one background thread and watches the files for sync updates.

States:
  locked      no vault; the master password is needed
  pin-locked  databases dropped from memory, credentials kept; the PIN reopens them
  unlocked    the main database open (attached ones open alongside it, or show an error)

The main database may list others to open with it (KeePassXC AutoOpen entries, see multivault.py).
Each is its own Vault with its own save, merge, backup and file watch; the UI sees one merged
Snapshot, and every change is sent to the database that owns the entry or folder.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal, Slot

from . import favicons, multivault, passgen, pin as pinlib, sharing
from .config import BACKUP_DIR, Config
from .multivault import MAIN, MOVES, AutoOpenItem, Router, VaultInfo
from .vault import (ChangeMasterPassword, DeleteEntries, DeleteFolder, EntryEdit, EntryView, FileUnreadable, IconUpdate,
                    ImportEntries, Snapshot, Transfer, Vault, VaultError, WrongCredentials, create_vault)


AUTOOPEN_NOTE = ("Opened together with this database by Vaultline and KeePassXC (AutoOpen). "
                 "URL: the other database's file. Password: its master password. "
                 "Delete this entry to stop opening it.")


@dataclass
class _NewAutoOpen:
    """The AutoOpen entry for a vault added in Settings (managed: this database keeps it in sync)."""
    name: str
    password: str
    path: str
    managed: bool
    uuid: None = None  # routed to the main database

    def apply_to(self, v: Vault) -> None:
        from .vault import set_data
        g = v._ensure_group_path(multivault.AUTOOPEN_GROUP)
        e = v._kp.add_entry(g, self.name, "", self.password, url=self.path, notes=AUTOOPEN_NOTE,
                            force_creation=True)
        if self.managed:
            set_data(e._element, sharing.KEY_MANAGE, "1")
        v.last_touched.append(e.uuid.hex)


@dataclass
class _Attached:
    item: AutoOpenItem
    vault: Vault | None = None
    snap: Snapshot | None = None
    error: str = ""
    vault_id: str = ""


class VaultController(QObject):
    unlocked = Signal(object)               # Snapshot
    unlock_failed = Signal(str)
    entries_changed = Signal(object, str)   # Snapshot, reason: reloaded|saved|merged|icons|changed|vaults
    save_failed = Signal(object, str)       # EntryEdit (or None), message
    locked = Signal(str)                    # full lock; reason ("" for user action)
    pin_locked = Signal(str)                # PIN lock; reason
    pin_failed = Signal(int)                # tries left
    busy_changed = Signal(bool)
    icon_progress = Signal(int, int)        # done, total (total 0 = finished)
    applied = Signal(object, bool, str, object)  # token, ok, message, report (for apply())
    vault_failed = Signal(str, str)         # attached database name, message
    sharing_changed = Signal(object, object)  # conflicts (list of sharing.Conflict), report (value-free)

    _done = Signal(int, str, object)        # generation, kind, payload (worker -> UI thread)
    _icon_one = Signal(int, str, object)    # generation, entry uuid, png bytes or None

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vault")
        self._net = ThreadPoolExecutor(max_workers=6, thread_name_prefix="icons")
        self._vault: Vault | None = None
        self._main_snap: Snapshot | None = None
        self._att: dict[str, _Attached] = {}
        self._pin_locked = False
        self._pin_tries = 0
        self._snapshot: Snapshot | None = None
        self._router = Router(None)
        self._gen = 0
        self._pending = 0
        self._stat: dict[str, tuple[int, int] | None] = {}
        self._reload_failures: dict[str, int] = {}
        self._icon_jobs: dict[str, bytes | None] = {}
        self._icon_total = 0
        self._icons_inflight: set[str] = set()
        self._conflicts: list = []
        self._decisions: dict[tuple[str, str], str] = {}
        self._sync_timer = QTimer(self, singleShot=True, interval=1500)
        self._sync_timer.timeout.connect(self._start_sync)
        self._syncing = False
        self._sync_failures = 0

        self._done.connect(self._on_done)
        self._icon_one.connect(self._on_icon)
        self._fs = QFileSystemWatcher(self)
        self._fs.fileChanged.connect(self._poke)
        self._fs.directoryChanged.connect(self._poke)
        self._debounce = QTimer(self, singleShot=True, interval=1200)
        self._debounce.timeout.connect(self._check_disk)
        # Polling backstop: Google Drive's virtual drive does not always send change events.
        self._poll = QTimer(self, interval=3000)
        self._poll.timeout.connect(self._check_disk)

    # ---- state ------------------------------------------------------------
    @property
    def is_unlocked(self) -> bool:
        return self._vault is not None and not self._pin_locked

    @property
    def is_pin_locked(self) -> bool:
        return self._vault is not None and self._pin_locked

    @property
    def pin_available(self) -> bool:
        c = self.config
        return bool(c.pin_enabled and c.pin_salt and c.pin_hash)

    @property
    def path(self) -> str:
        return str(self._vault.path) if self._vault else ""

    @property
    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    def vault_infos(self) -> list[VaultInfo]:
        """The attached databases (open or failed), without passwords."""
        return list(self._snapshot.vaults) if self._snapshot else []

    def check_main_password(self, password: str) -> bool:
        return bool(self._vault) and self._vault.check_password(password)

    def _vaults(self) -> dict[str, Vault]:
        """Open databases by key, the main one first."""
        out = {MAIN: self._vault} if self._vault else {}
        out.update({k: a.vault for k, a in self._att.items() if a.vault is not None and a.snap is not None})
        return out

    # ---- unlock / lock ------------------------------------------------------------
    def unlock(self, path: str, password: str, keyfile: str | None) -> None:
        self.lock(full=True, emit=False)
        vault = Vault(path, password, keyfile, BACKUP_DIR, self.config.backups_to_keep)
        self._submit("unlock", vault.open, vault)

    def create(self, path: str, password: str, items: list | None = None, delete_after: str = "") -> None:
        """Create a new database (with imported `items`, if any), then open it (reported like an unlock).
        `delete_after`: the export file to delete once the import is saved and checked."""
        self.lock(full=True, emit=False)
        vault = Vault(path, password, None, BACKUP_DIR, self.config.backups_to_keep)

        def run():
            create_vault(path, password)
            snap = vault.open()
            if items:
                snap, _ = vault.save([ImportEntries(items)])
                items.clear()
                if delete_after:  # holds every password in plain text (or encrypted with a weaker setting)
                    try:
                        os.remove(delete_after)
                    except OSError:
                        pass
            return snap
        self._submit("unlock", run, vault)

    def pin_unlock(self, pin: str) -> bool:
        """Check the PIN; on success reopen the databases in the background."""
        if not self.is_pin_locked:
            return False
        if pinlib.check(pin, self.config.pin_salt, self.config.pin_hash):
            self._pin_tries = 0
            vault = self._vault
            self._submit("unlock", vault.open, vault)
            return True
        self._pin_tries += 1
        left = pinlib.MAX_TRIES - self._pin_tries
        if left <= 0:
            self.lock("Too many wrong PIN attempts. Enter the master password.", full=True)
        else:
            self.pin_failed.emit(left)
        return False

    def lock(self, reason: str = "", emit: bool = True, full: bool = False) -> None:
        """Lock. Uses the PIN lock when it's set up, unless full=True."""
        self._gen += 1  # results from work already running are dropped
        self._poll.stop()
        self._debounce.stop()
        self._icons_inflight.clear()
        self._icon_jobs, self._icon_total = {}, 0
        if self._fs.files() or self._fs.directories():
            self._fs.removePaths(self._fs.files() + self._fs.directories())
        self._snapshot = None
        self._main_snap = None
        self._router = Router(None)
        self._stat = {}
        self._sync_timer.stop()
        self._conflicts, self._decisions = [], {}
        self._sync_failures = 0
        use_pin = not full and self._vault is not None and self.pin_available
        if use_pin:
            if not self._pin_locked:
                self._pin_locked = True
                self._pin_tries = 0
                self._pool.submit(self._vault.suspend)
                for a in self._att.values():
                    a.snap = None
                    if a.vault is not None:
                        self._pool.submit(a.vault.suspend)
            if emit:
                self.pin_locked.emit(reason)
            return
        self._pin_locked = False
        for a in self._att.values():
            if a.vault is not None:
                self._pool.submit(a.vault.close)
        self._att = {}
        if self._vault:
            vault, self._vault = self._vault, None
            self._pool.submit(vault.close)  # after any in-flight job on that vault
        if emit:
            self.locked.emit(reason)

    def shutdown(self) -> None:
        self.lock(full=True, emit=False)
        self._net.shutdown(wait=False, cancel_futures=True)
        self._pool.shutdown(wait=True, cancel_futures=True)

    # ---- attached databases ----------------------------------------------------
    def _sync_autoopen(self) -> None:
        """Open what the main database's AutoOpen entries list; close what they no longer list."""
        if self._main_snap is None or self._vault is None:
            return
        wanted = {it.key: it for it in multivault.autoopen_items(self._main_snap, str(self._vault.path))}
        for key in list(self._att):
            if key not in wanted:
                self._detach(key)
        for key, item in wanted.items():
            a = self._att.get(key)
            if a is not None and (a.item.password, a.item.keyfile) == (item.password, item.keyfile):
                a.item = item  # a new title only renames it
                continue
            if a is not None:
                self._detach(key)
            self._attach(item)

    def _attach(self, item: AutoOpenItem) -> None:
        vault = Vault(item.path, item.password, item.keyfile or None, BACKUP_DIR, self.config.backups_to_keep)
        self._att[item.key] = _Attached(item, vault)
        self._submit("attach", lambda: (vault.open(), sharing.vault_id(vault)), (item.key, vault))

    def _detach(self, key: str) -> None:
        a = self._att.pop(key, None)
        if a is None:
            return
        self._unwatch(key, a.item.path)
        if a.vault is not None:
            self._pool.submit(a.vault.close)

    def add_vault(self, path: str, name: str, password: str | None = None, token=None) -> str | None:
        """Open another database together with this one, by writing a KeePassXC AutoOpen entry for it
        into the main database (so KeePassXC opens both too). With no `password` a new database is
        created there with a long random one. Result on `applied(token, ...)`; returns an error
        message instead if nothing was started."""
        if not self.is_unlocked:
            return "The vault is locked."
        path = os.path.abspath(path)
        key = multivault.path_key(path)
        if key == multivault.path_key(self.path):
            return "That's the database that's open now."
        if key in self._att:
            return "That database is already open alongside this one."
        create = password is None
        if create and os.path.exists(path):
            return "A file with that name is already there."
        pw = passgen.generate(40) if create else password

        def run():
            if create:
                create_vault(path, pw)
            else:  # check the password before saving it
                v = Vault(path, pw, None, BACKUP_DIR, self.config.backups_to_keep)
                try:
                    v.open()
                finally:
                    v.close()
        self._submit("addvault", run, (path, name.strip() or Path(path).stem, pw, token, create))
        return None

    # ---- sharing ------------------------------------------------------------------
    @property
    def conflicts(self) -> list:
        return list(self._conflicts)

    def managed_vaults(self) -> list[VaultInfo]:
        return [v for v in self.vault_infos() if v.managed]

    def _schedule_sync(self) -> None:
        if self.is_unlocked and any(a.item.managed and a.snap is not None for a in self._att.values()):
            self._sync_timer.start()

    def resolve(self, main_uuid: str, vault_id: str, choice: str) -> None:
        """A conflict's answer: theirs / mine (changed there), restore / unshare (deleted there)."""
        self._decisions[(main_uuid, vault_id)] = choice
        self._conflicts = [c for c in self._conflicts if (c.main_uuid, c.vault) != (main_uuid, vault_id)]
        self.sharing_changed.emit(self.conflicts, [])
        self._sync_timer.start(100)

    def share(self, uuid: str, own: list[str], not_ids: list[str] | None = None, group: bool = False,
              token=None) -> bool:
        """Set what an entry (or folder) is shared with: its own vault ids, and for an entry the ids
        its folders share with that it should not be."""
        import json
        changes = [sharing.SetData(uuid, sharing.KEY_SHARE, json.dumps(sorted(own)) if own else None, group)]
        if not group:
            changes.append(sharing.SetData(uuid, sharing.KEY_NOT, json.dumps(sorted(not_ids)) if not_ids else None))
        return self.apply(changes, token)

    def set_managed(self, entry_uuid: str, on: bool, token=None) -> bool:
        """Whether this database keeps an attached vault in sync (flag on its AutoOpen entry)."""
        return self.apply([sharing.SetData(entry_uuid, sharing.KEY_MANAGE, "1" if on else None)], token)

    def _start_sync(self) -> None:
        if not self.is_unlocked or self._syncing:
            if self._syncing:
                self._sync_timer.start()
            return
        managed = {k: a for k, a in self._att.items() if a.item.managed and a.snap is not None and a.vault}
        if not managed:
            return
        main = self._vault
        vaults = {k: a.vault for k, a in managed.items()}
        names = {k: a.item.name for k, a in managed.items()}
        decisions = dict(self._decisions)
        self._syncing = True

        def run():
            ids = sharing.ensure_ids(vaults)
            saved, conflicts, report = sharing.sync(main, {ids[k]: (names[k], v) for k, v in vaults.items()},
                                                    decisions)
            key_of = {vid: k for k, vid in ids.items()}
            snaps = {MAIN if vid == "" else key_of[vid]: (main if vid == "" else vaults[key_of[vid]]).snapshot()
                     for vid in saved}
            return ids, snaps, conflicts, report, decisions
        self._submit("sync", run)

    def _on_sync(self, status: str, result) -> None:
        self._syncing = False
        if status != "ok":  # often a file held for a moment (Drive syncing it): try again a few times
            self._sync_failures += 1
            if self._sync_failures <= 5:
                self._sync_timer.start(5000 * self._sync_failures)
            else:
                self.sharing_changed.emit(self.conflicts, [{"error": str(result)}])
            return
        self._sync_failures = 0
        ids, snaps, conflicts, report, used = result
        new_id = False  # a vault just given its id: the window must hear of it (Share with… needs it)
        for key, vid in ids.items():
            if key in self._att and self._att[key].vault_id != vid:
                self._att[key].vault_id = vid
                new_id = True
        for key, snap in snaps.items():
            if key == MAIN:
                self._main_snap = snap
            elif key in self._att:
                self._att[key].snap = snap
            self._remember_stat(key)
        for k in used:  # answers given before this run are spent; later ones wait for the next
            if self._decisions.get(k) == used[k]:
                self._decisions.pop(k, None)
        self._conflicts = [c for c in conflicts if (c.main_uuid, c.vault) not in self._decisions]
        if MAIN in snaps:
            self._sync_autoopen()
        self._remerge("synced" if snaps or new_id else None)
        self.sharing_changed.emit(self.conflicts, report)

    def retry_attached(self) -> None:
        """Try again to open attached databases that failed."""
        if not self.is_unlocked:
            return
        for key, a in list(self._att.items()):
            if a.snap is None:
                self._detach(key)
                self._attach(a.item)
        self._remerge("vaults")

    def _attach_failed(self, key: str, message: str) -> None:
        a = self._att.get(key)
        if a is None:
            return
        a.snap, a.error = None, message
        self._unwatch(key, a.item.path)
        self._remerge("vaults")
        self.vault_failed.emit(a.item.name, message)

    def _remerge(self, reason: str | None) -> None:
        if self._main_snap is None:
            return
        names = multivault.display_names(self._main_snap, {k: a.item.name for k, a in self._att.items()
                                                           if not a.item.managed})
        names.update({k: a.item.name for k, a in self._att.items() if a.item.managed})
        attached = {k: (names[k], a.snap) for k, a in self._att.items() if a.snap is not None and not a.item.managed}
        infos = [VaultInfo(k, names[k], a.item.path, a.snap.root_uuid if a.snap else "", a.error, a.item.entry_uuid,
                           a.item.managed, a.vault_id)
                 for k, a in self._att.items()]
        main = self._main_snap
        known = {a.vault_id for a in self._att.values() if a.item.managed and a.vault_id}
        if known:  # each entry's effective shares, from its own list and its folders'
            gdata = {g.uuid: (dict(g.data), g.parent_uuid) for g in main.groups}
            entries = []
            for e in main.entries:
                anc, u = [], e.group_uuid
                while u in gdata:
                    anc.append(gdata[u][0])
                    u = gdata[u][1]
                shares = tuple(sorted(sharing.effective_shares(e.data, anc) & known))
                entries.append(replace(e, shares=shares) if shares else e)
            main = Snapshot(entries, main.groups, main.icons, main.root_uuid, main.binned, main.vaults)
        self._snapshot = multivault.merge(main, attached, infos)
        self._router = Router(self._snapshot)
        if reason:
            self.entries_changed.emit(self._snapshot, reason)

    # ---- changes -------------------------------------------------------------------
    def _write(self, kind: str, changes: list, ctx) -> str | None:
        """Send each change to its database; all of them are saved in one job. Returns an error message
        (nothing is started) if a change can't be routed."""
        try:
            routes = self._router.route(changes)
        except VaultError as exc:
            return str(exc)
        vaults = self._vaults()
        moves = routes.pop(MOVES, [])
        for key in {*routes, *(m.src for m in moves), *(m.dest for m in moves)}:
            if key not in vaults:
                a = self._att.get(key)
                return f"“{a.item.name if a else 'That database'}” isn't open."

        def run():
            out, touched, report = {}, [], []

            def save(key, part):
                v = vaults[key]
                try:
                    out[key] = v.save(part)
                except Exception as exc:
                    exc.vault_key = key  # so a wrong password on a shared vault doesn't lock everything
                    exc.saved = out
                    raise
                touched.extend(v.last_touched)
                report.extend(v.last_report)

            for key, part in routes.items():
                save(key, part)
            for m in moves:  # copy, check the copy as written, then bin the originals
                src, dest = vaults[m.src], vaults[m.dest]
                items = src.export(m.entries, m.folder)
                save(m.dest, [Transfer(items, m.group_uuid, m.new_group)])
                mapping = dict(dest.last_transfer)
                bad = [old for old, new in mapping.items() if src.digest(old) != dest.digest(new)]
                if bad or not mapping and m.entries:
                    exc = VaultError(f"The copy of {len(bad) or 'an'} entr{'y' if len(bad) <= 1 else 'ies'} didn't match "
                                     "the original, so the original was left where it was. Check for a duplicate.")
                    exc.vault_key, exc.saved = m.dest, out
                    raise exc
                if not m.keep:
                    save(m.src, [DeleteFolder(m.folder)] if m.folder else [DeleteEntries(list(m.entries))])
                report.append({"copied" if m.keep else "moved": len(mapping), "from": m.src, "to": m.dest})
            return out, touched, report
        self._submit(kind, run, ctx)
        return None

    def save(self, edit: EntryEdit) -> None:
        if not self.is_unlocked:
            return
        err = self._write("save", [edit], edit)
        if err:
            self.save_failed.emit(edit, err)

    def apply(self, changes: list, token=None) -> bool:
        """Apply any list of changes in one save per database. Result arrives on `applied(token, ok, msg)`."""
        if not self.is_unlocked:
            return False
        err = self._write("apply", changes, token)
        if err:
            QTimer.singleShot(0, lambda: self.applied.emit(token, False, err, None))
        return True

    def change_master_password(self, current: str, new: str, token=None) -> str | None:
        """The main database's password. Returns an error message, or None if the change was started."""
        if not self.is_unlocked:
            return "The vault is locked."
        if not self._vault.check_password(current):
            return "The current master password is wrong."
        if not new:
            return "The new password is empty."
        self.apply([ChangeMasterPassword(new)], token=("master", token))
        return None

    def download_icons(self, entries: list[EntryView]) -> int:
        """Fetch website icons for these entries in the background. Returns how many were queued."""
        if not self.is_unlocked:
            return 0
        todo = [e for e in entries if e.uuid not in self._icons_inflight and favicons.host_of(e.url)]
        if not todo:
            return 0
        gen = self._gen
        if not self._icons_inflight:
            self._icon_jobs, self._icon_total = {}, 0
        self._icon_total += len(todo)
        for e in todo:
            self._icons_inflight.add(e.uuid)

            def job(uuid=e.uuid, url=e.url):
                try:
                    png = favicons.fetch_icon(url)
                except Exception:
                    png = None
                self._icon_one.emit(gen, uuid, png)
            self._net.submit(job)
        self.icon_progress.emit(len(self._icon_jobs), self._icon_total)
        return len(todo)

    # ---- worker plumbing -------------------------------------------------
    def _submit(self, kind: str, fn, ctx=None) -> None:
        gen = self._gen
        self._pending += 1
        if self._pending == 1:
            self.busy_changed.emit(True)

        def run():
            try:
                self._done.emit(gen, kind, ("ok", fn(), ctx))
            except Exception as exc:  # reported on the UI thread
                self._done.emit(gen, kind, ("err", exc, ctx))

        self._pool.submit(run)

    def _take(self, saved: dict) -> bool:
        """Store snapshots from saves. Returns whether anything merged from disk."""
        any_merged = False
        for key, (snap, merged) in saved.items():
            any_merged |= merged
            if key == MAIN:
                self._main_snap = snap
            elif key in self._att:
                self._att[key].snap = snap
            self._remember_stat(key)
        if MAIN in saved:
            self._sync_autoopen()
        self._remerge(None)
        self._schedule_sync()
        return any_merged

    def _emit_changed(self, reason: str) -> None:
        if self._snapshot is not None:
            self.entries_changed.emit(self._snapshot, reason)

    @Slot(int, str, object)
    def _on_done(self, gen: int, kind: str, payload) -> None:
        self._pending -= 1
        if self._pending == 0:
            self.busy_changed.emit(False)
        status, result, ctx = payload
        if gen != self._gen:  # locked meanwhile
            if kind == "unlock" and status == "ok" and ctx is not self._vault:
                self._pool.submit(ctx.close)
            elif kind == "attach" and status == "ok":
                a = self._att.get(ctx[0])
                if a is None or a.vault is not ctx[1]:
                    self._pool.submit(ctx[1].close)
                elif self._pin_locked:
                    self._pool.submit(ctx[1].suspend)
            elif kind == "apply":
                self.applied.emit(ctx, False, "The vault was locked before the change finished.", None)
            elif kind == "sync":
                self._syncing = False
            return

        if status == "err":
            key = ctx[0] if kind in ("attach", "reload") else getattr(result, "vault_key", MAIN)
            if getattr(result, "saved", None):
                self._take(result.saved)  # databases saved before the one that failed
                self._emit_changed("changed")
            if key != MAIN and kind != "attach":
                if isinstance(result, WrongCredentials):
                    a = self._att.get(key)
                    self._attach_failed(key, "Its password was changed elsewhere. Update its AutoOpen entry.")
                    if a is not None and a.vault is not None:
                        self._pool.submit(a.vault.close)
                        a.vault = None
            elif key == MAIN and isinstance(result, WrongCredentials) and kind in ("save", "apply", "icons", "reload"):
                self.lock("The database password was changed elsewhere. Enter the new one.", full=True)
                return

        if kind == "sync":
            self._on_sync(status, result)
            return
        if kind == "unlock":
            if status == "ok":
                self._vault = ctx
                self._pin_locked = False
                self._main_snap = result
                self._watch(MAIN, ctx.path)
                reopen = [a for a in self._att.values() if a.vault is not None and not a.error]
                for a in reopen:  # after a PIN unlock: same credentials, open again
                    self._submit("attach", lambda v=a.vault: (v.open(), sharing.vault_id(v)), (a.item.key, a.vault))
                self._sync_autoopen()
                self._remerge(None)
                self.unlocked.emit(self._snapshot)
            elif ctx is self._vault and self._pin_locked:
                # PIN was right but the file no longer opens with the kept password
                self.lock(f"Enter the master password. ({result})", full=True)
            else:
                ctx.close()
                self.unlock_failed.emit(str(result))
        elif kind == "addvault":
            path, name, pw, token, created = ctx
            if status != "ok":
                msg = "Wrong password for that database." if isinstance(result, WrongCredentials) else str(result)
                self.applied.emit(token, False, msg, None)
                return
            self.apply([_NewAutoOpen(name, pw, path, managed=created)], token)
        elif kind == "attach":
            key, vault = ctx
            a = self._att.get(key)
            if a is None or a.vault is not vault:  # detached or replaced meanwhile
                if status == "ok":
                    self._pool.submit(vault.close)
                return
            if status == "ok":
                (a.snap, vid), a.error = result, ""
                a.vault_id = vid or ""
                self._watch(key, vault.path)
                self._remerge("vaults")
                self._schedule_sync()
            else:
                a.vault = None
                self._pool.submit(vault.close)
                self._attach_failed(key, str(result))
        elif kind == "save":
            if status == "ok":
                saved, touched, _report = result
                merged = self._take(saved)
                self._emit_changed("merged" if merged else "saved")
                if self.config.auto_download_icons and self._snapshot:
                    need = [e for e in self._snapshot.entries if e.uuid in touched and not e.icon_uuid]
                    self.download_icons(need)
            else:
                self.save_failed.emit(ctx, str(result))
        elif kind == "apply":
            if status == "ok":
                saved, _touched, report = result
                self._take(saved)
                self._emit_changed("changed")
                self.applied.emit(ctx, True, "", report)
            else:
                self.applied.emit(ctx, False, str(result), None)
        elif kind == "icons":
            if status == "ok":
                self._take(result[0])
                self._emit_changed("icons")
            else:
                self.save_failed.emit(None, f"Could not save icons: {result}")
        elif kind == "reload":
            key = ctx[0]
            if status == "ok":
                self._reload_failures[key] = 0
                if result is not None:
                    if key == MAIN:
                        self._main_snap = result
                        self._sync_autoopen()
                    elif key in self._att:
                        self._att[key].snap = result
                    self._remerge("reloaded")
                    self._schedule_sync()
            elif isinstance(result, (FileUnreadable, VaultError)) and not isinstance(result, WrongCredentials):
                # Usually a sync caught mid-write: try again shortly, keep showing current data.
                self._reload_failures[key] = self._reload_failures.get(key, 0) + 1
                if self._reload_failures[key] <= 10:
                    self._stat[key] = None
                    self._debounce.start(3000)

    @Slot(int, str, object)
    def _on_icon(self, gen: int, uuid: str, png) -> None:
        if gen != self._gen:
            return
        self._icon_jobs[uuid] = png
        self._icons_inflight.discard(uuid)
        if self._icons_inflight:
            self.icon_progress.emit(len(self._icon_jobs), self._icon_total)
            return
        found = {u: p for u, p in self._icon_jobs.items() if p}
        self._icon_jobs, self._icon_total = {}, 0
        self.icon_progress.emit(len(found), 0)
        if found and self.is_unlocked:
            err = self._write("icons", [IconUpdate(found)], None)
            if err:
                self.save_failed.emit(None, f"Could not save icons: {err}")

    # ---- file watching ----------------------------------------------------
    def _paths(self) -> dict[str, Path]:
        return {k: v.path for k, v in self._vaults().items()}

    def _watch(self, key: str, path: Path) -> None:
        self._fs.addPath(str(path))
        self._fs.addPath(str(path.parent))
        self._remember_stat(key)
        self._poll.start()

    def _unwatch(self, key: str, path: str) -> None:
        self._stat.pop(key, None)
        others = {str(p) for k, p in self._paths().items() if k != key}
        dirs = {str(Path(p).parent) for p in others}
        drop = [p for p in (str(Path(path)), str(Path(path).parent))
                if p not in others and p not in dirs and (p in self._fs.files() or p in self._fs.directories())]
        if drop:
            self._fs.removePaths(drop)

    def _current_stat(self, key: str) -> tuple[int, int] | None:
        path = self._paths().get(key)
        try:
            st = os.stat(path) if path else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st else None

    def _remember_stat(self, key: str) -> None:
        """After opening or saving: have the next check read the file and compare its contents. Taking
        the file's time and size now could swallow a change someone else made right after our write."""
        self._stat[key] = None

    @Slot(str)
    def _poke(self, _path: str = "") -> None:
        if self.is_unlocked:
            for p in self._paths().values():
                p = str(p)
                if p not in self._fs.files() and os.path.exists(p):
                    self._fs.addPath(p)  # replaced files drop out of the watch list
            self._debounce.start()

    @Slot()
    def _check_disk(self) -> None:
        if not self.is_unlocked:
            return
        for key, vault in self._vaults().items():
            st = self._current_stat(key)
            if st is None or st == self._stat.get(key):
                continue
            self._stat[key] = st
            self._submit("reload", vault.reload_if_changed, (key,))
