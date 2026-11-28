"""Settings page (inside the main window): security, PIN, auto-lock, clipboard, icons, command line.

Changes are saved when you leave the page (back arrow, Esc) or when Vaultline locks."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QMenu, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import autostart, hotkey, icons, passgen, theme
from .widgets import mark_shared_folders
from ..agent_access import folder_pass_minutes
from ..config import BACKUP_DIR, Config
from .unlock_view import recent_choices


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("SettingsGroup")
    box.setStyleSheet(f"#SettingsGroup {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
                      f"border-radius: 10px; }}")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(16, 12, 16, 14)
    lay.setSpacing(10)
    head = QLabel(title)
    head.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {theme.TEXT_2};")
    lay.addWidget(head)
    return box, lay


def _row(*widgets, stretch_after: int = 0) -> QHBoxLayout:
    r = QHBoxLayout()
    r.setSpacing(8)
    for i, w in enumerate(widgets):
        r.addWidget(w)
        if i == stretch_after:
            r.addStretch()
    return r


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Hint")
    lab.setWordWrap(True)
    return lab


def cli_command() -> str:
    """How to run the CLI on this machine."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).with_name("vaultline-cli.exe")
        return f'"{exe}"'
    root = Path(__file__).resolve().parents[2]
    return f'"{Path(sys.executable)}" -m vaultline.cli  (from {root})'


