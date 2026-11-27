"""Auto-Type: sequences, window matching, reading KeePass settings, typing and the app flow.
Made-up data only. The real-typing test runs only with VAULTLINE_TYPING_TEST=1 (it types on screen)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field

import pytest
from lxml import etree
from pykeepass import PyKeePass, create_database
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from vaultline import autotype as at, sendkeys
from vaultline.autotype import AutoTypeInfo, SequenceError, Step, build, find_matches, site_names, window_matches
from vaultline.vault import CustomField, EntryEdit, Vault

from test_gui import PW, app, setup, unlock, wait_for  # noqa: F401  (fixtures)


@dataclass(eq=False)
class E:
    title: str = ""
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    custom: tuple = ()
    autotype: AutoTypeInfo = field(default_factory=AutoTypeInfo)


def texts(steps):
    return [(s.kind, s.value or s.vk or s.ms) for s in steps]


# ---- sequences ------------------------------------------------------------------------------
def test_default_sequence():
    steps = build("", E(username="alex", password="p@ss{x}"))
    assert texts(steps) == [("text", "alex"), ("key", at.VK_TAB), ("text", "p@ss{x}"), ("key", at.VK_RETURN)]
    assert [s.part for s in steps] == ["username", "tab", "password", "enter"]


def test_placeholders_escapes_and_delays():
    e = E(title="Site", username="u", password="p", url="https://x.test",
          custom=(CustomField("PIN", "4321", True),))
    steps = build("{username}{TAB 2}{DELAY=25}{password}{DELAY 300}{{}a{+}{}}~{S:PIN}{S:Title}{URL}{SPACE}", e)
    assert texts(steps) == [("text", "u"), ("key", 9), ("key", 9), ("gap", 25), ("text", "p"), ("delay", 300),
                            ("text", "{a+}"), ("key", 13), ("text", "4321"), ("text", "Site"),
                            ("text", "https://x.test"), ("key", 32)]


def test_totp_placeholder():
    steps = build("{USERNAME}{TAB}{TOTP}", E(username="u"), otp_code=lambda: "123456")
    assert steps[-1].value == "123456" and steps[-1].part == "one-time code"
    assert build("{TIMEOTP}", E(), otp_code=lambda: "1")[0].value == "1"


@pytest.mark.parametrize("seq", ["{FOO}", "^v", "+a", "%x", "(a)", "{USERNAME", "a}", "{DELAY 99999}",
                                 "{TAB 500}", "{TOTP}"])
def test_bad_sequences_refused_before_typing(seq):
    with pytest.raises(SequenceError):
        build(seq, E(username="u"))


def test_step_repr_hides_values():
    assert "s3cret" not in repr(Step("text", value="s3cret", part="password"))


def test_progress_text():
    steps = build("", E(username="u", password="p"))
    assert at.describe_progress([]) == "before anything was typed"
    assert at.describe_progress(steps[:2]) == "after the username"
    assert at.describe_progress(steps[:3]) == "after the password"


# ---- matching -------------------------------------------------------------------------------
def test_window_patterns():
    assert window_matches("*GitHub*", "Sign in to GitHub - Google Chrome")
    assert window_matches("sign in to github*", "Sign in to GitHub - Google Chrome")
    assert not window_matches("GitHub", "Sign in to GitHub - Google Chrome")  # whole title
    assert window_matches("Remote Desktop?Connection", "Remote Desktop Connection")
    assert window_matches("//^Sign in to (GitHub|GitLab)//", "Sign in to GitLab — Mozilla Firefox")
    assert not window_matches("//[bad//", "anything")
    assert not window_matches("", "anything")


def test_site_names():
    assert site_names("https://accounts.github.com/login") == ["accounts.github.com", "github"]
    assert site_names("www.bbc.co.uk") == ["bbc.co.uk", "bbc"]
    assert site_names("http://192.168.1.1") == []
    assert site_names("http://localhost:8080") == []
    assert site_names("https://x.io") == ["x.io"]  # "x" is too short to look for
    assert site_names("") == []


def test_match_order_and_rules():
    gh = E("GitHub", url="https://github.com")
    assoc = E("Work SSO", autotype=AutoTypeInfo(associations=(("*Okta*", "{PASSWORD}{ENTER}"),)))
    bank = E("Bank", url="https://www.examplebank.co.uk")
    off = E("Netflix", autotype=AutoTypeInfo(enabled=False))
    old = E("Old Mail", autotype=AutoTypeInfo(expired=True))
    git = E("Git")  # must not match inside "GitHub"
    entries = [gh, assoc, bank, off, old, git]

    m = find_matches(entries, "Okta Sign In - Chrome")
    assert [(x.entry, x.how, x.sequence) for x in m] == [(assoc, "window", "{PASSWORD}{ENTER}")]
    m = find_matches(entries, "Sign in to GitHub · GitHub - Google Chrome")
    assert [(x.entry, x.how) for x in m] == [(gh, "title")]
    m = find_matches(entries, "Log on | ExampleBank - Microsoft Edge")
    assert [(x.entry, x.how) for x in m] == [(bank, "url")]
    assert find_matches(entries, "Netflix - Chrome") == []          # Auto-Type off
    assert find_matches(entries, "Old Mail") == []                  # expired
    assert find_matches(entries, "") == []
    m = find_matches(entries, "Okta GitHub")  # associations win over titles
    assert [x.entry for x in m] == [assoc]


def test_browser_name_is_not_a_match():
    google, gh, edge = E("Google"), E("GitHub"), E("Edge Router")
    assert at.page_title("Sign in to GitHub - Google Chrome") == "Sign in to GitHub"
    assert at.page_title("Inbox and 3 more pages - Microsoft​ Edge") == "Inbox"
    assert at.page_title("Login - Netflix - Microsoft​ Edge") == "Login - Netflix"
    assert at.page_title("Docs — Mozilla Firefox") == "Docs"
    assert at.page_title("Sign in - GitHub - Helium") == "Sign in - GitHub"
    assert at.page_title("Notepad") == "Notepad"
    m = find_matches([google, gh, edge], "Sign in to GitHub · GitHub - Google Chrome")
    assert [x.entry for x in m] == [gh]
    assert find_matches([google, edge], "New Tab - Microsoft​ Edge") == []
    assert [x.entry for x in find_matches([google], "Google - Google Chrome")] == [google]
    # associations still see the whole window title, as in KeePass
    chrome_only = E("Any", autotype=AutoTypeInfo(associations=(("* - Google Chrome", ""),)))
    assert find_matches([chrome_only], "X - Google Chrome")[0].how == "window"


def test_association_with_empty_sequence_uses_entrys():
    e = E("X", autotype=AutoTypeInfo(sequence="{USERNAME}{ENTER}", associations=(("App*", ""),)))
    assert find_matches([e], "App login")[0].sequence == "{USERNAME}{ENTER}"


# ---- reading the kdbx (and never rewriting it) ----------------------------------------------------
def _autotype_xml(entry) -> bytes:
    return etree.tostring(entry._element.find("AutoType"))


def test_reads_keepass_settings_and_keeps_them(tmp_path):
    db = tmp_path / "at.kdbx"
    kp = create_database(str(db), password=PW)
    quiet = kp.add_group(kp.root_group, "Quiet")
    flag = quiet._element.find("EnableAutoType")
    if flag is None:
        flag = etree.SubElement(quiet._element, "EnableAutoType")
    flag.text = "false"
    seqgroup = kp.add_group(kp.root_group, "Seq")
    el = seqgroup._element.find("DefaultAutoTypeSequence")
    if el is None:
        el = etree.SubElement(seqgroup._element, "DefaultAutoTypeSequence")
    el.text = "{USERNAME}{ENTER}"
    a = kp.add_entry(kp.root_group, "Portal", "alex", "pw-a")
    a.autotype_sequence = "{USERNAME}{TAB}{TAB}{PASSWORD}{ENTER}"
    at_el = a._element.find("AutoType")
    assoc = etree.SubElement(at_el, "Association")
    etree.SubElement(assoc, "Window").text = "*Portal Login*"
    etree.SubElement(assoc, "KeystrokeSequence").text = "{PASSWORD}{ENTER}"
    assoc2 = etree.SubElement(at_el, "Association")
    etree.SubElement(assoc2, "Window").text = "Other*"
    etree.SubElement(assoc2, "KeystrokeSequence")
    b = kp.add_entry(quiet, "Hidden", "sam", "pw-b")
    c = kp.add_entry(seqgroup, "Inherits", "kim", "pw-c")
    d = kp.add_entry(kp.root_group, "Off", "lee", "pw-d")
    d.autotype_enabled = False
    kp.save()
    before = _autotype_xml(PyKeePass(str(db), password=PW).find_entries(title="Portal", first=True))

    v = Vault(str(db), PW, None, tmp_path / "backups")
    views = {x.title: x for x in v.open().entries}
    assert views["Portal"].autotype == AutoTypeInfo(
        True, "{USERNAME}{TAB}{TAB}{PASSWORD}{ENTER}",
        (("*Portal Login*", "{PASSWORD}{ENTER}"), ("Other*", "")))
    assert not views["Hidden"].autotype.enabled       # folder turned off
    assert views["Inherits"].autotype.sequence == "{USERNAME}{ENTER}"  # folder's sequence
    assert not views["Off"].autotype.enabled
    assert views["Off"].autotype.effective_sequence == at.DEFAULT_SEQUENCE

    # an ordinary edit leaves the Auto-Type block exactly as it was
    p = views["Portal"]
    v.save_entry(EntryEdit(p.uuid, "Portal", "alex2", "pw-a2", "", "", None))
    after = PyKeePass(str(db), password=PW).find_entries(title="Portal", first=True)
    assert after.username == "alex2"
    assert _autotype_xml(after) == before


# ---- typing (with fakes) ----------------------------------------------------------------------
class FakeScreen:
    def __init__(self, target=100, move_after=None):
        self.target, self.front, self.typed, self.move_after = target, target, [], move_after

    def char(self, ch):
        self.typed.append(ch)
        if self.move_after is not None and len(self.typed) >= self.move_after:
            self.front = 999
        return True

    def key(self, vk):
        self.typed.append({9: "<TAB>", 13: "<ENTER>"}[vk])
        if self.move_after is not None and len(self.typed) >= self.move_after:
            self.front = 999
        return True

    def run(self, steps, **kw):
        return sendkeys.type_steps(steps, self.target, foreground=lambda: self.front, char=self.char,
                                   key=self.key, sleep=lambda s: None, modifiers=lambda: False, **kw)


def test_types_whole_sequence():
    s = FakeScreen()
    r = s.run(build("", E(username="ab", password="pä😀")))
    assert r.ok and "".join(s.typed) == "ab<TAB>pä😀<ENTER>"


def test_stops_when_focus_moves_and_says_where():
    s = FakeScreen(move_after=3)  # "ab", Tab, then focus goes elsewhere
    r = s.run(build("", E(username="ab", password="secret")))
    assert not r.ok and "after the username" in r.message and "lost focus" in r.message
    assert "".join(s.typed) == "ab<TAB>"
    assert "secret" not in r.message
    s = FakeScreen(move_after=5)  # two characters into the password
    r = s.run(build("", E(username="ab", password="secret")))
    assert "partway through the password" in r.message and "".join(s.typed) == "ab<TAB>se"


def test_cancelled_by_lock():
    s = FakeScreen()
    r = s.run(build("", E(username="ab", password="p")), cancelled=lambda: True)
    assert not r.ok and "locked" in r.message and s.typed == []


def test_waits_for_shortcut_keys_to_be_let_go():
    held = iter([True, True, False])
    s = FakeScreen()
    r = sendkeys.type_steps(build("x", E()), 100, foreground=lambda: 100, char=s.char, key=s.key,
                            sleep=lambda _s: None, modifiers=lambda: next(held))
    assert r.ok and s.typed == ["x"]
    r = sendkeys.type_steps(build("x", E()), 100, foreground=lambda: 100, char=s.char, key=s.key,
                            sleep=lambda _s: None, modifiers=lambda: True)
    assert not r.ok and "Let go" in r.message


# ---- the app flow (Windows calls faked) ----------------------------------------------------------
@pytest.fixture
def fake_os(monkeypatch):
    """A pretend desktop: window 500 titled `title` in front, recorded typing."""
    st = type("St", (), {})()
    st.front, st.title, st.admin, st.typed, st.own = 500, "Sign in to GitHub - Chrome", False, [], False
    monkeypatch.setattr(sendkeys, "foreground_window", lambda: st.front)
    monkeypatch.setattr(sendkeys, "window_title", lambda h: st.title)
    monkeypatch.setattr(sendkeys, "is_own", lambda h: st.own)
    monkeypatch.setattr(sendkeys, "is_window", lambda h: h == 500)
    monkeypatch.setattr(sendkeys, "is_fill_target", lambda h: h == 500 and not st.own)
    monkeypatch.setattr(sendkeys, "runs_above_us", lambda h: st.admin)
    monkeypatch.setattr(sendkeys, "bring_to_front", lambda h: True)
    st.box = ""  # the kind of box with the focus: "password", "text" or "" (can't tell)
    monkeypatch.setattr(sendkeys, "focused_box", lambda h: st.box)

    def fake_type(steps, target, cancelled=lambda: False, **kw):
        st.typed.append((target, [(s.kind, s.value or s.vk) for s in steps]))
        return sendkeys.Result(True)
    monkeypatch.setattr(sendkeys, "type_steps", fake_type)
    return st


def _flow(win):
    return win.autotype


def test_shortcut_title_match_asks_first_then_types(setup, fake_os):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    flow = _flow(win)
    flow.hotkey_pressed()
    assert flow.picker.isVisible()  # a page title match is confirmed, never typed straight away
    assert [v.title for v, _ in flow.picker._shown] == ["GitHub"]
    QTest.keyClick(flow.picker.search, Qt.Key_Return)
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    assert fake_os.typed == [(500, [("text", "alex-dev"), ("key", 9), ("text", "gh-pass"), ("key", 13)])]


def test_shortcut_no_match_offers_search(setup, fake_os):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    fake_os.title = "Some Login Window"
    flow = _flow(win)
    flow.hotkey_pressed()
    assert flow.picker.isVisible() and len(flow.picker._shown) == 5  # everything, to search
    QTest.keyClicks(flow.picker.search, "netf")
    assert [v.title for v, _ in flow.picker._shown] == ["Netflix"]
    QTest.keyClick(flow.picker.search, Qt.Key_Escape)
    assert not flow.picker.isVisible()
    wait_for(app, lambda: False, 0.3)
    assert fake_os.typed == []


def test_shortcut_association_types_straight_away(setup, fake_os, monkeypatch):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    netflix = next(v for v in win.vault_view.views if v.title == "Netflix")
    monkeypatch.setattr(netflix, "autotype", AutoTypeInfo(associations=(("*Streaming Login*", "{PASSWORD}"),)))
    fake_os.title = "Streaming Login - Edge"
    _flow(win).hotkey_pressed()
    assert not _flow(win).picker.isVisible()
    assert wait_for(app, lambda: fake_os.typed and not _flow(win).busy)
    assert fake_os.typed == [(500, [("text", "n-pass")])]


def test_refuses_own_and_admin_windows(setup, fake_os):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    fake_os.own = True
    _flow(win).hotkey_pressed()
    assert not _flow(win).picker.isVisible() and _flow(win).note.isVisible()
    fake_os.own, fake_os.admin = False, True
    _flow(win).hotkey_pressed()
    assert not _flow(win).picker.isVisible()
    assert "administrator" in _flow(win).note.body.text()
    wait_for(app, lambda: False, 0.2)
    assert fake_os.typed == []


def test_shortcut_while_locked_unlocks_then_carries_on(setup, fake_os):
    app, win, vc, *_ = setup
    win.hide_to_tray()
    _flow(win).hotkey_pressed()
    assert win.isVisible() and not vc.is_unlocked  # came up for the password
    unlock(app, win, vc)
    assert wait_for(app, lambda: _flow(win).picker.isVisible())
    assert not win.isVisible()  # back to the tray: it only came up for the unlock
    QTest.keyClick(_flow(win).picker.search, Qt.Key_Return)
    assert wait_for(app, lambda: fake_os.typed)


def test_lock_closes_picker_and_forgets(setup, fake_os):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    _flow(win).hotkey_pressed()
    assert _flow(win).picker.isVisible()
    vc.lock(full=True)
    assert wait_for(app, lambda: not _flow(win).picker.isVisible())
    assert fake_os.typed == []


def test_fill_types_into_previous_window(setup, fake_os, monkeypatch):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    flow = _flow(win)
    view = win.vault_view
    QTest.keyClicks(view.search, "google")
    monkeypatch.setattr(flow.watcher, "target", lambda: 0)
    QTest.keyClick(view.search, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
    wait_for(app, lambda: False, 0.2)
    assert fake_os.typed == []  # nowhere to fill: says so instead
    monkeypatch.setattr(flow.watcher, "target", lambda: 500)
    fake_os.title = "<b>Team Chat</b> & more"
    win.activated.emit()  # coming to the front shows where Fill would type
    view.open_details()
    # the window is named in the Fill icon's tooltip, never as HTML
    assert view.panel.fill_btn.toolTip() == "Fill into “‹b›Team Chat‹/b› & more” (Ctrl+Shift+V)"
    view.panel.close_panel()
    QTest.keyClick(view.search, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    assert fake_os.typed == [(500, [("text", "someone@example.com"), ("key", 9), ("text", "g-pass"), ("key", 13)])]
    # the details panel's button does the same
    fake_os.typed.clear()
    view.open_details()
    view.panel.fill_btn.click()
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    view.panel.close_panel()
    # and so does the icon on the row in the list, for that row's entry
    fake_os.typed.clear()
    i = next(i for i, v in enumerate(view.views) if v.title == "Google")
    row = view._rows[i]
    assert "into “‹b›Team Chat‹/b› & more”" in row.fill_btn.toolTip()
    row.fill_btn.click()
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    assert fake_os.typed[0][1][0] == ("text", "someone@example.com")


def test_fill_in_a_password_box_types_only_the_password(setup, fake_os, monkeypatch):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    flow = _flow(win)
    monkeypatch.setattr(flow.watcher, "target", lambda: 500)
    google = next(v for v in win.vault_view.views if v.title == "Google")
    fake_os.box = "password"
    flow.fill(google)
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    assert fake_os.typed[-1] == (500, [("text", "g-pass"), ("key", 13)])
    for box in ("text", ""):  # a username box, or can't tell: the whole login
        fake_os.box = box
        flow.fill(google)
        assert wait_for(app, lambda: len(fake_os.typed) == (2 if box == "text" else 3) and not flow.busy)
        assert fake_os.typed[-1][1] == [("text", "someone@example.com"), ("key", 9), ("text", "g-pass"), ("key", 13)]


def test_fill_one_field_from_the_panel_icons(setup, fake_os, monkeypatch):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    flow = _flow(win)
    monkeypatch.setattr(flow.watcher, "target", lambda: 500)
    view = win.vault_view
    QTest.keyClicks(view.search, "github")
    win.activated.emit()
    view.open_details()
    icons_ = view.panel._field_fills
    assert [b.property("fill_name") for b in icons_] == ["username", "password", "one-time code"]
    assert "Type just the password into" in icons_[1].toolTip()
    fake_os.box = "password"  # a single field is typed as asked, wherever you are
    for b, want in zip(icons_, ("alex-dev", "gh-pass")):
        fake_os.typed.clear()
        b.click()
        assert wait_for(app, lambda: fake_os.typed and not flow.busy)
        assert fake_os.typed == [(500, [("text", want)])]
    fake_os.typed.clear()
    icons_[2].click()
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    code = fake_os.typed[0][1]
    assert len(code) == 1 and code[0][1].isdigit() and len(code[0][1]) == 6


def test_quick_box_suggests_logins_for_the_window(setup, fake_os, monkeypatch):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    flow = _flow(win)
    monkeypatch.setattr(flow.watcher, "target", lambda: 500)
    view = win.vault_view
    win.resize(560, 420)
    assert wait_for(app, lambda: view.compact)
    shown = lambda: [v.title for i, v in enumerate(view.views) if not view.list.isRowHidden(i)]  # noqa: E731
    win.activated.emit()  # came from "Sign in to GitHub - Chrome"
    assert shown() == ["GitHub"] and "Suggested" in view.search.placeholderText()
    QTest.keyClicks(view.search, "net")  # typing searches as usual
    assert shown() == ["Netflix"]
    view.search.clear()
    assert shown() == ["GitHub"]
    fake_os.title = "Some unrelated page - Google Chrome"
    win.activated.emit()
    assert shown() == [] and view.search.placeholderText() == "Search…"
    # turned off in Settings: the quick box stays empty until you type
    fake_os.title = "Sign in to GitHub - Chrome"
    win.config.autotype_suggest = False
    win._apply_suggest()
    win.activated.emit()
    assert shown() == []


def test_autotype_shortcut_setting(setup):
    from PySide6.QtGui import QKeySequence
    from vaultline import config as cfgmod
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    win.open_settings()
    page = win.settings_page
    assert not page.autotype_on.isChecked() and page.autotype_text() == "Ctrl+Alt+A"  # off by default
    assert page.autotype_suggest.isChecked()  # suggestions: on by default
    page.autotype_suggest.setChecked(False)
    page.autotype_on.setChecked(True)
    page.autotype_keys.setKeySequence(QKeySequence("Ctrl+Alt+F"))
    win.close_settings()
    saved = cfgmod.Config.load()
    assert saved.autotype_hotkey_enabled and saved.autotype_hotkey == "Ctrl+Alt+F"
    assert not saved.autotype_suggest and not win.vault_view.suggest
    # the same keys as the search shortcut are reported
    win.config.hotkey_enabled, win.config.hotkey = True, "Ctrl+Alt+F"
    win.apply_hotkey()
    assert "search shortcut" in win.hotkey_error


# ---- real typing (opt-in: it types on your screen) ------------------------------------------------
TARGET_APP = textwrap.dedent("""
    import sys
    from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget
    app = QApplication(sys.argv)
    w = QWidget(); w.setWindowTitle("Vaultline typing test")
    a, b = QLineEdit(), QLineEdit()
    b.setEchoMode(QLineEdit.Password)
    lay = QVBoxLayout(w); lay.addWidget(a); lay.addWidget(b)
    def done():
        print(repr((a.text(), b.text())), flush=True); app.quit()
    b.returnPressed.connect(done)
    w.show(); w.raise_(); w.activateWindow(); a.setFocus()
    print("ready", flush=True)
    app.exec()
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("VAULTLINE_TYPING_TEST") != "1",
                    reason="types on the real screen: set VAULTLINE_TYPING_TEST=1")
