"""New login for the window you came from (a sign-up page): title from the window title, your
profile's email as the username, a generated password. AutoTypeFlow saves it first and only types
once the save has worked, so the password is never lost if the sign-up fails."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import icons, identity as idlib, passgen
from ..vault import EntryEdit, EntryView, GroupView
from .entry_dialog import _Section, _small_tool
from .folder_picker import FolderPicker
from .widgets import ErrorLabel, mark_shared_folders

PASSWORD_LENGTH = 20


class NewLoginDialog(QDialog):
    def __init__(self, parent: QWidget | None, window_title: str, site: str, profiles: list[EntryView],
                 profile_uuid: str = "", groups: list[GroupView] | None = None, root_uuid: str = "",
                 folder_uuid: str = "", generate=None, shared: set[str] | None = None,
                 url: str = "", generator_config=None):
        super().__init__(parent)
        self.setWindowTitle("New login")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.result_edit: EntryEdit | None = None
        self._new_password = generate or passgen.generate  # Settings > Password generator
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        head = QLabel("New login for this window")
        head.setObjectName("DialogTitle")
        lay.addWidget(head)
        where = QLabel()
        where.setObjectName("Hint")
        where.setWordWrap(True)
        where.setTextFormat(Qt.PlainText)  # a page title: never drawn as HTML
        where.setText(f"Saved first, then typed into “{window_title or 'the window you came from'}”. "
                      "If the save fails, nothing is typed.")
        lay.addWidget(where)

        self.title = QLineEdit(site)
        self.title.setPlaceholderText("e.g. GitHub")
        lay.addWidget(_Section("Title", self.title))

        self.profile = QComboBox()
        with_email = [p for p in profiles if p.get_field(idlib.EMAIL).strip()]
        self._profile_uuids = [p.uuid for p in with_email]
        for p in with_email:
            email = p.get_field(idlib.EMAIL).strip()
            self.profile.addItem(f"{p.title or 'Profile'} · {email}", email)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Email or username")
        user_w = QWidget()
        user_row = QVBoxLayout(user_w)
        user_row.setContentsMargins(0, 0, 0, 0)
        user_row.setSpacing(6)
        if self.profile.count():
            self.profile.setCurrentIndex(next((i for i, p in enumerate(with_email) if p.uuid == profile_uuid), 0))
            self.profile.currentIndexChanged.connect(lambda _i: self.username.setText(self.profile.currentData()))
            user_row.addWidget(self.profile)
            self.username.setText(self.profile.currentData())
        user_row.addWidget(self.username)
        if not self.profile.count():
            tip = QLabel("Add a profile (New entry > Identity) and its email is filled in here.")
            tip.setObjectName("Hint")
            tip.setWordWrap(True)
            user_row.addWidget(tip)
        lay.addWidget(_Section("Username", user_w))

        pw_w = QWidget()
        pw_row = QHBoxLayout(pw_w)
        pw_row.setContentsMargins(0, 0, 0, 0)
        pw_row.setSpacing(6)
        self.password = QLineEdit(self._new_password())
        self.password.setObjectName("Mono")
        self.password.setEchoMode(QLineEdit.Password)
        eye = _small_tool(icons.EYE, "Show / hide")
        eye.setCheckable(True)
        eye.toggled.connect(lambda on: self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        again = _small_tool(icons.DICE, "Generate another")
        again.clicked.connect(lambda: self.password.setText(self._new_password()))
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(eye)
        pw_row.addWidget(again)
        self.gen_options = None
        if generator_config is not None:  # the generator's options, beside it
            from .gen_options import gear_button
            self.gen_options = gear_button(generator_config, lambda: self.password.setText(self._new_password()))
            pw_row.addWidget(self.gen_options)
        lay.addWidget(_Section("Password", pw_w))

        self.folder = FolderPicker(groups or [], root_uuid)
        i = self.folder.findData(folder_uuid) if folder_uuid else -1
        self.folder.setCurrentIndex(i if i >= 0 else 0)
        mark_shared_folders(self.folder, shared or set())
        lay.addWidget(_Section("Folder", self.folder))

        self.url = QLineEdit(url)  # the site part of the browser's address bar
        self.url.setPlaceholderText("https:// (optional: copy it from the address bar)")
        lay.addWidget(_Section("URL", self.url))

        self.error = ErrorLabel()
        self.error.hide()
        lay.addWidget(self.error)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save and fill")
        self.save_btn.setObjectName("Primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(self.save_btn)
        lay.addLayout(btns)
        self.title.setFocus()
        self.title.selectAll()

    @property
    def profile_uuid(self) -> str:
        """The profile picked for this sign-up ("" if none): its name, phone and address fill the form."""
        i = self.profile.currentIndex()
        return self._profile_uuids[i] if 0 <= i < len(self._profile_uuids) else ""

    def _accept(self) -> None:
        title = self.title.text().strip()
        if not title:
            self.error.setText("Give the login a title.")
            self.error.show()
            self.title.setFocus()
            return
        if not self.password.text():
            self.error.setText("The password is empty. Press the dice for a new one.")
            self.error.show()
            self.password.setFocus()
            return
        self.result_edit = EntryEdit(None, title, self.username.text().strip(), self.password.text(),
                                     self.url.text().strip(), "",
                                     None, self.folder.currentData() or None)
        self.accept()
