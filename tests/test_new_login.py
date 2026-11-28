"""New login for the window you came from: saved first, then typed. Made-up data only;
Windows calls are faked (see test_autotype.fake_os)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from vaultline import hotkey, identity as idlib, sendkeys
from vaultline.autotype import is_browser_title, is_signup_title, site_from_title
from vaultline.vault import CustomField, EntryEdit

from test_autotype import fake_os  # noqa: F401  (fixture)
from test_gui import PW, app, setup, unlock, wait_for  # noqa: F401  (fixtures)

SIGNUP = "Sign up · Example Shop - Google Chrome"


@pytest.mark.parametrize("title,site,signup", [
    ("Sign up · GitHub - Google Chrome", "GitHub", True),
    ("Create your account - Example Bank — Mozilla Firefox", "Example Bank", True),
    ("GitHub: Let's build from here - Helium", "GitHub", False),
    ("Example | Register", "Example", True),
    ("Registered users - Example", "Example", False),
    ("Créer un compte - Exemple - Microsoft Edge", "Exemple", True),
    ("", "", False),
])
def test_site_name_and_signup_words(title, site, signup):
    assert site_from_title(title) == site and is_signup_title(title) == signup


@pytest.mark.parametrize("address,site", [
    ("facebook.com/r.php?code=123", "https://facebook.com"), ("https://www.bestbuy.ca/en-ca/account", "https://www.bestbuy.ca"),
    ("localhost:8765/x.html?code=1", "http://localhost:8765"), ("192.168.1.1/admin", "http://192.168.1.1"),
    ("how to sign up", ""), ("file:///C:/x.html", ""), ("chrome://settings", ""), ("", "")])
def test_site_url_from_the_address_bar(address, site):
    from vaultline.autotype import site_url
    assert site_url(address) == site


def test_browser_title():
    assert is_browser_title("Inbox - Gmail - Google Chrome") and is_browser_title("Docs - Helium")
    assert not is_browser_title("Downloads") and not is_browser_title("Untitled - Notepad")


# A pretend page: (kind, label) per Tab stop, in order, like Facebook's sign-up form.
FACEBOOK = [("text", "First name"), ("text", "Last name"), ("", "Birthday help"), ("choice", "Month"),
            ("choice", "Day"), ("choice", "Year"), ("", "Gender help"), ("choice", "Select your gender"),
            ("text", "Mobile number or email"),
            ("", "Learn why we ask"), ("password", "Password"), ("", "Submit")]
CONFIRM = [("text", "Email address"), ("password", "Password"), ("password", "Confirm password"), ("", "Sign up")]


@pytest.fixture
def signup(setup, fake_os, monkeypatch):
    """Unlocked, with a profile, the sign-up page in front, and the dialog answered by pressing Save."""
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    vc.save(EntryEdit(None, "Personal", "", "", "", "", None,
                      custom=[CustomField(idlib.FIRST, "Alex", False), CustomField(idlib.LAST, "Tester", False),
                              CustomField(idlib.EMAIL, "alex@example.com", False),
                              CustomField(idlib.BIRTH, "1990-01-05", True),
                              CustomField(idlib.GENDER, "male", False)]))
    assert wait_for(app, lambda: any(v.is_identity for v in vc.snapshot.entries))
    flow = win.autotype
    monkeypatch.setattr(flow.watcher, "target", lambda: 500)
    fake_os.title = SIGNUP
    st = type("St", (), {})()
    st.dialogs, st.saved_when_typed = [], []
    st.page, st.at = list(FACEBOOK), 0   # the boxes, and which one has the focus
    st.cold = 0                          # how many first questions get "can't tell" (accessibility warming up)

    def run_dialog(dlg):
        st.dialogs.append(dlg)
        dlg._accept()
        return QDialog.Accepted if dlg.result_edit else QDialog.Rejected
    monkeypatch.setattr(win, "_run_dialog", run_dialog)

    def info(h, label=False):
        if st.cold:
            st.cold -= 1
            return "", ""
        kind, name = st.page[st.at] if st.at < len(st.page) else ("", "")
        return kind, (name if label else "")
    monkeypatch.setattr(sendkeys, "focused_info", info)
    monkeypatch.setattr(sendkeys, "focused_box", lambda h: info(h)[0])
    st.address = "www.example.com/signup?invite=abc123"  # what the browser's address bar shows
    monkeypatch.setattr(sendkeys, "browser_address", lambda h: st.address)
    for name in ("SETTLE_SECONDS", "NEW_SETTLE_SECONDS", "NEW_STEP_SECONDS"):
        monkeypatch.setattr(f"vaultline.ui.autotype_flow.{name}", 0)

    def fake_type(steps, target, cancelled=lambda: False, **kw):
        st.saved_when_typed.append(any(v.title == "Example Shop" for v in vc.snapshot.entries))
        fake_os.typed.append((target, [(s.kind, s.value or s.vk) for s in steps]))
        st.at += sum(1 for s in steps if s.kind == "key" and s.vk == 9)
        return sendkeys.Result(True)
    monkeypatch.setattr(sendkeys, "type_steps", fake_type)
    return app, win, vc, flow, st


def _typed(fake_os):
    return [step for _target, steps in fake_os.typed for step in steps if step != ("key", 9)]


def _finished(app, flow):
    return wait_for(app, lambda: flow.note.isVisible() and not flow.busy)


def test_facebook_style_form_by_labels(signup, fake_os):
    app, win, vc, flow, st = signup
    st.cold = 1  # the browser's first answer is "can't tell"
    flow.new_login()
    assert _finished(app, flow)
    dlg = st.dialogs[0]
    assert dlg.title.text() == "Example Shop" and dlg.username.text() == "alex@example.com"
    pw = dlg.password.text()
    assert len(pw) == 20
    # names, the birthday and gender drop-downs (typing picks the option), the email, the password;
    # help buttons and links skipped; never Enter
    assert _typed(fake_os) == [("text", "Alex"), ("text", "Tester"), ("text", "Jan"), ("text", "5"),
                               ("text", "1990"), ("text", "Male"), ("text", "alex@example.com"), ("text", pw)]
    assert st.at == 11  # stopped on Submit, without pressing it
    assert flow.note.title.text() == ("Typed first name, last name, birth month, birth day, birth year, gender, "
                                      "your email, the password")
    assert all(st.saved_when_typed)  # the entry was on disk before the first key
    entry = next(v for v in vc.snapshot.entries if v.title == "Example Shop")
    # the website: only the site part of the address bar, never its path or codes
    assert dlg.url.text() == "https://www.example.com"
    assert entry.username == "alex@example.com" and entry.password == pw and entry.url == "https://www.example.com"
    from vaultline.ui import autotype_flow
    log = autotype_flow.DIAG_LOG.read_text(encoding="utf-8")
    assert "box 0: text first" in log and "box 10: password" in log
    assert "alex@example.com" not in log and "Alex" not in log and "Example" not in log and pw not in log
    assert "1990" not in log and "Jan" not in log


def test_starting_in_the_email_box_with_a_confirm_box(signup, fake_os):
    app, win, vc, flow, st = signup
    st.page = list(CONFIRM)
    flow.new_login()
    assert _finished(app, flow)
    pw = st.dialogs[0].password.text()
    assert _typed(fake_os) == [("text", "alex@example.com"), ("text", pw), ("text", pw)]
    assert flow.note.title.text() == "Typed your email, the password, the password again"


def test_never_types_the_password_into_a_box_it_cant_tell(signup, fake_os):
    app, win, vc, flow, st = signup
    st.page = [("text", "Email"), ("", "Password (not reported)"), ("", "x")]
    flow.new_login()
    assert _finished(app, flow)
    assert _typed(fake_os) == [("text", "alex@example.com")]
    assert flow.note.title.text() == "Typed your email" and "No password box" in flow.note.body.text()
    assert any(v.title == "Example Shop" for v in vc.snapshot.entries)  # saved all the same


def test_a_password_label_on_a_text_box_gets_no_password(signup, fake_os):
    """Pages choose their labels: the password goes only where Windows says the box hides what's typed."""
    app, win, vc, flow, st = signup
    st.page = [("text", "Password"), ("text", "Company"), ("", "Submit")]
    flow.new_login()
    assert _finished(app, flow)
    assert _typed(fake_os) == []


