"""Help page (inside the main window): moving to KeePass, and using the same vault on phones.

Static text only. F1 or the ? button opens it; the back arrow or Esc closes it."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import icons, theme
from .settings_page import _hint, _section

SECTIONS: list[tuple[str, list[str]]] = [
    ("YOUR VAULT IS A NORMAL KEEPASS FILE", [
        "Vaultline keeps everything in one .kdbx file (KeePass format, version 4). Any KeePass app opens it "
        "with the same master password, and the same key file if you use one.",
        "One-time codes are stored in the standard “otp” field, so KeePassXC, KeePassium and KeePassDX "
        "show the same codes.",
    ]),
    ("MOVING FROM ANOTHER PASSWORD MANAGER (KEEPASSXC)", [
        "1.  Install KeePassXC on a computer (keepassxc.org).",
        "2.  Export your passwords from the old app (for example Bitwarden, 1Password, or a CSV file).",
        "3.  In KeePassXC choose Database > Import, pick the export, and save the new .kdbx file.",
        "4.  In Vaultline open it from Settings > Database > Open another database.",
        "5.  Delete the export file afterwards: it holds your passwords as plain text.",
        "Cards imported from Bitwarden keep their number, expiry and code, and Vaultline shows them as cards.",
    ]),
    ("IPHONE AND IPAD: KEEPASSIUM", [
        "1.  Keep the .kdbx file in Google Drive (or another cloud folder this PC syncs).",
        "2.  Install Google Drive and KeePassium from the App Store.",
        "3.  In KeePassium, open an existing database and pick the file from Google Drive in the Files picker.",
        "4.  To fill passwords in Safari and apps, turn on KeePassium for AutoFill in the iPhone Settings app "
        "(search Settings for “AutoFill”).",
    ]),
    ("ANDROID: KEEPASSDX", [
        "1.  Install Google Drive and KeePassDX (Play Store or F-Droid).",
        "2.  In KeePassDX, open an existing database and choose Google Drive in the file picker.",
        "3.  To fill passwords in apps, pick KeePassDX as the autofill service in Android Settings "
        "(search Settings for “Autofill”).",
    ]),
    ("EDITING ON SEVERAL DEVICES", [
        "Before every save, Vaultline reads the file again and merges in changes made on your phone, checks "
        "the result, and keeps a local backup (Settings > Database > Open backups folder).",
        "Let Google Drive finish syncing a phone change before editing the same entry on the PC. If one entry "
        "was changed in two places at once, check it afterwards.",
        "If a phone doesn't show a recent change, close and reopen the database there.",
        "If you use a key file, copy it to the phone separately. Don't keep it in the same cloud folder as "
        "the vault.",
    ]),
]


class HelpPage(QWidget):
    done = Signal()

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 6, 16, 6)
        bar.setSpacing(6)
        self.back = QPushButton()
        self.back.setObjectName("Ghost")
        self.back.setIcon(icons.glyph(icons.BACK, theme.TEXT_2, 16))
        self.back.setIconSize(QSize(16, 16))
        self.back.setFixedSize(40, 40)
        self.back.setToolTip("Back (Esc)")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(self.done)
        bar.addWidget(self.back)
        head = QLabel("Using your vault elsewhere")
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
        for title, lines in SECTIONS:
            box, lay = _section(title)
            for line in lines:
                lab = QLabel(line)
                lab.setWordWrap(True)
                lab.setTextFormat(Qt.PlainText)
                lab.setStyleSheet("font-size: 13px;")
                lay.addWidget(lab)
            root.addWidget(box)
        root.addWidget(_hint("Vaultline is for Windows. Phones use one of the apps above with the same file."))
        root.addStretch()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape:
            self.done.emit()
        else:
            super().keyPressEvent(e)
