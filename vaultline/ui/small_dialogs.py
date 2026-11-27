"""Small styled dialogs: confirm, text prompt, set PIN, change master password."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import pin as pinlib


class _Base(QDialog):
    def __init__(self, parent: QWidget | None, title: str, text: str = "", width: int = 400):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 20, 22, 18)
        self.lay.setSpacing(12)
        head = QLabel(title)
        head.setObjectName("DialogTitle")
        self.lay.addWidget(head)
        if text:
            body = QLabel(text)
            body.setWordWrap(True)
            body.setObjectName("Sub")
            body.setStyleSheet("font-size: 13px;")
            self.lay.addWidget(body)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.lay.addLayout(self.body)
        self.error = QLabel()
        self.error.setObjectName("Error")
        self.error.setWordWrap(True)
        self.error.hide()
        self.lay.addWidget(self.error)

    def buttons(self, ok_text: str, danger: bool = False) -> QPushButton:
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(ok_text)
        ok.setObjectName("Danger" if danger else "Primary")
        ok.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.lay.addLayout(row)
        return ok

    def fail(self, msg: str, focus: QWidget | None = None) -> None:
        self.error.setText(msg)
        self.error.show()
        if focus:
            focus.setFocus()
            if isinstance(focus, QLineEdit):
                focus.selectAll()


def confirm(parent: QWidget, title: str, text: str, ok_text: str = "OK", danger: bool = False) -> bool:
    d = _Base(parent, title, text)
    ok = d.buttons(ok_text, danger)
    ok.clicked.connect(d.accept)
    return d.exec() == QDialog.Accepted


def ask_close(parent: QWidget) -> tuple[str, bool] | None:
    """The window's X: ("tray" | "quit", remember the choice), or None to cancel."""
    d = _Base(parent, "Close Vaultline?",
              "Keep it running in the tray (next to the clock) so the shortcut and the command line still "
              "work, or quit it completely?")
    remember = QCheckBox("Remember my choice (you can change it in Settings)")
    d.body.addWidget(remember)
    row = QHBoxLayout()
    row.addStretch()
    choice: dict = {}
    for text, value, name in (("Cancel", None, "Ghost"), ("Quit", "quit", "Ghost"), ("Keep in tray", "tray", "Primary")):
        b = QPushButton(text)
        b.setObjectName(name)
        if value is None:
            b.clicked.connect(d.reject)
        else:
            b.clicked.connect(lambda _=False, v=value: (choice.update(v=v), d.accept()))
        if value == "tray":
            b.setDefault(True)
        row.addWidget(b)
    d.lay.addLayout(row)
    if d.exec() != QDialog.Accepted or "v" not in choice:
        return None
    return choice["v"], remember.isChecked()


def ask_text(parent: QWidget, title: str, label: str, value: str = "", placeholder: str = "") -> str | None:
    d = _Base(parent, title)
    edit = QLineEdit(value)
    edit.setPlaceholderText(placeholder)
    lab = QLabel(label)
    lab.setObjectName("FieldLabel")
    d.body.addWidget(lab)
    d.body.addWidget(edit)
    ok = d.buttons("Save")

    def accept():
        if not edit.text().strip():
            d.fail("This can't be empty.", edit)
            return
        d.accept()
    ok.clicked.connect(accept)
    edit.selectAll()
    edit.setFocus()
    return edit.text().strip() if d.exec() == QDialog.Accepted else None


def ask_folder(parent: QWidget, title: str, text: str, groups, root_uuid: str,
               shared: set[str] | None = None, ok_text: str = "OK") -> str | None:
    """Pick a folder (or the top level). Returns its uuid, or None if cancelled."""
    from PySide6.QtWidgets import QComboBox
    from .widgets import mark_shared_folders
    d = _Base(parent, title, text)
    box = QComboBox()
    box.addItem("Top level", root_uuid)
    for g in groups:
        box.addItem(("      " * g.depth) + g.name, g.uuid)
    mark_shared_folders(box, shared or set())
    d.body.addWidget(box)
    ok = d.buttons(ok_text)
    ok.clicked.connect(d.accept)
    box.setFocus()
    return box.currentData() if d.exec() == QDialog.Accepted else None


def _field(d: _Base, label: str, password: bool = True, digits: bool = False) -> QLineEdit:
    lab = QLabel(label)
    lab.setObjectName("FieldLabel")
    e = QLineEdit()
    if password:
        e.setEchoMode(QLineEdit.Password)
    if digits:
        e.setMaxLength(12)
        e.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,12}"), e))
    d.body.addWidget(lab)
    d.body.addWidget(e)
    return e


def ask_new_pin(parent: QWidget) -> str | None:
    d = _Base(parent, "Set a PIN",
              "After you unlock with the master password, locking Vaultline will only ask for this PIN. "
              f"After {pinlib.MAX_TRIES} wrong tries, or when Vaultline restarts, the master password is needed.")
    p1 = _field(d, "PIN (4–12 digits)", digits=True)
    p2 = _field(d, "Repeat PIN", digits=True)
    ok = d.buttons("Set PIN")

    def accept():
        err = pinlib.valid(p1.text())
        if err:
            return d.fail(err, p1)
        if p1.text() != p2.text():
            return d.fail("The PINs don't match.", p2)
        d.accept()
    ok.clicked.connect(accept)
    p1.setFocus()
    return p1.text() if d.exec() == QDialog.Accepted else None


def ask_master_change(parent: QWidget, check_current) -> tuple[str, str] | None:
    """check_current(pw) -> bool. Returns (current, new) or None."""
    d = _Base(parent, "Change master password",
              "The database file is re-encrypted with the new password. Other devices will need the new "
              "password too.", width=440)
    cur = _field(d, "Current master password")
    new1 = _field(d, "New master password")
    new2 = _field(d, "Repeat new password")
    ok = d.buttons("Change password")

    def accept():
        if not check_current(cur.text()):
            return d.fail("The current master password is wrong.", cur)
        if len(new1.text()) < 8:
            return d.fail("Use at least 8 characters. A few random words work well.", new1)
        if new1.text() != new2.text():
            return d.fail("The new passwords don't match.", new2)
        if new1.text() == cur.text():
            return d.fail("That's the same as the current password.", new1)
        d.accept()
    ok.clicked.connect(accept)
    cur.setFocus()
    return (cur.text(), new1.text()) if d.exec() == QDialog.Accepted else None

