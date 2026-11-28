"""Create a new database: where to save it and its master password, optionally filled from a
Bitwarden .json export (plain or password protected; see bitwarden.py)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from .widgets import ErrorLabel
from .. import bitwarden, icons, theme

MIN_LENGTH = 8


def default_path() -> str:
    docs = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or Path.home())
    p = docs / "Vaultline.kdbx"
    n = 2
    while p.exists():
        p = docs / f"Vaultline {n}.kdbx"
        n += 1
    return str(p)


class NewDatabaseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create a new database")
        self.setModal(True)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)

        head = QLabel("Create a new database")
        head.setObjectName("Heading")
        lay.addWidget(head)
        intro = QLabel("A standard KeePass file (.kdbx). KeePassXC on a computer, and KeePassium or "
                       "KeePassDX on a phone, can open it too. Save it in a synced folder (Google Drive, "
                       "OneDrive, Dropbox) to use it on more than one device.")
        intro.setObjectName("Hint")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addSpacing(4)

        lay.addWidget(QLabel("Save as"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.path = QLineEdit(default_path())
        self.path.setMinimumHeight(36)
        row.addWidget(self.path, 1)
        browse = QPushButton("Change…")
        browse.setFocusPolicy(Qt.NoFocus)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        lay.addSpacing(4)
        lay.addWidget(QLabel("Master password"))
        self.password = self._password_field("At least 8 characters")
        lay.addWidget(self.password)
        self.confirm = self._password_field("Type it again")
        lay.addWidget(self.confirm)
        warn = QLabel("Write it down somewhere safe. If it's forgotten, nobody can open the database, "
                      "not even Vaultline.")
        warn.setObjectName("Hint")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        # optional: fill it from a Bitwarden export
        lay.addSpacing(4)
        self.import_btn = QPushButton("  Import from a Bitwarden export (.json)…")
        self.import_btn.setObjectName("Link")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.clicked.connect(self._choose_export)
        lay.addWidget(self.import_btn, 0, Qt.AlignLeft)
        self.export_path = ""
        self.export_label = QLabel()
        self.export_label.setObjectName("Hint")
        self.export_label.setWordWrap(True)
        self.export_label.setTextFormat(Qt.PlainText)
        self.export_label.hide()
        lay.addWidget(self.export_label)
        self.export_password = self._password_field("The password you set when exporting from Bitwarden")
        self.export_password.hide()
        lay.addWidget(self.export_password)
        self.delete_export = QCheckBox("Delete the export file after importing (it holds every password)")
        self.delete_export.setChecked(True)
        self.delete_export.hide()
        lay.addWidget(self.delete_export)
        self.items: list = []  # filled on Create, after the export is read (and decrypted)

        self.error = ErrorLabel()
        self.error.hide()
        lay.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.create_btn = QPushButton("Create")
        self.create_btn.setObjectName("Primary")
        self.create_btn.setDefault(True)
        self.create_btn.clicked.connect(self._accept)
        buttons.addWidget(self.create_btn)
        lay.addSpacing(6)
        lay.addLayout(buttons)
        self.password.setFocus()

    def _password_field(self, placeholder: str) -> QLineEdit:
        f = QLineEdit()
        f.setEchoMode(QLineEdit.Password)
        f.setPlaceholderText(placeholder)
        f.setMinimumHeight(36)
        eye = f.addAction(icons.glyph(icons.EYE, theme.TEXT_2, 16), QLineEdit.TrailingPosition)
        eye.setCheckable(True)
        eye.setToolTip("Show / hide")
        eye.toggled.connect(lambda on: (
            f.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
            eye.setIcon(icons.glyph(icons.EYE_OFF if on else icons.EYE, theme.TEXT_2, 16))))
        f.returnPressed.connect(self._accept)
        return f

    def _browse(self) -> None:
        start = self.path.text().strip() or default_path()
        path, _ = QFileDialog.getSaveFileName(self, "Save the new database as", start,
                                              "KeePass database (*.kdbx)",
                                              options=QFileDialog.DontConfirmOverwrite)
        if path:
            self.path.setText(str(Path(path)))

    def _choose_export(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the Bitwarden export", str(Path.home() / "Downloads"),
                                              "Bitwarden export (*.json)")
        if not path:
            return
        self.export_path = str(Path(path))
        encrypted = bitwarden.is_encrypted(self.export_path)
        self.export_label.setText(f"Import: {Path(path).name}" + ("  (password protected)" if encrypted else ""))
        self.export_label.show()
        self.export_password.setVisible(encrypted)
        self.delete_export.show()
        self.create_btn.setText("Create and import")
        if encrypted:
            self.export_password.setFocus()

    def chosen_path(self) -> str:
        text = self.path.text().strip().strip('"')
        if text and not text.lower().endswith(".kdbx"):
            text += ".kdbx"
        return str(Path(text)) if text else ""

    def problem(self) -> str:
        path = self.chosen_path()
        if not path:
            return "Choose where to save the database."
        if Path(path).exists():
            return "A file with that name already exists. Choose another name."
        if not Path(path).parent.is_dir():
            return "That folder doesn't exist."
        if len(self.password.text()) < MIN_LENGTH:
            return f"Use a master password of at least {MIN_LENGTH} characters."
        if self.password.text() != self.confirm.text():
            return "The two passwords don't match."
        return ""

    def _accept(self) -> None:
        msg = self.problem()
        if not msg and self.export_path:
            try:  # read (and decrypt) now, so a wrong export password is said here
                self.items = bitwarden.items(bitwarden.load(self.export_path, self.export_password.text()))
            except bitwarden.BitwardenError as exc:
                msg = str(exc)
            if not msg and not self.items:
                msg = "That export has no entries in it."
        self.error.setText(msg)
        self.error.setVisible(bool(msg))
        if not msg:
            self.accept()

    @property
    def delete_after(self) -> str:
        """The export file to delete once the import is saved ("" to keep it)."""
        return self.export_path if self.export_path and self.delete_export.isChecked() else ""
