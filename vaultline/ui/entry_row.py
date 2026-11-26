"""One row of the entry list: avatar, title/username, and copy buttons."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..vault import EntryView
from .widgets import Avatar, ElidedLabel, OtpButton

ROW_HEIGHT = 58


class EntryRow(QWidget):
    copy_requested = Signal(str)   # "username" | "password" | "otp"

    def __init__(self, view: EntryView, pixmap: QPixmap | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.view = view
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 10, 0)
        lay.setSpacing(12)

        lay.addWidget(Avatar(view.title or view.url or "?", pixmap=pixmap))

        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        self.title = ElidedLabel(view.title or "(untitled)")
        self.title.setObjectName("Title")
        card = view.is_card
        if card:
            label = view.card_brand or "Card"
            parts = [f"{label} •••• {view.card_last4}" if view.card_last4 else label]
        else:
            parts = [view.username] if view.username else []
        if view.group:
            parts.append(view.group)
        self.sub = ElidedLabel("  ·  ".join(parts) or "no username")
        self.sub.setObjectName("Sub" if parts else "Muted")
        text.addStretch()
        text.addWidget(self.title)
        text.addWidget(self.sub)
        text.addStretch()
        lay.addLayout(text, 1)

        if card:
            first = ("Card", "card_number", "Copy card number (Ctrl+B)")
        else:
            first = ("User", "username", "Copy username (Ctrl+B)")
        for label, kind, tip, enabled in (
            (*first, bool(view.card_number if card else view.username)),
            ("Pass", "password", "Copy password (Enter / Ctrl+C)", bool(view.password)),
        ):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setFixedWidth(52)
            b.setEnabled(enabled)
            b.clicked.connect(lambda _=False, k=kind: self.copy_requested.emit(k))
            lay.addWidget(b)

        self.otp_btn: OtpButton | None = None
        if card:
            b = QPushButton("Code")
            b.setObjectName("Chip")
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Copy card code (Ctrl+T)")
            b.setFixedSize(98, 26)
            b.setEnabled(bool(view.card_code))
            b.clicked.connect(lambda: self.copy_requested.emit("card_code"))
            lay.addWidget(b)
        elif view.totp is not None:
            self.otp_btn = OtpButton(view.totp)
            self.otp_btn.clicked.connect(lambda: self.copy_requested.emit("otp"))
            lay.addWidget(self.otp_btn)
        else:
            spacer = QWidget()
            spacer.setFixedSize(98, 26)  # keeps the User/Pass columns aligned
            lay.addWidget(spacer)

        self.setFixedHeight(ROW_HEIGHT)
