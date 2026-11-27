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
    # Esc with nothing left to clear does nothing: it never hides Vaultline
    QTest.keyClick(search, Qt.Key_Escape)
    assert win.isVisible()
    # the x in the search box clears it, shown only while there's text
    assert not view.clear_action.isVisible()
    QTest.keyClicks(search, "net")
    assert view.clear_action.isVisible()
    view.clear_action.trigger()
    assert search.text() == "" and not view.clear_action.isVisible() and search.hasFocus()

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


def test_settings_is_a_page(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    win.vault_view.settings_action.trigger()
    page = win.settings_page
    assert page is not None and win.stack.currentWidget() is page
    shot(win, "settings-page")
    page.clip.setValue(42)
    QTest.keyClick(page, Qt.Key_Escape)  # Esc saves and goes back to the list
    assert win.settings_page is None and win.stack.currentWidget() is win.vault_view
    assert win.config.clipboard_clear_seconds == 42 and clip.clear_seconds == 42

    win.vault_view.settings_action.trigger()
    win.settings_page.clip.setValue(15)
    vc.lock(full=True)  # locking while the page is open saves it and shows the lock screen
    assert wait_for(app, lambda: win.stack.currentWidget() is win.unlock_view)
    assert win.settings_page is None and win.config.clipboard_clear_seconds == 15


def test_window_reopens_where_it_was(setup):
    app, win, vc, clip, db = setup
    area = app.primaryScreen().availableGeometry()
    win.setGeometry(area.x() + 40, area.y() + 30, 740, 450)
    app.processEvents()
    before = win.geometry()
    win.hide_to_tray()  # also what happens before Quit from the tray
    saved = cfgmod.Config.load()
    assert saved.window_geometry
    win2 = MainWindow(saved, VaultController(saved, app), clip)
    try:
        assert win2.geometry() == before
    finally:
        win2.allow_close = True
        win2.vc.shutdown()
        win2.close()


def test_recent_databases_and_switch(setup, tmp_path):
    app, win, vc, clip, db = setup
    other = tmp_path / "other.kdbx"
    create_database(str(other), password="other-pw").save()
    cfg = win.config
    cfg.remember_database(str(other))
    unlock(app, win, vc)
    assert [r["path"] for r in cfg.recent_databases] == [str(db), str(other)]  # newest first, no repeats
    assert cfgmod.Config.load().recent_databases == cfg.recent_databases     # saved; paths only

    from vaultline.ui.unlock_view import recent_choices
    choices = recent_choices(cfg.recent_databases, vc.path)
    assert [c["path"] for c in choices] == [str(other)]  # the open one isn't offered again

    win.open_settings()  # Settings > Open another database > other.kdbx
    win.settings_page.open_database.emit(str(other), "")
    app.processEvents()
    assert win.settings_page is None and not vc.is_unlocked and not vc.is_pin_locked
    assert win.stack.currentWidget() is win.unlock_view
    unlock(app, win, vc, pw="other-pw")
    assert vc.is_unlocked and Path(vc.path) == other
    assert cfg.recent_databases[0]["path"] == str(other)

    win.open_settings()  # Settings > Close database: full lock
    win.settings_page.close_database.emit()
    app.processEvents()
    assert not vc.is_unlocked and not vc.is_pin_locked and win.stack.currentWidget() is win.unlock_view


def test_recent_list_is_capped_and_skips_missing(tmp_path):
    cfg = Config()
    for i in range(10):
        cfg.remember_database(str(tmp_path / f"v{i}.kdbx"))
    assert len(cfg.recent_databases) == Config.RECENT_MAX
    assert cfg.recent_databases[0]["path"].endswith("v9.kdbx")
    from vaultline.ui.unlock_view import UnlockView
    view = UnlockView()
    view.set_recent(cfg.recent_databases)
    assert view.recent_choices() == []  # none of those files exist


def test_icon_style(app):
    from PySide6.QtGui import QColor, QPixmap
    from vaultline.ui.widgets import icon_style

    def icon(fill, dot):
        pm = QPixmap(32, 32)
        pm.fill(QColor(fill))
        if dot is not None:
            from PySide6.QtGui import QPainter
            p = QPainter(pm)
            p.fillRect(8, 8, 16, 16, QColor(dot))
            p.end()
        return pm
    assert icon_style(icon("transparent", "#f5a623")) == "plain"  # bright logo, no background
    assert icon_style(icon("transparent", "#111111")) == "tile"   # dark logo: keeps a light tile
    assert icon_style(icon("#ffffff", "#1877f2")) == "opaque"     # brings its own square


def test_settings_numbers_and_tray_follow_settings(setup):
    app, win, vc, clip, db = setup
    from vaultline.app import Tray
    unlock(app, win, vc)
    tray = Tray(win, vc, win.config)
    win.open_settings()
    page = win.settings_page
    assert page.minutes.suffix() == "" and page.clip.suffix() == ""  # the unit is in the label
    assert "(minutes)" in page.auto.text()
    page.auto.setChecked(True)
    page.minutes.setValue(20)
    page.clip.setValue(30)
    win.close_settings()
    assert win.config.auto_lock_minutes == 20 and win.config.clipboard_clear_seconds == 30
    tray._refresh()  # runs as the tray menu opens
    assert tray.auto_action.text() == "Auto-lock after 20 minutes" and tray.auto_action.isChecked()
    tray.icon.hide()


def test_title_bar_holds_the_vault_controls(setup):
    app, win, vc, clip, db = setup
    tb = win.titlebar
    assert not tb.title.isVisible() and not win.vault_view.top_bar.isVisible()  # lock screen: its own big logo, bar stays empty
    unlock(app, win, vc)
    assert win.vault_view.top_bar.parentWidget() is tb and win.vault_view.top_bar.isVisible()
    assert not tb.title.isVisible()

    tb.set_window_buttons(True)  # off Windows they're hidden (the system frame has its own)
    app.processEvents()

    def hit(widget, x=8, y=8):
        return tb.hit(widget.mapTo(tb, QPoint(x, y)))

    # what Windows is told: drag areas, the maximise button (snap layouts), and normal controls
    assert hit(tb.logo) == "caption" and hit(tb.drag_space) == "caption"
    assert hit(tb.max_btn) == "max"
    for w in (win.vault_view.search, win.vault_view.add_btn, win.vault_view.folder_btn,
              win.vault_view.lock_btn, tb.min_btn, tb.close_btn):
        assert hit(w) is None, w
    assert tb.hit(QPoint(-1, 5)) is None and tb.hit(QPoint(5, tb.height() + 2)) is None

    win.open_settings()  # other screens show the app name again
    assert tb.title.isVisible() and not win.vault_view.top_bar.isVisible()
    win.close_settings()
    assert win.vault_view.top_bar.isVisible()

    tb.set_maximized(True)
    assert tb.max_btn.kind == "restore"
    vc.lock(full=True)
    assert wait_for(app, lambda: win.stack.currentWidget() is win.unlock_view)
    assert not tb.title.isVisible()  # lock screen: its own big logo, no name in the bar


def _visible(view):
    return [v.title for i, v in enumerate(view._views) if not view.list.isRowHidden(i)]


def test_compact_lists_nothing_until_asked(setup, monkeypatch):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    view = win.vault_view
    assert not win.compact and len(_visible(view)) == 5
    win.resize(600, 480)  # narrow window = compact view
    app.processEvents()
    assert win.compact and view.compact and win.titlebar.compact
    assert not view.footer.isVisible() and not view.menu_btn.isVisible() and view.pin_btn.isVisible()
    assert not view.list.isVisible() and not view.empty.isVisible() and view.current() is None
    assert win.height() == win.titlebar.height() + 8  # idle palette: just the bar
    QTest.keyClick(view.search, Qt.Key_Return)  # nothing selected: Enter copies nothing
    assert QGuiApplication.clipboard().text() != "gh-pass"
    shot(win, "compact-idle")

    QTest.keyClicks(view.search, "git")  # results as you type
    assert _visible(view) == ["GitHub"] and view.list.isVisible()
    assert win.height() == win.titlebar.height() + 48 + 2 + 8  # grows with the results
    QTest.keyClick(view.search, Qt.Key_Return)  # shortcuts still work
    assert QGuiApplication.clipboard().text() == "gh-pass"
    shot(win, "compact-search")
    view.search.clear()
    assert _visible(view) == []

    view.set_folder(next(g for g in view.groups if g.name == "Work"))  # or open a folder
    assert _visible(view) == ["AWS Console"]
    view.set_folder(None)
    assert _visible(view) == []

    opened = []
    from vaultline.ui.entry_dialog import EntryDialog
    monkeypatch.setattr(EntryDialog, "exec", lambda dlg: opened.append(dlg.width()) or 0)
    view.add_btn.click()  # Add opens the form, sized for the compact window
    assert opened and opened[0] <= max(win.frameGeometry().width(), 360)


def test_compact_details_on_click(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    view = win.vault_view
    win.resize(600, 480)
    app.processEvents()
    QTest.keyClicks(view.search, "o")
    _click_row(app, view, "Google")
    assert view.panel.isVisible() and view.panel.view.title == "Google"
    wait_for(app, lambda: False, 0.3)
    assert view.panel.width() >= view.width() - 20  # over the results, full width
    view.panel.copy_text.emit("Username", "someone@example.com")
    assert QGuiApplication.clipboard().text() == "someone@example.com"
    QTest.keyClick(view.search, Qt.Key_B, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == "someone@example.com"
    QTest.keyClick(view.search, Qt.Key_Escape)
    assert not view.panel.isVisible()


def test_compact_pin_is_remembered(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    view = win.vault_view
    assert view.pin_btn.isVisible() and not win.on_top  # in the full window too; not pinned at first
    view.pin_btn.click()
    app.processEvents()
    assert win.on_top and cfgmod.Config.load().compact_pinned
    win.resize(600, 480)  # stays pinned when it becomes the palette
    app.processEvents()
    assert win.compact and view.pin_btn.isVisible() and win.on_top

    win.hide_to_tray()  # the size and the pin come back next time
    saved = cfgmod.Config.load()
    win2 = MainWindow(saved, VaultController(saved, app), clip)
    try:
        win2.show()
        app.processEvents()
        assert win2.compact and win2.vault_view.pin_btn.isChecked() and win2.on_top
    finally:
        win2.allow_close = True
        win2.vc.shutdown()
        win2.close()


def test_switching_size_keeps_search_and_selection(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    view = win.vault_view
    QTest.keyClicks(view.search, "e")
    QTest.keyClick(view.search, Qt.Key_Down)
    chosen = view.current().title
    win.resize(600, 480)
    app.processEvents()
    assert win.compact and view.search.text() == "e" and view.current().title == chosen
    win.resize(800, 560)
    app.processEvents()
    assert not win.compact and view.search.text() == "e" and view.current().title == chosen

    view.search.clear()  # empty search: compact lists nothing, normal brings the selection back
    QTest.keyClick(view.search, Qt.Key_Down)
    chosen = view.current().title
    win.resize(600, 480)
    app.processEvents()
    assert view.current() is None
    win.resize(800, 560)
    app.processEvents()
    assert view.current().title == chosen and len(_visible(view)) == 5



def test_palette_height_help_and_card_enter(setup):
    app, win, vc, clip, db = setup
    kp = PyKeePass(str(db), password=PW)
    card = kp.add_entry(kp.root_group, "Visa test", "", "")
    card.set_custom_property("card_number", "4111111111111111", protect=True)
    kp.save()
    unlock(app, win, vc)
    view = win.vault_view
    win.resize(800, 520)
    app.processEvents()
    win.resize(600, 520)  # into the palette: the full view's height is remembered
    app.processEvents()
    assert win.compact and win.config.full_height == 520
    QTest.keyClicks(view.search, "e")
    shown = len(_visible(view))
    assert win.height() == min(480, win.titlebar.height() + shown * 50 + 8)
    view.open_details()
    app.processEvents()
    assert win.height() == 480  # details get the full (capped) height
    QTest.keyClick(view.search, Qt.Key_Escape)
    app.processEvents()
    assert win.height() < 480
    win.resize(800, win.height())  # widened again: back to the full view at its own height
    app.processEvents()
    assert not win.compact and win.height() == 520

    view.search.clear()
    QTest.keyClicks(view.search, "visa")
    QTest.keyClick(view.search, Qt.Key_Return)  # a card without a password: Enter copies the number
    assert QGuiApplication.clipboard().text() == "4111111111111111"

    QTest.keyClick(view.search, Qt.Key_F1)
    assert win.help_page is not None and win.stack.currentWidget() is win.help_page
    texts = " ".join(w.text() for w in win.help_page.findChildren(QLabel))
    assert "KeePassium" in texts and "KeePassDX" in texts and "Database > Import" in texts
    QTest.keyClick(win.help_page, Qt.Key_Escape)
    assert win.help_page is None and win.stack.currentWidget() is view
    view.help_action.trigger()
    vc.lock(full=True)  # a lock closes it
    assert wait_for(app, lambda: win.stack.currentWidget() is win.unlock_view) and win.help_page is None


def test_view_button_ctrl_m_and_edges(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    view, tb = win.vault_view, win.titlebar
    win.resize(740, 500)
    app.processEvents()
    assert not win.compact and tb.mode_btn.isVisible() and tb.mode_btn.kind == "collapse"
    tb.mode_btn.click()  # full -> palette, at the palette's own width
    app.processEvents()
    assert win.compact and win.width() == 600 and tb.mode_btn.kind == "expand"
    assert win.config.full_width == 740 and win.config.full_height == 500
    QTest.keyClicks(view.search, "goo")
    QTest.keyClick(view.search, Qt.Key_M, Qt.ControlModifier)  # Ctrl+M back to the full window
    app.processEvents()
    assert not win.compact and (win.width(), win.height()) == (740, 500) and view.search.text() == "goo"
    QTest.keyClick(view.search, Qt.Key_M, Qt.ControlModifier)
    app.processEvents()
    assert win.compact

    # empty strip under the results moves the window; the results themselves don't
    assert win._hit(QPoint(300, win.height() - 3)) == "caption"
    row = view.list.visualItemRect(view.list.item(next(i for i, v in enumerate(view._views) if v.title == "Google")))
    assert win._hit(view.list.viewport().mapTo(win, row.center())) is None

    # bottom edge: pull the palette down to open the full window, push it back up to return
    win._size_move_started()
    start_h = win.compact_height()
    assert win._sizing_height(600, start_h + 20, 6) == (600, start_h)      # a little: stays
    assert win._sizing_height(600, start_h + 100, 6) == (740, 420)         # far enough: full window
    win.resize(740, 500)
    app.processEvents()
    win._size_move_started()
    assert win._sizing_height(740, 400, 6) == (740, 420)                   # not past the limit
    assert win._sizing_height(740, 300, 6) == (600, win.compact_height())  # far enough: palette
    assert tb.mode_btn.isVisible()
    vc.lock(full=True)
    assert wait_for(app, lambda: win.stack.currentWidget() is win.unlock_view)
    assert not tb.mode_btn.isVisible()  # only the vault list has two views


def test_create_a_new_database(setup, tmp_path):
    app, win, vc, clip, db = setup
    uv = win.unlock_view
    uv._path = ""
    uv._refresh()
    assert uv.welcome.isVisibleTo(uv) and not uv.password.isVisibleTo(uv)  # first visit: create or open
    uv.create_database()
    dlg = uv._new_dialog
    target = tmp_path / "Fresh.kdbx"
    dlg.path.setText(str(target))
    dlg.password.setText("short")
    dlg.confirm.setText("short")
    dlg._accept()
    assert dlg.isVisible() and "8 characters" in dlg.error.text()
    dlg.password.setText("a long secret 1")
    dlg.confirm.setText("a long secret 2")
    dlg._accept()
    assert dlg.isVisible() and "match" in dlg.error.text()
    dlg.confirm.setText("a long secret 1")
    dlg._accept()
    assert wait_for(app, lambda: vc.is_unlocked) and vc.path == str(target)
    assert win.stack.currentWidget() is win.vault_view and vc.snapshot.entries == []
    assert win.config.last_database == str(target)
    vc.lock(full=True)  # next visit: the usual password screen for that database
    assert wait_for(app, lambda: win.stack.currentWidget() is win.unlock_view)
    assert uv.password.isVisibleTo(uv) and not uv.welcome.isVisibleTo(uv) and uv._path == str(target)
    from pykeepass import PyKeePass
    kp = PyKeePass(str(target), "a long secret 1")  # a normal KDBX 4 file
    assert kp.version == (4, 0) and kp.kdf_algorithm == "argon2"


def test_create_never_overwrites(tmp_path):
    from vaultline.vault import VaultError, create_vault
    target = tmp_path / "Taken.kdbx"
    target.write_bytes(b"not mine")
    with pytest.raises(VaultError):
        create_vault(target, "a long secret 1")
    assert target.read_bytes() == b"not mine"


def test_username_suggestions(setup, monkeypatch):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    from vaultline.ui.entry_dialog import EntryDialog, known_usernames
    names = known_usernames(vc.snapshot.entries)
    assert "someone@example.com" in names
    assert len({n.casefold() for n in names}) == len(names)  # no duplicates
    assert not any(v.is_card and v.username in names for v in vc.snapshot.entries if v.username)

    seen = []

    def typed(dlg):
        dlg.show()
        dlg.username.setFocus()
        QTest.keyClicks(dlg.username, "EXAMPLE")  # matches anywhere, any case
        app.processEvents()
        comp = dlg.username.completer()
        seen.extend(comp.completionModel().index(i, 0).data() for i in range(comp.completionCount()))
        comp.popup().hide()
        return 0

    monkeypatch.setattr(EntryDialog, "exec", typed)
    win.vault_view.add_btn.click()
    assert "someone@example.com" in seen


def test_username_suggestion_hide_and_return(setup, monkeypatch):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    from vaultline.ui.entry_dialog import EntryDialog, _SuggestionDelegate

    def shown(dlg):
        comp = dlg.username.completer()
        return [comp.completionModel().index(i, 0).data() for i in range(comp.completionCount())]

    def hide_with_x_and_delete(dlg):
        dlg.show()
        dlg.username.setFocus()
        QTest.keyClicks(dlg.username, "example")
        app.processEvents()
        popup = dlg.username.completer().popup()
        assert popup.isVisible() and set(shown(dlg)) == {"someone@example.com", "family@example.com"}
        # click the × on the first row
        idx = popup.model().index(0, 0)
        first = idx.data()
        x = _SuggestionDelegate.x_rect(popup.visualRect(idx)).center()
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        QApplication.sendEvent(popup.viewport(), QMouseEvent(QEvent.MouseMove, QPointF(x), QPointF(x),
                                                             Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        assert popup.itemDelegate().hot_row == 0  # the × goes red under the mouse
        QTest.mouseClick(popup.viewport(), Qt.LeftButton, pos=x)
        app.processEvents()
        assert first not in shown(dlg) and popup.isVisible()
        assert dlg.username.text() == "example"  # the × didn't pick it
        # Delete on the highlighted one
        popup.setCurrentIndex(popup.model().index(0, 0))
        QTest.keyClick(popup, Qt.Key_Delete)
        app.processEvents()
        assert shown(dlg) == [] and not popup.isVisible()
        return 0

    monkeypatch.setattr(EntryDialog, "exec", hide_with_x_and_delete)
    win.vault_view.add_btn.click()
    cfg = win.config
    assert cfg.username_hidden("someone@example.com") and cfg.username_hidden("Family@Example.com")
    assert "example.com" not in cfgmod.CONFIG_FILE.read_text(encoding="utf-8")  # hashes only

    seen = []

    def reopen_and_save(dlg):
        seen.extend(dlg.username.completer().model().stringList())
        dlg.title.setText("Shop")
        dlg.username.setText("family@example.com")  # used again
        dlg.password.setText("x")
        dlg._accept()
        return 1

    monkeypatch.setattr(EntryDialog, "exec", reopen_and_save)
    win.vault_view.add_btn.click()
    assert "someone@example.com" not in seen and "family@example.com" not in seen
    assert not cfg.username_hidden("family@example.com") and cfg.username_hidden("someone@example.com")


def test_close_button_choices(setup, monkeypatch):
    app, win, vc, clip, db = setup
    from vaultline.ui import small_dialogs
    quits = []
    win.quit_requested.connect(lambda: quits.append(1))
    win.can_quit = True  # as the tray sets it
    asked = []

    def answer(result):
        def fake(parent):
            asked.append(1)
            return result
        monkeypatch.setattr(small_dialogs, "ask_close", fake)

    assert win.config.close_action == "ask"  # the first X asks
    answer(None)  # Cancel: nothing happens
    win.close()
    app.processEvents()
    assert asked and win.isVisible() and not quits

    answer(("tray", False))  # to the tray, not remembered
    win.close()
    app.processEvents()
    assert not win.isVisible() and win.config.close_action == "ask" and not quits

    win.show()
    answer(("quit", True))  # quit, remembered
    win.close()
    app.processEvents()
    assert quits == [1] and cfgmod.Config.load().close_action == "quit"

    asked.clear()
    win.config.close_action = "tray"  # set in Settings: no question
    win.show()
    win.close()
    app.processEvents()
    assert not asked and not win.isVisible() and quits == [1]


def test_windows_settings_and_background_start(setup, monkeypatch):
    app, win, vc, clip, db = setup
    from vaultline import autostart
    calls = []
    monkeypatch.setattr(autostart, "supported", lambda: True)
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(autostart, "set_enabled", lambda on: calls.append(on) or True)
    unlock(app, win, vc)
    win.vault_view.settings_action.trigger()
    page = win.settings_page
    assert page.autostart.isEnabled() and not page.autostart.isChecked()
    page.autostart.setChecked(True)
    page.close_action.setCurrentIndex(page.close_action.findData("quit"))
    QTest.keyClick(page, Qt.Key_Escape)
    assert calls == [True] and win.config.close_action == "quit"

    win.vault_view.settings_action.trigger()  # unchanged: the registry isn't touched again
    monkeypatch.setattr(autostart, "is_enabled", lambda: True)
    QTest.keyClick(win.settings_page, Qt.Key_Escape)
    assert calls == [True]

    win.hide()
    vc.lock(full=True)
    app.processEvents()
    win.start(background=True)  # a Windows sign-in start: stays in the tray
    app.processEvents()
    assert not win.isVisible()
    win.start()
    assert win.isVisible() and win.stack.currentWidget() is win.unlock_view


def test_autostart_command_points_at_exe(monkeypatch):
    from vaultline import autostart
    monkeypatch.setattr(autostart.sys, "executable", r"C:\Apps\Vaultline\Vaultline.exe")
    assert autostart.command() == r'"C:\Apps\Vaultline\Vaultline.exe" --background'
    monkeypatch.setattr(autostart.sys, "frozen", False, raising=False)
    assert not autostart.supported() and not autostart.set_enabled(True)  # never from source
