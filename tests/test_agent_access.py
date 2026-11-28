"""Sharing one folder with agents: `vaultline-cli secret` over the real pipe, with a stand-in for the prompt.

Made-up data only. Pipe names are private to this test run (tests/conftest.py).
"""
from __future__ import annotations

import contextlib
import os
import io
import json
import threading
import time

import pyotp
import pytest
from pykeepass import PyKeePass
from PySide6.QtCore import QTimer

from test_gui import PW, SECRET, app, setup, unlock, wait_for  # noqa: F401  (fixtures)
from vaultline import agent_access, cli
from vaultline import pin as pinlib
from vaultline.cli_server import CliServer

SECRETS = ("gh-pass", "g-pass", "aws-pass", "n-pass", "r-pass", SECRET)


class FakeAsk:
    """Stands in for the approval prompt. Answers with the next queued decision, or never (None)."""

    def __init__(self):
        self.answers: list[str | None] = []
        self.asked: list = []
        self.closed = 0

    def __call__(self, request, decide):
        self.asked.append(request)
        answer = self.answers.pop(0) if self.answers else None
        if answer is not None:
            QTimer.singleShot(0, lambda: decide(answer))
        fake = self

        class Prompt:
            def close(self):
                fake.closed += 1
        return Prompt()


@pytest.fixture
def agent(setup, tmp_path, monkeypatch):
    app, win, vc, clip, db = setup
    monkeypatch.setattr(agent_access, "ACCESS_LOG", tmp_path / "agent-access.log")
    ask = FakeAsk()
    gate = agent_access.AgentGate(vc, win.config, ask=ask, parent=app)
    server = CliServer(vc, app, gate=gate)
    win.config.cli_enabled = True
    unlock(app, win, vc)
    work = next(g for g in vc.snapshot.groups if g.name == "Work")
    ids = {v.title: v.uuid for v in vc.snapshot.entries}

    def run(*argv):
        out = {}

        def target():
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                out["code"] = cli.main(list(argv))
            out["out"], out["err"] = buf.getvalue(), err.getvalue()
        t = threading.Thread(target=target)
        t.start()
        wait_for(app, lambda: not t.is_alive(), 90)
        return out

    def share(folder=work.uuid, on=True):
        win.config.agent_share_enabled = on
        win.config.agent_folder_uuid = folder if on else ""

    yield {"app": app, "win": win, "vc": vc, "db": db, "gate": gate, "ask": ask, "run": run, "share": share,
           "ids": ids, "work": work, "log": tmp_path / "agent-access.log"}
    server.server.close()


def log_text(a) -> str:
    return a["log"].read_text(encoding="utf-8") if a["log"].exists() else ""


def test_refused_when_off_outside_or_locked(agent):
    a = agent
    run, aws, gh = a["run"], a["ids"]["AWS Console"][:8], a["ids"]["GitHub"][:8]

    r = run("secret", aws)
    assert r["code"] == 1 and "Sharing with agents is off" in r["err"] and r["out"] == ""

    a["share"]()
    r = run("secret", gh)  # top level, not in Work
    assert r["code"] == 1 and "isn't in the folder shared" in r["err"]

    a["win"].config.cli_enabled = False
    r = run("secret", aws)
    assert r["code"] == 1 and "Command-line access is off" in r["err"]
    a["win"].config.cli_enabled = True

    cfg = a["win"].config
    cfg.pin_enabled, (cfg.pin_salt, cfg.pin_hash) = True, pinlib.make("2468")
    a["vc"].lock()
    a["app"].processEvents()
    assert a["vc"].is_pin_locked
    r = run("secret", aws)
    assert r["code"] == 1 and "locked" in r["err"]
    a["vc"].lock(full=True)
    a["app"].processEvents()
    r = run("secret", aws)
    assert r["code"] == 1 and "locked" in r["err"]
    assert a["ask"].asked == []  # none of these reached a prompt
    assert "refused (outside shared folder)" in log_text(a)


