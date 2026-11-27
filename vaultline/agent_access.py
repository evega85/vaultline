"""Opt-in: let an agent fetch one secret at a time from ONE shared folder, with the user approving.

Off by default (Settings > "Share a folder with agents"). A `secret` request over the CLI pipe only
works while the vault is fully unlocked (not PIN-locked), CLI access is on, sharing is on, and the
entry is inside the shared folder (subfolders included). Every request shows an approval prompt:
Allow once / Allow this entry for 15 min / Allow the folder (for the time chosen in Settings:
15 min by default, 1 hour, until locked, or not offered) / Deny; no answer means Deny. A folder
pass answers later requests for that folder without a prompt; every check below still applies to
each one, and each is logged. Locking, a settings change or "Stop agent access" in the tray
cancels all allowances and open prompts. Every request is written to agent-access.log: time, outcome, field,
entry title and id, and the calling program. Never the value.

This is scoping + approval + audit. It doesn't protect against a malicious program running as
the same Windows user, which could read the value from the pipe reply or the screen.
"""
from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from . import otp as otplib
from .config import DATA_DIR, Config
from .vault import EntryView, Snapshot

ACCESS_LOG = DATA_DIR / "agent-access.log"
LOG_MAX_BYTES = 1024 * 1024
FIELDS = ("password", "username", "otp")
ALLOW_MINUTES = 15
FOLDER_PASS_CHOICES = (15, 60, -1, 0)  # minutes; -1 = until Vaultline locks, 0 = not offered
PROMPT_SECONDS = 60
MAX_WAITING = 5


class AccessRefused(Exception):
    pass


def folder_pass_minutes(config: Config) -> int:
    """The folder pass length from Settings (anything unexpected counts as not offered)."""
    m = config.agent_folder_pass
    return m if isinstance(m, int) and not isinstance(m, bool) and m in FOLDER_PASS_CHOICES else 0


def folder_pass_label(minutes: int) -> str:
    return {15: "15 min", 60: "1 hour", -1: "until locked"}.get(minutes, "")


def shared_folder_ids(snap: Snapshot, folder_uuid: str) -> set[str]:
    """The shared folder and every folder below it. Empty if it isn't in this database (or is in the bin)."""
    if not folder_uuid or not any(g.uuid == folder_uuid for g in snap.groups):
        return set()
    children: dict[str, list[str]] = {}
    for g in snap.groups:
        children.setdefault(g.parent_uuid, []).append(g.uuid)
    out, todo = set(), [folder_uuid]
    while todo:
        u = todo.pop()
        if u not in out:
            out.add(u)
            todo.extend(children.get(u, ()))
    return out


def shared_ids_for(config: Config, snap: Snapshot | None) -> set[str]:
    if snap is None or not config.agent_share_enabled:
        return set()
    return shared_folder_ids(snap, config.agent_folder_uuid)


