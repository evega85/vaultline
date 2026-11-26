"""Local command-line endpoint inside the running app.

`vaultline-cli` connects to a named pipe that only the current Windows user can
open, and only while Settings > "Allow command-line access" is on and the vault
is unlocked. It can read structure (titles, usernames, URLs, folders, field
names) and reorganise (move, rename, tag, delete to recycle bin, folders).
It never returns secrets: no passwords, card numbers or codes, notes, OTP
secrets or custom field values.
"""
from __future__ import annotations

import json
from itertools import count

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from shiboken6 import isValid

from . import pipe_name
from .controller import VaultController
from .vault import (CreateFolder, DeleteEntries, DeleteFolder, EntryView, MoveEntries, MoveFolder, NormalizeCards,
                    RenameFolder, Snapshot, UpdateEntry, card_fixes)

PIPE_NAME = pipe_name("cli")
MAX_REQUEST = 4 * 1024 * 1024


class CliError(Exception):
    pass


def entry_meta(v: EntryView) -> dict:
    """Everything the CLI may know about an entry. Never put secret values in here."""
    username = v.username
    if v.is_card and username and username == v.card_number:
        username = f"•••• {v.card_last4}" if v.card_last4 else "••••"
    meta = {
        "id": v.uuid,
        "title": v.title,
        "username": username,
        "url": v.url,
        "folder": v.group.replace(" / ", "/"),
        "folder_id": v.group_uuid,
        "tags": list(v.tags),
        "type": "card" if v.is_card else "entry",
        "has_password": bool(v.password),
        "has_otp": v.totp is not None,
        "has_notes": bool(v.notes),
        "custom_fields": [{"name": f.key, "hidden": f.protected} for f in v.custom],
        "attachments": list(v.attachments),
        "has_icon": bool(v.icon_uuid),
        "created": v.created.isoformat() if v.created else None,
        "modified": v.modified.isoformat() if v.modified else None,
    }
    if v.is_card:
        meta["card"] = {
            "brand": v.card_brand,
            "last4": v.card_last4,
            "has_number": bool(v.card_number),
            "has_code": bool(v.card_code),
            "has_expiry": v.card_expiry is not None,
            "has_cardholder": bool(v.card_holder),
            "has_website": bool(v.url),
            "has_phone": bool(v.card_phone),
            "has_local_phone": bool(v.card_local_phone),
            "fixes": card_fixes(v),
        }
    return meta