def test_deny_timeout_and_allow_once(agent):
    a = agent
    a["share"]()
    run, ask, aws = a["run"], a["ask"], a["ids"]["AWS Console"][:8]

    ask.answers = ["deny"]
    r = run("secret", aws)
    assert r["code"] == 1 and "Denied" in r["err"] and r["out"] == ""

    ask.answers = [None]  # nobody answers
    a["gate"].timeout_ms = 300
    r = run("secret", aws)
    assert r["code"] == 1 and "No answer" in r["err"] and ask.closed >= 1

    ask.answers = ["once"]
    r = run("secret", aws, "--field", "password")
    assert r["code"] == 0 and r["out"] == "aws-pass"
    ask.answers = ["once"]
    r = run("secret", aws, "--field", "username")
    assert r["code"] == 0 and r["out"] == "admin"
    ask.answers = ["once"]
    r = run("secret", aws, "--field", "otp")
    now = time.time()
    assert r["code"] == 0 and r["out"] in {pyotp.TOTP(SECRET).at(now + d) for d in (-30, 0)}
    assert len(ask.asked) == 5  # every request asked: "once" grants nothing further
    req = ask.asked[-1]
    assert req.title == "AWS Console" and req.field == "otp" and req.folder == "Work"
    assert "python" in req.client.lower() and "PID" in req.client  # the calling program, from the pipe
    # its parent: whatever started this pytest run (python.exe via a venv locally, pwsh.exe on CI)
    import ctypes
    from vaultline.agent_access import _parent_name
    parent = _parent_name(ctypes.WinDLL("kernel32", use_last_error=True), os.getpid())
    assert parent and req.client.endswith(f", started by {parent}"), req.client

    log = log_text(a)
    for s in SECRETS + ("admin", r["out"]):
        assert s not in log.replace("AWS Console", ""), s  # never a value (the title may contain anything)
    for outcome in ("\tdenied\t", "denied (no answer)", "allowed once"):
        assert outcome in log


def test_allow_entry_for_a_while_ends_on_lock(agent):
    a = agent
    a["share"]()
    run, ask, aws = a["run"], a["ask"], a["ids"]["AWS Console"][:8]
    ask.answers = ["entry"]
    assert run("secret", aws)["out"] == "aws-pass"
    r = run("secret", aws, "--field", "username")  # no prompt this time
    assert r["code"] == 0 and r["out"] == "admin" and len(ask.asked) == 1
    assert a["gate"].has_allowances()
    assert "within 15-min allowance" in log_text(a)

    a["vc"].lock("Locked after inactivity.", full=True)  # auto-lock path
    a["app"].processEvents()
    assert not a["gate"].has_allowances()
    unlock(a["app"], a["win"], a["vc"])
    ask.answers = ["deny"]
    r = run("secret", aws)
    assert r["code"] == 1 and len(ask.asked) == 2  # asked again after the lock

    ask.answers = ["entry"]
    assert run("secret", aws)["out"] == "aws-pass"
    assert a["gate"].has_allowances()
    a["vc"].lock()  # the lock button / Ctrl+L path
    a["app"].processEvents()
    assert not a["gate"].has_allowances()
    unlock(a["app"], a["win"], a["vc"])
    ask.answers = ["deny"]
    assert run("secret", aws)["code"] == 1 and len(ask.asked) == 4


def test_lock_cancels_a_waiting_prompt(agent):
    a = agent
    a["share"]()
    ask = a["ask"]
    ask.answers = [None]
    out = {}

    def target():
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            out["code"] = cli.main(["secret", a["ids"]["AWS Console"][:8]])
        out["out"], out["err"] = buf.getvalue(), err.getvalue()
    t = threading.Thread(target=target)
    t.start()
    assert wait_for(a["app"], lambda: ask.asked)
    a["vc"].lock(full=True)
    wait_for(a["app"], lambda: not t.is_alive(), 30)
    assert out["code"] == 1 and "Cancelled" in out["err"] and ask.closed == 1


def test_no_other_command_returns_secrets_and_moves_in_are_blocked(agent):
    a = agent
    a["share"]()
    run, ids = a["run"], a["ids"]
    outs = []
    for argv in (["--json", "status"], ["--json", "list"], ["--json", "folders"], ["--json", "cards"],
                 ["--json", "show", ids["AWS Console"][:8]], ["--json", "list", "--folder", "Work"]):
        r = run(*argv)
        assert r["code"] == 0, r
        outs.append(r["out"])
    assert json.loads(outs[0])["agent_folder"] == "Work"
    for out in outs:
        for s in SECRETS:
            assert s not in out, (s, out)

    # bringing entries or folders into the shared folder from outside is refused...
    r = run("move", ids["GitHub"][:8], "--to", "Work")
    assert r["code"] == 1 and "shared with agents" in r["err"]
    r = run("move", ids["GitHub"][:8], "--to", "Work/New sub")  # a path that doesn't exist yet
    assert r["code"] == 1 and "shared with agents" in r["err"]
    assert run("folder", "create", "Elsewhere")["code"] == 0
    r = run("folder", "move", "Elsewhere", "--to", "Work")
    assert r["code"] == 1 and "shared with agents" in r["err"]
    # ...moving within it or out of it is fine
    assert run("folder", "create", "Work/Sub")["code"] == 0
    assert run("move", ids["AWS Console"][:8], "--to", "Work/Sub")["code"] == 0
    a["ask"].answers = ["once"]
    r = run("secret", ids["AWS Console"][:8])  # subfolders count
    assert r["code"] == 0 and r["out"] == "aws-pass"
    kp = PyKeePass(str(a["db"]), password=PW)
    assert kp.find_entries(title="GitHub", first=True).group.is_root_group