def shared_folder_path(config: Config, snap: Snapshot | None) -> str | None:
    if snap is None or not config.agent_share_enabled:
        return None
    g = next((g for g in snap.groups if g.uuid == config.agent_folder_uuid), None)
    return "/".join(g.path) if g else None


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def log_access(outcome: str, field_name: str, title: str, entry_id: str, client: str) -> None:
    """One line per request. Never pass a secret value in here."""
    def clean(s: str) -> str:
        return _CONTROL.sub(" ", s or "")[:200]
    line = "\t".join([datetime.now().astimezone().isoformat(timespec="seconds"), clean(outcome),
                      clean(field_name), clean(title), clean(entry_id[:8]), clean(client)])
    try:
        ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
        if ACCESS_LOG.exists() and ACCESS_LOG.stat().st_size > LOG_MAX_BYTES:
            os.replace(ACCESS_LOG, ACCESS_LOG.with_name(ACCESS_LOG.name + ".1"))
        with open(ACCESS_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def client_process(sock) -> str:
    """Name and PID of the program on the other end of the pipe (Windows), plus the program that
    started it, for the prompt and log. The parent is a hint only: a program can choose its parent."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = wintypes.HANDLE(int(sock.socketDescriptor()))
        pid = wintypes.ULONG()
        if not k32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
            return "unknown program"
        name, _ = _process_info(k32, pid.value)
        text = f"{name or 'unknown'} (PID {pid.value})"
        parent = _parent_name(k32, pid.value)
        return f"{text}, started by {parent}" if parent else text
    except Exception:
        return "unknown program"


def _process_info(k32, pid: int) -> tuple[str, int]:
    """(exe name, creation time as a FILETIME int) of a running process; ("", 0) if unreadable."""
    import ctypes
    from ctypes import wintypes
    proc = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not proc:
        return "", 0
    try:
        name = ""
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value)
        times = [wintypes.FILETIME() for _ in range(4)]
        created = 0
        if k32.GetProcessTimes(proc, *(ctypes.byref(t) for t in times)):
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return name, created
    finally:
        k32.CloseHandle(proc)


def _parent_name(k32, pid: int) -> str:
    """Exe name of the process that started `pid`, or "" if it has exited (its PID may since have
    been reused, so a "parent" newer than the child is ignored)."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return ""
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ppid = None
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.th32ProcessID == pid:
                ppid = e.th32ParentProcessID
                break
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    if not ppid:
        return ""
    _, child_created = _process_info(k32, pid)
    parent, parent_created = _process_info(k32, ppid)
    if not parent or not parent_created or (child_created and parent_created > child_created):
        return ""
    return parent


@dataclass
class Request:
    entry_id: str
    title: str
    username: str
    url: str
    folder: str
    field: str
    client: str
    reply: Callable[[dict], None]
    key: object = None
    prompt: object = field(default=None, repr=False)
    folder_pass: str = ""  # the prompt's folder button ("15 min", "1 hour", "until locked"); "" = none
    shared_folder: str = ""  # path of the shared folder the pass would cover, for the button


class AgentGate(QObject):
    """Decides `secret` requests. `ask(request, decide)` shows the approval prompt and returns an
    object with close(); it calls decide("once" | "entry" | "folder" | "deny"). Without `ask`, everything
    is denied."""

    def __init__(self, controller, config: Config, ask: Callable | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.vc, self.config, self.ask = controller, config, ask
        self.timeout_ms = PROMPT_SECONDS * 1000
        self._allowed: dict[str, float] = {}   # entry uuid -> monotonic expiry
        self._folder_until = 0.0               # folder pass: monotonic expiry (inf = until locked)
        self._scope = self._current_scope()
        self._queue: list[Request] = []
        self._current: Request | None = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(lambda: self._decide("timeout"))
        controller.locked.connect(lambda *_: self.reset())
        controller.pin_locked.connect(lambda *_: self.reset())
        controller.unlocked.connect(lambda *_: self.reset())

    # ---- state ----------------------------------------------------------------------
    def _current_scope(self) -> tuple:
        c = self.config
        return (c.cli_enabled, c.agent_share_enabled, c.agent_folder_uuid, self.vc.path)

    def reset(self) -> None:
        """Drop every allowance and deny everything waiting (lock, unlock, settings change)."""
        self._allowed.clear()
        self._folder_until = 0.0
        self._scope = self._current_scope()
        pending = ([self._current] if self._current else []) + self._queue
        self._current, self._queue = None, []
        self._timer.stop()
        for r in pending:
            self._close_prompt(r)
            log_access("denied (locked or settings changed)", r.field, r.title, r.entry_id, r.client)
            r.reply({"ok": False, "error": "Cancelled: Vaultline was locked or its settings changed."})

    def has_allowances(self) -> bool:
        now = time.monotonic()
        return self.folder_pass_active() or any(t > now for t in self._allowed.values())

    def folder_pass_active(self) -> bool:
        return self._folder_until > time.monotonic()

    def folder_pass_left(self) -> float | None:
        """Seconds left on the folder pass (inf = until locked), or None when there isn't one."""
        return self._folder_until - time.monotonic() if self.folder_pass_active() else None

    def stop(self) -> None:
        """The tray's "Stop agent access": end every allowance and deny anything waiting."""
        if self.has_allowances():
            log_access("all allowances ended by user", "", "", "", "")
        self.reset()

    def cancel(self, key) -> None:
        """The client hung up: drop its waiting requests."""
        if self._current is not None and self._current.key is key:
            self._decide("gone")
        kept = []
        for r in self._queue:
            if r.key is key:
                log_access("denied (caller went away)", r.field, r.title, r.entry_id, r.client)
            else:
                kept.append(r)
        self._queue = kept

    # ---- checks -----------------------------------------------------------------------
    def _check(self, entry: EntryView, field_name: str, title_for_log: str, client: str) -> str:
        """Raise AccessRefused (logged) unless this entry/field may be handed out. Returns the value."""
        def refuse(why: str, msg: str):
            log_access(f"refused ({why})", field_name, title_for_log, entry.uuid if entry else "", client)
            raise AccessRefused(msg)

        c = self.config
        if not c.cli_enabled:
            refuse("command-line access off", "Command-line access is off.")
        if not self.vc.is_unlocked or self.vc.snapshot is None:
            refuse("vault locked", "The vault is locked. Unlock Vaultline first.")
        if not c.agent_share_enabled or not c.agent_folder_uuid:
            refuse("sharing off", "Sharing with agents is off. Turn it on in Vaultline > Settings.")
        snap = self.vc.snapshot
        shared = shared_folder_ids(snap, c.agent_folder_uuid)
        if not shared:
            refuse("shared folder missing", "The shared folder isn't in this database.")
        current = next((v for v in snap.entries if v.uuid == entry.uuid), None)
        if current is None or current.group_uuid not in shared:
            refuse("outside shared folder", "That entry isn't in the folder shared with agents.")
        if current.is_card:
            refuse("card", "Cards can't be shared with agents.")
        if field_name not in FIELDS:
            refuse("unknown field", f"Field must be one of: {', '.join(FIELDS)}")
        if field_name in current.references:
            refuse("field reference", f"The {field_name} is a reference to another entry; it can't be shared.")
        if field_name == "otp":
            if current.totp is None:
                refuse("no value", "That entry has no one-time code.")
            return otplib.current(current.totp)[0]
        value = current.password if field_name == "password" else current.username
        if not value:
            refuse("no value", f"That entry has no {field_name}.")
        return value

    # ---- requests -----------------------------------------------------------------------
    def request(self, entry: EntryView, field_name: str, client: str, reply: Callable[[dict], None],
                key=None) -> None:
        """Answer now (refused / allowance) or queue a prompt. `reply` gets the pipe response dict."""
        if self._scope != self._current_scope():
            self.reset()
        value = self._check(entry, field_name, entry.title, client)  # raises AccessRefused
        if self._allowed.get(entry.uuid, 0) > time.monotonic():
            log_access(f"allowed (within {ALLOW_MINUTES}-min allowance)", field_name, entry.title, entry.uuid, client)
            reply({"ok": True, "result": {"value": value}})
            return
        if self.folder_pass_active():
            log_access("allowed (folder pass)", field_name, entry.title, entry.uuid, client)
            reply({"ok": True, "result": {"value": value}})
            return
        if len(self._queue) + (self._current is not None) >= MAX_WAITING:
            log_access("refused (too many waiting)", field_name, entry.title, entry.uuid, client)
            raise AccessRefused("Too many requests are waiting for approval.")
        self._queue.append(Request(entry.uuid, entry.title, entry.username, entry.url,
                                   entry.group.replace(" / ", "/"), field_name, client, reply, key,
                                   folder_pass=folder_pass_label(folder_pass_minutes(self.config)),
                                   shared_folder=shared_folder_path(self.config, self.vc.snapshot) or ""))
        self._next()

    def _next(self) -> None:
        if self._current is not None:
            return
        while self._queue and self.folder_pass_active():  # waiting behind the prompt that granted it
            self._serve(self._queue.pop(0), "allowed (folder pass)")
        if not self._queue:
            return
        r = self._current = self._queue.pop(0)
        if self.ask is None:
            self._decide("deny")
            return
        self._timer.start(self.timeout_ms)
        r.prompt = self.ask(r, self._decide)

    def _decide(self, decision: str) -> None:
        r = self._current
        if r is None:
            return
        self._current = None
        self._timer.stop()
        self._close_prompt(r)
        minutes = folder_pass_minutes(self.config)
        if decision == "folder" and (not r.folder_pass or not minutes):
            decision = "once"  # the pass was turned off meanwhile: this one request only
        if decision in ("once", "entry", "folder"):
            outcome = {"once": "allowed once", "entry": f"allowed for {ALLOW_MINUTES} min",
                       "folder": f"allowed, folder pass {folder_pass_label(minutes)}"}[decision]
            if self._serve(r, outcome):
                if decision == "entry":
                    self._allowed[r.entry_id] = time.monotonic() + ALLOW_MINUTES * 60
                elif decision == "folder":
                    self._folder_until = math.inf if minutes < 0 else time.monotonic() + minutes * 60
        else:
            outcome = {"timeout": "denied (no answer)", "gone": "denied (caller went away)"}.get(decision, "denied")
            log_access(outcome, r.field, r.title, r.entry_id, r.client)
            if decision != "gone":
                r.reply({"ok": False, "error": "Denied in Vaultline." if decision == "deny"
                         else "No answer in Vaultline; denied."})
        self._next()

    def _serve(self, r: Request, outcome: str) -> bool:
        """Hand the value over after checking everything again (things may have changed meanwhile)."""
        snap = self.vc.snapshot
        entry = next((v for v in snap.entries if v.uuid == r.entry_id), None) if snap else None
        try:
            if entry is None:
                log_access("refused (entry gone)", r.field, r.title, r.entry_id, r.client)
                raise AccessRefused("That entry no longer exists.")
            value = self._check(entry, r.field, r.title, r.client)
        except AccessRefused as exc:
            r.reply({"ok": False, "error": str(exc)})
            return False
        log_access(outcome, r.field, r.title, r.entry_id, r.client)
        r.reply({"ok": True, "result": {"value": value}})
        return True

    @staticmethod
    def _close_prompt(r: Request) -> None:
        p, r.prompt = r.prompt, None
        if p is not None:
            try:
                p.close()
            except RuntimeError:  # already deleted
                pass
