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
    ("FILLING LOGINS ON THIS PC (AUTO-TYPE)", [
        "Fill: click into the login box, then find the entry in Vaultline and press the keyboard icon at the "
        "end of its row (or Ctrl+Shift+V). It types the username, Tab, password and Enter into the window you "
        "were in before Vaultline; in a password box, just the password and Enter. Point at the icon to see "
        "which window.",
        "The quick search lists the logins for the page you came from before you type (Settings > Auto-Type). "
        "In the details panel, the keyboard icon beside the username, password or one-time code types just "
        "that one, for example a code asked on a second page.",
        "Auto-Type shortcut (Settings > Auto-Type, Ctrl+Alt+A): press it in a login window or web page. "
        "Vaultline finds the entry and shows it by the cursor: press Enter to fill it, or search for another.",
        "Entries with a window association set in KeePass or KeePassXC are typed straight away. Their "
        "Auto-Type settings (on/off, sequence, associations) are used as they are and never changed.",
        "Windows doesn't let Vaultline type into programs running as administrator: copy and paste there.",
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
    ("SHARING PASSWORDS 1: MAKE A SHARED VAULT (ON YOUR PC)", [
        "Make one shared vault per person or group (for example one for each designer). Your own vault "
        "stays the master copy; each shared vault only holds what you share with it.",
        "1.  In Google Drive (G:), make a new folder just for this person, for example "
        "“Shared with Sam”. Nothing else goes in it, and never your own vault file.",
        "2.  In Vaultline: Settings > Shared vaults > Create a shared vault. Save it in that folder and "
        "name it after the person (Sam.kdbx). Leave the suggested name in the folder list as it is.",
        "3.  Vaultline makes a long random password for it and keeps it in your AutoOpen folder, so "
        "your own master password opens both. You never have to type it.",
        "4.  On drive.google.com, share that folder (only that folder) with the person's Google account.",
    ]),
    ("SHARING PASSWORDS 2: SET UP THE OTHER PERSON'S PC (ONCE)", [
        "1.  Get the password: in your vault, open the AutoOpen folder, open the entry with the vault's "
        "name and copy its password. Best pasted on their PC yourself; if you send it, don't send the "
        "Drive link in the same message.",
        "2.  On their PC, open drive.google.com > Shared with me. Right-click the folder > Organize > "
        "Add shortcut, to their own Drive. (Shared folders don't show in File Explorer until you do "
        "this.) After a minute it appears under Google Drive in File Explorer (G:).",
        "3.  In their Vaultline: Settings > Shared vaults > Open existing. Pick the .kdbx in that folder, "
        "paste the password. Leave “I manage it” unticked: only your PC keeps the copies up to date.",
        "4.  From now on the shared vault opens whenever they unlock their own vault. They can also open "
        "it in KeePassXC, or KeePassium on a phone, with the same password.",
    ]),
    ("SHARING PASSWORDS 3: DAY TO DAY", [
        "Share one login: right-click it > Share with… and tick one or more vaults. The copy arrives in a "
        "few seconds and the row says who it's shared with.",
        "Share a folder: point at the folder in the folder list, press the share icon and tick the "
        "vaults. Logins you add to that folder later are shared too.",
        "Stop sharing: Share with… again and untick. Their copy goes to their vault's recycle bin. "
        "Deleting the login in your vault removes the copies too.",
        "Your changes reach them on their own. If they change or delete a copy, a bar appears in your "
        "vault: press Review and choose whose version to keep. Logins they add come into your vault, in "
        "a folder named after their vault.",
        "Each shared vault also shows in your folder list as “Shared with …”, listing what it holds.",
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