def test_field_references_cant_be_planted_or_shared(agent):
    a = agent
    a["share"]()
    run, ids = a["run"], a["ids"]
    gh_ref = "{REF:P@I:" + ids["GitHub"].upper() + "}"
    r = run("update", ids["AWS Console"][:8], "--username", gh_ref)
    assert r["code"] == 1 and "references" in r["err"]
    r = run("update", ids["AWS Console"][:8], "--username", gh_ref.lower())
    assert r["code"] == 1

    # a reference made in another app: not shown by list, and not handed out by secret
    kp = PyKeePass(str(a["db"]), password=PW)
    e = kp.find_entries(title="AWS Console", first=True)
    e.username = gh_ref
    kp.save()
    assert wait_for(a["app"], lambda: "username" in next(v for v in a["vc"].snapshot.entries
                                                        if v.title == "AWS Console").references, 20)
    out = run("--json", "list")["out"]
    assert "gh-pass" not in out and "(reference)" in out
    a["ask"].answers = ["once"]
    r = run("secret", ids["AWS Console"][:8], "--field", "username")
    assert r["code"] == 1 and "reference" in r["err"] and "gh-pass" not in r["out"]


def test_settings_pick_folder_and_prompt_defaults(agent, monkeypatch):
    from vaultline.ui.agent_prompt import AgentPrompt
    from vaultline.ui.settings_page import SettingsPage
    a = agent
    vc, cfg = a["vc"], a["win"].config
    dlg = SettingsPage(cfg, vc.path, vc.snapshot.groups)
    assert not dlg.folder.isEnabled()
    dlg.share.setChecked(True)
    dlg.folder.setCurrentIndex(dlg.folder.findData(a["work"].uuid))
    dlg.save()
    assert cfg.agent_share_enabled and cfg.agent_folder_uuid == a["work"].uuid

    dlg = SettingsPage(cfg, vc.path, vc.snapshot.groups)
    dlg.share.setChecked(False)
    dlg.save()
    assert not cfg.agent_share_enabled and cfg.agent_folder_uuid == ""

    decisions = []
    req = agent_access.Request("x", "Listing site", "me@example.com", "https://example.com", "Work",
                               "password", "python.exe (PID 1)", lambda _r: None)
    p = AgentPrompt(req, decisions.append)
    p.show()
    assert p.deny.isDefault() and not p.once.isEnabled() and not p.entry.isEnabled()
    assert wait_for(a["app"], p.once.isEnabled, 5)
    p.reject()  # Esc
    assert decisions == ["deny"]


