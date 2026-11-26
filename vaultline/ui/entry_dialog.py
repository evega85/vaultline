"""Add / edit entry dialog: standard fields, folder, TOTP, custom fields."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression, QSize, Qt, QTimer
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from .. import icons, otp as otplib, passgen, theme
from ..vault import (CARD_BRAND_KEY, CARD_CODE_KEY, CARD_LOCAL_PHONE_KEY, CARD_PHONE_KEY, CARD_HOLDER_KEY, CARD_MONTH_KEY, CARD_NUMBER_KEY,
                      CARD_SLOT_KEYS, CARD_YEAR_KEY, LEGACY_CODE_KEY, LEGACY_EXPIRY_KEY, CustomField,
                      EntryEdit, EntryView, GroupView, expiry_from_parts, is_internal_field, parse_expiry)

NEW_FOLDER = "__new__"

# name -> (custom fields, standard fields to hide, relabels)
TEMPLATES = {
    "Login": ([], set(), {}),
    # Cards: number in the username slot, a password, and the card code where TOTP would be.
    "Card": ([], {"otp", "username"}, {"url": "Website (login page)"}),
    "API key": ([], {"otp"}, {"username": "Key ID / Client ID", "password": "Secret / API key",
                              "url": "Endpoint or dashboard URL"}),
    "Note": ([], {"username", "password", "url", "otp"}, {}),
}


def card_fields(number: str, code: str, exp: tuple[str, str] | None, year4: bool,
                holder: str, brand: str, phone: str = "", local_phone: str = "") -> list[CustomField]:
    """Card values as custom fields, in the KeePassXC/Bitwarden-import layout and a fixed order."""
    out = []
    if number:
        out.append(CustomField(CARD_NUMBER_KEY, number, True))
    if code:
        out.append(CustomField(CARD_CODE_KEY, code, True))
    if exp:
        out.append(CustomField(CARD_MONTH_KEY, exp[0], False))
        out.append(CustomField(CARD_YEAR_KEY, ("20" + exp[1]) if year4 else exp[1], False))
    if holder:
        out.append(CustomField(CARD_HOLDER_KEY, holder, False))
    if brand:
        out.append(CustomField(CARD_BRAND_KEY, brand, False))
    if phone:
        out.append(CustomField(CARD_PHONE_KEY, phone, False))
    if local_phone:
        out.append(CustomField(CARD_LOCAL_PHONE_KEY, local_phone, False))
    return out


def _label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("FieldLabel")
    return lab


def _small_tool(glyph: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.glyph(glyph, theme.TEXT_2, 15))
    b.setIconSize(QSize(15, 15))
    b.setToolTip(tip)
    b.setFixedSize(34, 34)
    return b


class _Section(QWidget):
    """Label + widget, hideable as one unit."""

    def __init__(self, label: str, widget: QWidget, hint: QLabel | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self.label = _label(label)
        lay.addWidget(self.label)
        lay.addWidget(widget)
        if hint is not None:
            lay.addWidget(hint)


class _CustomRow(QWidget):
    def __init__(self, dialog: "EntryDialog", key: str = "", value: str = "", protected: bool = False):
        super().__init__()
        self.dialog = dialog
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.key = QLineEdit(key)
        self.key.setPlaceholderText("Field name")
        self.key.setFixedWidth(150)
        self.value = QLineEdit(value)
        self.value.setPlaceholderText("Value")
        self.lock = QPushButton()
        self.lock.setObjectName("Hidden")
        self.lock.setCheckable(True)
        self.lock.setFixedWidth(72)
        self.lock.setCursor(Qt.PointingHandCursor)
        self.lock.setToolTip("Hidden: masked on screen and protected in the file, like a password.\n"
                             "Visible: plain text, like a username.")
        self.lock.toggled.connect(self._apply_lock)
        self.lock.setChecked(protected)
        self._apply_lock(protected)
        remove = _small_tool(icons.CLOSE, "Remove field")
        remove.clicked.connect(lambda: dialog.remove_custom_row(self))
        lay.addWidget(self.key)
        lay.addWidget(self.value, 1)
        lay.addWidget(self.lock)
        lay.addWidget(remove)

    def _apply_lock(self, on: bool) -> None:
        self.value.setEchoMode(QLineEdit.Password if on else QLineEdit.Normal)
        self.value.setObjectName("Mono" if on else "")
        self.value.style().polish(self.value)
        self.lock.setText("Hidden" if on else "Visible")

    def field(self) -> CustomField:
        return CustomField(self.key.text().strip(), self.value.text(), self.lock.isChecked())


class EntryDialog(QDialog):
    def __init__(self, parent: QWidget | None, view: EntryView | None = None,
                 draft: EntryEdit | None = None, error: str = "",
                 groups: list[GroupView] | None = None, root_uuid: str = "",
                 default_group: str | None = None):
        super().__init__(parent)
        self._uuid = draft.uuid if draft else (view.uuid if view else None)
        editing = self._uuid is not None
        self._groups = groups or []
        self._root_uuid = root_uuid
        self.setWindowTitle("Edit entry" if editing else "New entry")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.result_edit: EntryEdit | None = None
        self._original: EntryEdit | None = None
        self._rows: list[_CustomRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head_w = QWidget()
        head = QVBoxLayout(head_w)
        head.setContentsMargins(24, 20, 24, 8)
        head.setSpacing(12)
        title = QLabel("Edit entry" if editing else "New entry")
        title.setObjectName("DialogTitle")
        head.addWidget(title)
        if not editing:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            self._tpl_group = QButtonGroup(self)
            for name in TEMPLATES:
                b = QPushButton(name)
                b.setObjectName("Template")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.setFocusPolicy(Qt.NoFocus)
                b.clicked.connect(lambda _=False, n=name: self._apply_template(n))
                self._tpl_group.addButton(b)
                chips.addWidget(b)
                if name == "Login":
                    b.setChecked(True)
            chips.addStretch()
            head.addLayout(chips)
        outer.addWidget(head_w)

        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(24, 6, 24, 6)
        root.setSpacing(14)

        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. GitHub")
        root.addWidget(_Section("Title", self.title))

        # folder
        folder_w = QWidget()
        fl = QVBoxLayout(folder_w)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(6)
        self.folder = QComboBox()
        self.folder.addItem("Top level", root_uuid)
        for g in self._groups:
            self.folder.addItem(("      " * g.depth) + g.name, g.uuid)
        self.folder.addItem("＋ New folder…", NEW_FOLDER)
        self.new_folder = QLineEdit()
        self.new_folder.setPlaceholderText("Folder name (use / for a subfolder, e.g. Work/Clients)")
        self.new_folder.hide()
        self.folder.currentIndexChanged.connect(self._folder_changed)
        fl.addWidget(self.folder)
        fl.addWidget(self.new_folder)
        root.addWidget(_Section("Folder", folder_w))

        self.card_number = QLineEdit()
        self.card_number.setObjectName("Mono")
        self.card_number.setPlaceholderText("1234 5678 9012 3456")
        self.sec_card_number = _Section("Card number", self.card_number)
        self.sec_card_number.hide()
        root.addWidget(self.sec_card_number)

        self.username = QLineEdit()
        self.username.setPlaceholderText("Username or email")
        self.sec_username = _Section("Username", self.username)
        root.addWidget(self.sec_username)

        pw_wrap = QWidget()
        pw_row = QHBoxLayout(pw_wrap)
        pw_row.setContentsMargins(0, 0, 0, 0)
        pw_row.setSpacing(6)
        self.password = QLineEdit()
        self.password.setObjectName("Mono")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Password")
        self._eye = _small_tool(icons.EYE, "Show / hide")
        self._eye.setCheckable(True)
        self._eye.toggled.connect(self._toggle_pw)
        gen = _small_tool(icons.DICE, "Generate a strong password")
        gen.clicked.connect(self._generate)
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(self._eye)
        pw_row.addWidget(gen)
        self.sec_password = _Section("Password", pw_wrap)
        root.addWidget(self.sec_password)

        self.url = QLineEdit()
        self.url.setPlaceholderText("https://")
        self.sec_url = _Section("URL", self.url)
        root.addWidget(self.sec_url)

        self.card_phone = QLineEdit()
        self.card_phone.setPlaceholderText("Toll-free, e.g. 1-800-…")
        self.card_local_phone = QLineEdit()
        self.card_local_phone.setPlaceholderText("Local number")
        self.sec_phone = QWidget()
        phone_row = QHBoxLayout(self.sec_phone)
        phone_row.setContentsMargins(0, 0, 0, 0)
        phone_row.setSpacing(16)
        phone_row.addWidget(_Section("Phone", self.card_phone), 1)
        phone_row.addWidget(_Section("Local phone", self.card_local_phone), 1)
        self.sec_phone.hide()
        root.addWidget(self.sec_phone)

        self.otp = QLineEdit()
        self.otp.setObjectName("Mono")
        self.otp.setPlaceholderText("Base32 secret or otpauth://totp/… URI (optional)")
        self.otp_hint = QLabel()
        self.otp_hint.setObjectName("Hint")
        self.otp.textChanged.connect(self._check_otp)
        self.sec_otp = _Section("TOTP secret", self.otp, self.otp_hint)
        root.addWidget(self.sec_otp)

        code_wrap = QWidget()
        code_row = QHBoxLayout(code_wrap)
        code_row.setContentsMargins(0, 0, 0, 0)
        code_row.setSpacing(6)
        self.card_code = QLineEdit()
        self.card_code.setObjectName("Mono")
        self.card_code.setEchoMode(QLineEdit.Password)
        self.card_code.setPlaceholderText("CVV / security code")
        code_eye = _small_tool(icons.EYE, "Show / hide")
        code_eye.setCheckable(True)
        code_eye.toggled.connect(lambda on: self.card_code.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        code_row.addWidget(self.card_code, 1)
        code_row.addWidget(code_eye)

        exp_wrap = QWidget()
        exp_row = QHBoxLayout(exp_wrap)
        exp_row.setContentsMargins(0, 0, 0, 0)
        exp_row.setSpacing(6)
        two_digits = QRegularExpressionValidator(QRegularExpression(r"\d{0,2}"), self)
        self.exp_month = QLineEdit()
        self.exp_year = QLineEdit()
        for box, ph in ((self.exp_month, "MM"), (self.exp_year, "YY")):
            box.setObjectName("Mono")
            box.setPlaceholderText(ph)
            box.setMaxLength(2)
            box.setValidator(two_digits)
            box.setAlignment(Qt.AlignCenter)
            box.setFixedWidth(58)
        self.exp_month.textEdited.connect(lambda t: self.exp_year.setFocus() if len(t) == 2 else None)
        slash = QLabel("/")
        slash.setObjectName("Hint")
        exp_row.addWidget(self.exp_month)
        exp_row.addWidget(slash)
        exp_row.addWidget(self.exp_year)

        # expiry and card code side by side, as on the card
        self.sec_code = QWidget()
        card_row = QHBoxLayout(self.sec_code)
        card_row.setContentsMargins(0, 0, 0, 0)
        card_row.setSpacing(16)
        card_row.addWidget(_Section("Expiry", exp_wrap))
        card_row.addWidget(_Section("Card code", code_wrap), 1)
        self.sec_code.hide()
        root.addWidget(self.sec_code)

        self.card_holder = QLineEdit()
        self.card_holder.setPlaceholderText("Name on card")
        self.card_brand = QLineEdit()
        self.card_brand.setPlaceholderText("Visa, Mastercard…")
        self.sec_holder = QWidget()
        holder_row = QHBoxLayout(self.sec_holder)
        holder_row.setContentsMargins(0, 0, 0, 0)
        holder_row.setSpacing(16)
        holder_row.addWidget(_Section("Cardholder", self.card_holder), 2)
        holder_row.addWidget(_Section("Brand", self.card_brand), 1)
        self.sec_holder.hide()
        root.addWidget(self.sec_holder)
        self._card = False
        self._year4 = True  # write card_expYear as 2029 (as Bitwarden does) unless it was "29"

        # custom fields
        custom_w = QWidget()
        self._custom_lay = QVBoxLayout(custom_w)
        self._custom_lay.setContentsMargins(0, 0, 0, 0)
        self._custom_lay.setSpacing(6)
        add_field = QPushButton("＋ Add field")
        add_field.setObjectName("Link")
        add_field.setCursor(Qt.PointingHandCursor)
        add_field.clicked.connect(lambda: self.add_custom_row().key.setFocus())
        self._custom_lay.addWidget(add_field, 0, Qt.AlignLeft)
        root.addWidget(_Section("Custom fields", custom_w))

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes")
        self.notes.setFixedHeight(84)
        root.addWidget(_Section("Notes", self.notes))
        root.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        body.setStyleSheet("background: transparent;")
        outer.addWidget(scroll, 1)

        foot_w = QWidget()
        foot = QVBoxLayout(foot_w)
        foot.setContentsMargins(24, 8, 24, 18)
        self.error = QLabel(error)
        self.error.setObjectName("Error")
        self.error.setWordWrap(True)
        self.error.setVisible(bool(error))
        foot.addWidget(self.error)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.setDefault(True)
        save.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(save)
        foot.addLayout(btns)
        outer.addWidget(foot_w)

        # fill in values
        src = draft or (self._edit_from_view(view) if view else None)
        if src:
            self.title.setText(src.title)
            self.username.setText(src.username)
            self.password.setText(src.password)
            self.url.setText(src.url)
            self.notes.setPlainText(src.notes)
            self.otp.setText(src.otp if src.otp is not None else (view.otp_text if view else ""))
            is_card = src.card
            values = {f.key: f.value for f in (src.custom or ())}
            for f in (src.custom or ()):
                if not (is_card and f.key in CARD_SLOT_KEYS):
                    self.add_custom_row(f.key, f.value, f.protected)
            if is_card:
                self.card_number.setText(values.get(CARD_NUMBER_KEY, ""))
                self.card_code.setText(values.get(CARD_CODE_KEY, ""))
                self.card_holder.setText(values.get(CARD_HOLDER_KEY, ""))
                self.card_brand.setText(values.get(CARD_BRAND_KEY, ""))
                self.card_phone.setText(values.get(CARD_PHONE_KEY, ""))
                self.card_local_phone.setText(values.get(CARD_LOCAL_PHONE_KEY, ""))
                year = values.get(CARD_YEAR_KEY, "")
                exp = expiry_from_parts(values.get(CARD_MONTH_KEY, ""), year)
                if exp:
                    self.exp_month.setText(exp[0])
                    self.exp_year.setText(exp[1])
                    self._year4 = len(year) != 2
                self._set_card_mode(True)
        if view is not None:
            self._original = self._edit_from_view(view)
        group = (draft.group_uuid if draft and draft.group_uuid else None) or \
            (view.group_uuid if view else None) or default_group or root_uuid
        idx = self.folder.findData(group)
        self.folder.setCurrentIndex(idx if idx >= 0 else 0)
        if draft and draft.new_group:
            self.folder.setCurrentIndex(self.folder.findData(NEW_FOLDER))
            self.new_folder.setText(draft.new_group)

        self._check_otp()
        self._otp_timer = QTimer(self, interval=1000)
        self._otp_timer.timeout.connect(self._check_otp)
        self._otp_timer.start()
        screen = self.screen().availableGeometry() if self.screen() else None
        self.resize(560, min(820, int(screen.height() * 0.85)) if screen else 760)
        self.title.setFocus()

    @staticmethod
    def _edit_from_view(view: EntryView) -> EntryEdit:
        """The entry as the dialog would save it unchanged (so 'nothing changed' is exact)."""
        hidden = {view.password_alias} if view.password_alias else set()
        username = view.username
        card = []
        if view.is_card:
            hidden |= set(CARD_SLOT_KEYS) | {LEGACY_CODE_KEY}
            month, year = view.get_field(CARD_MONTH_KEY), view.get_field(CARD_YEAR_KEY)
            if not expiry_from_parts(month, year) and parse_expiry(view.get_field(LEGACY_EXPIRY_KEY)):
                hidden.add(LEGACY_EXPIRY_KEY)  # older layout: expiry lived in "Expiry"
            if not view.has_field(CARD_NUMBER_KEY):
                username = ""  # older layout: the number lived in the username
            card = card_fields(view.card_number, view.card_code, view.card_expiry, len(year.strip()) != 2,
                               view.card_holder, view.card_brand, view.card_phone, view.card_local_phone)
        custom = [f for f in view.custom if f.key not in hidden] + card
        return EntryEdit(view.uuid, view.title, username, view.password, view.url, view.notes,
                         view.otp_text, view.group_uuid, "", custom, card=view.is_card)

    def _set_card_mode(self, on: bool) -> None:
        self._card = on
        for w in (self.sec_card_number, self.sec_code, self.sec_holder, self.sec_phone):
            w.setVisible(on)
        if on:
            self.sec_username.hide()
            self.sec_otp.hide()
            self.sec_url.label.setText("Website (login page)")

    # ---- custom fields ---------------------------------------------------------
    def add_custom_row(self, key: str = "", value: str = "", protected: bool = False) -> _CustomRow:
        row = _CustomRow(self, key, value, protected)
        self._custom_lay.insertWidget(len(self._rows), row)
        self._rows.append(row)
        return row

    def remove_custom_row(self, row: _CustomRow) -> None:
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    # ---- templates ---------------------------------------------------------------
    def _apply_template(self, name: str) -> None:
        fields, hide, relabel = TEMPLATES[name]
        for b in self._tpl_group.buttons():
            b.setChecked(b.text() == name)
        for row in list(self._rows):  # drop untouched rows from a previous template
            if not row.value.text():
                self.remove_custom_row(row)
        have = {r.key.text().strip() for r in self._rows}
        for key, prot in fields:
            if key not in have:
                self.add_custom_row(key, "", prot)
        sections = {"username": self.sec_username, "password": self.sec_password,
                    "url": self.sec_url, "otp": self.sec_otp}
        defaults = {"username": "Username", "password": "Password", "url": "URL"}
        for k, sec in sections.items():
            sec.setVisible(k not in hide)
            if k in defaults:
                sec.label.setText(relabel.get(k, defaults[k]))
        self._set_card_mode(name == "Card")

    # ---- helpers ------------------------------------------------------------------
    def _folder_changed(self, _i: int) -> None:
        is_new = self.folder.currentData() == NEW_FOLDER
        self.new_folder.setVisible(is_new)
        if is_new:
            self.new_folder.setFocus()

    def _toggle_pw(self, show: bool) -> None:
        self.password.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self._eye.setIcon(icons.glyph(icons.EYE_OFF if show else icons.EYE, theme.TEXT_2, 15))

    def _generate(self) -> None:
        self.password.setText(passgen.generate(20))
        self._eye.setChecked(True)

    def _check_otp(self) -> bool:
        text = self.otp.text().strip()
        if not text:
            self.otp_hint.setText("Stored in the standard KeePassXC / KeePassium “otp” field.")
            self.otp_hint.setStyleSheet("")
            self.otp.setProperty("error", False)
            self.otp.style().polish(self.otp)
            return True
        try:
            uri = otplib.normalize(text, self.title.text(), self.username.text())
            totp = otplib.parse_uri(uri)
            code, frac = otplib.current(totp)
            secs = int(frac * totp.interval + 0.999)
            self.otp_hint.setText(f"Current code  {otplib.pretty(code)}   ·   {secs}s")
            self.otp_hint.setStyleSheet(f"color: {theme.SUCCESS};")
            ok = True
        except otplib.OtpError as exc:
            self.otp_hint.setText(str(exc))
            self.otp_hint.setStyleSheet(f"color: {theme.DANGER};")
            ok = False
        self.otp.setProperty("error", not ok)
        self.otp.style().polish(self.otp)
        return ok

    def _fail(self, msg: str, focus: QWidget) -> None:
        self.error.setText(msg)
        self.error.show()
        focus.setFocus()

    def _accept(self) -> None:
        title = self.title.text().strip()
        if not title:
            return self._fail("Give the entry a title.", self.title)
        if not self._check_otp():
            return self._fail("Fix the TOTP secret or leave it empty.", self.otp)

        custom, seen = [], set()
        for row in self._rows:
            f = row.field()
            if not f.key and not f.value:
                continue
            if not f.key:
                return self._fail("Every custom field needs a name.", row.key)
            if is_internal_field(f.key) or (self._card and f.key in CARD_SLOT_KEYS):
                return self._fail(f"“{f.key}” is a reserved field name. Pick another.", row.key)
            if f.key in seen:
                return self._fail(f"There are two fields named “{f.key}”.", row.key)
            seen.add(f.key)
            custom.append(f)

        group_uuid, new_group = self.folder.currentData(), ""
        if group_uuid == NEW_FOLDER:
            new_group = self.new_folder.text().strip().strip("/")
            if not new_group:
                return self._fail("Name the new folder.", self.new_folder)
            group_uuid = None

        if self._card:
            mm, yy = self.exp_month.text().strip(), self.exp_year.text().strip()
            exp = None
            if mm or yy:
                if not (mm.isdigit() and 1 <= int(mm) <= 12):
                    return self._fail("Expiry month should be 01 to 12.", self.exp_month)
                if len(yy) != 2:
                    return self._fail("Expiry year should be two digits, e.g. 29.", self.exp_year)
                exp = (f"{int(mm):02d}", yy)
            custom += card_fields(self.card_number.text().strip(), self.card_code.text().strip(), exp,
                                  self._year4, self.card_holder.text().strip(), self.card_brand.text().strip(),
                                  self.card_phone.text().strip(), self.card_local_phone.text().strip())

        otp_text = self.otp.text().strip()
        if self._card or (self._original and otp_text == (self._original.otp or "").strip()):
            otp_uri = None  # unchanged: leave the stored OTP fields exactly as they are
        else:
            otp_uri = otplib.normalize(otp_text, title, self.username.text().strip())

        edit = EntryEdit(self._uuid, title, self.username.text().strip(), self.password.text(),
                         self.url.text().strip(), self.notes.toPlainText(), otp_uri,
                         group_uuid, new_group, custom, card=self._card)
        if self._original:
            orig = EntryEdit(**{**self._original.__dict__, "otp": None})
            if edit == orig:
                self.reject()  # nothing changed, no write needed
                return
        self.result_edit = edit
        self.accept()
