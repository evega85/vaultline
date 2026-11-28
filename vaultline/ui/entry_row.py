"""One row of the entry list: avatar, title/username, copy buttons and Fill."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import icons, theme
from ..vault import EntryView
from .widgets import Avatar, ElidedLabel, OtpButton

ROW_HEIGHT = 58
COMPACT_ROW_HEIGHT = 48


class EntryRow(QWidget):
    copy_requested = Signal(str)   # "username" | "password" | "otp"
    fill_requested = Signal()      # type this entry into the window you came from

    def __init__(self, view: EntryView, pixmap: QPixmap | None = None, parent: QWidget | None = None,
                 shared: list[str] | None = None):
        super().__init__(parent)
        self.view = view
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = self._lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 10, 0)
        lay.setSpacing(12)

        self.avatar = Avatar(view.title or view.url or "?", pixmap=pixmap)
        lay.addWidget(self.avatar)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        self.title = ElidedLabel(view.title or "(untitled)")
        self.title.setObjectName("Title")
        card = view.is_card
        if card:
            label = view.card_brand or "Card"
            parts = [f"{label} •••• {view.card_last4}" if view.card_last4 else label]
        elif view.is_identity:
            parts = ["Profile"]
        else:
            parts = [view.username] if view.username else []
        if view.group:
            parts.append(view.group)
        if shared:
            parts.append("shared with " + ", ".join(shared))
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
            self.third = b
        elif view.totp is not None:
            self.otp_btn = OtpButton(view.totp)
            self.otp_btn.clicked.connect(lambda: self.copy_requested.emit("otp"))
            self.third = self.otp_btn
        else:
            self.third = QWidget()
            self.third.setFixedSize(98, 26)  # keeps the User/Pass columns aligned
        lay.addWidget(self.third)

        if card:
            self.fill_btn = None
            spacer = QWidget()
            spacer.setFixedSize(34, 26)
            lay.addWidget(spacer)
        else:
            b = self.fill_btn = QPushButton()
            b.setObjectName("Chip")
            b.setIcon(icons.glyph(icons.FILL, theme.TEXT, 16))
            b.setIconSize(QSize(16, 16))
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(34, 26)
            b.setEnabled(view.autotype.enabled)
            b.clicked.connect(self.fill_requested)
            self.set_fill_target("")
            lay.addWidget(b)

        self.setFixedHeight(ROW_HEIGHT)

    def set_fill_target(self, title: str) -> None:
        """Tooltip naming the window Fill types into (a page title: never shown as HTML)."""
        if self.fill_btn is None:
            return
        if not self.view.autotype.enabled:
            tip = "Auto-Type is turned off for this entry"
        elif title:
            tip = ("Fill into “%s” (Ctrl+Shift+V)" % title).replace("<", "‹").replace(">", "›")
        else:
            tip = "Fill: click into a login box in another window first, then come back and press this"
        self.fill_btn.setToolTip(tip)

    def set_compact(self, on: bool) -> None:
        """Compact window: shorter rows, smaller icon; all the copy buttons stay."""
        self.setFixedHeight(COMPACT_ROW_HEIGHT if on else ROW_HEIGHT)
        self.avatar.set_size(28 if on else 32)
        self._lay.setContentsMargins(10 if on else 12, 0, 8 if on else 10, 0)
        self._lay.setSpacing(10 if on else 12)
        self.title.setProperty("compact", on)
        self.title.style().polish(self.title)
