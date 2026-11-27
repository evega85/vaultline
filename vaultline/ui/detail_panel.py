"""Slide-over panel showing every field of the selected entry."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)

from .. import icons, identity as idlib, otp as otplib, theme
from ..vault import CARD_SLOT_KEYS, is_toll_free, CARD_TAG, LEGACY_CODE_KEY, LEGACY_EXPIRY_KEY, EntryView
from .widgets import Avatar

_MASK = "•" * 12
_OPENABLE = ("http://", "https://", "ftp://", "mailto:")


def _breakable(text: str) -> str:
    """Let very long tokens (API keys, tokens) wrap inside a QLabel."""
    return " ".join(
        "\u200b".join(tok[i:i + 8] for i in range(0, len(tok), 8)) if len(tok) > 24 else tok
        for tok in text.split(" "))


def _tool(glyph: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.glyph(glyph, theme.TEXT_2, 15))
    b.setIconSize(QSize(15, 15))
    b.setToolTip(tip)
    b.setFixedSize(30, 30)
    b.setFocusPolicy(Qt.NoFocus)
    b.setCursor(Qt.PointingHandCursor)
    return b


class _Field(QWidget):
    def __init__(self, label: str, value: str, panel: "DetailPanel", secret: bool = False,
                 mono: bool = False, url: bool = False, wrap: bool = False, copy_value: str | None = None,
                 fill: str = "", fill_options: list[tuple[str, str]] | None = None):
        super().__init__()
        self._value, self._secret, self._shown = value, secret, not secret
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 8, 10)
        lay.setSpacing(3)
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        lab.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px; font-weight: 600;")
        lay.addWidget(lab)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.text = QLabel()
        self.text.setWordWrap(True)
        if wrap:  # notes: free text, selectable. Other values use the copy button,
            # since their display may contain invisible wrap points.
            self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.text.setCursor(Qt.IBeamCursor)
        style = "font-size: 13px;"
        if mono:
            style += f' font-family: "{theme.MONO_FONT}", Consolas, monospace;'
        if url:
            style += f" color: {theme.ACCENT_SOFT};"
        self.text.setStyleSheet(style)
        self._wrap = wrap
        row.addWidget(self.text, 1)
        if secret:
            self.eye = _tool(icons.EYE, "Show / hide")
            self.eye.clicked.connect(self._toggle)
            row.addWidget(self.eye, 0, Qt.AlignTop)
        if url and value.lower().startswith(_OPENABLE):
            b = _tool(icons.OPEN, "Open in browser")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(value)))
            row.addWidget(b, 0, Qt.AlignTop)
        if fill_options:  # typed in a choice of formats (identity.fill_options): a token per format
            row.addWidget(panel.field_fill_button("", label.lower(), fill_options), 0, Qt.AlignTop)
        elif fill:
            row.addWidget(panel.field_fill_button(fill, label.lower()), 0, Qt.AlignTop)
        copy = _tool(icons.COPY, f"Copy {label.lower()}")
        to_copy = value if copy_value is None else copy_value
        copy.clicked.connect(lambda: panel.copy_text.emit(label, to_copy))
        row.addWidget(copy, 0, Qt.AlignTop)
        lay.addLayout(row)
        self._render()

    def _toggle(self) -> None:
        self._shown = not self._shown
        self.eye.setIcon(icons.glyph(icons.EYE_OFF if self._shown else icons.EYE, theme.TEXT_2, 15))
        self._render()

    def _render(self) -> None:
        if not self._shown:
            self.text.setText(_MASK)
        else:
            self.text.setText(self._value if self._wrap else _breakable(self._value))


class _OtpField(QWidget):
    def __init__(self, view: EntryView, panel: "DetailPanel"):
        super().__init__()
        self.totp = view.totp
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 8, 10)
        lay.setSpacing(3)
        lab = QLabel("One-time code")
        lab.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px; font-weight: 600;")
        lay.addWidget(lab)
        row = QHBoxLayout()
        self.code = QLabel()
        self.code.setStyleSheet(f'font-family: "{theme.MONO_FONT}", Consolas; font-size: 20px; font-weight: 600;')
        self.secs = QLabel()
        self.secs.setObjectName("Hint")
        row.addWidget(self.code)
        row.addSpacing(8)
        row.addWidget(self.secs)
        row.addStretch()
        if not view.is_card:
            row.addWidget(panel.field_fill_button("otp", "one-time code"))
        copy = _tool(icons.COPY, "Copy code (Ctrl+T)")
        copy.clicked.connect(lambda: panel.copy_text.emit("OTP", otplib.current(self.totp)[0]))
        row.addWidget(copy)
        lay.addLayout(row)
        self.tick()

    def tick(self) -> None:
        code, frac = otplib.current(self.totp)
        secs = int(frac * self.totp.interval + 0.999)
        self.code.setText(otplib.pretty(code))
        self.secs.setText(f"{secs}s")
        color = theme.WARN if secs <= 5 else theme.TEXT
        self.code.setStyleSheet(f'font-family: "{theme.MONO_FONT}", Consolas; font-size: 20px; '
                                f'font-weight: 600; color: {color};')


def _group_box(widgets: list[QWidget]) -> QFrame:
    box = QFrame()
    box.setObjectName("FieldGroup")
    box.setStyleSheet(f"#FieldGroup {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER}; "
                      f"border-radius: 10px; }}")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    for i, w in enumerate(widgets):
        if i:
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {theme.BORDER};")
            lay.addWidget(line)
        lay.addWidget(w)
    return box


def _fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    try:
        return dt.astimezone().strftime("%b %d, %Y  %H:%M")
    except (ValueError, OSError):
        return str(dt)


class DetailPanel(QFrame):
    copy_text = Signal(str, str)      # label, value
    edit_requested = Signal(object)   # EntryView
    delete_requested = Signal(object)  # EntryView
    restore_requested = Signal(object)  # EntryView in the recycle bin
    fill_requested = Signal(object)   # EntryView: type it into the window you came from
    # EntryView, "username" | "password" | "otp" | an identity.fill_options token: just that field
    fill_field_requested = Signal(object, str)
    closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("DetailPanel")
        self.setStyleSheet(f"#DetailPanel {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
                           f"border-radius: 12px; }}")
        shadow = QGraphicsDropShadowEffect(self, blurRadius=32, offset=QPoint(0, 6))
        shadow.setColor(QColor(0, 0, 0, 150))
        self.setGraphicsEffect(shadow)
        self.view: EntryView | None = None
        self._field_fills: list[QToolButton] = []  # the keyboard icons beside fields
        self._fill_title = ""
        self._otp: _OtpField | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # header
        head = QWidget()
        h = QHBoxLayout(head)
        h.setContentsMargins(16, 14, 10, 12)
        h.setSpacing(12)
        self._avatar_slot = QHBoxLayout()
        h.addLayout(self._avatar_slot)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.folder = QLabel()
        self.folder.setObjectName("Hint")
        titles.addWidget(self.title)
        titles.addWidget(self.folder)
        h.addLayout(titles, 1)
        self.fill_btn = _tool(icons.FILL, "")
        self.fill_btn.clicked.connect(lambda: self.view and self.fill_requested.emit(self.view))
        self.edit_btn = edit = QPushButton(" Edit")
        edit.setObjectName("Ghost")
        edit.setIcon(icons.glyph(icons.EDIT, theme.TEXT_2, 14))
        edit.setFocusPolicy(Qt.NoFocus)
        edit.setCursor(Qt.PointingHandCursor)
        edit.setToolTip("Edit (Ctrl+E or double-click)")
        edit.clicked.connect(lambda: self.view and self.edit_requested.emit(self.view))
        self.delete_btn = delete = _tool(icons.DELETE, "Delete (moves to the recycle bin)")
        delete.clicked.connect(lambda: self.view and self.delete_requested.emit(self.view))
        close = _tool(icons.CLOSE, "Close (Esc)")
        close.clicked.connect(self.close_panel)
        self.restore_btn = QPushButton(" Restore")
        self.restore_btn.setObjectName("Primary")
        self.restore_btn.setFocusPolicy(Qt.NoFocus)
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.setToolTip("Take it out of the recycle bin and put it in a folder")
        self.restore_btn.clicked.connect(lambda: self.view and self.restore_requested.emit(self.view))
        self.restore_btn.hide()
        self._in_bin = False
        h.addWidget(self.restore_btn, 0, Qt.AlignTop)
        h.addWidget(edit, 0, Qt.AlignTop)
        h.addWidget(self.fill_btn, 0, Qt.AlignTop)
        h.addWidget(delete, 0, Qt.AlignTop)
        h.addWidget(close, 0, Qt.AlignTop)
        outer.addWidget(head)

        self.set_fill_target("")

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        outer.addWidget(self.scroll, 1)

        self._timer = QTimer(self, interval=500)
        self._timer.timeout.connect(lambda: self._otp and self._otp.tick())
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.hide()

    def set_bin_mode(self, on: bool) -> None:
        """Entries in the recycle bin: Restore instead of Edit, Fill and Delete."""
        self._in_bin = on
        self.restore_btn.setVisible(on)
        self.edit_btn.setVisible(not on)
        self.delete_btn.setVisible(not on)
        if on:
            self.fill_btn.hide()

    def set_fill_target(self, title: str) -> None:
        """Show which window Fill types into (a page title: drawn as plain text, never HTML)."""
        self._fill_title = title
        for b in self._field_fills:
            self._tip_field_fill(b)
        self._tip_fill()

    def _tip_fill(self) -> None:
        if self.view and not self.view.autotype.enabled:
            tip = "Auto-Type is turned off for this entry"
        elif self._fill_title:
            tip = ("Fill into “%s” (Ctrl+Shift+V)" % self._fill_title).replace("<", "‹").replace(">", "›")
        else:
            tip = "Fill: click into a login box in another window first, then come back and press this"
        self.fill_btn.setToolTip(tip)

    def field_fill_button(self, field: str, name: str, options: list[tuple[str, str]] | None = None) -> QToolButton:
        """The keyboard icon beside a field: types just that field into the window you came from.
        `options` ((menu label, token), ...): with more than one, a click opens a menu of formats."""
        b = _tool(icons.FILL, "")
        b.setProperty("fill_name", name)
        b.setEnabled(bool(self.view and self.view.autotype.enabled))
        if options and len(options) > 1:
            b.clicked.connect(lambda: self._fill_menu(b, options))
        else:
            token = options[0][1] if options else field
            b.clicked.connect(lambda: self.view and self.fill_field_requested.emit(self.view, token))
        self._field_fills.append(b)
        self._tip_field_fill(b)
        return b

    def _fill_menu(self, button: QToolButton, options: list[tuple[str, str]]) -> None:
        if not self.view:
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(False)
        head = menu.addAction("Type it as:")
        head.setEnabled(False)
        for label, token in options:
            # labels can hold a page-independent value (a phone number); QMenu draws plain text
            act = menu.addAction(label.replace("&", "&&"))
            act.triggered.connect(lambda _=False, t=token, v=self.view: self.fill_field_requested.emit(v, t))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _tip_field_fill(self, b: QToolButton) -> None:
        name = b.property("fill_name")
        if not (self.view and self.view.autotype.enabled):
            tip = "Auto-Type is turned off for this entry"
        elif self._fill_title:
            tip = (f"Type just the {name} into “{self._fill_title}”").replace("<", "‹").replace(">", "›")
        else:
            tip = f"Type just the {name}: click into the box in another window first, then come back and press this"
        b.setToolTip(tip)

    # ---- content ------------------------------------------------------------
    def show_entry(self, view: EntryView, pixmap: QPixmap | None) -> None:
        self.view = view
        while self._avatar_slot.count():
            w = self._avatar_slot.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._avatar_slot.addWidget(Avatar(view.title or view.url or "?", size=40, pixmap=pixmap), 0, Qt.AlignTop)
        self.title.setText(view.title or "(untitled)")
        self.fill_btn.setEnabled(view.autotype.enabled)
        # a profile has no login to type: its fields have their own keyboard icons
        self.fill_btn.setVisible(not self._in_bin and (not view.is_identity or bool(view.username or view.password)))
        self._tip_fill()
        self.folder.setText(view.group or "Top level")

        self._field_fills = []
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 0, 14, 16)
        lay.setSpacing(10)

        login = []
        card = view.is_card
        ident = view.is_identity
        if ident:
            self._identity_fields(view, lay)
        if card and view.card_number:
            login.append(_Field("Card number", view.card_number, self, secret=True, mono=True,
                                fill_options=idlib.fill_options(view, "card_number")))
        if view.username and not (card and view.username == view.card_number):
            login.append(_Field("Username", view.username, self, fill="" if card else "username"))
        if view.password:
            login.append(_Field("Password", view.password, self, secret=True, mono=True,
                                fill="" if card else "password"))
        exp = view.card_expiry if card else None
        if exp:  # shown MM/YY, copied as MMYY (how card forms ask for it)
            login.append(_Field("Expiry", f"{exp[0]}/{exp[1]}", self, mono=True, copy_value=f"{exp[0]}{exp[1]}",
                                fill_options=idlib.fill_options(view, "card_expiry")))
        if card and view.card_code:
            login.append(_Field("Card code", view.card_code, self, secret=True, mono=True,
                                fill_options=idlib.fill_options(view, "card_code")))
        self._otp = _OtpField(view, self) if view.totp else None
        if self._otp:
            login.append(self._otp)
        if login:
            lay.addWidget(_group_box(login))
        site = []
        if view.url:
            site.append(_Field("Website", view.url, self, url=True))
        if card and view.card_phone:
            site.append(_Field("Toll-free" if is_toll_free(view.card_phone) else "Phone", view.card_phone, self))
        if card and view.card_local_phone:
            site.append(_Field("Local phone", view.card_local_phone, self))
        if site:
            lay.addWidget(_group_box(site))
        if card and (view.card_holder or view.card_brand):
            extra = []
            if view.card_holder:
                extra.append(_Field("Cardholder", view.card_holder, self,
                                    fill_options=idlib.fill_options(view, "card_holder")))
            if view.card_brand:
                extra.append(_Field("Brand", view.card_brand, self))
            lay.addWidget(_group_box(extra))
        hidden = {view.password_alias} if view.password_alias else set()
        if card:
            hidden |= set(CARD_SLOT_KEYS) | {LEGACY_CODE_KEY}
            if exp:
                hidden.add(LEGACY_EXPIRY_KEY)
        if ident:
            hidden |= set(idlib.KEYS)
        custom = [f for f in view.custom if f.key not in hidden]
        if custom:
            lay.addWidget(_group_box([
                _Field(f.key, f.value, self, secret=f.protected, mono=f.protected)
                for f in custom]))
        if view.notes:
            lay.addWidget(_group_box([_Field("Notes", view.notes, self, wrap=True)]))
        if view.attachments:
            att = QLabel("Attachments:  " + ", ".join(view.attachments))
            att.setObjectName("Hint")
            att.setWordWrap(True)
            att.setToolTip("Open attachments in KeePassium or KeePassXC")
            lay.addWidget(att)
        shown_tags = [t for t in view.tags if not (card and t == CARD_TAG)]
        if shown_tags:
            tags = QLabel("Tags:  " + ", ".join(shown_tags))
            tags.setObjectName("Hint")
            lay.addWidget(tags)
        meta = QLabel(f"Modified  {_fmt_time(view.modified)}\nCreated   {_fmt_time(view.created)}")
        meta.setObjectName("Hint")
        lay.addWidget(meta)
        lay.addStretch()

        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(body)
        if self._otp:
            self._timer.start()
        else:
            self._timer.stop()

    def _identity_fields(self, view: EntryView, lay: QVBoxLayout) -> None:
        """A sign-up profile: each field with a keyboard icon (a menu where it has several formats)."""
        def field(key: str, label: str | None = None, fill: str | None = None) -> _Field | None:
            value = view.get_field(key)
            if not value.strip():
                return None
            secret = key in idlib.PROTECTED
            return _Field(label or idlib.LABELS[key], value, self, secret=secret, mono=secret,
                          fill_options=idlib.fill_options(view, fill or key))

        groups = [
            [field(idlib.FIRST), field(idlib.LAST)],
            [field(idlib.EMAIL), field(idlib.PHONE), field(idlib.BIRTH), field(idlib.GENDER)],
            [field(idlib.ADDRESS), field(idlib.CITY), field(idlib.STATE), field(idlib.POSTAL), field(idlib.COUNTRY)],
        ]
        first, last = view.get_field(idlib.FIRST).strip(), view.get_field(idlib.LAST).strip()
        if first and last:  # for forms with one "Full name" box
            groups[0].append(_Field("Full name", f"{first} {last}", self,
                                    fill_options=idlib.fill_options(view, "name")))
        for g in groups:
            shown = [w for w in g if w is not None]
            if shown:
                lay.addWidget(_group_box(shown))

    # ---- open / close -----------------------------------------------------------
    def open_at(self, target: QPoint, animate: bool) -> None:
        if self.isVisible() or not animate:
            self.move(target)
            self.show()
        else:
            self.move(target + QPoint(40, 0))
            self.show()
            self._anim.stop()
            self._anim.setStartValue(self.pos())
            self._anim.setEndValue(target)
            self._anim.start()
        self.raise_()

    def close_panel(self) -> None:
        if self.isVisible():
            self._timer.stop()
            self.hide()
            self.view = None
            self.closed.emit()
