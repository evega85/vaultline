"""Add / edit entry dialog: standard fields, folder, TOTP, custom fields."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QRegularExpression, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QRegularExpressionValidator
from PySide6.QtWidgets import (QButtonGroup, QCompleter, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QStyle, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget)

from .. import icons, identity as idlib, otp as otplib, passgen, theme
from .folder_picker import FolderPicker
from .widgets import ErrorLabel, mark_shared_folders
from ..vault import (CARD_BRAND_KEY, CARD_CODE_KEY, CARD_LOCAL_PHONE_KEY, CARD_PHONE_KEY, CARD_HOLDER_KEY, CARD_MONTH_KEY, CARD_NUMBER_KEY,
                      CARD_SLOT_KEYS, CARD_YEAR_KEY, LEGACY_CODE_KEY, LEGACY_EXPIRY_KEY, CustomField,
                      EntryEdit, EntryView, GroupView, expiry_from_parts, is_internal_field, is_url_field, parse_expiry)

NEW_FOLDER = "__new__"

# name -> (custom fields, standard fields to hide, relabels)
TEMPLATES = {
    "Login": ([], set(), {}),
    # Cards: number in the username slot, a password, and the card code where TOTP would be.
    "Card": ([], {"otp", "username"}, {"url": "Website (login page)"}),
    # Sign-up profile: identity_* fields (see identity.py), filled one box at a time.
    "Identity": ([], {"otp", "username", "password", "url"}, {}),
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


def identity_fields(values: dict[str, str]) -> list[CustomField]:
    """Profile values as custom fields, in a fixed order; empty ones are left out."""
    return [CustomField(k, values[k], k in idlib.PROTECTED) for k in idlib.KEYS if values.get(k, "").strip()]


def known_usernames(views) -> list[str]:
    """Usernames already in the vault, most used first, for the Username field's suggestions.

    Cards are skipped (older layouts kept the card number in the username) and so are references.
    """
    counts: dict[str, int] = {}
    spelling: dict[str, str] = {}
    for v in views:
        name = (v.username or "").strip()
        if not name or v.is_card or "username" in v.references or name.upper().startswith("{REF:"):
            continue
        key = name.casefold()
        counts[key] = counts.get(key, 0) + 1
        spelling.setdefault(key, name)
    return [spelling[k] for k in sorted(counts, key=lambda k: (-counts[k], k))]


class _SuggestionDelegate(QStyledItemDelegate):
    """Draws an × on the highlighted suggestion, like Outlook's address list; red under the mouse."""
    X_WIDTH = 36
    X_BOX = 24
    X_GLYPH = 13

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hot_row = -1  # row whose × the mouse is over

    @classmethod
    def x_rect(cls, rect: QRect) -> QRect:
        return QRect(rect.right() - cls.X_WIDTH + 1, rect.top(), cls.X_WIDTH, rect.height())

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if not option.state & (QStyle.State_MouseOver | QStyle.State_Selected):
            return
        centre = self.x_rect(option.rect).center()
        hot = index.row() == self.hot_row
        if hot:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.DANGER_STRONG))
            disc = QRect(0, 0, self.X_BOX, self.X_BOX)
            disc.moveCenter(centre)
            painter.drawRoundedRect(disc, 6, 6)
            painter.restore()
        box = QRect(0, 0, self.X_GLYPH, self.X_GLYPH)
        box.moveCenter(centre)
        icons.glyph(icons.CLOSE, "#ffffff" if hot else theme.TEXT, self.X_GLYPH).paint(painter, box)


