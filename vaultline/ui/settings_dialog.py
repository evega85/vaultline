"""Settings: security, PIN, auto-lock, clipboard, icons, command line."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import theme
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


class SettingsDialog(QDialog):
    change_master = Signal()
    pin_toggled = Signal(bool)       # MainWindow asks for the PIN and may refuse
    change_pin = Signal()
    download_icons = Signal(bool)    # True = missing only

    def __init__(self, parent: QWidget, config: Config, db_path: str):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setFixedWidth(500)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        head = QLabel("Settings")
        head.setObjectName("DialogTitle")
        root.addWidget(head)

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

        done = QPushButton("Done")
        done.setObjectName("Primary")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        root.addLayout(_row(QWidget(), done, stretch_after=0))

    def set_pin_checked(self, on: bool) -> None:
        self.pin.blockSignals(True)
        self.pin.setChecked(on)
        self.pin.blockSignals(False)
        self.pin_change.setEnabled(on)

    def _open_backups(self) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(BACKUP_DIR)))

    def accept(self) -> None:
        c = self.config
        c.auto_lock_enabled = self.auto.isChecked()
        c.auto_lock_minutes = self.minutes.value()
        c.clipboard_clear_seconds = self.clip.value()
        c.auto_download_icons = self.icons_auto.isChecked()
        c.cli_enabled = self.cli.isChecked()
        c.save()
        super().accept()

