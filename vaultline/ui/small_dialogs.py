"""Small styled dialogs: confirm, text prompt, set PIN, change master password."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt
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
               shared: set[str] | None = None, ok_text: str = "OK", current: str | None = None,
               exclude: set[str] | None = None) -> str | None:
    """Pick a folder (or the top of a vault). Returns its uuid, or None if cancelled."""
    from .folder_picker import FolderPicker
    from .widgets import mark_shared_folders
    d = _Base(parent, title, text)
    box = FolderPicker(groups, root_uuid, exclude=exclude)
    if current:
        i = box.findData(current)
        box.setCurrentIndex(i if i >= 0 else 0)
    mark_shared_folders(box, shared or set())
    d.body.addWidget(box)
    ok = d.buttons(ok_text)
    ok.clicked.connect(d.accept)
    box.setFocus()
    return box.currentData() if d.exec() == QDialog.Accepted else None


def ask_move(parent: QWidget, what: str, groups, root_uuid: str, shared: set[str] | None = None,
             current: str | None = None, exclude: set[str] | None = None,
             copy: bool = False) -> tuple[str, bool] | None:
    """Where to move (or copy) `what`, in this vault or another. Returns (folder uuid, copy)."""
    from .folder_picker import FolderPicker
    from .widgets import mark_shared_folders
    d = _Base(parent, "Copy" if copy else "Move", f"{what} to:")
    box = FolderPicker(groups, root_uuid, exclude=exclude)
    if current:
        i = box.findData(current)
        box.setCurrentIndex(i if i >= 0 else 0)
    mark_shared_folders(box, shared or set())
    d.body.addWidget(box)
    keep = QCheckBox("Keep the original (copy instead of move)")
    keep.setChecked(copy)
    d.body.addWidget(keep)
    ok = d.buttons("Copy" if copy else "Move")
    keep.toggled.connect(lambda on: (ok.setText("Copy" if on else "Move"), d.setWindowTitle("Copy" if on else "Move")))

    def accept():
        if box.currentData() == current and not keep.isChecked():
            d.fail("That's where it is now.")
            return
        d.accept()
    ok.clicked.connect(accept)
    box.setFocus()
    return (box.currentData(), keep.isChecked()) if d.exec() == QDialog.Accepted else None


def ask_share(parent: QWidget, what: str, vaults: list[tuple[str, str]], checked: set[str],
              inherited: dict[str, str] | None = None, folder: bool = False) -> set[str] | None:
    """Tick the vaults to share with. `vaults`: (id, name); `inherited`: id -> the folder that shares
    it (still tickable: unticking keeps this one entry out). Returns the ticked ids."""
    inherited = inherited or {}
    text = (f"Everything in {what}, and whatever is added to it later, is kept in step in the vaults you tick."
            if folder else f"A linked copy of {what} is kept in each vault you tick. Your changes go out "
            "automatically; changes made there wait for you to review.")
    d = _Base(parent, "Share with", text, width=420)
    boxes = {}
    for vid, name in vaults:
        b = QCheckBox(name + (f"   (through folder {inherited[vid]})" if vid in inherited else ""))
        b.setChecked(vid in checked)
        d.body.addWidget(b)
        boxes[vid] = b
    note = QLabel("Unticking removes the copy there (to that vault's recycle bin). Someone who saw a "
                  "password may still know it.")
    note.setWordWrap(True)
    note.setObjectName("Hint")
    d.body.addWidget(note)
    ok = d.buttons("Save")
    ok.clicked.connect(d.accept)
    return {vid for vid, b in boxes.items() if b.isChecked()} if d.exec() == QDialog.Accepted else None


class ReviewShared(_Base):
    """Shared entries someone else changed or removed: the user decides each one."""

    def __init__(self, parent: QWidget, conflicts: list, names: dict[str, str], decide):
        super().__init__(parent, "Review shared entries",
                         "These were changed in a shared vault. Nothing is applied until you choose.", width=520)
        self._decide = decide
        self._rows = []
        for c in conflicts:
            self._add(c, names.get(c.vault, "a shared vault"))
        close = QPushButton("Close")
        close.setObjectName("Ghost")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        self.lay.addLayout(row)

    def _add(self, c, vault: str) -> None:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 4)
        if c.kind == "changed":
            what = ", ".join(c.fields) or "something"
            text = f"{c.title}\nchanged in {vault}: {what}"
            buttons = (("Use theirs", "theirs"), ("Keep mine", "mine"))
        elif c.kind == "deleted":
            text = f"{c.title}\nremoved from {vault}"
            buttons = (("Put it back", "restore"), (f"Stop sharing with {vault}", "unshare"))
        else:
            text = f"{c.title}\ncan't be shared with {vault}: {c.message}"
            buttons = ()
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        lay.addWidget(label, 1)
        for caption, choice in buttons:
            b = QPushButton(caption)
            b.clicked.connect(lambda _=False, ch=choice, row=w, c=c: self._pick(c, ch, row))
            lay.addWidget(b)
        self.body.addWidget(w)
        self._rows.append(w)

    def _pick(self, c, choice: str, row: QWidget) -> None:
        self._decide(c.main_uuid, c.vault, choice)
        row.setEnabled(False)
        self._rows.remove(row)
        row.hide()
        if not self._rows:
            self.accept()


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


def ask_shared_vault(parent: QWidget, path: str, existing: bool, name: str = "Shared") -> tuple[str, str] | None:
    """Name (and, for an existing file, master password) of a shared vault. Returns (name, password)."""
    from pathlib import Path
    text = (f"{Path(path).name} will open together with this database. Ask whoever made it for its password."
            if existing else
            f"A new database {Path(path).name} is made with a long random password, kept in an "
            "AutoOpen entry in this database. To share it, share its folder in Google Drive and "
            "give the other person that password once.")
    d = _Base(parent, "Open with this database" if existing else "Create a shared vault", text, width=440)
    lab = QLabel("Name in the folder list")
    lab.setObjectName("FieldLabel")
    d.body.addWidget(lab)
    title = QLineEdit(name)
    d.body.addWidget(title)
    pw = _field(d, "Its master password") if existing else None
    ok = d.buttons("Open" if existing else "Create")

    def accept():
        if not title.text().strip() or "/" in title.text():
            d.fail("Give it a name without /.", title)
            return
        if pw is not None and not pw.text():
            d.fail("Enter its master password.", pw)
            return
        d.accept()
    ok.clicked.connect(accept)
    (pw or title).setFocus()
    if d.exec() != QDialog.Accepted:
        return None
    return title.text().strip(), (pw.text() if pw is not None else "")


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

