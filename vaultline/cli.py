"""vaultline-cli: organise the vault from a terminal while Vaultline is unlocked.

Talks to the running app over a named pipe only your Windows account can open.
Turn it on in Vaultline > Settings > "Allow command-line access".
Never prints secrets (passwords, card numbers or codes, notes, OTP secrets,
custom field values), except `secret` below, which is off unless you share a
folder with agents in Settings, and asks you in Vaultline every time.

Examples
  vaultline-cli status
  vaultline-cli folders
  vaultline-cli list --folder Work --search github
  vaultline-cli list --json
  vaultline-cli show 3f2a9c
  vaultline-cli move 3f2a9c 81bb02 --to "Work/Clients"
  vaultline-cli update 3f2a9c --title "GitHub (work)" --add-tag work
  vaultline-cli delete 3f2a9c
  vaultline-cli folder create "Finance/Cards"
  vaultline-cli folder rename "Finance/Cards" "Credit cards"
  vaultline-cli folder move "Old stuff" --to Archive
  vaultline-cli folder delete "Old stuff"
  vaultline-cli batch ops.json          (one save for many changes; "-" reads stdin)
  vaultline-cli cards                   (cards: brand, last 4 digits, what's filled in)
  vaultline-cli cards fix --dry-run     (show what would be tidied, change nothing)
  vaultline-cli cards fix               (hide card numbers/codes, move imported
                                         Password_xxxxx fields into Password, etc.)
  vaultline-cli update 3f2a9c --brand Visa --cardholder "A Person"
  vaultline-cli secret 3f2a9c --field password
                                        (only for entries in the folder shared with
                                         agents; Vaultline asks you to allow it. Prints
                                         the bare value for a helper script to pipe into
                                         a browser field: never print or log it)

batch file: a JSON list of ops, e.g.
  [{"op": "move", "ids": ["3f2a9c"], "to": "Work"},
   {"op": "update", "id": "81bb02", "title": "Bank", "add_tags": ["finance"]},
   {"op": "delete", "ids": ["aa11bb"]},
   {"op": "folder-create", "path": "Archive/2024"},
   {"op": "folder-rename", "folder": "Misc", "name": "Other"},
   {"op": "folder-move", "folder": "Other", "to": "Archive"},
   {"op": "folder-delete", "folder": "Junk"},
   {"op": "merge", "keep": "3f2a9c", "drop": "81bb02"},
   {"op": "copy", "ids": ["3f2a9c"], "to": "Shared/Business"},
   {"op": "share", "ids": ["3f2a9c"], "with": ["Sam"]}]
  share: keep linked copies in vaults managed from this database (Settings > Shared vaults);
  unsharing is done in Vaultline (right-click > Share with…).
  A shared vault opened with this one (Settings > Shared vaults) is a top-level folder:
  "to": "Shared/Business" moves entries or folders into that file (copied, checked, then
  the originals go to the recycle bin). merge: two entries for the same login in one vault;
  keep's empty fields (one-time code, URL, notes, custom fields) are filled from drop, then
  drop is binned. Refused if the passwords, codes or other fields differ (no values shown).
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import pipe_name

PIPE = "\\\\.\\pipe\\" + pipe_name("cli")


class CliFailure(Exception):
    pass


def call(cmd: str, args: dict | None = None, timeout: float = 120.0) -> object:
    deadline = time.monotonic() + 5
    while True:
        try:
            fh = open(PIPE, "r+b", buffering=0)
            break
        except FileNotFoundError:
            raise CliFailure("Vaultline isn't running. Start it and unlock the vault.") from None
        except OSError as exc:  # pipe busy: another client is talking
            if time.monotonic() > deadline:
                raise CliFailure(f"Could not reach Vaultline: {exc}") from None
            time.sleep(0.1)
    with fh:
        fh.write(json.dumps({"cmd": cmd, "args": args or {}}).encode("utf-8") + b"\n")
        buf = b""
        end = time.monotonic() + timeout
        while b"\n" not in buf:
            chunk = fh.read(65536)
            if not chunk:
                break
            buf += chunk
            if time.monotonic() > end:
                raise CliFailure("Timed out waiting for Vaultline")
    if not buf:
        raise CliFailure("No answer from Vaultline")
    resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    if not resp.get("ok"):
        raise CliFailure(resp.get("error") or "Unknown error")
    return resp.get("result")


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    widths = [min(w, 48) for w in widths]

    def fmt(r):
        return "  ".join((c if len(c) <= w else c[: w - 1] + "…").ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([fmt(headers), fmt(["-" * w for w in widths]), *map(fmt, rows)])


def _print(cmd: str, result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if cmd == "cards":
        def yn(b):
            return "yes" if b else "no"
        rows = [[e["id"][:8], e["title"], e["card"]["brand"] or "-", e["card"]["last4"] or "-",
                 yn(e["card"]["has_code"]), yn(e["card"]["has_expiry"]), yn(e["has_password"]),
                 yn(e["card"]["has_website"]), yn(e["card"]["has_phone"]), str(len(e["card"]["fixes"]))]
                for e in result]
        print(_table(rows, ["ID", "TITLE", "BRAND", "LAST4", "CODE", "EXPIRY", "PASSWORD", "WEBSITE", "PHONE",
                            "FIXES"]))
        print(f"\n{len(result)} cards")
        return
    if cmd == "cards-fix":
        items = result.get("would_change", result.get("changed", []))
        head = "Would change:" if result.get("dry_run") else ("Done: " + result["done"] if "done" in result
                                                             else "Nothing to fix.")
        print(head)
        for it in items:
            print(f"  {it['id'][:8]}  {it['title']}: " + "; ".join(it["fixes"]))
        return
    if cmd in ("list", "bin"):
        rows = [[e["id"][:8], e["title"], e["username"], e["folder"] or "/",
                 ("card " if e["type"] == "card" else "") + ("otp" if e["has_otp"] else "")] for e in result]
        print(_table(rows, ["ID", "TITLE", "USERNAME", "FOLDER", ""]))
        print(f"\n{len(result)} entries")
    elif cmd == "folders":
        for f in result:
            print(f"{f['id'][:8]}  {'  ' * f['depth']}{f['name']}  ({f['entries']})")
    elif isinstance(result, dict) and "done" in result:
        print("Done:", result["done"])
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="vaultline-cli", description=__doc__.split("\n\n")[0],
                                epilog="\n\n".join(__doc__.split("\n\n")[1:]),
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="print raw JSON")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("folders")
    sp = sub.add_parser("list")
    sp.add_argument("--folder")
    sp.add_argument("--search")
    sp.add_argument("--cards", action="store_true")
    sp.add_argument("--no-subfolders", action="store_true")
    sp = sub.add_parser("show")
    sp.add_argument("id")
    sp = sub.add_parser("move")
    sp.add_argument("ids", nargs="+")
    sp.add_argument("--to", required=True, help='folder path or id; "/" for top level (created if missing)')
    sp = sub.add_parser("update")
    sp.add_argument("id")
    sp.add_argument("--title")
    sp.add_argument("--username")
    sp.add_argument("--url")
    sp.add_argument("--brand", help="card brand (cards only)")
    sp.add_argument("--cardholder", help="name on the card (cards only)")
    sp.add_argument("--phone", help="the bank's (toll-free) phone number (cards only)")
    sp.add_argument("--local-phone", help="the bank's local phone number (cards only)")
    sp.add_argument("--add-tag", action="append", default=[])
    sp.add_argument("--remove-tag", action="append", default=[])
    sp = sub.add_parser("delete", help="move entries to the recycle bin")
    sp.add_argument("ids", nargs="+")
    fp = sub.add_parser("folder").add_subparsers(dest="fcmd", required=True)
    f = fp.add_parser("create")
    f.add_argument("path")
    f = fp.add_parser("rename")
    f.add_argument("folder")
    f.add_argument("name")
    f = fp.add_parser("move")
    f.add_argument("folder")
    f.add_argument("--to", required=True)
    f = fp.add_parser("delete", help="move a folder and its contents to the recycle bin")
    f.add_argument("folder")
    cp = sub.add_parser("cards", help="list cards, or tidy them with 'cards fix'")
    csub = cp.add_subparsers(dest="ccmd")
    cf = csub.add_parser("fix", help="bring cards to the standard layout (values stay inside Vaultline)")
    cf.add_argument("ids", nargs="*", help="only these cards (default: all)")
    cf.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    sp = sub.add_parser("secret", help="one value from the folder shared with agents (asks you in Vaultline)")
    sp.add_argument("id")
    sp.add_argument("--field", choices=("password", "username", "otp"), default="password")
    sub.add_parser("bin", help="list what's in the recycle bin (newest first)")
    sp = sub.add_parser("compare", help="are two entries duplicates? same/different per field, no values shown")
    sp.add_argument("a")
    sp.add_argument("b")
    sp = sub.add_parser("restore", help="move entries out of the recycle bin")
    sp.add_argument("ids", nargs="+")
    sp.add_argument("--to", default="", help="folder to put them in (default: top level)")
    sp = sub.add_parser("batch")
    sp.add_argument("file", help='JSON file with a list of ops, or "-" for stdin')
    pp = sub.add_parser("profile", help="add a sign-up profile (Vaultline shows every value for you to approve)")
    psub = pp.add_subparsers(dest="pcmd", required=True)
    pa = psub.add_parser("add")
    pa.add_argument("file", help='JSON file: {"title": ..., "folder": optional, "fields": {"firstName": ...}}, '
                                 'or "-" for stdin (kept off the command line so values stay out of process lists)')

    a = p.parse_args(argv)
    try:
        if a.cmd == "list":
            cmd, args = "list", {"folder": a.folder, "search": a.search, "cards": a.cards,
                                 "recursive": not a.no_subfolders}
        elif a.cmd == "show":
            cmd, args = "show", {"id": a.id}
        elif a.cmd == "move":
            cmd, args = "move", {"ids": a.ids, "to": a.to}
        elif a.cmd == "update":
            cmd, args = "update", {"id": a.id, "title": a.title, "username": a.username, "url": a.url,
                                   "add_tags": a.add_tag, "remove_tags": a.remove_tag,
                                   "brand": a.brand, "cardholder": a.cardholder, "phone": a.phone,
                                   "local_phone": a.local_phone}
        elif a.cmd == "cards":
            if a.ccmd == "fix":
                cmd, args = "cards-fix", {"ids": a.ids, "dry_run": a.dry_run}
            else:
                cmd, args = "cards", {}
        elif a.cmd == "delete":
            cmd, args = "delete", {"ids": a.ids}
        elif a.cmd == "compare":
            cmd, args = "compare", {"a": a.a, "b": a.b}
        elif a.cmd == "restore":
            cmd, args = "restore", {"ids": a.ids, "to": a.to}
        elif a.cmd == "folder":
            cmd = f"folder-{a.fcmd}"
            args = {"path": a.path} if a.fcmd == "create" else {"folder": a.folder}
            if a.fcmd == "rename":
                args["name"] = a.name
            if a.fcmd == "move":
                args["to"] = a.to
        elif a.cmd == "secret":
            result = call("secret", {"id": a.id, "field": a.field})
            sys.stdout.write(result["value"])  # bare, no newline: for a script to pipe on, never to display
            sys.stdout.flush()
            return 0
        elif a.cmd == "batch":
            text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
            cmd, args = "batch", {"ops": json.loads(text)}
        elif a.cmd == "profile":
            text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("the profile file must hold one JSON object")
            result = call("profile-add", {"title": data.get("title"), "folder": data.get("folder"),
                                          "fields": data.get("fields")}, timeout=200)
            print(result if isinstance(result, str) else "Profile added.")
            return 0
        else:
            cmd, args = a.cmd, {}
        _print(cmd, call(cmd, args), a.json)
        return 0
    except CliFailure as exc:
        print(f"vaultline-cli: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"vaultline-cli: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