@pytest.mark.parametrize("how", ["once", "entry", "deny", "esc", "x"])
def test_prompt_closes_after_any_answer(app, how):
    """The real prompt must leave the screen after a decision, and decide exactly once.
    The gate closes the prompt from inside decide(), as AgentGate._close_prompt does."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from vaultline.ui.agent_prompt import ARM_MS, show_prompt
    req = agent_access.Request("x", "Listing site", "me@example.com", "https://example.com", "Work",
                               "password", "python.exe (PID 1)", lambda _r: None)
    decisions, holder = [], []

    def decide(d):
        decisions.append(d)
        holder[0].close()

    p = show_prompt(req, decide)
    holder.append(p)
    QTest.qWait(ARM_MS + 200)
    if how == "esc":
        QTest.keyClick(p, Qt.Key_Escape)
    elif how == "x":
        QApplication.sendEvent(p, QCloseEvent())  # the title bar's X
    else:
        QTest.mouseClick(getattr(p, how), Qt.LeftButton)
    QTest.qWait(100)
    assert decisions == [{"esc": "deny", "x": "deny"}.get(how, how)]
    with contextlib.suppress(RuntimeError):  # already deleted (WA_DeleteOnClose) is fine too
        assert not p.isVisible()


def test_prompt_opens_over_the_vaultline_window(app):
    from PySide6.QtWidgets import QWidget
    from vaultline.ui.agent_prompt import AgentPrompt, place_near
    req = agent_access.Request("x", "Listing site", "me@example.com", "https://example.com", "Work",
                               "password", "python.exe (PID 1)", lambda _r: None)
    win = QWidget()
    area = app.primaryScreen().availableGeometry()
    win.setGeometry(area.x() + area.width() // 4, area.y() + area.height() // 4, area.width() // 2, area.height() // 2)
    win.show()
    app.processEvents()
    p = AgentPrompt(req, lambda _d: None)
    place_near(p, win)
    assert (p.frameGeometry().center() - win.frameGeometry().center()).manhattanLength() < 40
    win.hide()  # in the tray: centred on the screen the window was on, fully inside it
    place_near(p, win)
    assert win.screen().availableGeometry().contains(p.frameGeometry())
    p._done = True
    p.close()


def test_folder_pass(agent):
    """'Whole folder' answers later requests for the shared folder without a prompt; everything
    else (other folders, cards, locks, settings) still applies, and each read is logged."""
    a = agent
    a["share"]()
    run, ask, ids, gate, cfg = a["run"], a["ask"], a["ids"], a["gate"], a["win"].config
    aws, gh = ids["AWS Console"][:8], ids["GitHub"][:8]
    assert cfg.agent_folder_pass == 15  # the default
    ask.answers = ["folder"]
    assert run("secret", aws)["out"] == "aws-pass"
    assert ask.asked[-1].folder_pass == "15 min" and ask.asked[-1].shared_folder == "Work"
    left = gate.folder_pass_left()
    assert gate.folder_pass_active() and 14 * 60 < left <= 15 * 60
    assert run("secret", aws, "--field", "username")["out"] == "admin" and len(ask.asked) == 1  # no prompt
    r = run("secret", gh)  # outside the shared folder: still refused, pass or not
    assert r["code"] == 1 and "isn't in the folder shared" in r["err"]
    assert "allowed, folder pass 15 min" in log_text(a) and "allowed (folder pass)" in log_text(a)

    gate.stop()  # the tray's "Stop agent access"
    assert not gate.has_allowances() and "all allowances ended by user" in log_text(a)
    ask.answers = ["deny"]
    assert run("secret", aws)["code"] == 1 and len(ask.asked) == 2

    # GitHub and Netflix moved into the shared folder: they come without asking, until a lock
    from vaultline.vault import MoveEntries
    a["vc"].apply([MoveEntries([ids["GitHub"], ids["Netflix"]], a["work"].uuid)])
    assert wait_for(a["app"], lambda: sum(v.group_uuid == a["work"].uuid for v in a["vc"].snapshot.entries) == 3)
    cfg.agent_folder_pass = -1
    ask.answers = ["folder"]
    assert run("secret", gh)["out"] == "gh-pass" and ask.asked[-1].folder_pass == "until locked"
    assert gate.folder_pass_left() == float("inf")
    n = len(ask.asked)
    assert run("secret", ids["Netflix"][:8])["out"] == "n-pass" and len(ask.asked) == n
    a["share"](on=False)  # sharing changed: refused, and the pass is gone
    assert run("secret", aws)["code"] == 1
    a["share"]()
    ask.answers = ["deny"]
    assert run("secret", aws)["code"] == 1 and len(ask.asked) == n + 1 and not gate.folder_pass_active()
    ask.answers = ["folder"]
    assert run("secret", gh)["out"] == "gh-pass"
    a["vc"].lock()
    a["app"].processEvents()
    assert not gate.has_allowances()
    unlock(a["app"], a["win"], a["vc"])

    # not offered: no folder button, and a stale "folder" answer counts as once
    cfg.agent_folder_pass = 0
    ask.answers = ["folder"]
    assert run("secret", gh)["out"] == "gh-pass" and ask.asked[-1].folder_pass == ""
    assert not gate.has_allowances()
    cfg.agent_folder_pass = 7  # anything unexpected in config.json: not offered either
    ask.answers = ["deny"]
    run("secret", gh)
    assert ask.asked[-1].folder_pass == ""
    for s in SECRETS:
        assert s not in log_text(a)


def test_folder_pass_prompt_button(agent):
    from vaultline.ui.agent_prompt import AgentPrompt
    req = agent_access.Request("id", "Title", "user", "", "Work", "password", "x.exe (PID 1)", lambda r: None,
                               folder_pass="1 hour", shared_folder="Work/Agents")
    got = []
    dlg = AgentPrompt(req, got.append)
    assert dlg.folder.isVisibleTo(dlg) and dlg.folder.text() == "All of Agents, 1 hour" and not dlg.folder.isEnabled()
    assert "Work/Agents" in dlg.folder.toolTip() and "subfolders" in dlg.folder.toolTip()
    assert dlg.folder.objectName() == "Caution" and "Denies automatically" in dlg.countdown.text()
    dlg._arm()
    dlg.folder.click()
    assert got == ["folder"]
    req.folder_pass = ""
    dlg2 = AgentPrompt(req, got.append)
    assert not dlg2.folder.isVisibleTo(dlg2)
    dlg2.close()


def test_cli_bin_and_restore(agent):
    a = agent
    run, ids = a["run"], a["ids"]
    r = run("delete", ids["Netflix"][:8])
    assert r["code"] == 0
    r = run("--json", "bin")
    binned = json.loads(r["out"])
    assert [e["title"] for e in binned] == ["Netflix"] and "n-pass" not in r["out"]  # no secrets
    a["share"]()  # never straight into the folder shared with agents
    r = run("restore", ids["Netflix"][:8], "--to", "Work")
    assert r["code"] == 1 and "shared with agents" in r["err"]
    r = run("restore", ids["Netflix"][:8])
    assert r["code"] == 0 and "restored Netflix" in r["out"]
    assert wait_for(a["app"], lambda: any(v.title == "Netflix" for v in a["vc"].snapshot.entries))
    assert run("restore", "zzzzzz12")["code"] == 1


def test_cli_compare_says_same_or_different_only(agent):
    a = agent
    run, ids, vc = a["run"], a["ids"], a["vc"]
    from vaultline.vault import EntryEdit
    vc.save(EntryEdit(None, "Netflix copy", "family@example.com", "n-pass", "", "", None))
    assert wait_for(a["app"], lambda: any(v.title == "Netflix copy" for v in vc.snapshot.entries))
    copy = next(v.uuid for v in vc.snapshot.entries if v.title == "Netflix copy")
    r = run("--json", "compare", ids["Netflix"][:8], copy[:8])
    out = json.loads(r["out"])
    assert out["same"]["password"] and out["same"]["username"] and not out["same"]["title"]
    assert "n-pass" not in r["out"] and "family@example.com" not in r["out"]  # never a value
    r = run("--json", "compare", ids["Netflix"][:8], ids["GitHub"][:8])
    out = json.loads(r["out"])
    assert not out["same"]["password"] and not out["identical"] and "gh-pass" not in r["out"]


def test_autoopen_entries_never_go_to_agents(agent):
    from vaultline.vault import EntryEdit
    a = agent
    vc, app = a["vc"], a["app"]
    vc.save(EntryEdit(None, "Shared", "", "made-up-shared-pw", r"C:\nowhere\Shared.kdbx", "", None,
                      new_group="AutoOpen"))
    assert wait_for(app, lambda: any(v.title == "Shared" for v in vc.snapshot.entries))
    ao = next(g for g in vc.snapshot.groups if g.name == "AutoOpen")
    entry = next(v for v in vc.snapshot.entries if v.title == "Shared")
    a["share"](ao.uuid)
    r = a["run"]("secret", entry.uuid[:8])
    assert r["code"] == 1 and "never shared with agents" in r["err"] and "made-up" not in r["out"]
    assert a["ask"].asked == []


def test_shared_folder_with_same_id_in_two_databases_is_refused():
    from vaultline.vault import GroupView, Snapshot
    g1 = GroupView("a" * 32, "Agents", ("Agents",), 0, "r")
    g2 = GroupView("a" * 32, "Agents", ("Shared", "Agents"), 1, "t", vault="k")
    one = Snapshot([], [g1], {}, "r")
    two = Snapshot([], [g1, g2], {}, "r")
    assert agent_access.shared_folder_ids(one, "a" * 32) == {"a" * 32}
    assert agent_access.shared_folder_ids(two, "a" * 32) == set()