class _SuggestionRemover(QObject):
    """Clicking a suggestion's × (or Delete on the highlighted one) stops suggesting it."""

    def __init__(self, completer: QCompleter, on_hide):
        super().__init__(completer)
        self.completer, self.on_hide = completer, on_hide
        popup = completer.popup()
        popup.setMouseTracking(True)
        self.delegate = _SuggestionDelegate(popup)
        popup.setItemDelegate(self.delegate)
        popup.setToolTip("Click × or press Delete to stop suggesting this one")
        popup.installEventFilter(self)  # installed after QCompleter's own filter, so it runs first
        popup.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        popup = self.completer.popup()
        t = ev.type()
        if obj is popup.viewport() and t in (QEvent.MouseMove, QEvent.Leave, QEvent.Hide):
            hot = -1
            if t == QEvent.MouseMove:
                pos = ev.position().toPoint()
                idx = popup.indexAt(pos)
                if idx.isValid() and _SuggestionDelegate.x_rect(popup.visualRect(idx)).contains(pos):
                    hot = idx.row()
            if hot != self.delegate.hot_row:
                self.delegate.hot_row = hot
                popup.viewport().update()
            return False
        if obj is popup.viewport() and t in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                                             QEvent.MouseButtonDblClick):
            pos = ev.position().toPoint()
            idx = popup.indexAt(pos)
            if idx.isValid() and _SuggestionDelegate.x_rect(popup.visualRect(idx)).contains(pos):
                if t == QEvent.MouseButtonRelease:
                    self.remove(idx.data())
                return True
        elif obj is popup and t == QEvent.KeyPress and ev.key() == Qt.Key_Delete:
            idx = popup.currentIndex()
            if popup.isVisible() and idx.isValid():
                self.remove(idx.data())
                return True
        return False

    def remove(self, name: str) -> None:
        model = self.completer.model()
        model.setStringList([n for n in model.stringList() if n != name])
        self.delegate.hot_row = -1
        self.on_hide(name)
        if self.completer.completionCount():
            self.completer.complete()  # refresh the list and its size
        else:
            self.completer.popup().hide()


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
                 default_group: str | None = None, usernames: list[str] | None = None,
                 hide_username=None, copy_secret=None, shared: set[str] | None = None, generate=None,
                 generator_config=None):
        super().__init__(parent)
        self._new_password = generate or passgen.generate  # Settings > Password generator
        self._uuid = draft.uuid if draft else (view.uuid if view else None)
        editing = self._uuid is not None
        self._groups = groups or []
        self._root_uuid = root_uuid
        self.setWindowTitle("Edit entry" if editing else "New entry")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.result_edit: EntryEdit | None = None
        self._original: EntryEdit | None = None
        self._copy_secret = copy_secret  # copies with auto-clear; returns the seconds until it clears
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
        self.folder = FolderPicker(self._groups, root_uuid, extras=[("＋ New folder…", NEW_FOLDER)])
        mark_shared_folders(self.folder, shared or set())
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
        if usernames:
            # Like Outlook's address box: start typing and earlier usernames/emails are offered.
            comp = QCompleter(usernames, self.username)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            comp.setModelSorting(QCompleter.UnsortedModel)  # keep most-used first
            comp.setMaxVisibleItems(8)
            comp.popup().setObjectName("Suggestions")
            self.username.setCompleter(comp)
            if hide_username is not None:
                self._suggestions = _SuggestionRemover(comp, hide_username)
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
        # Generate; after a password is made it turns into Copy, and back to Generate once clicked
        self.gen_btn = gen = _small_tool(icons.DICE, "Generate a strong password")
        self._generated = ""  # the password Copy would copy; "" = the button generates
        gen.clicked.connect(self._gen_clicked)
        self.password.textEdited.connect(lambda _t: self._set_gen_mode(""))
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(self._eye)
        pw_row.addWidget(gen)
        self.gen_options = None
        if generator_config is not None:  # the generator's options, beside it
            from .gen_options import gear_button
            self.gen_options = gear_button(generator_config, self._generate)
            pw_row.addWidget(self.gen_options)
        self.pw_copied = QLabel()
        self.pw_copied.setObjectName("Hint")
        self.pw_copied.setWordWrap(True)
        self.pw_copied.hide()
        self._pw_copied_timer = QTimer(self, singleShot=True, interval=4000, timeout=self.pw_copied.hide)
        self.sec_password = _Section("Password", pw_wrap, self.pw_copied)
        root.addWidget(self.sec_password)

        self.url = QLineEdit()
        self.url.setPlaceholderText("https://")
        url_w = QWidget()
        self._url_lay = QVBoxLayout(url_w)
        self._url_lay.setContentsMargins(0, 0, 0, 0)
        self._url_lay.setSpacing(6)
        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        url_row.addWidget(self.url, 1)
        self.add_url_btn = _small_tool(icons.ADD, "Add another website for this login")
        self.add_url_btn.clicked.connect(lambda: self.add_url_row("", focus=True))
        url_row.addWidget(self.add_url_btn)
        self._url_lay.addLayout(url_row)
        self._url_rows: list[tuple[QWidget, QLineEdit]] = []
        self.sec_url = _Section("URL", url_w)
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
        self.otp_hint.setWordWrap(True)  # the form also opens inside the narrow (compact) window
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
        self._year4 = True

        # sign-up profile: one box per field, typed one at a time by Fill
        self.id_boxes: dict[str, QLineEdit] = {}
        self.sec_identity = QWidget()
        id_lay = QVBoxLayout(self.sec_identity)
        id_lay.setContentsMargins(0, 0, 0, 0)
        id_lay.setSpacing(14)
        placeholders = {idlib.EMAIL: "name@example.com", idlib.PHONE: "416-555-0123",
                        idlib.BIRTH: "YYYY-MM-DD", idlib.GENDER: "e.g. Male", idlib.STATE: "ON",
                        idlib.POSTAL: "A1A 1A1",
                        idlib.COUNTRY: "Canada"}
        for key in idlib.KEYS:
            box = QLineEdit()
            box.setPlaceholderText(placeholders.get(key, ""))
            self.id_boxes[key] = box
        birth = self.id_boxes[idlib.BIRTH]
        birth.setObjectName("Mono")
        birth.setEchoMode(QLineEdit.Password)
        birth_wrap = QWidget()
        birth_row = QHBoxLayout(birth_wrap)
        birth_row.setContentsMargins(0, 0, 0, 0)
        birth_row.setSpacing(6)
        birth_eye = _small_tool(icons.EYE, "Show / hide")
        birth_eye.setCheckable(True)
        birth_eye.toggled.connect(lambda on: birth.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        birth_row.addWidget(birth, 1)
        birth_row.addWidget(birth_eye)
        for keys in ((idlib.FIRST, idlib.LAST), (idlib.EMAIL,), (idlib.PHONE,), (idlib.BIRTH, idlib.GENDER),
                     (idlib.ADDRESS,),
                     (idlib.CITY, idlib.STATE), (idlib.POSTAL, idlib.COUNTRY)):
            line = QHBoxLayout()
            line.setSpacing(16)
            for key in keys:
                widget = birth_wrap if key == idlib.BIRTH else self.id_boxes[key]
                line.addWidget(_Section(idlib.LABELS[key], widget), 1)
            id_lay.addLayout(line)
        self.id_hint = QLabel("The name and address were split up from the imported fields. "
                              "Check them before you save.")
        self.id_hint.setObjectName("Hint")
        self.id_hint.setWordWrap(True)
        self.id_hint.hide()
        id_lay.addWidget(self.id_hint)
        self.sec_identity.hide()
        root.addWidget(self.sec_identity)
        self._identity = False  # write card_expYear as 2029 (as Bitwarden does) unless it was "29"

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
        self.error = ErrorLabel(error)
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
            for u in src.extra_urls or ():
                self.add_url_row(u)
            self.notes.setPlainText(src.notes)
            self.otp.setText(src.otp if src.otp is not None else (view.otp_text if view else ""))
            is_card = src.card
            values = {f.key: f.value for f in (src.custom or ())}
            is_identity = not is_card and any(idlib.is_identity_key(k) for k in values)
            split = self._split_imported(values) if is_identity else {}
            for f in (src.custom or ()):
                if is_card and f.key in CARD_SLOT_KEYS:
                    continue
                if is_identity and (f.key in idlib.KEYS or f.key in split.get("_drop", ())):
                    continue
                self.add_custom_row(f.key, f.value, f.protected)
            if is_identity:
                for key, box in self.id_boxes.items():
                    box.setText(split.get(key) or values.get(key, ""))
                self.id_hint.setVisible(bool(split.get("_drop")))
                self._set_identity_mode(True, keep={"username": bool(src.username), "password": bool(src.password),
                                                    "url": bool(src.url), "otp": bool(view and view.otp_text)})
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

    def add_url_row(self, text: str = "", focus: bool = False) -> QLineEdit:
        """Another website for this login (stored as KeePassXC's KP2A_URL fields)."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        edit = QLineEdit(text)
        edit.setPlaceholderText("https:// (another address for the same login)")
        remove = _small_tool(icons.CLOSE, "Remove this website")
        lay.addWidget(edit, 1)
        lay.addWidget(remove)
        pair = (row, edit)
        self._url_rows.append(pair)

        def drop():
            self._url_rows.remove(pair)
            self._url_lay.removeWidget(row)
            row.deleteLater()
            self.url.setFocus()
        remove.clicked.connect(drop)
        self._url_lay.addWidget(row)
        if focus:
            edit.setFocus()
        return edit

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
        ident = []
        if view.is_identity:
            hidden |= set(idlib.KEYS)
            ident = identity_fields({k: view.get_field(k) for k in idlib.KEYS})
        custom = [f for f in view.custom if f.key not in hidden] + card + ident
        return EntryEdit(view.uuid, view.title, username, view.password, view.url, view.notes,
                         view.otp_text, view.group_uuid, "", custom, card=view.is_card,
                         extra_urls=list(view.extra_urls))

    @staticmethod
    def _split_imported(values: dict[str, str]) -> dict:
        """KeePassXC's joined identity_name / identity_address, split into the profile's boxes when those
        are empty. "_drop" lists the imported keys that the split replaces."""
        out: dict = {"_drop": []}
        name = values.get(idlib.IMPORTED_NAME, "")
        if name.strip() and not (values.get(idlib.FIRST, "").strip() or values.get(idlib.LAST, "").strip()):
            out[idlib.FIRST], out[idlib.LAST] = idlib.split_name(name)
            out["_drop"].append(idlib.IMPORTED_NAME)
        addr = values.get(idlib.IMPORTED_ADDRESS, "")
        parts = (idlib.ADDRESS, idlib.CITY, idlib.STATE, idlib.POSTAL, idlib.COUNTRY)
        if addr.strip() and not any(values.get(k, "").strip() for k in parts):
            out.update(idlib.split_address(addr))
            out["_drop"].append(idlib.IMPORTED_ADDRESS)
        return out

    def _set_identity_mode(self, on: bool, keep: dict | None = None) -> None:
        """Profile form. `keep`: standard boxes left showing because the entry already uses them."""
        self._identity = on
        self.sec_identity.setVisible(on)
        if on:
            keep = keep or {}
            for k, sec in (("username", self.sec_username), ("password", self.sec_password),
                           ("url", self.sec_url), ("otp", self.sec_otp)):
                sec.setVisible(bool(keep.get(k)))

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
        self._set_identity_mode(name == "Identity")

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
        self.password.setText(self._new_password())
        self._eye.setChecked(True)
        if self._copy_secret is not None:  # next click copies it for the site's "new password" box
            self._set_gen_mode(self.password.text())

    def _gen_clicked(self) -> None:
        if self._generated and self._generated == self.password.text():
            self._copy_generated()
        else:
            self._generate()

    def _copy_generated(self) -> None:
        seconds = self._copy_secret(self.password.text())
        self.pw_copied.setText(f"Copied. The clipboard clears in {seconds} s.")
        self.pw_copied.show()
        self._pw_copied_timer.start()
        self._set_gen_mode("")

    def _set_gen_mode(self, generated: str) -> None:
        self._generated = generated
        copy = bool(generated)
        self.gen_btn.setIcon(icons.glyph(icons.COPY if copy else icons.DICE, theme.TEXT_2, 15))
        self.gen_btn.setToolTip("Copy the new password" if copy else "Generate a strong password")

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
            if is_internal_field(f.key) or is_url_field(f.key) or (self._card and f.key in CARD_SLOT_KEYS) or \
                    (self._identity and f.key in idlib.KEYS):
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

        if self._identity:
            values = {k: box.text().strip() for k, box in self.id_boxes.items()}
            birth = values[idlib.BIRTH]
            if birth:
                d = idlib.parse_birth_date(birth)
                if d is None:
                    return self._fail("Write the date of birth as YYYY-MM-DD, e.g. 1990-01-31.",
                                      self.id_boxes[idlib.BIRTH])
                values[idlib.BIRTH] = idlib.format_date(d, "iso")
            if not any(values.values()):
                return self._fail("Fill in at least one profile field.", self.id_boxes[idlib.FIRST])
            custom += identity_fields(values)

        otp_text = self.otp.text().strip()
        if self._card or (self._original and otp_text == (self._original.otp or "").strip()):
            otp_uri = None  # unchanged: leave the stored OTP fields exactly as they are
        else:
            otp_uri = otplib.normalize(otp_text, title, self.username.text().strip())

        edit = EntryEdit(self._uuid, title, self.username.text().strip(), self.password.text(),
                         self.url.text().strip(), self.notes.toPlainText(), otp_uri,
                         group_uuid, new_group, custom, card=self._card,
                         extra_urls=[e.text().strip() for _w, e in self._url_rows if e.text().strip()])
        if self._original:
            orig = EntryEdit(**{**self._original.__dict__, "otp": None})
            if edit == orig:
                self.reject()  # nothing changed, no write needed
                return
        self.result_edit = edit
        self.accept()
