"""Local command-line endpoint inside the running app.

`vaultline-cli` connects to a named pipe that only the current Windows user can
open, and only while Settings > "Allow command-line access" is on and the vault
is unlocked. It can read structure (titles, usernames, URLs, folders, field
names) and reorganise (move, rename, tag, delete to recycle bin, folders).
It never returns secrets: no passwords, card numbers or codes, notes, OTP
secrets or custom field values.

The one exception is `secret` (see agent_access.py): off by default, limited to one folder the
user shares in Settings, and every request needs the user's approval in the app.
"""
from __future__ import annotations

import json
from itertools import count

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from shiboken6 import isValid

import re

from . import identity as idlib, pipe_name
from .agent_access import AccessRefused, AgentGate, client_process, log_access, shared_folder_path, shared_ids_for
from .controller import VaultController
from .vault import (CopyEntries, CreateFolder, DeleteEntries, DeleteFolder, EntryView, MergeDuplicate, MoveEntries, MoveFolder, NormalizeCards,
                    RenameFolder, Snapshot, UpdateEntry, card_fixes, CustomField, EntryEdit)

PIPE_NAME = pipe_name("cli")
# `profile add`: short names (Bitwarden's) -> profile keys. The one place the CLI hands over values
# to be written, and only after the user approves every value in Vaultline (see _profile_add).
PROFILE_FIELDS = {k[len(idlib.PREFIX):]: k for k in idlib.KEYS}
_PROFILE_BAD = re.compile(r"[\x00-\x1f\x7f]")
MAX_REQUEST = 4 * 1024 * 1024


class CliError(Exception):
    pass


