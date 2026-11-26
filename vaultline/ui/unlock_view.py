"""Locked screen: pick database, enter master password (and optional key file)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, icons, theme
from .widgets import ElidedLabel


class UnlockView(QWidget):
    unlock_requested = Signal(str, str, str)  # path, password, keyfile

    def __init__(self, db_path: str = "", keyfile: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._path = db_path
        self._keyfile = keyfile

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(2)

        card = QWidget()
        card.setObjectName("Card")
        card.setAttribute(Qt.WA_StyledBackground)
        card.setFixedWidth(380)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 28, 28, 24)
        lay.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(96))
        logo.setFixedSize(48, 48)
        logo.setScaledContents(True)
        lay.addWidget(logo, 0, Qt.AlignHCenter)
        head = QLabel(APP_NAME)
        head.setObjectName("Heading")
        head.setAlignment(Qt.AlignCenter)
        lay.addWidget(head)

        # database picker
        db_row = QHBoxLayout()
        db_row.setSpacing(6)
        self.db_label = ElidedLabel()
        self.db_label.setObjectName("Sub")
        self.db_label.setAlignment(Qt.AlignCenter)
        change = QPushButton("Change")
        change.setObjectName("Link")
        change.setCursor(Qt.PointingHandCursor)
        change.setFocusPolicy(Qt.NoFocus)
        change.clicked.connect(self._choose_db)
        db_row.addWidget(self.db_label, 1)
        db_row.addWidget(change)
        lay.addLayout(db_row)
        lay.addSpacing(6)

        pw_row = QHBoxLayout()
        pw_row.setSpacing(6)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Master password")
        self.password.returnPressed.connect(self._submit)
        self._eye = QToolButton()
        self._eye.setCheckable(True)
        self._eye.setIcon(icons.glyph(icons.EYE, theme.TEXT_2, 16))
        self._eye.setFixedSize(36, 36)
        self._eye.setFocusPolicy(Qt.NoFocus)
        self._eye.toggled.connect(lambda on: (
            self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
            self._eye.setIcon(icons.glyph(icons.EYE_OFF if on else icons.EYE, theme.TEXT_2, 16))))
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(self._eye)
        lay.addLayout(pw_row)

        kf_row = QHBoxLayout()
        self.kf_label = ElidedLabel()
        self.kf_label.setObjectName("Hint")
        self.kf_btn = QPushButton()
        self.kf_btn.setObjectName("Link")
        self.kf_btn.setCursor(Qt.PointingHandCursor)
        self.kf_btn.setFocusPolicy(Qt.NoFocus)
        self.kf_btn.clicked.connect(self._toggle_keyfile)
        kf_row.addWidget(self.kf_label, 1)
        kf_row.addWidget(self.kf_btn)
        lay.addLayout(kf_row)

        self.error = QLabel()
        self.error.setObjectName("Error")
        self.error.setWordWrap(True)
        self.error.setAlignment(Qt.AlignCenter)
        self.error.hide()
        lay.addWidget(self.error)

        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.setObjectName("Primary")
        self.unlock_btn.setFixedHeight(38)
        self.unlock_btn.setCursor(Qt.PointingHandCursor)
        self.unlock_btn.clicked.connect(self._submit)
        lay.addWidget(self.unlock_btn)

        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(3)
        self._refresh()

    # ---- state ---------------------------------------------------------------
    def _refresh(self) -> None:
        if self._path:
            self.db_label.setText(Path(self._path).name)
            self.db_label.setToolTip(self._path)
        else:
            self.db_label.setText("No database selected")
        self.kf_label.setText(f"Key file: {Path(self._keyfile).name}" if self._keyfile else "")
        self.kf_btn.setText("Remove key file" if self._keyfile else "Use a key file")

    def set_busy(self, busy: bool) -> None:
        self.unlock_btn.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.unlock_btn.setText("Unlocking…" if busy else "Unlock")

    def show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.setVisible(bool(msg))

    def reset(self, message: str = "") -> None:
        self.password.clear()
        self._eye.setChecked(False)
        self.set_busy(False)
        self.show_error(message)
        self.password.setFocus()

    def focus(self) -> None:
        self.password.setFocus()

    # ---- actions -----------------------------------------------------------
    def _choose_db(self) -> None:
        start = str(Path(self._path).parent) if self._path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open KeePass database", start,
                                              "KeePass database (*.kdbx);;All files (*)")
        if path:
            self._path = str(Path(path))
            self._refresh()
            self.show_error("")
            self.password.setFocus()

    def _toggle_keyfile(self) -> None:
        if self._keyfile:
            self._keyfile = ""
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose key file", str(Path.home()),
                                                  "Key files (*.keyx *.key);;All files (*)")
            if path:
                self._keyfile = str(Path(path))
        self._refresh()
        self.password.setFocus()

    def _submit(self) -> None:
        if not self._path:
            self._choose_db()
            if not self._path:
                return
        if not Path(self._path).exists():
            self.show_error("That database file no longer exists. Choose it again.")
            return
        if not self.password.text() and not self._keyfile:
            self.show_error("Enter the master password.")
            return
        self.show_error("")
        self.set_busy(True)
        self.unlock_requested.emit(self._path, self.password.text(), self._keyfile)