def test_nothing_typed_when_the_save_fails(signup, fake_os, monkeypatch):
    app, win, vc, flow, st = signup
    monkeypatch.setattr(vc, "apply", lambda changes, token=None: (vc.applied.emit(token, False, "disk full", None),
                                                                  True)[1])
    flow.new_login()
    wait_for(app, lambda: False, 0.3)
    assert fake_os.typed == [] and flow._new is None


def test_lock_while_saving_types_nothing(signup, fake_os, monkeypatch):
    app, win, vc, flow, st = signup
    held = []
    monkeypatch.setattr(vc, "apply", lambda changes, token=None: held.append(token) or True)
    flow.new_login()
    assert held and flow._new is not None
    vc.lock()
    app.processEvents()
    vc.applied.emit(held[0], True, "", None)  # a late "saved" after the lock
    wait_for(app, lambda: False, 0.2)
    assert fake_os.typed == [] and flow._new is None


def test_cancelled_dialog_saves_and_types_nothing(signup, fake_os, monkeypatch):
    app, win, vc, flow, st = signup
    monkeypatch.setattr(win, "_run_dialog", lambda dlg: QDialog.Rejected)
    n = len(vc.snapshot.entries)
    flow.new_login()
    wait_for(app, lambda: False, 0.3)
    assert fake_os.typed == [] and len(vc.snapshot.entries) == n