def entry_meta(v: EntryView) -> dict:
    """Everything the CLI may know about an entry. Never put secret values in here."""
    username = v.username
    if "username" in v.references:
        username = "(reference)"  # it could point at another entry's password
    if v.is_card and username and username == v.card_number:
        username = f"•••• {v.card_last4}" if v.card_last4 else "••••"
    meta = {
        "id": v.uuid,
        "title": v.title,
        "username": username,
        "url": v.url,
        "more_urls": list(v.extra_urls),
        "shared_with": list(v.shares),
        "folder": v.group.replace(" / ", "/"),
        "folder_id": v.group_uuid,
        "tags": list(v.tags),
        "type": "card" if v.is_card else ("identity" if v.is_identity else "entry"),
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


def compare_entries(a: EntryView, b: EntryView) -> dict:
    """Which fields two entries share, as true/false only (for finding real duplicates). No values."""
    def otp(v):
        return v.totp.secret if v.totp is not None else ""
    ca, cb = {f.key: f.value for f in a.custom}, {f.key: f.value for f in b.custom}
    same = {"title": a.title == b.title, "username": a.username == b.username, "password": a.password == b.password,
            "url": a.url == b.url, "more_urls": a.extra_urls == b.extra_urls, "notes": a.notes == b.notes, "otp": otp(a) == otp(b),
            "custom_fields": ca == cb, "attachments": a.attachments == b.attachments}
    return {"a": a.uuid, "b": b.uuid, "same": same, "identical": all(same.values()),
            "only_in_a": sorted(set(ca) - set(cb)), "only_in_b": sorted(set(cb) - set(ca)),
            "a_has": {"notes": bool(a.notes), "otp": a.totp is not None, "custom_fields": len(ca)},
            "b_has": {"notes": bool(b.notes), "otp": b.totp is not None, "custom_fields": len(cb)}}


class CliServer(QObject):
    changed_by_cli = Signal(str)  # human summary, for a toast

    def __init__(self, controller: VaultController, parent: QObject | None = None, gate: AgentGate | None = None,
                 ask_profile=None):
        super().__init__(parent)
        self.vc = controller
        self.gate = gate  # None: `secret` is always refused
        self.ask_profile = ask_profile  # (request, decide) -> prompt with .close(); None: `profile add` refused
        self._profile: tuple | None = None  # (socket, prompt) while one is being asked
        for sig in (controller.locked, controller.pin_locked):
            sig.connect(lambda *_: self._end_profile("The vault was locked. Nothing was added."))
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
        if self._profile is not None and self._profile[0] is sock:
            self._end_profile("")
        if self.gate is not None:
            self.gate.cancel(sock)
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
                out["agent_folder"] = shared_folder_path(self.vc.config, s)
            return out
        snap = self._snap()
        if cmd == "secret":
            return self._secret(snap, args, sock)
        if cmd == "profile-add":
            return self._profile_add(snap, args, sock)
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
        if cmd == "compare":  # are two entries the same? Answers same/different per field, never a value
            a, b = self._entry(snap, args["a"]), self._entry(snap, args["b"])
            return compare_entries(a, b)
        if cmd == "bin":  # what's in the recycle bin, newest first (no secrets, as everywhere)
            return [entry_meta(v) for v in snap.binned]
        if cmd == "restore":  # move entries out of the recycle bin into a folder
            binned = {v.uuid: v for v in snap.binned}
            ids = []
            for ident in self._list(args, "ids"):
                low = str(ident).lower().replace("-", "")
                hits = [u for u in binned if len(low) >= 6 and u.startswith(low)]
                if len(hits) != 1:
                    raise CliError(f"{ident}: not one entry in the recycle bin (see `vaultline-cli bin`)")
                ids.append(hits[0])
            target = str(args.get("to") or "").strip()
            fid = snap.root_uuid if target in ("", "/") else self._folder_id(snap, target)
            self._guard_shared(snap, [""], fid)
            titles = ", ".join(binned[u].title for u in ids)
            return self._apply(sock, [MoveEntries(ids, group_uuid=fid)], f"restored {titles}")
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

    def _secret(self, snap: Snapshot, args: dict, sock: QLocalSocket):
        if self.gate is None:
            raise CliError("Sharing with agents isn't available.")
        field_name = str(args.get("field") or "password")
        entry = self._entry(snap, args["id"])
        try:
            self.gate.request(entry, field_name, client_process(sock), lambda resp: self._reply(sock, resp), key=sock)
        except AccessRefused as exc:
            raise CliError(str(exc)) from None
        return None  # reply comes from the gate (now, or after the prompt)

    # ---- profile add (asks in Vaultline) ----------------------------------------------------------
    def _profile_add(self, snap: Snapshot, args: dict, sock: QLocalSocket):
        """Add a sign-up profile, only if the user presses Add in a prompt showing every value.
        Logged (title and outcome, never values) in the agent access log."""
        if self.ask_profile is None:
            raise CliError("Adding profiles from the command line isn't available.")
        if self._profile is not None:
            raise CliError("Vaultline is already asking about a profile. Answer that first.")
        title = args.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 100 or _PROFILE_BAD.search(title):
            raise CliError("Give the profile a title (one line, up to 100 characters).")
        raw = args.get("fields")
        if not isinstance(raw, dict) or not raw:
            raise CliError("No profile fields given.")
        fields: dict[str, str] = {}
        for name, value in raw.items():
            key = PROFILE_FIELDS.get(str(name))
            if key is None:
                raise CliError(f"Unknown profile field: {name}. Use: {', '.join(PROFILE_FIELDS)}")
            if not isinstance(value, str) or len(value) > 200 or _PROFILE_BAD.search(value):
                raise CliError(f"{name} must be one line of text (up to 200 characters).")
            if value.strip():
                fields[key] = value.strip()
        for v in [title, *fields.values()]:
            if "{REF:" in v.upper():
                raise CliError("Field references ({REF:...}) can't be set from the command line")
        if idlib.BIRTH in fields:
            d = idlib.parse_birth_date(fields[idlib.BIRTH])
            if d is None:
                raise CliError("birthDate must be YYYY-MM-DD.")
            fields[idlib.BIRTH] = idlib.format_date(d, "iso")
        if not fields:
            raise CliError("No profile fields given.")
        folder = str(args.get("folder") or "").strip()
        fid = self._folder_id(snap, folder) if folder not in ("", "/") else snap.root_uuid
        self._guard_shared(snap, [""], fid)  # never straight into the folder shared with agents
        ordered = {k: fields[k] for k in idlib.KEYS if k in fields}
        client = client_process(sock)
        request = {"title": title.strip(), "folder": folder if folder not in ("", "/") else "",
                   "fields": ordered, "client": client}

        def decide(decision: str) -> None:
            if self._profile is None or self._profile[0] is not sock:
                return  # already ended (lock, caller gone)
            self._profile = None
            if decision != "add" or not self.vc.is_unlocked:
                log_access("profile add: cancelled", "profile", title, "", client)
                self._reply(sock, {"ok": False, "error": "Cancelled in Vaultline. Nothing was added."})
                return
            log_access("profile add: approved", "profile", title, "", client)
            custom = [CustomField(k, v, k in idlib.PROTECTED) for k, v in ordered.items()]
            edit = EntryEdit(None, title.strip(), "", "", "", "", None, fid, "", custom)
            try:
                self._apply(sock, [edit], f"added profile {title.strip()}")
            except CliError as exc:
                self._reply(sock, {"ok": False, "error": str(exc)})

        log_access("profile add: asked", "profile", title, "", client)
        self._profile = (sock, None)
        prompt = self.ask_profile(request, decide)
        if self._profile is not None and self._profile[0] is sock:
            self._profile = (sock, prompt)
        return None  # the reply comes after the prompt

    def _end_profile(self, message: str) -> None:
        """Lock or caller gone: close the prompt; nothing is added."""
        if self._profile is None:
            return
        sock, prompt = self._profile
        self._profile = None
        if prompt is not None:
            prompt.close()
        if message:
            self._reply(sock, {"ok": False, "error": message})

    def _guard_shared(self, snap: Snapshot, moving_from: list[str], dest_uuid: str | None, dest_path: str = ""):
        """Refuse to bring entries or folders INTO the agents' folder from outside it: otherwise the
        CLI could move any entry there and then ask for its password. Moves within it are fine."""
        shared = shared_ids_for(self.vc.config, snap)
        if not shared:
            return
        if dest_uuid is None and dest_path:  # path that may not exist yet: check its existing parents
            parts = tuple(p.strip() for p in dest_path.replace("\\", "/").split("/") if p.strip())
            for n in range(len(parts), 0, -1):
                hit = [g for g in snap.groups if g.path == parts[:n]]
                if hit:
                    dest_uuid = hit[0].uuid if len(hit) == 1 else None
                    if len(hit) > 1 and any(g.uuid in shared for g in hit):
                        dest_uuid = next(g.uuid for g in hit if g.uuid in shared)
                    break
        if dest_uuid in shared and any(src not in shared for src in moving_from):
            raise CliError("Can't move things into the folder shared with agents from the command line. "
                           "Do it in Vaultline.")

    @staticmethod
    def _no_refs(op: dict, keys: tuple[str, ...]) -> None:
        for k in keys:
            vals = op.get(k)
            for v in (vals if isinstance(vals, list) else [vals]):
                if isinstance(v, str) and "{REF:" in v.upper():
                    raise CliError("Field references ({REF:...}) can't be set from the command line")

    def _change(self, snap: Snapshot, op: dict) -> tuple[list, str]:
        kind = op.get("op")
        if kind == "move":
            views = [self._entry(snap, i) for i in self._list(op, "ids")]
            ids = [v.uuid for v in views]
            target = str(op.get("to", "")).strip()
            if target in ("", "/"):
                return [MoveEntries(ids, group_uuid=snap.root_uuid)], f"moved {len(ids)} to top level"
            fid = self._folder_id(snap, target, must_exist=False)
            self._guard_shared(snap, [v.group_uuid for v in views], fid, target)
            ch = MoveEntries(ids, group_uuid=fid) if fid else MoveEntries(ids, new_group=target)
            return [ch], f"moved {len(ids)} to {target}"
        if kind == "share":  # which managed vaults entries are shared with (adds to what they have)
            import json
            from .sharing import KEY_NOT, KEY_SHARE, SetData
            managed = {v.name.casefold(): v.vault_id for v in self.vc.managed_vaults() if v.vault_id}
            wanted = [str(n) for n in (op.get("with") or [])]
            unknown = [n for n in wanted if n.casefold() not in managed]
            if unknown or not wanted:
                raise CliError(f"Not a vault managed from here: {', '.join(unknown) or '(none given)'}. "
                               f"Managed: {', '.join(v.name for v in self.vc.managed_vaults()) or 'none'}")
            ids = {managed[n.casefold()] for n in wanted}
            changes = []
            for i in self._list(op, "ids"):
                v = self._entry(snap, i)
                if v.vault:
                    raise CliError(f"{v.title} is in an attached vault, not in this database")
                own = set(json.loads(v.data.get(KEY_SHARE) or "[]")) | ids
                nots = set(json.loads(v.data.get(KEY_NOT) or "[]")) - ids
                changes.append(SetData(v.uuid, KEY_SHARE, json.dumps(sorted(own))))
                changes.append(SetData(v.uuid, KEY_NOT, json.dumps(sorted(nots)) if nots else None))
            return changes, f"shared {len(changes) // 2} with {', '.join(wanted)}"
        if kind == "copy":  # like move, but the originals stay
            views = [self._entry(snap, i) for i in self._list(op, "ids")]
            ids = [v.uuid for v in views]
            target = str(op.get("to", "")).strip()
            if target in ("", "/"):
                return [CopyEntries(ids, group_uuid=snap.root_uuid)], f"copied {len(ids)} to top level"
            fid = self._folder_id(snap, target, must_exist=False)
            self._guard_shared(snap, [v.group_uuid for v in views], fid, target)
            ch = CopyEntries(ids, group_uuid=fid) if fid else CopyEntries(ids, new_group=target)
            return [ch], f"copied {len(ids)} to {target}"
        if kind == "update":
            self._no_refs(op, ("title", "username", "url", "add_tags", "brand", "cardholder", "phone",
                               "local_phone"))
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
        if kind == "merge":  # duplicates: fills keep's empty fields from drop inside the app; no values here
            keep, drop = self._entry(snap, op["keep"]), self._entry(snap, op["drop"])
            self._guard_shared(snap, [drop.group_uuid], keep.group_uuid)
            return [MergeDuplicate(keep.uuid, drop.uuid)], f"merged {keep.title}"
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
            self._guard_shared(snap, [fid], parent)
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
