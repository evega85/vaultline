"""Locked screen: pick database, enter master password (and optional key file)."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, icons, theme
from .new_database import NewDatabaseDialog
from .widgets import ElidedLabel


def recent_choices(recent: list, current: str = "") -> list[dict]:
    """Recent databases ([{"path", "keyfile"}]) that still exist, other than `current`."""
    cur = os.path.normcase(os.path.abspath(current)) if current else ""
    return [r for r in recent
            if isinstance(r, dict) and r.get("path") and Path(r["path"]).exists()
            and os.path.normcase(os.path.abspath(r["path"])) != cur]


class UnlockView(QWidget):
    unlock_requested = Signal(str, str, str)  # path, password, keyfile
    create_requested = Signal(str, str)        # path, master password

    def __init__(self, db_path: str = "", keyfile: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._path = db_path
        self._keyfile = keyfile
        self._recent: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.addStretch(2)

        card = QWidget()
        card.setObjectName("LockPanel")  # no box: the window itself is the panel
        card.setAttribute(Qt.WA_StyledBackground)
        card.setMinimumWidth(290)  # shrinks with a compact window
        card.setMaximumWidth(340)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(128))
        logo.setFixedSize(64, 64)
        logo.setScaledContents(True)
        lay.addWidget(logo, 0, Qt.AlignHCenter)
        head = QLabel(APP_NAME)
        head.setObjectName("Heading")
        head.setAlignment(Qt.AlignCenter)
        lay.addWidget(head)

        # first visit (no database yet): two plain choices instead of a password box
        self.welcome = QWidget()
        wl = QVBoxLayout(self.welcome)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(10)
        intro = QLabel("Your passwords in one encrypted file,\nopened only by your master password.")
        intro.setObjectName("Sub")
        intro.setAlignment(Qt.AlignCenter)
        wl.addWidget(intro)
        wl.addSpacing(10)
        self.create_btn = QPushButton("  Create a new database")
        self.create_btn.setObjectName("Primary")
        self.create_btn.setIcon(icons.glyph(icons.ADD, "white", 14))
        self.create_btn.setFixedHeight(40)
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.clicked.connect(self.create_database)
        wl.addWidget(self.create_btn)
        self.open_btn = QPushButton("  Open an existing database")
        self.open_btn.setIcon(icons.glyph(icons.FOLDER, theme.TEXT_2, 14))
        self.open_btn.setFixedHeight(40)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._choose_db)
        wl.addWidget(self.open_btn)
        hint = QLabel("Already use KeePass, KeePassXC or KeePassium?\nOpen your .kdbx file.")
        hint.setObjectName("Hint")
        hint.setAlignment(Qt.AlignCenter)
        wl.addWidget(hint)
        lay.addWidget(self.welcome)

        # database: one centred button with the file name; click for recent databases / Browse
        self.change_btn = QPushButton()
        self.change_btn.setObjectName("Ghost")
        self.change_btn.setIcon(icons.glyph(icons.CHEVRON_DOWN, theme.TEXT_3, 12))
        self.change_btn.setLayoutDirection(Qt.RightToLeft)  # chevron after the name
        self.change_btn.setCursor(Qt.PointingHandCursor)
        self.change_btn.setFocusPolicy(Qt.NoFocus)
        self.change_btn.setStyleSheet(f"QPushButton {{ color: {theme.TEXT_2}; font-size: 13px; padding: 4px 10px; }}")
        self.change_btn.clicked.connect(self.open_chooser)
        lay.addWidget(self.change_btn, 0, Qt.AlignHCenter)
        lay.addSpacing(8)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Master password")
        self.password.setMinimumHeight(40)
        self.password.returnPressed.connect(self._submit)
        self._eye = self.password.addAction(icons.glyph(icons.EYE, theme.TEXT_2, 16), QLineEdit.TrailingPosition)
        self._eye.setCheckable(True)
        self._eye.setToolTip("Show / hide")
        self._eye.toggled.connect(lambda on: (
            self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
            self._eye.setIcon(icons.glyph(icons.EYE_OFF if on else icons.EYE, theme.TEXT_2, 16))))
        lay.addWidget(self.password)

        self.error = QLabel()
        self.error.setObjectName("Error")
        self.error.setWordWrap(True)
        self.error.setAlignment(Qt.AlignCenter)
        self.error.hide()
        lay.addWidget(self.error)

        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.setObjectName("Primary")
        self.unlock_btn.setFixedHeight(40)
        self.unlock_btn.setCursor(Qt.PointingHandCursor)
        self.unlock_btn.clicked.connect(self._submit)
        lay.addWidget(self.unlock_btn)

        # key file: a quiet link under the button (with the file's name once one is chosen)
        self.kf_row = QWidget()
        kf_row = QHBoxLayout(self.kf_row)
        kf_row.setContentsMargins(0, 0, 0, 0)
        kf_row.setSpacing(8)
        kf_row.addStretch(1)
        self.kf_label = ElidedLabel()
        self.kf_label.setObjectName("Hint")
        self.kf_btn = QPushButton()
        self.kf_btn.setObjectName("Link")
        self.kf_btn.setCursor(Qt.PointingHandCursor)
        self.kf_btn.setFocusPolicy(Qt.NoFocus)
        self.kf_btn.clicked.connect(self._toggle_keyfile)
        kf_row.addWidget(self.kf_label)
        kf_row.addWidget(self.kf_btn)
        kf_row.addStretch(1)
        lay.addWidget(self.kf_row)

        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(3)
        self._refresh()

    # ---- state ---------------------------------------------------------------
    def _refresh(self) -> None:
        name = Path(self._path).name if self._path else "Choose a database"
        fm = self.change_btn.fontMetrics()
        self.change_btn.setText(fm.elidedText(name, Qt.ElideMiddle, 240))
        self.change_btn.setToolTip(f"{self._path}\nClick to open another database" if self._path
                                   else "Open a KeePass database")
        self.kf_label.setText(f"Key file: {Path(self._keyfile).name}" if self._keyfile else "")
        self.kf_label.setVisible(bool(self._keyfile))
        first = not self._path  # welcome: create or open; afterwards the usual password screen
        self.welcome.setVisible(first)
        for w in (self.change_btn, self.password, self.unlock_btn, self.kf_row):
            w.setVisible(not first)
        self.kf_btn.setText("Remove key file" if self._keyfile else "Use a key file")

    def set_busy(self, busy: bool) -> None:
        self.unlock_btn.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.unlock_btn.setText("Unlocking…" if busy else "Unlock")
        self.create_btn.setEnabled(not busy)
        self.open_btn.setEnabled(not busy)
        self.create_btn.setText("  Creating…" if busy and self.welcome.isVisible() else "  Create a new database")

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
        (self.create_btn if self.welcome.isVisibleTo(self) else self.password).setFocus()

    def set_recent(self, recent: list) -> None:
        """Databases opened before, newest first: [{"path", "keyfile"}]."""
        self._recent = [r for r in recent if isinstance(r, dict) and r.get("path")]

    def recent_choices(self) -> list[dict]:
        """Recent databases that still exist, other than the one already selected."""
        return recent_choices(self._recent, self._path)

    # ---- actions -----------------------------------------------------------
    def open_chooser(self) -> None:
        """Recent databases, then Open another… and Create a new database…"""
        choices = self.recent_choices()
        menu = QMenu(self)
        for r in choices:
            p = Path(r["path"])
            act = menu.addAction(f"{p.name}    ({p.parent.name or p.parent})")
            act.setToolTip(str(p))
            act.triggered.connect(lambda _=False, r=r: self.select_database(r["path"], r.get("keyfile", "")))
        menu.setToolTipsVisible(True)
        if choices:
            menu.addSeparator()
        menu.addAction(icons.glyph(icons.FOLDER, theme.TEXT_2, 16), "Open another database…", self._choose_db)
        menu.addAction(icons.glyph(icons.ADD, theme.TEXT_2, 16), "Create a new database…", self.create_database)
        menu.popup(self.change_btn.mapToGlobal(self.change_btn.rect().bottomLeft()))

    def select_database(self, path: str, keyfile: str = "") -> None:
        self._path = str(Path(path))
        self._keyfile = keyfile or ""
        self._refresh()
        self.show_error("")
        self.password.clear()
        self.password.setFocus()

    def create_database(self) -> None:
        self._new_dialog = dlg = NewDatabaseDialog(self.window())
        dlg.finished.connect(lambda result: self._create_finished(dlg, result))
        dlg.open()

    def _create_finished(self, dlg: NewDatabaseDialog, result: int) -> None:
        # finished comes before accepted, so read the fields here, then wipe them
        if result == NewDatabaseDialog.Accepted:
            self.create_requested.emit(dlg.chosen_path(), dlg.password.text())
        dlg.password.clear()
        dlg.confirm.clear()
        dlg.deleteLater()

    def browse(self) -> None:
        self._choose_db()

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