class SettingsPage(QWidget):
    change_master = Signal()
    pin_toggled = Signal(bool)       # MainWindow asks for the PIN and may refuse
    change_pin = Signal()
    download_icons = Signal(bool)    # True = missing only
    done = Signal()                  # back arrow / Esc: MainWindow saves and returns to the list
    open_database = Signal(str, str)  # path, keyfile ("" path = browse for one)
    close_database = Signal()
    create_shared = Signal()         # make a new shared vault and open it with this one
    add_shared = Signal()            # open an existing one with this one
    detach_shared = Signal(str)      # VaultInfo key
    manage_shared = Signal(str, bool)  # AutoOpen entry uuid, on

    def __init__(self, config: Config, db_path: str, groups=(), profiles=(), shared: set[str] | None = None,
                 vaults=()):
        super().__init__()
        self.config = config
        self._db_path = db_path
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # header: back arrow + title (stays put while the page scrolls)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 10, 16, 6)
        bar.setSpacing(6)
        self.back = QPushButton()
        self.back.setObjectName("Ghost")
        self.back.setIcon(icons.glyph(icons.BACK, theme.TEXT_2, 16))
        self.back.setIconSize(QSize(16, 16))
        self.back.setFixedSize(42, 42)
        self.back.setToolTip("Back (Esc)")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(self.done)
        bar.addWidget(self.back)
        head = QLabel("Settings")
        head.setObjectName("DialogTitle")
        bar.addWidget(head)
        bar.addStretch()
        outer.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        outer.addWidget(scroll, 1)
        body = QWidget()
        scroll.setWidget(body)
        centre = QHBoxLayout(body)
        centre.setContentsMargins(16, 4, 16, 20)
        column = QWidget()
        column.setMaximumWidth(620)
        centre.addStretch()
        centre.addWidget(column, 100)
        centre.addStretch()
        root = QVBoxLayout(column)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # security
        box, lay = _section("SECURITY")
        master = QPushButton("Change master password…")
        master.clicked.connect(self.change_master)
        lay.addLayout(_row(QLabel("Master password"), master))
        self.pin = QCheckBox("Unlock with a PIN after locking")
        self.pin.setChecked(config.pin_enabled and bool(config.pin_hash))
        self.pin.toggled.connect(self.pin_toggled)
        self.pin_change = QPushButton("Change PIN…")
        self.pin_change.clicked.connect(self.change_pin)
        self.pin_change.setEnabled(self.pin.isChecked())
        lay.addLayout(_row(self.pin, self.pin_change))
        lay.addWidget(_hint("The master password is still needed when Vaultline starts, and after 3 wrong PINs. "
                            "Tray > “Lock completely” also asks for it."))
        self.auto = QCheckBox("Auto-lock after (minutes)")
        self.auto.setChecked(config.auto_lock_enabled)
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 240)
        self.minutes.setValue(config.auto_lock_minutes)
        self.minutes.setFixedWidth(80)
        self.minutes.setToolTip("Minutes without using Vaultline before it locks")
        lay.addLayout(_row(self.auto, self.minutes))
        self.clip = QSpinBox()
        self.clip.setRange(5, 300)
        self.clip.setValue(config.clipboard_clear_seconds)
        self.clip.setFixedWidth(80)
        lay.addLayout(_row(QLabel("Clear copied items after (seconds)"), self.clip))
        root.addWidget(box)

        # global shortcut
        box, lay = _section("SHORTCUT")
        self.hotkey_on = QCheckBox("Open the search from any program with")
        self.hotkey_on.setChecked(config.hotkey_enabled)
        self.hotkey_keys = QKeySequenceEdit(QKeySequence.fromString(config.hotkey or hotkey.DEFAULT))
        self.hotkey_keys.setMaximumSequenceLength(1)
        self.hotkey_keys.setFixedWidth(160)
        self.hotkey_keys.setToolTip("Click, then press the keys you want")
        self.hotkey_keys.keySequenceChanged.connect(self._check_hotkey)
        lay.addLayout(_row(self.hotkey_on, self.hotkey_keys))
        self.hotkey_hint = _hint("Click the box and press the keys you want. Use Ctrl, Alt or Win with a key, "
                                 "for example Ctrl+Alt+Space. Press it again to put the search away.")
        lay.addWidget(self.hotkey_hint)
        root.addWidget(box)

        # Auto-Type
        box, lay = _section("AUTO-TYPE")
        self.autotype_on = QCheckBox("Fill the login for the window in front with")
        self.autotype_on.setChecked(config.autotype_hotkey_enabled)
        self.autotype_keys = QKeySequenceEdit(QKeySequence.fromString(config.autotype_hotkey
                                                                      or hotkey.AUTOTYPE_DEFAULT))
        self.autotype_keys.setMaximumSequenceLength(1)
        self.autotype_keys.setFixedWidth(160)
        self.autotype_keys.setToolTip("Click, then press the keys you want")
        self.autotype_keys.keySequenceChanged.connect(self._check_hotkey)
        lay.addLayout(_row(self.autotype_on, self.autotype_keys))
        self.autotype_hint = _hint(
            "Finds the entry for the window in front and types its username, Tab, password and Enter "
            "(or the entry's own Auto-Type settings from KeePass). A match on the page title is shown "
            "for you to confirm first. Or pick an entry here and press Fill (Ctrl+Shift+V) to type it "
            "into the window you came from.")
        lay.addWidget(self.autotype_hint)
        self.autotype_suggest = QCheckBox("Suggest logins for the window you came from")
        self.autotype_suggest.setChecked(config.autotype_suggest)
        lay.addWidget(self.autotype_suggest)
        lay.addWidget(_hint("Open the quick search from a login page and its matching logins are already "
                            "listed. Nothing is typed until you press Fill."))
        root.addWidget(box)

        # New login for a sign-up page
        box, lay = _section("NEW LOGINS")
        self.newlogin_on = QCheckBox("Make a new login for the window in front with")
        self.newlogin_on.setChecked(config.newlogin_hotkey_enabled)
        self.newlogin_keys = QKeySequenceEdit(QKeySequence.fromString(config.newlogin_hotkey
                                                                      or hotkey.NEWLOGIN_DEFAULT))
        self.newlogin_keys.setMaximumSequenceLength(1)
        self.newlogin_keys.setFixedWidth(160)
        self.newlogin_keys.setToolTip("Click, then press the keys you want")
        self.newlogin_keys.keySequenceChanged.connect(self._check_hotkey)
        lay.addLayout(_row(self.newlogin_on, self.newlogin_keys))
        self.newlogin_hint = _hint(
            "Or open the quick search from the sign-up page and pick “New login”. It's saved first (your "
            "profile's email and a new password), then typed: the email in an email box, the password in "
            "a password box, and again in a confirm box right after it.")
        lay.addWidget(self.newlogin_hint)
        self.profile = QComboBox()
        self.profile.addItem("First profile (by name)", "")
        for p in sorted(profiles, key=lambda v: (v.title or "").casefold()):
            self.profile.addItem(p.title or "(untitled profile)", p.uuid)
        i = self.profile.findData(config.default_profile_uuid)
        self.profile.setCurrentIndex(i if i >= 0 else 0)
        lay.addLayout(_row(QLabel("Email from profile"), self.profile))
        if not profiles:
            lay.addWidget(_hint("No profiles yet: add one with New entry > Identity."))
        self.newlogin_folder = QComboBox()
        self.newlogin_folder.addItem("Top level", "")
        for g in groups:
            self.newlogin_folder.addItem("    " * g.depth + g.name, g.uuid)
        i = self.newlogin_folder.findData(config.newlogin_folder_uuid)
        self.newlogin_folder.setCurrentIndex(i if i >= 0 else 0)
        mark_shared_folders(self.newlogin_folder, shared)
        lay.addLayout(_row(QLabel("Save them in"), self.newlogin_folder))
        root.addWidget(box)

        # password generator: set once, used by Generate in the form and by New login
        box, lay = _section("PASSWORD GENERATOR")
        self.pw_length = QSpinBox()
        self.pw_length.setRange(passgen.MIN_LENGTH, passgen.MAX_LENGTH)
        self.pw_length.setValue(max(passgen.MIN_LENGTH, min(passgen.MAX_LENGTH, config.pw_length or 20)))
        self.pw_length.setFixedWidth(80)
        lay.addLayout(_row(QLabel("Length (characters)"), self.pw_length))
        self.pw_sets = []
        sets = QHBoxLayout()
        sets.setSpacing(18)
        for text, attr in (("A–Z", "pw_upper"), ("a–z", "pw_lower"), ("0–9", "pw_digits"),
                           ("Symbols  ! # $ %", "pw_symbols")):
            cb = QCheckBox(text)
            cb.setChecked(getattr(config, attr))
            cb.setProperty("attr", attr)
            cb.toggled.connect(lambda on, b=cb: self._pw_set_toggled(b, on))
            sets.addWidget(cb)
            self.pw_sets.append(cb)
        sets.addStretch()
        lay.addLayout(sets)
        self.pw_example = QLabel()
        self.pw_example.setStyleSheet(f"font-family: Consolas, monospace; color: {theme.TEXT_2};")
        self.pw_example.setTextInteractionFlags(Qt.NoTextInteraction)
        lay.addWidget(self.pw_example)
        self.pw_length.valueChanged.connect(self._pw_example)
        self._pw_example()
        lay.addWidget(_hint("Used every time you press Generate in an entry, and for new logins. If a site "
                            "wants something different, type over the generated password."))
        root.addWidget(box)

        # starting and closing
        box, lay = _section("WINDOWS")
        self.autostart = QCheckBox("Start Vaultline when I sign in to Windows")
        self._autostart_was = autostart.is_enabled()
        self.autostart.setChecked(self._autostart_was)
        self.autostart.setEnabled(autostart.supported())
        lay.addWidget(self.autostart)
        lay.addWidget(_hint("It waits in the tray (or as the floating icon) until you need it."
                            if autostart.supported() else "Only the installed Vaultline.exe can start with Windows."))
        self.close_action = QComboBox()
        for text, value in (("Ask me", "ask"), ("Keep running in the tray", "tray"), ("Quit Vaultline", "quit")):
            self.close_action.addItem(text, value)
        self.close_action.setCurrentIndex(max(0, self.close_action.findData(config.close_action)))
        lay.addLayout(_row(QLabel("When I close the window"), self.close_action))
        lay.addWidget(_hint("In the tray, the shortcut, the command line and auto-lock keep working. "
                            "Quit from the tray icon's menu at any time."))
        root.addWidget(box)

        # icons
        box, lay = _section("WEBSITE ICONS")
        self.icons_auto = QCheckBox("Fetch the icon when I save an entry with a URL")
        self.icons_auto.setChecked(config.auto_download_icons)
        lay.addWidget(self.icons_auto)
        missing = QPushButton("Download missing")
        missing.clicked.connect(lambda: self.download_icons.emit(True))
        again = QPushButton("Re-download all")
        again.setObjectName("Ghost")
        again.clicked.connect(lambda: self.download_icons.emit(False))
        r = QHBoxLayout()
        r.addWidget(missing)
        r.addWidget(again)
        r.addStretch()
        lay.addLayout(r)
        lay.addWidget(_hint("Icons come straight from each website and are saved inside the database."))
        root.addWidget(box)

        # command line
        box, lay = _section("COMMAND LINE")
        self.cli = QCheckBox("Allow command-line access while unlocked")
        self.cli.setChecked(config.cli_enabled)
        lay.addWidget(self.cli)
        lay.addWidget(_hint("Lets vaultline-cli list and reorganise entries and folders (move, rename, tag, "
                            "delete to the recycle bin). It can see titles, usernames, URLs and folders, but "
                            "never passwords, card numbers or codes, notes, or OTP secrets. "
                            "Only your Windows account can connect."))
        self.share = QCheckBox("Share a folder with agents")
        self.share.setChecked(config.agent_share_enabled and bool(config.agent_folder_uuid))
        self.folder = QComboBox()
        self.folder.addItem("Choose a folder…", "")
        for g in groups:
            self.folder.addItem("    " * g.depth + g.name, g.uuid)
        if config.agent_folder_uuid:
            i = self.folder.findData(config.agent_folder_uuid)
            if i < 0:
                self.folder.addItem("(folder not in this database)", config.agent_folder_uuid)
                i = self.folder.count() - 1
            self.folder.setCurrentIndex(i)
        self.folder.setMinimumWidth(200)
        lay.addLayout(_row(self.share, self.folder))
        self.folder_pass = QComboBox()
        for minutes, text in ((15, "15 minutes"), (60, "1 hour"), (-1, "until Vaultline locks"),
                              (0, "never (ask every time)")):
            self.folder_pass.addItem(text, minutes)
        self.folder_pass.setCurrentIndex(max(0, self.folder_pass.findData(folder_pass_minutes(config))))
        self.folder_pass.setMinimumWidth(200)
        self.folder_pass_label = QLabel("Allow the whole folder for")
        lay.addLayout(_row(self.folder_pass_label, self.folder_pass))
        self.share_hint = _hint("With this on, vaultline-cli secret can ask for the username, password or "
                                "one-time code of an entry in this folder or its subfolders, for example so an "
                                "agent can sign in to a website. Vaultline asks you each time: allow once, "
                                "allow that entry for 15 minutes, or allow the whole folder for the time "
                                "above. Every request is logged, never the value. Locking, or Stop agent "
                                "access in the tray, ends every allowance. This limits and records access; "
                                "it doesn't protect against a harmful program already running on this PC.")
        lay.addWidget(self.share_hint)
        self.cli.toggled.connect(self._sync_share)
        self.share.toggled.connect(self._sync_share)
        self._sync_share()
        cmd = QLabel(cli_command())
        cmd.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cmd.setWordWrap(True)
        cmd.setStyleSheet(f'font-family: "{theme.MONO_FONT}", Consolas; font-size: 11px; color: {theme.TEXT_2};')
        lay.addWidget(cmd)
        root.addWidget(box)

        # database
        box, lay = _section("DATABASE")
        path = QLabel(db_path or "—")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path.setObjectName("Sub")
        lay.addWidget(path)
        self.open_other = QPushButton("Open another database…")
        self.open_other.clicked.connect(self._open_other)
        self.close_db = QPushButton("Close database")
        self.close_db.setToolTip("Lock completely: the master password is needed to open it again")
        self.close_db.clicked.connect(self.close_database)
        backups = QPushButton("Open backups folder")
        backups.setObjectName("Ghost")
        backups.clicked.connect(self._open_backups)
        lay.addLayout(_row(self.open_other, self.close_db, backups, stretch_after=2))
        root.addWidget(box)

        # shared vaults (AutoOpen)
        box, lay = _section("SHARED VAULTS")
        lay.addWidget(_hint("Other databases that open with this one, e.g. passwords you share with someone "
                            "through a shared Google Drive folder. Each shows as a folder in the list. "
                            "KeePassXC opens them too (AutoOpen); on a phone, open them separately."))
        for v in vaults:
            state = f"didn't open: {v.error}" if v.error and not v.root_uuid else ("open" if v.root_uuid else "opening…")
            label = QLabel(f"{v.name}  ·  {state}\n{v.path}")
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setObjectName("Sub")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detach = QPushButton("Stop opening")
            detach.setObjectName("Ghost")
            detach.setToolTip("Delete its AutoOpen entry from this database. The shared file itself is kept.")
            detach.clicked.connect(lambda _=False, k=v.key: self.detach_shared.emit(k))
            row = QHBoxLayout()
            row.addWidget(label, 1)
            row.addWidget(detach, 0, Qt.AlignTop)
            lay.addLayout(row)
            manage = QCheckBox("I manage it: share entries into it from this database")
            manage.setToolTip("This database stays the master copy. Entries and folders you share are kept in "
                              "step there; changes others make wait for your review. Leave it off on the "
                              "other person's computer.")
            manage.setChecked(v.managed)
            manage.toggled.connect(lambda on, u=v.entry_uuid: self.manage_shared.emit(u, on))
            lay.addWidget(manage)
        self.create_shared_btn = QPushButton("Create a shared vault…")
        self.create_shared_btn.clicked.connect(self.create_shared)
        self.add_shared_btn = QPushButton("Open an existing one with this…")
        self.add_shared_btn.clicked.connect(self.add_shared)
        lay.addLayout(_row(self.create_shared_btn, self.add_shared_btn, stretch_after=1))
        root.addWidget(box)

        root.addStretch()

    def _sync_share(self, *_):
        self.share.setEnabled(self.cli.isChecked())
        self.folder.setEnabled(self.cli.isChecked() and self.share.isChecked())
        self.folder_pass.setEnabled(self.cli.isChecked() and self.share.isChecked())

    def set_pin_checked(self, on: bool) -> None:
        self.pin.blockSignals(True)
        self.pin.setChecked(on)
        self.pin.blockSignals(False)
        self.pin_change.setEnabled(on)

    def _open_other(self) -> None:
        """Recent databases in a menu, plus Browse. Straight to Browse when there are none."""
        choices = recent_choices(self.config.recent_databases, self._db_path)
        if not choices:
            self.open_database.emit("", "")
            return
        menu = QMenu(self)
        for r in choices:
            p = Path(r["path"])
            act = menu.addAction(f"{p.name}    ({p.parent.name or p.parent})")
            act.setToolTip(str(p))
            act.triggered.connect(lambda _=False, r=r: self.open_database.emit(r["path"], r.get("keyfile", "")))
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        menu.addAction("Browse…", lambda: self.open_database.emit("", ""))
        menu.popup(self.open_other.mapToGlobal(self.open_other.rect().bottomLeft()))

    def _open_backups(self) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(BACKUP_DIR)))

    def hotkey_text(self) -> str:
        return self.hotkey_keys.keySequence().toString(QKeySequence.PortableText)

    def autotype_text(self) -> str:
        return self.autotype_keys.keySequence().toString(QKeySequence.PortableText)

    def newlogin_text(self) -> str:
        return self.newlogin_keys.keySequence().toString(QKeySequence.PortableText)

    def _check_hotkey(self, *_):
        ok = hotkey.parse(self.hotkey_text()) is not None
        self.hotkey_hint.setStyleSheet("" if ok else f"color: {theme.DANGER};")
        ok = hotkey.parse(self.autotype_text()) is not None and not hotkey.same(self.autotype_text(),
                                                                                self.hotkey_text())
        self.autotype_hint.setStyleSheet("" if ok else f"color: {theme.DANGER};")
        ok = hotkey.parse(self.newlogin_text()) is not None and not any(
            hotkey.same(self.newlogin_text(), t) for t in (self.hotkey_text(), self.autotype_text()))
        self.newlogin_hint.setStyleSheet("" if ok else f"color: {theme.DANGER};")

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape:
            self.done.emit()
        else:
            super().keyPressEvent(e)

    def _pw_set_toggled(self, box: QCheckBox, on: bool) -> None:
        if not on and not any(b.isChecked() for b in self.pw_sets):
            box.setChecked(True)  # at least one kind of character
            return
        self._pw_example()

    def _pw_example(self, *_args) -> None:
        on = {b.property("attr"): b.isChecked() for b in self.pw_sets}
        self.pw_example.setText("Example:  " + passgen.generate(self.pw_length.value(), on["pw_upper"],
                                                               on["pw_lower"], on["pw_digits"], on["pw_symbols"]))

    def save(self) -> None:
        c = self.config
        c.auto_lock_enabled = self.auto.isChecked()
        c.auto_lock_minutes = self.minutes.value()
        c.clipboard_clear_seconds = self.clip.value()
        c.auto_download_icons = self.icons_auto.isChecked()
        c.cli_enabled = self.cli.isChecked()
        c.hotkey_enabled = self.hotkey_on.isChecked()
        if hotkey.parse(self.hotkey_text()) is not None:  # an unusable combination keeps the old one
            c.hotkey = self.hotkey_text()
        c.autotype_hotkey_enabled = self.autotype_on.isChecked()
        c.autotype_suggest = self.autotype_suggest.isChecked()
        if hotkey.parse(self.autotype_text()) is not None:
            c.autotype_hotkey = self.autotype_text()
        c.newlogin_hotkey_enabled = self.newlogin_on.isChecked()
        if hotkey.parse(self.newlogin_text()) is not None:
            c.newlogin_hotkey = self.newlogin_text()
        c.default_profile_uuid = self.profile.currentData() or ""
        c.newlogin_folder_uuid = self.newlogin_folder.currentData() or ""
        c.pw_length = self.pw_length.value()
        for cb in self.pw_sets:
            setattr(c, cb.property("attr"), cb.isChecked())
        folder = self.folder.currentData() or ""
        c.agent_share_enabled = bool(self.share.isChecked() and folder)
        c.agent_folder_uuid = folder if c.agent_share_enabled else ""
        c.agent_folder_pass = self.folder_pass.currentData()
        c.close_action = self.close_action.currentData()
        c.save()
        if self.autostart.isEnabled() and self.autostart.isChecked() != self._autostart_was:
            if autostart.set_enabled(self.autostart.isChecked()):
                self._autostart_was = self.autostart.isChecked()

