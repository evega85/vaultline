"""PIN lock screen shown after locking when quick unlock is set up."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import icons
from .widgets import ErrorLabel, ElidedLabel


class PinView(QWidget):
    pin_entered = Signal(str)
    use_master = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.addStretch(2)

        card = QWidget()
        card.setObjectName("LockPanel")  # no box: the window itself is the panel
        card.setAttribute(Qt.WA_StyledBackground)
        card.setMinimumWidth(290)
        card.setMaximumWidth(340)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(128))
        logo.setFixedSize(64, 64)
        logo.setScaledContents(True)
        lay.addWidget(logo, 0, Qt.AlignHCenter)
        head = QLabel("Locked")
        head.setObjectName("Heading")
        head.setAlignment(Qt.AlignCenter)
        lay.addWidget(head)
        self.db = ElidedLabel()
        self.db.setObjectName("Sub")
        self.db.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.db)
        lay.addSpacing(4)

        self.pin = QLineEdit()
        self.pin.setObjectName("Pin")
        self.pin.setEchoMode(QLineEdit.Password)
        self.pin.setPlaceholderText("PIN")
        self.pin.setAlignment(Qt.AlignCenter)
        self.pin.setMaxLength(12)
        self.pin.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,12}"), self))
        self.pin.returnPressed.connect(self._submit)
        lay.addWidget(self.pin)

        self.error = ErrorLabel(center=True)
        self.error.hide()
        lay.addWidget(self.error)

        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.setObjectName("Primary")
        self.unlock_btn.setFixedHeight(40)
        self.unlock_btn.setCursor(Qt.PointingHandCursor)
        self.unlock_btn.clicked.connect(self._submit)
        lay.addWidget(self.unlock_btn)

        master = QPushButton("Use master password instead")
        master.setObjectName("Link")
        master.setCursor(Qt.PointingHandCursor)
        master.setFocusPolicy(Qt.NoFocus)
        master.clicked.connect(self.use_master)
        lay.addWidget(master, 0, Qt.AlignHCenter)

        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(3)

    def reset(self, db_name: str, message: str = "") -> None:
        self.db.setText(f"Enter your PIN to open {db_name}")
        self.pin.clear()
        self.set_busy(False)
        self.show_error(message)
        self.pin.setFocus()

    def set_busy(self, busy: bool) -> None:
        self.pin.setEnabled(not busy)
        self.unlock_btn.setEnabled(not busy)
        self.unlock_btn.setText("Unlocking…" if busy else "Unlock")

    def show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.setVisible(bool(msg))

    def wrong(self, tries_left: int) -> None:
        self.pin.clear()
        self.set_busy(False)
        self.show_error(f"Wrong PIN. {tries_left} {'try' if tries_left == 1 else 'tries'} left "
                        f"before the master password is needed.")
        self.pin.setFocus()

    def focus(self) -> None:
        self.pin.setFocus()

    def _submit(self) -> None:
        if self.pin.text():
            self.set_busy(True)
            self.pin_entered.emit(self.pin.text())
