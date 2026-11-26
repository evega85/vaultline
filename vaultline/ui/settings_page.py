"""Settings page (inside the main window): security, PIN, auto-lock, clipboard, icons, command line.

Changes are saved when you leave the page (back arrow, Esc) or when Vaultline locks."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import icons, theme
from ..config import BACKUP_DIR, Config


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

    def __init__(self, config: Config, db_path: str, groups=()):
        super().__init__()
        self.config = config
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
        self.auto = QCheckBox("Auto-lock after")
        self.auto.setChecked(config.auto_lock_enabled)
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 240)
        self.minutes.setValue(config.auto_lock_minutes)
        self.minutes.setSuffix(" min of inactivity")
        self.minutes.setFixedWidth(170)
        lay.addLayout(_row(self.auto, self.minutes))
        self.clip = QSpinBox()
        self.clip.setRange(5, 300)
        self.clip.setValue(config.clipboard_clear_seconds)
        self.clip.setSuffix(" s")
        self.clip.setFixedWidth(90)
        lay.addLayout(_row(QLabel("Clear copied items after"), self.clip))
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
        self.share_hint = _hint("With this on, vaultline-cli secret can ask for the username, password or "
                                "one-time code of an entry in this folder or its subfolders, for example so an "
                                "agent can sign in to a website. Vaultline asks you every time (or allows one "
                                "entry for 15 minutes) and logs each request, never the value. Locking cancels "
                                "every allowance. This limits and records access; it doesn't protect against "
                                "a harmful program already running on this PC.")
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
        backups = QPushButton("Open backups folder")
        backups.setObjectName("Ghost")
        backups.clicked.connect(self._open_backups)
        lay.addLayout(_row(backups))
        root.addWidget(box)

        root.addStretch()

    def _sync_share(self, *_):
        self.share.setEnabled(self.cli.isChecked())
        self.folder.setEnabled(self.cli.isChecked() and self.share.isChecked())

    def set_pin_checked(self, on: bool) -> None:
        self.pin.blockSignals(True)
        self.pin.setChecked(on)
        self.pin.blockSignals(False)
        self.pin_change.setEnabled(on)

    def _open_backups(self) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(BACKUP_DIR)))

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape:
            self.done.emit()
        else:
            super().keyPressEvent(e)

    def save(self) -> None:
        c = self.config
        c.auto_lock_enabled = self.auto.isChecked()
        c.auto_lock_minutes = self.minutes.value()
        c.clipboard_clear_seconds = self.clip.value()
        c.auto_download_icons = self.icons_auto.isChecked()
        c.cli_enabled = self.cli.isChecked()
        folder = self.folder.currentData() or ""
        c.agent_share_enabled = bool(self.share.isChecked() and folder)
        c.agent_folder_uuid = folder if c.agent_share_enabled else ""
        c.save()