def test_refuses_admin_and_missing_windows(signup, fake_os, monkeypatch):
    app, win, vc, flow, st = signup
    fake_os.admin = True
    flow.new_login()
    assert st.dialogs == [] and flow.note.isVisible()
    fake_os.admin = False
    monkeypatch.setattr(flow.watcher, "target", lambda: 0)
    flow.new_login()
    assert st.dialogs == []


def test_quick_box_link_and_picker_button(signup, fake_os):
    app, win, vc, flow, st = signup
    view = win.vault_view
    win.resize(600, 480)
    app.processEvents()
    assert view.compact
    view.set_fill_target(SIGNUP)
    assert view.new_login_btn.isVisible() and "Example Shop" in view.new_login_btn.text()
    assert view._root.indexOf(view.new_login_btn) == 0  # sign-up page: first
    view.set_fill_target("Inbox - Gmail - Google Chrome")
    assert view.new_login_btn.isVisible() and view._root.indexOf(view.new_login_btn) > view._root.indexOf(view.list)
    view.set_fill_target("Downloads")  # not a web page
    assert not view.new_login_btn.isVisible()
    view.set_fill_target(SIGNUP)
    view.new_login_btn.click()
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)

    # the Auto-Type picker offers it too, first on a sign-up page, for the window the shortcut came from
    fake_os.typed.clear()
    st.dialogs.clear()
    flow.hotkey_pressed()
    assert flow.picker.isVisible() and flow.picker._lay.indexOf(flow.picker.new_btn) == 1
    assert all(not v.is_identity for v in flow.picker._all)  # profiles have no login to pick
    st.at = 0
    flow.picker.new_btn.click()
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    assert st.dialogs and not flow.picker.isVisible()


def test_settings_and_shortcut_clash(setup):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    c = win.config
    assert not c.newlogin_hotkey_enabled and c.newlogin_hotkey == hotkey.NEWLOGIN_DEFAULT
    c.autotype_hotkey_enabled, c.autotype_hotkey = True, "Ctrl+Alt+N"
    assert win._shortcut_clash(hotkey.NEWLOGIN_ID, "Ctrl+Alt+N") == "Auto-Type"
    assert win._shortcut_clash(hotkey.NEWLOGIN_ID, "Ctrl+Alt+J") == ""
    win.open_settings()
    page = win.settings_page
    page.pw_length.setValue(32)
    page.close_action.setCurrentIndex(0)
    win.close_settings()
    assert c.pw_length == 32 and c.default_profile_uuid == "" and c.newlogin_folder_uuid == ""


def test_generator_settings_and_wheel(setup):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from vaultline import passgen
    from vaultline.ui.widgets import WheelGuard
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    guard = WheelGuard()
    QApplication.instance().installEventFilter(guard)
    try:
        win.open_settings()
        page = win.settings_page
        # the wheel over a number box or drop-down doesn't change it
        before, close_before = page.pw_length.value(), page.close_action.currentIndex()
        for w in (page.pw_length, page.close_action):
            ev = QWheelEvent(QPointF(5, 5), QPointF(w.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                             Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            QApplication.sendEvent(w, ev)
        assert page.pw_length.value() == before and page.close_action.currentIndex() == close_before
        # at least one kind of character stays ticked
        for cb in page.pw_sets:
            cb.setChecked(False)
        assert sum(cb.isChecked() for cb in page.pw_sets) == 1
        for cb in page.pw_sets:
            cb.setChecked(cb.property("attr") in ("pw_lower", "pw_digits"))
        page.pw_length.setValue(14)
        win.close_settings()
        c = win.config
        assert c.pw_length == 14 and not c.pw_upper and not c.pw_symbols and c.pw_lower and c.pw_digits
        pw = passgen.from_config(c)
        assert len(pw) == 14 and all(ch in passgen.LOWER + passgen.DIGITS for ch in pw)
    finally:
        QApplication.instance().removeEventFilter(guard)


def test_gear_beside_generate(setup, monkeypatch):
    from vaultline import passgen
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    c = win.config
    c.pw_length, c.pw_upper, c.pw_lower, c.pw_digits, c.pw_symbols = 20, True, True, True, True
    saved = []
    monkeypatch.setattr(type(c), "save", lambda self: saved.append(1))
    win.open_editor(None)
    dlg = win._dialog
    assert dlg is not None and dlg.gen_options is not None
    dlg.gen_options.click()
    pop = dlg.gen_options._popup
    assert pop.isVisible()
    pop.length.setValue(12)
    for cb in pop.sets:
        cb.setChecked(cb.property("attr") == "pw_digits")
    pop.close()
    app.processEvents()
    assert saved and c.pw_length == 12 and c.pw_digits and not (c.pw_upper or c.pw_lower or c.pw_symbols)
    pw = dlg.password.text()  # a new one, made with the new options
    assert len(pw) == 12 and all(ch in passgen.DIGITS for ch in pw)
    # closing without a change leaves the password alone
    dlg.gen_options.click()
    dlg.gen_options._popup.close()
    app.processEvents()
    assert dlg.password.text() == pw and len(saved) == 1
    dlg.done(0)