def test_real_typing_into_qlineedit(tmp_path):
    import queue
    import threading
    script = tmp_path / "target.py"
    script.write_text(TARGET_APP, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True,
                            encoding="utf-8", env=env)
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x.strip()) for x in proc.stdout], daemon=True).start()
    try:
        assert lines.get(timeout=20) == "ready"
        hwnd = 0
        for _ in range(100):
            hwnd = sendkeys.foreground_window()
            if sendkeys.window_title(hwnd) == "Vaultline typing test":
                break
            time.sleep(0.05)
        assert sendkeys.window_title(hwnd) == "Vaultline typing test",             f"test window didn't come to the front ({sendkeys.window_title(hwnd)!r} is)"
        assert not sendkeys.runs_above_us(hwnd)
        def box():  # on its own thread, as in the app (it sets up COM)
            out = []
            t = threading.Thread(target=lambda: out.append(sendkeys.focused_box(hwnd)))
            t.start()
            t.join(10)
            return out[0] if out else "timed out"
        assert box() == "text"
        entry = E(username="user.name", password="pässwörd😀 {x}%~")
        r = sendkeys.type_steps(build("{USERNAME}{TAB}", entry), hwnd)
        assert r.ok, r.message
        time.sleep(0.2)
        assert box() == "password"
        r = sendkeys.type_steps(build("{PASSWORD}{ENTER}", entry), hwnd)
        assert r.ok, r.message
        assert lines.get(timeout=10) == repr(("user.name", "pässwörd😀 {x}%~"))
    finally:
        proc.kill()