class CliServer(QObject):
    changed_by_cli = Signal(str)  # human summary, for a toast

    def __init__(self, controller: VaultController, parent: QObject | None = None):
        super().__init__(parent)
        self.vc = controller
        self._ids = count(1)
        self._waiting: dict[int, tuple[QLocalSocket, str]] = {}
        self._buffers: dict[QLocalSocket, bytes] = {}
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        QLocalServer.removeServer(PIPE_NAME)
        self.server.listen(PIPE_NAME)
        self.server.newConnection.connect(self._on_conn)
        controller.applied.connect(self._on_applied)

    # ---- transport ---------------------------------------------------------------
    def _on_conn(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))

    def _drop(self, sock: QLocalSocket) -> None:
        self._buffers.pop(sock, None)
        if isValid(sock):
            sock.deleteLater()

    def _on_read(self, sock: QLocalSocket) -> None:
        buf = self._buffers.get(sock, b"") + bytes(sock.readAll())
        if len(buf) > MAX_REQUEST:
            self._reply(sock, {"ok": False, "error": "Request too large"})
            return
        if b"\n" not in buf:
            self._buffers[sock] = buf
            return
        line = buf.split(b"\n", 1)[0]
        self._buffers[sock] = b""
        try:
            req = json.loads(line.decode("utf-8"))
            if not isinstance(req, dict):
                raise CliError("Bad request")
            result = self._handle(req, sock)
        except CliError as exc:
            self._reply(sock, {"ok": False, "error": str(exc)})
            return
        except (ValueError, KeyError, TypeError) as exc:
            self._reply(sock, {"ok": False, "error": f"Bad request: {exc}"})
            return
        if result is not None:
            self._reply(sock, {"ok": True, "result": result})

    def _reply(self, sock: QLocalSocket, obj: dict) -> None:
        if isValid(sock) and sock.state() == QLocalSocket.ConnectedState:  # client may have gone
            sock.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
            sock.flush()

    # ---- commands --------------------------------------------------------------------
    def _snap(self) -> Snapshot:
        if not self.vc.config.cli_enabled:
            raise CliError("Command-line access is off. Turn it on in Vaultline > Settings.")
        if not self.vc.is_unlocked or self.vc.snapshot is None:
            raise CliError("The vault is locked. Unlock Vaultline first.")
        return self.vc.snapshot

    def _handle(self, req: dict, sock: QLocalSocket):
        cmd = req.get("cmd")
        args = req.get("args") or {}
        if cmd == "status":
            state = "unlocked" if self.vc.is_unlocked else "pin-locked" if self.vc.is_pin_locked else "locked"
            out = {"state": state, "cli_enabled": self.vc.config.cli_enabled}
            if self.vc.is_unlocked and self.vc.config.cli_enabled and self.vc.snapshot:
                s = self.vc.snapshot
                out.update(database=self.vc.path, entries=len(s.entries), folders=len(s.groups))
            return out
        snap = self._snap()
        if cmd == "folders":
            counts: dict[str, int] = {}
            for v in snap.entries:
                counts[v.group_uuid] = counts.get(v.group_uuid, 0) + 1
            return [{"id": g.uuid, "path": "/".join(g.path), "name": g.name, "depth": g.depth,
                     "entries": counts.get(g.uuid, 0)} for g in snap.groups]
        if cmd == "list":
            items = snap.entries
            if args.get("folder"):
                fid = self._folder_id(snap, args["folder"])
                paths = {g.uuid: g.display for g in snap.groups}
                prefix = paths.get(fid, "")
                items = [v for v in items if v.group_uuid == fid or
                         (args.get("recursive", True) and prefix and v.group.startswith(prefix + " / "))]
            if args.get("search"):
                terms = str(args["search"]).lower().split()
                items = [v for v in items if v.matches(terms)]
            if args.get("cards"):
                items = [v for v in items if v.is_card]
            return [entry_meta(v) for v in items]
        if cmd == "show":
            return entry_meta(self._entry(snap, args["id"]))
        if cmd == "cards":
            return [entry_meta(v) for v in snap.entries if v.is_card]
        if cmd == "cards-fix":
            if args.get("ids"):
                targets = [self._entry(snap, i) for i in self._list(args, "ids")]
                targets = [v for v in targets if v.is_card]
            else:
                targets = [v for v in snap.entries if v.is_card]
            plan = [{"id": v.uuid, "title": v.title, "fixes": card_fixes(v)} for v in targets]
            plan = [p for p in plan if p["fixes"]]
            if args.get("dry_run") or not plan:
                return {"dry_run": bool(args.get("dry_run")), "would_change": plan}
            return self._apply(sock, [NormalizeCards([p["id"] for p in plan])],
                               f"fixed {len(plan)} card{'s' if len(plan) != 1 else ''}")
        if cmd == "batch":
            ops = args.get("ops")
            if not isinstance(ops, list) or not ops:
                raise CliError("batch needs a non-empty list of ops")
            changes, summary = [], []
            for op in ops:
                ch, s = self._change(snap, op)
                changes.extend(ch)
                summary.append(s)
            return self._apply(sock, changes, f"{len(ops)} changes")
        if cmd in ("move", "update", "delete", "folder-create", "folder-rename", "folder-move", "folder-delete"):
            changes, summary = self._change(snap, {"op": cmd, **args})
            return self._apply(sock, changes, summary)
        raise CliError(f"Unknown command: {cmd}")

    def _change(self, snap: Snapshot, op: dict) -> tuple[list, str]:
        kind = op.get("op")
        if kind == "move":
            ids = [self._entry(snap, i).uuid for i in self._list(op, "ids")]
            target = str(op.get("to", "")).strip()
            if target in ("", "/"):
                return [MoveEntries(ids, group_uuid=snap.root_uuid)], f"moved {len(ids)} to top level"
            fid = self._folder_id(snap, target, must_exist=False)
            ch = MoveEntries(ids, group_uuid=fid) if fid else MoveEntries(ids, new_group=target)
            return [ch], f"moved {len(ids)} to {target}"
        if kind == "update":
            v = self._entry(snap, op["id"])
            ch = UpdateEntry(v.uuid, title=op.get("title"), username=op.get("username"), url=op.get("url"),
                             tags_add=list(op.get("add_tags") or []), tags_remove=list(op.get("remove_tags") or []),
                             card_brand=op.get("brand"), card_holder=op.get("cardholder"),
                             card_phone=op.get("phone"), card_local_phone=op.get("local_phone"))
            if v.is_card and ch.username is not None and v.username == v.card_number:
                raise CliError("Card numbers can't be changed from the command line")
            if not v.is_card and (ch.card_brand is not None or ch.card_holder is not None
                                  or ch.card_phone is not None or ch.card_local_phone is not None):
                raise CliError(f"{v.title} isn't a card")
            return [ch], f"updated {v.title}"
        if kind == "delete":
            ids = [self._entry(snap, i).uuid for i in self._list(op, "ids")]
            return [DeleteEntries(ids)], f"moved {len(ids)} to the recycle bin"
        if kind == "folder-create":
            return [CreateFolder(str(op["path"]))], f"created folder {op['path']}"
        if kind == "folder-rename":
            fid = self._folder_id(snap, op["folder"])
            return [RenameFolder(fid, str(op["name"]))], f"renamed a folder to {op['name']}"
        if kind == "folder-move":
            fid = self._folder_id(snap, op["folder"])
            to = str(op.get("to", "")).strip()
            parent = None if to in ("", "/") else self._folder_id(snap, to)
            return [MoveFolder(fid, parent)], "moved a folder"
        if kind == "folder-delete":
            fid = self._folder_id(snap, op["folder"])
            return [DeleteFolder(fid)], "moved a folder to the recycle bin"
        raise CliError(f"Unknown op: {kind}")

    def _apply(self, sock: QLocalSocket, changes: list, summary: str):
        token = next(self._ids)
        self._waiting[token] = (sock, summary)
        if not self.vc.apply(changes, token=("cli", token)):
            self._waiting.pop(token)
            raise CliError("The vault is locked. Unlock Vaultline first.")
        return None  # reply comes from _on_applied

    def _on_applied(self, token, ok: bool, msg: str, report=None) -> None:
        if not (isinstance(token, tuple) and token[0] == "cli"):
            return
        sock, summary = self._waiting.pop(token[1], (None, ""))
        if sock is None:
            return
        if ok:
            self.changed_by_cli.emit(summary)
            result = {"done": summary}
            if report:
                result["changed"] = report  # value-free: ids, titles and what was fixed
            self._reply(sock, {"ok": True, "result": result})
        else:
            self._reply(sock, {"ok": False, "error": msg})

    # ---- lookups -------------------------------------------------------------------------
    @staticmethod
    def _list(op: dict, key: str) -> list:
        v = op.get(key)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not v:
            raise CliError(f"'{key}' must be a non-empty list")
        return v

    @staticmethod
    def _entry(snap: Snapshot, ident) -> EntryView:
        ident = str(ident).strip().lower().replace("-", "")
        if len(ident) < 6:
            raise CliError(f"Entry id '{ident}' is too short; use at least 6 characters")
        hits = [v for v in snap.entries if v.uuid.startswith(ident)]
        if not hits:
            raise CliError(f"No entry with id {ident}")
        if len(hits) > 1:
            raise CliError(f"Id {ident} matches {len(hits)} entries; use more characters")
        return hits[0]

    @staticmethod
    def _folder_id(snap: Snapshot, ident, must_exist: bool = True) -> str | None:
        ident = str(ident).strip()
        if ident in ("/", ""):
            return snap.root_uuid
        low = ident.lower().replace("-", "")
        by_id = [g for g in snap.groups if len(low) >= 6 and g.uuid.startswith(low)]
        if len(by_id) == 1:
            return by_id[0].uuid
        parts = tuple(p.strip() for p in ident.replace("\\", "/").split("/") if p.strip())
        by_path = [g for g in snap.groups if g.path == parts]
        if len(by_path) == 1:
            return by_path[0].uuid
        if len(by_path) > 1:
            raise CliError(f"More than one folder is called {ident}; use its id")
        if must_exist:
            raise CliError(f"No folder {ident}")
        return None
