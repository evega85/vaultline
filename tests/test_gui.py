"""End-to-end UI checks with a real event loop. Run: python -m pytest -q tests/test_gui.py

Set VAULTLINE_SHOTS=<folder> to also save screenshots of each screen.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pyotp
import pytest
from pykeepass import PyKeePass, create_database
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from vaultline import config as cfgmod
from vaultline import theme
from vaultline.clipboard import SecureClipboard
from vaultline.config import Config
from vaultline.controller import VaultController
from vaultline.ui.main_window import MainWindow

PW = "pw"
SECRET = "JBSWY3DPEHPK3PXP"
SHOTS = os.environ.get("VAULTLINE_SHOTS")


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    a.setStyle("Fusion")
    a.setStyleSheet(theme.STYLESHEET)
    return a


def wait_for(app, cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def shot(widget, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(Path(SHOTS) / f"{name}.png"))


@pytest.fixture
def setup(app, tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    db = tmp_path / "vault.kdbx"
    kp = create_database(str(db), password=PW)
    work = kp.add_group(kp.root_group, "Work")
    kp.add_entry(kp.root_group, "GitHub", "alex-dev", "gh-pass",
                 otp=f"otpauth://totp/GitHub:alex-dev?secret={SECRET}&issuer=GitHub")
    kp.add_entry(kp.root_group, "Google", "someone@example.com", "g-pass")
    kp.add_entry(work, "AWS Console", "admin", "aws-pass",
                 otp=f"otpauth://totp/AWS?secret={SECRET}")
    kp.add_entry(kp.root_group, "Netflix", "family@example.com", "n-pass")
    kp.add_entry(kp.root_group, "Router", "", "r-pass", url="http://192.168.1.1")
    kp.save()

    config = Config(last_database=str(db), auto_download_icons=False)  # no network in tests
    clip = SecureClipboard(20, app)
    vc = VaultController(config, app)
    win = MainWindow(config, vc, clip)
    win.resize(720, 560)
    win.show()
    shot(win, "1-unlock")
    yield app, win, vc, clip, db
    win.allow_close = True
    vc.shutdown()
    win.close()
    clip.wipe()


def unlock(app, win, vc, pw=PW):
    QTest.keyClicks(win.unlock_view.password, pw)
    QTest.keyClick(win.unlock_view.password, Qt.Key_Return)
    wait_for(app, lambda: vc.is_unlocked or win.unlock_view.error.isVisible())


def test_wrong_password_shows_error(setup):
    app, win, vc, *_ = setup
    unlock(app, win, vc, "wrong")
    assert not vc.is_unlocked
    assert "Wrong master password" in win.unlock_view.error.text()
    shot(win, "2-unlock-error")


def test_search_shortcuts_and_clipboard(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    assert vc.is_unlocked and win.stack.currentWidget() is win.vault_view
    view = win.vault_view
    assert app.focusWidget() is view.search or not win.isActiveWindow()
    wait_for(app, lambda: False, 0.6)  # let OTP rings paint
    shot(win, "3-list")

    search = view.search
    QTest.keyClicks(search, "git")
    assert view.current().title == "GitHub"
    shot(win, "4-search")

    QTest.keyClick(search, Qt.Key_Return)
    assert QGuiApplication.clipboard().text() == "gh-pass"
    assert win.toast.isVisible() and "Copied Password" in win.toast._label.text()
    shot(win, "5-toast")

    QTest.keyClick(search, Qt.Key_B, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == "alex-dev"
    QTest.keyClick(search, Qt.Key_T, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == pyotp.TOTP(SECRET).now()

    # Ctrl+C copies the password when nothing is selected in the search box
    search.deselect()
    QTest.keyClick(search, Qt.Key_C, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == "gh-pass"

    # arrows move through filtered results only; Esc clears search
    QTest.keyClick(search, Qt.Key_Escape)
    assert search.text() == ""
    first = view.current().title
    QTest.keyClick(search, Qt.Key_Down)
    assert view.current().title != first
    QTest.keyClick(search, Qt.Key_Up)
    assert view.current().title == first
    # Esc with empty search hides the window
    QTest.keyClick(search, Qt.Key_Escape)
    assert not win.isVisible()
    win.bring_to_front()

    # clipboard wipe only clears our own value
    clip.wipe()
    assert QGuiApplication.clipboard().text() == ""


def test_external_change_reloads_and_keeps_search(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    view = win.vault_view
    QTest.keyClicks(view.search, "net")
    assert view.current().title == "Netflix"

    phone = PyKeePass(str(db), password=PW)
    phone.add_entry(phone.root_group, "Netflix Kids", "kids", "k")
    phone.save()
    assert wait_for(app, lambda: any(v.title == "Netflix Kids" for v in view._views), 20)
    assert view.search.text() == "net"
    assert view.current().title == "Netflix"  # selection kept
    shot(win, "6-reloaded")


def test_add_dialog_saves(setup, monkeypatch):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    from vaultline.ui import entry_dialog

    captured = {}
    orig_exec = entry_dialog.EntryDialog.exec

    def fake_exec(dlg):
        dlg.show()
        QTest.keyClicks(dlg.title, "Dropbox")
        QTest.keyClicks(dlg.username, "me@example.com")
        dlg._generate()
        QTest.keyClicks(dlg.otp, SECRET.lower())
        app.processEvents()
        shot(dlg, "7-dialog")
        captured["pw"] = dlg.password.text()
        dlg._accept()
        return dlg.result()

    monkeypatch.setattr(entry_dialog.EntryDialog, "exec", fake_exec)
    QTest.keyClick(win.vault_view.search, Qt.Key_N, Qt.ControlModifier)
    assert wait_for(app, lambda: any(v.title == "Dropbox" for v in win.vault_view._views), 20)
    e = PyKeePass(str(db), password=PW).find_entries(title="Dropbox", first=True)
    assert e.password == captured["pw"] and len(e.password) == 20
    assert e.otp.startswith("otpauth://totp/Dropbox:me%40example.com?secret=" + SECRET) or \
        e.otp.startswith("otpauth://totp/Dropbox:me@example.com?secret=" + SECRET)
    assert orig_exec


def _click_row(app, view, title, double=False):
    row = next(i for i, v in enumerate(view._views) if v.title == title)
    rect = view.list.visualItemRect(view.list.item(row))
    pos = rect.topLeft() + QPoint(80, rect.height() // 2)  # on the title, not a button
    QTest.mouseClick(view.list.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
    if double:  # a real double-click is press/release then a double-click event
        QTest.mouseDClick(view.list.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
    app.processEvents()


def test_single_click_shows_details(setup):
    app, win, vc, clip, db = setup
    kp = PyKeePass(str(db), password=PW)
    e = kp.find_entries(title="GitHub", first=True)
    e.set_custom_property("Recovery codes", "aaaa-bbbb cccc-dddd", protect=True)
    e.notes = "Personal account"
    kp.save()
    unlock(app, win, vc)
    view = win.vault_view
    _click_row(app, view, "GitHub")
    assert view.panel.isVisible() and view.panel.view.title == "GitHub"
    wait_for(app, lambda: False, 0.4)  # slide-in animation
    texts = [w.text() for w in view.panel.findChildren(QLabel)]
    assert "alex-dev" in texts and "Personal account" in texts and "Recovery codes" in texts
    assert "gh-pass" not in texts  # password masked until revealed
    shot(win, "8-details")

    # field copy buttons go through the same clipboard
    view.panel.copy_text.emit("Recovery codes", "aaaa-bbbb cccc-dddd")
    assert QGuiApplication.clipboard().text() == "aaaa-bbbb cccc-dddd"

    # arrow keys move the panel along with the selection
    QTest.keyClick(view.search, Qt.Key_Down)
    assert view.panel.view.title == view.current().title != "GitHub"
    # Esc closes the panel first, search keeps focus
    QTest.keyClick(view.search, Qt.Key_Escape)
    assert not view.panel.isVisible() and win.isVisible()
    # Ctrl+I toggles it from the keyboard
    QTest.keyClick(view.search, Qt.Key_I, Qt.ControlModifier)
    assert view.panel.isVisible()


def test_folder_filter(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    view = win.vault_view
    work = next(g for g in view.groups if g.name == "Work")
    view.set_folder(work)
    visible = [v.title for i, v in enumerate(view._views) if not view.list.isRowHidden(i)]
    assert visible == ["AWS Console"]
    assert "Work" in view.status.text()
    shot(win, "9-folder")
    QTest.keyClick(view.search, Qt.Key_Escape)  # Esc on empty search leaves the folder first
    assert view.folder_uuid is None and win.isVisible()


def test_card_template_and_double_click_edit(setup, monkeypatch):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    from vaultline.ui import entry_dialog

    def add_card(dlg):
        dlg.show()
        dlg._apply_template("Card")
        app.processEvents()
        assert dlg.sec_card_number.isVisible() and not dlg.sec_username.isVisible()
        assert dlg.sec_password.isVisible() and dlg.sec_code.isVisible() and not dlg.sec_otp.isVisible()
        QTest.keyClicks(dlg.title, "Visa")
        dlg.card_number.setText("4111 1111 1111 1234")
        dlg.card_brand.setText("Visa")
        assert dlg.sec_phone.isVisible() and dlg.sec_url.label.text() == "Website (login page)"
        dlg.card_phone.setText("1-800-222-3333")
        dlg.card_local_phone.setText("604-555-0000")
        dlg.url.setText("https://bank.example/login")
        dlg.password.setText("online-banking-pw")
        dlg.card_code.setText("123")
        QTest.keyClicks(dlg.exp_month, "12")
        assert dlg.exp_year.hasFocus() or not dlg.isActiveWindow()  # jumps to the year box
        dlg.exp_year.setText("29")
        row = dlg.add_custom_row("Branch", "Main St")
        assert row.lock.text() == "Visible"
        dlg.folder.setCurrentIndex(dlg.folder.findData(entry_dialog.NEW_FOLDER))
        dlg.new_folder.setText("Finance/Cards")
        shot(dlg, "10-card-dialog")
        dlg._accept()
        return dlg.result()

    monkeypatch.setattr(entry_dialog.EntryDialog, "exec", add_card)
    QTest.keyClick(win.vault_view.search, Qt.Key_N, Qt.ControlModifier)
    assert wait_for(app, lambda: any(v.title == "Visa" for v in win.vault_view._views), 20)
    e = PyKeePass(str(db), password=PW).find_entries(title="Visa", first=True)
    assert e.get_custom_property("card_number") == "4111 1111 1111 1234" and e.password == "online-banking-pw"
    assert e.get_custom_property("card_code") == "123" and e.is_custom_property_protected("card_code")
    assert e.get_custom_property("card_expMonth") == "12" and e.get_custom_property("card_expYear") == "2029"
    assert not e.username and e.get_custom_property("card_brand") == "Visa"
    assert e.get_custom_property("Phone") == "1-800-222-3333" and e.url == "https://bank.example/login"
    assert e.get_custom_property("Phone (local)") == "604-555-0000"
    assert e.get_custom_property("Branch") == "Main St"
    assert e.group.name == "Cards" and e.group.parentgroup.name == "Finance"

    view = win.vault_view
    visa = next(v for v in view._views if v.title == "Visa")
    row = view._rows[view._views.index(visa)]
    assert "Visa •••• 1234" in row.sub._full and "4111" not in row.sub._full  # number masked in the list
    # search by card number, with or without spaces
    for q in ("1234", "4111111111111234", "4111 1111"):
        view.search.setText(q)
        assert view.current() is not None and view.current().title == "Visa", q
    # Ctrl+T on a card copies the card code; Ctrl+B the number
    QTest.keyClick(view.search, Qt.Key_T, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == "123"
    QTest.keyClick(view.search, Qt.Key_B, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == "4111 1111 1111 1234"
    view.search.clear()
    _click_row(app, view, "Visa")
    wait_for(app, lambda: False, 0.4)
    shot(win, "11-card-details")
    from PySide6.QtWidgets import QToolButton
    exp_copy = next(b for b in view.panel.findChildren(QToolButton) if b.toolTip() == "Copy expiry")
    exp_copy.click()
    assert QGuiApplication.clipboard().text() == "1229"  # copied together as MMYY

    opened = {}

    def capture(dlg):
        opened.update(title=dlg.title.text(), folder=dlg.folder.currentText().strip(),
                      code=dlg.card_code.text(), card=dlg._card,
                      exp=(dlg.exp_month.text(), dlg.exp_year.text()),
                      rows=[r.key.text() for r in dlg._rows])
        return 0

    monkeypatch.setattr(entry_dialog.EntryDialog, "exec", capture)
    _click_row(app, view, "Visa", double=True)
    assert opened == {"title": "Visa", "folder": "Cards", "code": "123", "card": True,
                      "exp": ("12", "29"), "rows": ["Branch"]}


def test_pin_lock_and_lockout(setup):
    from vaultline import pin as pinlib
    app, win, vc, clip, db = setup
    win.config.pin_salt, win.config.pin_hash = pinlib.make("482916")
    win.config.pin_enabled = True
    unlock(app, win, vc)
    win.vault_view.search.setText("net")
    QTest.keyClick(win.vault_view.search, Qt.Key_L, Qt.ControlModifier)
    app.processEvents()
    assert vc.is_pin_locked and win.stack.currentWidget() is win.pin_view
    assert win.vault_view._views == []  # nothing left in the UI
    shot(win, "12-pin")

    QTest.keyClicks(win.pin_view.pin, "482916")
    QTest.keyClick(win.pin_view.pin, Qt.Key_Return)
    assert wait_for(app, lambda: vc.is_unlocked)
    assert win.vault_view.search.text() == "net"  # search kept across the PIN lock

    vc.lock()
    app.processEvents()
    for i in range(3):
        QTest.keyClicks(win.pin_view.pin, "111111")
        QTest.keyClick(win.pin_view.pin, Qt.Key_Return)
        app.processEvents()
        if i < 2:
            assert vc.is_pin_locked and "left" in win.pin_view.error.text()
    assert not vc.is_pin_locked and not vc.is_unlocked
    assert win.stack.currentWidget() is win.unlock_view
    assert "Too many" in win.unlock_view.error.text()


def test_folders_rename_delete_and_entry_delete(setup, monkeypatch):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    from vaultline.ui import small_dialogs
    work = next(g for g in win.vault_view.groups if g.name == "Work")
    monkeypatch.setattr(small_dialogs, "ask_text", lambda *a, **k: "Office")
    win.rename_folder(work.uuid)
    assert wait_for(app, lambda: any(g.name == "Office" for g in win.vault_view.groups))

    monkeypatch.setattr(small_dialogs, "confirm", lambda *a, **k: True)
    office = next(g for g in win.vault_view.groups if g.name == "Office")
    win.delete_folder(office.uuid)
    assert wait_for(app, lambda: not any(g.name == "Office" for g in win.vault_view.groups))
    assert not any(v.title == "AWS Console" for v in win.vault_view._views)  # went with the folder

    google = next(v for v in win.vault_view._views if v.title == "Google")
    win.delete_entry(google)
    assert wait_for(app, lambda: not any(v.title == "Google" for v in win.vault_view._views))
    kp = PyKeePass(str(db), password=PW)
    assert kp.find_entries(title="Google", first=True).group.name == "Recycle Bin"

    # folder popup: fixed height, scrolls
    from vaultline.ui.folder_popup import MAX_LIST_H, FolderPopup
    from vaultline.vault import GroupView
    many = [GroupView(f"{i:032x}", f"Folder {i}", (f"Folder {i}",), 0) for i in range(40)]
    pop = FolderPopup(win, many, {}, 0, None)
    pop.popup_below(win.vault_view.folder_btn)
    app.processEvents()
    assert pop.list.height() == MAX_LIST_H and pop.list.verticalScrollBar().maximum() > 0
    shot(pop, "13-folder-popup")
    pop.close()


def test_change_master_password(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    assert vc.change_master_password("wrong", "new-pass-123") == "The current master password is wrong."
    assert vc.change_master_password(PW, "new-pass-123") is None
    assert wait_for(app, lambda: "changed" in win.toast._label.text().lower())
    # the file opens with the new password, and later syncs still work
    phone = PyKeePass(str(db), password="new-pass-123")
    phone.add_entry(phone.root_group, "After change", "a", "b")
    phone.save()
    assert wait_for(app, lambda: any(v.title == "After change" for v in win.vault_view._views), 20)


def test_cli_end_to_end(setup):
    """Real named pipe: vaultline-cli talking to the running window."""
    import contextlib
    import io
    import json
    import threading

    from vaultline import cli
    from vaultline.cli_server import CliServer
    app, win, vc, clip, db = setup
    server = CliServer(vc, app)
    server.changed_by_cli.connect(lambda s: win.toast.show_message("Command line", s, "info"))  # as in app.py

    def run(*argv):
        out = {}

        def target():
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                out["code"] = cli.main(list(argv))
            out["out"], out["err"] = buf.getvalue(), err.getvalue()
        t = threading.Thread(target=target)
        t.start()
        wait_for(app, lambda: not t.is_alive(), 60)
        return out

    r = run("status")
    assert r["code"] == 0 and '"locked"' in r["out"], r
    unlock(app, win, vc)
    r = run("list")
    assert r["code"] == 1 and "Command-line access is off" in r["err"]
    win.config.cli_enabled = True

    r = run("--json", "list")
    items = json.loads(r["out"])
    assert {i["title"] for i in items} >= {"GitHub", "Google", "AWS Console"}
    for secret in ("gh-pass", "g-pass", "aws-pass", "JBSWY3DPEHPK3PXP"):
        assert secret not in r["out"]  # never leaks secrets
    gh = next(i for i in items if i["title"] == "GitHub")
    assert gh["has_otp"] and gh["has_password"] and "password" not in gh

    r = run("move", gh["id"][:8], "--to", "Dev/Tools")
    assert r["code"] == 0, r
    assert wait_for(app, lambda: any(v.title == "GitHub" and v.group == "Dev / Tools"
                                     for v in win.vault_view._views))
    assert "Command line" in win.toast._label.text()

    ops = [{"op": "update", "id": gh["id"], "title": "GitHub (work)", "add_tags": ["work"]},
           {"op": "folder-rename", "folder": "Dev/Tools", "name": "Tooling"},
           {"op": "delete", "ids": [next(i["id"] for i in items if i["title"] == "Netflix")]}]
    path = db.parent / "ops.json"
    path.write_text(json.dumps(ops), encoding="utf-8")
    r = run("batch", str(path))
    assert r["code"] == 0, r
    kp = PyKeePass(str(db), password=PW)
    e = kp.find_entries(title="GitHub (work)", first=True)
    assert e.group.name == "Tooling" and "work" in e.tags
    assert kp.find_entries(title="Netflix", first=True).group.name == "Recycle Bin"

    r = run("show", "zz")
    assert r["code"] == 1 and "too short" in r["err"]
    server.server.close()


def test_cli_cards_fix_never_shows_values(setup):
    import contextlib
    import io
    import json
    import threading

    from vaultline import cli
    from vaultline.cli_server import CliServer
    app, win, vc, clip, db = setup
    kp = PyKeePass(str(db), password=PW)
    e = kp.add_entry(kp.root_group, "Example Bank CC", "", "")
    for k, v, prot in (("card_number", "5500000000000004", False), ("card_code", "777", True),
                       ("card_expMonth", "3", False), ("card_expYear", "2028", False),
                       ("card_brand", "Mastercard", False), ("card_cardholderName", "A Sample", False),
                       ("Password_c724b", "bank-pw-xyz", True)):
        e._set_string_field(k, v, prot)
    kp.save()
    server = CliServer(vc, app)
    win.config.cli_enabled = True
    unlock(app, win, vc)

    def run(*argv):
        out = {}

        def target():
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                out["code"] = cli.main(list(argv))
            out["out"], out["err"] = buf.getvalue(), err.getvalue()
        t = threading.Thread(target=target)
        t.start()
        wait_for(app, lambda: not t.is_alive(), 60)
        return out

    secrets = ("5500000000000004", "777", "bank-pw-xyz", "03/28", "0328")
    outputs = []
    before = db.read_bytes()
    for argv in (["--json", "cards"], ["cards"], ["cards", "fix", "--dry-run"], ["--json", "list"]):
        r = run(*argv)
        assert r["code"] == 0, r
        outputs.append(r["out"])
    cards = json.loads(outputs[0])
    assert len(cards) == 1 and cards[0]["card"]["last4"] == "0004" and cards[0]["card"]["brand"] == "Mastercard"
    assert "Would change" in outputs[2] and "Password_c724b" in outputs[2]
    assert db.read_bytes() == before  # reading and the dry run wrote nothing

    r = run("--json", "cards", "fix")
    assert r["code"] == 0, r
    outputs.append(r["out"])
    assert json.loads(r["out"])["changed"][0]["title"] == "Example Bank CC"
    for out in outputs:
        for s in secrets:
            assert s not in out, (s, out)
    e = PyKeePass(str(db), password=PW).find_entries(title="Example Bank CC", first=True)
    assert e.password == "bank-pw-xyz" and e.is_custom_property_protected("card_number")

    r = run("cards", "fix")
    assert "Nothing to fix" in r["out"]
    r = run("update", e.uuid.hex[:8], "--brand", "Visa", "--cardholder", "E V")
    assert r["code"] == 0, r
    e = PyKeePass(str(db), password=PW).find_entries(title="Example Bank CC", first=True)
    assert e.get_custom_property("card_brand") == "Visa" and e.get_custom_property("card_cardholderName") == "E V"
    r = run("update", e.uuid.hex[:8], "--username", "4111")
    assert r["code"] == 0  # username is separate from card_number in this layout
    server.server.close()


def test_lock_clears_everything(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    QTest.keyClick(win.vault_view.search, Qt.Key_Return)
    vc.lock("Locked after inactivity.", full=True)
    app.processEvents()
    assert win.stack.currentWidget() is win.unlock_view
    assert win.vault_view._views == []
    assert QGuiApplication.clipboard().text() == ""
    assert "inactivity" in win.unlock_view.error.text()
