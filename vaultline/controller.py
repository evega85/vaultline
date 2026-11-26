"""Runs vault work on one background thread and watches the file for sync updates.

States:
  locked      no vault; the master password is needed
  pin-locked  database dropped from memory, credentials kept; the PIN reopens it
  unlocked    database open
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal, Slot

from . import favicons, pin as pinlib
from .config import BACKUP_DIR, Config
from .vault import (ChangeMasterPassword, EntryEdit, EntryView, FileUnreadable, IconUpdate, Snapshot, Vault,
                    VaultError, WrongCredentials)


class VaultController(QObject):
    unlocked = Signal(object)               # Snapshot
    unlock_failed = Signal(str)
    entries_changed = Signal(object, str)   # Snapshot, reason: reloaded|saved|merged|icons|changed
    save_failed = Signal(object, str)       # EntryEdit (or None), message
    locked = Signal(str)                    # full lock; reason ("" for user action)
    pin_locked = Signal(str)                # PIN lock; reason
    pin_failed = Signal(int)                # tries left
    busy_changed = Signal(bool)
    icon_progress = Signal(int, int)        # done, total (total 0 = finished)
    applied = Signal(object, bool, str, object)  # token, ok, message, report (for apply())

    _done = Signal(int, str, object)        # generation, kind, payload (worker -> UI thread)
    _icon_one = Signal(int, str, object)    # generation, entry uuid, png bytes or None

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vault")
        self._net = ThreadPoolExecutor(max_workers=6, thread_name_prefix="icons")
        self._vault: Vault | None = None
        self._pin_locked = False
        self._pin_tries = 0
        self._snapshot: Snapshot | None = None
        self._gen = 0
        self._pending = 0
        self._stat: tuple[int, int] | None = None
        self._reload_failures = 0
        self._icon_jobs: dict[str, bytes | None] = {}
        self._icon_total = 0
        self._icons_inflight: set[str] = set()

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

    # ---- unlock / lock ------------------------------------------------------------
    def unlock(self, path: str, password: str, keyfile: str | None) -> None:
        self.lock(full=True, emit=False)
        vault = Vault(path, password, keyfile, BACKUP_DIR, self.config.backups_to_keep)
        self._submit("unlock", vault.open, vault)

    def pin_unlock(self, pin: str) -> bool:
        """Check the PIN; on success reopen the database in the background."""
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
        use_pin = not full and self._vault is not None and self.pin_available
        if use_pin:
            if not self._pin_locked:
                self._pin_locked = True
                self._pin_tries = 0
                self._pool.submit(self._vault.suspend)
            if emit:
                self.pin_locked.emit(reason)
            return
        self._pin_locked = False
        if self._vault:
            vault, self._vault = self._vault, None
            self._pool.submit(vault.close)  # after any in-flight job on that vault
        if emit:
            self.locked.emit(reason)

    def shutdown(self) -> None:
        self.lock(full=True, emit=False)
        self._net.shutdown(wait=False, cancel_futures=True)
        self._pool.shutdown(wait=True, cancel_futures=True)

    # ---- changes -------------------------------------------------------------------
    def save(self, edit: EntryEdit) -> None:
        if not self.is_unlocked:
            return
        vault = self._vault
        self._submit("save", lambda: (vault.save([edit]), list(vault.last_touched)), edit)

    def apply(self, changes: list, token=None) -> bool:
        """Apply any list of changes in one save. Result arrives on `applied(token, ok, msg)`."""
        if not self.is_unlocked:
            return False
        vault = self._vault
        self._submit("apply", lambda: (vault.save(changes), list(vault.last_report)), token)
        return True

    def change_master_password(self, current: str, new: str, token=None) -> str | None:
        """Returns an error message, or None if the change was started."""
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

    def _set_snapshot(self, snap: Snapshot, reason: str) -> None:
        self._snapshot = snap
        self.entries_changed.emit(snap, reason)

    @Slot(int, str, object)
    def _on_done(self, gen: int, kind: str, payload) -> None:
        self._pending -= 1
        if self._pending == 0:
            self.busy_changed.emit(False)
        status, result, ctx = payload
        if gen != self._gen:  # locked meanwhile
            if kind == "unlock" and status == "ok" and ctx is not self._vault:
                self._pool.submit(ctx.close)
            elif kind == "apply":
                self.applied.emit(ctx, False, "The vault was locked before the change finished.", None)
            return

        if status == "err" and isinstance(result, WrongCredentials) and kind != "unlock":
            self.lock("The database password was changed elsewhere. Enter the new one.", full=True)
            return

        if kind == "unlock":
            if status == "ok":
                self._vault = ctx
                self._pin_locked = False
                self._snapshot = result
                self._watch(ctx.path)
                self.unlocked.emit(result)
            elif ctx is self._vault and self._pin_locked:
                # PIN was right but the file no longer opens with the kept password
                self.lock(f"Enter the master password. ({result})", full=True)
            else:
                ctx.close()
                self.unlock_failed.emit(str(result))
        elif kind == "save":
            if status == "ok":
                (snap, merged), touched = result
                self._remember_stat()
                self._set_snapshot(snap, "merged" if merged else "saved")
                if self.config.auto_download_icons:
                    need = [e for e in snap.entries if e.uuid in touched and not e.icon_uuid]
                    self.download_icons(need)
            else:
                self.save_failed.emit(ctx, str(result))
        elif kind == "apply":
            if status == "ok":
                (snap, _merged), report = result
                self._remember_stat()
                self._set_snapshot(snap, "changed")
                self.applied.emit(ctx, True, "", report)
            else:
                self.applied.emit(ctx, False, str(result), None)
        elif kind == "icons":
            if status == "ok":
                snap, _merged = result
                self._remember_stat()
                self._set_snapshot(snap, "icons")
            else:
                self.save_failed.emit(None, f"Could not save icons: {result}")
        elif kind == "reload":
            if status == "ok":
                self._reload_failures = 0
                if result is not None:
                    self._set_snapshot(result, "reloaded")
            elif isinstance(result, (FileUnreadable, VaultError)):
                # Usually a sync caught mid-write: try again shortly, keep showing current data.
                self._reload_failures += 1
                if self._reload_failures <= 10:
                    self._stat = None
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
            vault = self._vault
            self._submit("icons", lambda: vault.save([IconUpdate(found)]))

    # ---- file watching ----------------------------------------------------
    def _watch(self, path: Path) -> None:
        self._fs.addPath(str(path))
        self._fs.addPath(str(path.parent))
        self._remember_stat()
        self._poll.start()

    def _current_stat(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self._vault.path) if self._vault else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st else None

    def _remember_stat(self) -> None:
        self._stat = self._current_stat()

    @Slot(str)
    def _poke(self, _path: str = "") -> None:
        if self.is_unlocked:
            p = str(self._vault.path)
            if p not in self._fs.files() and os.path.exists(p):
                self._fs.addPath(p)  # replaced files drop out of the watch list
            self._debounce.start()

    @Slot()
    def _check_disk(self) -> None:
        if not self.is_unlocked:
            return
        st = self._current_stat()
        if st is None or st == self._stat:
            return
        self._stat = st
        vault = self._vault
        self._submit("reload", vault.reload_if_changed)
