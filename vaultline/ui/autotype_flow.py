"""Auto-Type in the app: the global shortcut, Fill, the picker near the cursor, and notices.

The shortcut remembers the window in front, finds its entry (autotype.find_matches) and types:
straight away only for a window association (set in KeePass/KeePassXC); a match on the page or
entry title (which any web page can set) is shown in the picker to confirm with Enter. Fill types
the chosen entry into the window you were in before Vaultline. Both only run while fully unlocked;
the shortcut pressed while locked asks for the unlock first, then carries on.

Typing runs on its own short-lived thread (sendkeys.type_steps), stops when focus moves, and
is cancelled by any lock. Nothing typed is ever logged or shown.
"""
from __future__ import annotations

import html
import threading
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from .. import identity as idlib, otp as otplib, passgen, sendkeys, theme
from ..config import DATA_DIR
from ..autotype import (VK_TAB, Match, SequenceError, Step, build, find_matches, is_browser_title, is_signup_title,
                        site_from_title, site_url, window_matches)
from ..vault import EntryView
from .widgets import Avatar, ElidedLabel

DIAG_LOG = DATA_DIR / "autotype.log"  # New login's steps: box kinds and outcomes only, never values or titles
DIAG_MAX_BYTES = 256 * 1024


def diag(event: str) -> None:
    """One line about what New login did (e.g. "box=text", "typed username"). Never pass a value or a title."""
    try:
        DIAG_LOG.parent.mkdir(parents=True, exist_ok=True)
        if DIAG_LOG.exists() and DIAG_LOG.stat().st_size > DIAG_MAX_BYTES:
            DIAG_LOG.replace(DIAG_LOG.with_name(DIAG_LOG.name + ".1"))
        with open(DIAG_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{event}\n")
    except OSError:
        pass


NEW_SETTLE_SECONDS = 0.4   # New login: let the browser bring focus back into the page first
NEW_STEP_SECONDS = 0.3     # after each Tab, before asking what the next box is (browsers lag behind)
NEW_MAX_BOXES = 30         # New login never walks further than this (Facebook: password is the 16th stop)
PENDING_SECONDS = 60   # the shortcut pressed while locked carries on after an unlock within this
SETTLE_SECONDS = 0.12  # after switching back to the window, before the first key


def _near_cursor(size, gap: int = 12) -> QPoint:
    """Top-left for a popup of `size` just below-right of the cursor, kept on the cursor's screen."""
    pos = QCursor.pos()
    screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    area: QRect = screen.availableGeometry()
    x, y = pos.x() + gap, pos.y() + gap
    if x + size.width() > area.right():
        x = pos.x() - gap - size.width()
    if y + size.height() > area.bottom():
        y = pos.y() - gap - size.height()
    return QPoint(max(area.left(), x), max(area.top(), y))


class CursorNote(QWidget):
    """A short message by the cursor that never takes focus (Vaultline may be hidden)."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        box = QFrame(self)
        box.setObjectName("Note")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(box)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.body = QLabel()
        self.body.setWordWrap(True)
        for label in (self.title, self.body):  # window titles come from web pages: never as HTML
            label.setTextFormat(Qt.PlainText)
        self.body.setObjectName("Hint")
        inner.addWidget(self.title)
        inner.addWidget(self.body)
        self.setMaximumWidth(380)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)
        self._box = box

    def show_message(self, title: str, body: str = "", kind: str = "error", ms: int = 5000) -> None:
        color = {"error": theme.DANGER, "warn": theme.WARN}.get(kind, theme.ACCENT_SOFT)
        self._box.setStyleSheet(f"#Note {{ background: {theme.SURFACE}; border: 1px solid {color}; "
                                f"border-radius: 10px; }}")
        self.title.setText(title)
        self.title.setStyleSheet(f"font-weight: 600; color: {color};")
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.adjustSize()
        self.move(_near_cursor(self.sizeHint()))
        self.show()
        self.raise_()
        self._timer.start(ms)


class _PickRow(QWidget):
    def __init__(self, view: EntryView, pixmap=None):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(10)
        lay.addWidget(Avatar(view.title or view.url or "?", size=26, pixmap=pixmap))
        text = QVBoxLayout()
        text.setSpacing(0)
        title = ElidedLabel(view.title or "(untitled)")
        title.setObjectName("Title")
        sub = ElidedLabel("  ·  ".join(p for p in (view.username, view.group) if p) or "no username")
        sub.setObjectName("Sub")
        text.addStretch()
        text.addWidget(title)
        text.addWidget(sub)
        text.addStretch()
        lay.addLayout(text, 1)


class AutoTypePicker(QWidget):
    """Which entry to fill: the matches first, or a search over the vault. Enter fills, Esc cancels,
    clicking elsewhere cancels."""
    chosen = Signal(object, str)  # EntryView, sequence ("" = the entry's own)
    cancelled = Signal()
    new_login = Signal()          # "New login for this window"
    ROW = 46

    def __init__(self, pixmap_for=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Vaultline: fill")
        self._pixmap_for = pixmap_for or (lambda v: None)
        self._all: list[EntryView] = []
        self._matches: list[Match] = []
        self._shown: list[tuple[EntryView, str]] = []
        self._was_active = False
        self._done = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("Picker")
        box.setStyleSheet(f"#Picker {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER_STRONG}; "
                          f"border-radius: 12px; }}")
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.header = ElidedLabel()
        self.header.setObjectName("Hint")
        lay.addWidget(self.header)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._refresh)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("Entries")
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemClicked.connect(lambda it: self._choose(self.list.row(it)))
        lay.addWidget(self.list, 1)
        self.empty = QLabel("No matches")
        self.empty.setObjectName("Hint")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)
        self.new_btn = QPushButton("＋ New login for this window")
        self.new_btn.setObjectName("Link")
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.setFocusPolicy(Qt.NoFocus)
        self.new_btn.clicked.connect(self._new_login)
        lay.addWidget(self.new_btn, 0, Qt.AlignLeft)
        self._lay = lay
        self.hint = QLabel("↵ Fill  ·  Esc Cancel")
        self.hint.setObjectName("Hint")
        lay.addWidget(self.hint)
        self.setFixedWidth(400)

    def open(self, window_title: str, matches: list[Match], entries: list[EntryView]) -> None:
        self._matches = matches
        self._all = [v for v in entries if v.autotype.enabled and _has_login(v)]
        # a sign-up page (going by its title, which the page sets): the new-login link goes first
        self._lay.removeWidget(self.new_btn)
        self._lay.insertWidget(1 if is_signup_title(window_title) else self._lay.indexOf(self.hint), self.new_btn,
                               0, Qt.AlignLeft)
        self._done = False
        self._was_active = False
        self.header.setText(f"Fill into: {window_title or '(untitled window)'}")
        self.header.setToolTip(f"<qt>{html.escape(window_title)}</qt>")  # a page title, never as HTML
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.setPlaceholderText("Search to pick another entry…" if matches else "No match. Search…")
        self._refresh()
        self.move(_near_cursor(self.sizeHint()))
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def _refresh(self, *_):
        terms = self.search.text().lower().split()
        if terms:
            self._shown = [(v, "") for v in self._all if v.matches(terms)]
        elif self._matches:
            self._shown = [(m.entry, m.sequence) for m in self._matches]
        else:
            self._shown = [(v, "") for v in self._all]
        self.list.clear()
        for v, _seq in self._shown[:200]:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, self.ROW))
            self.list.addItem(item)
            self.list.setItemWidget(item, _PickRow(v, self._pixmap_for(v)))
        if self._shown:
            self.list.setCurrentRow(0)
        self.list.setVisible(bool(self._shown))
        self.empty.setVisible(not self._shown)
        rows = min(len(self._shown), 6)
        self.list.setFixedHeight(max(1, rows * (self.ROW + 2)))
        self.adjustSize()

    def _choose(self, row: int) -> None:
        if self._done or not (0 <= row < len(self._shown)):
            return
        self._done = True
        view, seq = self._shown[row]
        self.hide()
        self.chosen.emit(view, seq)

    def _new_login(self) -> None:
        if self._done:
            return
        self._done = True
        self.hide()
        self.new_login.emit()

    def cancel(self) -> None:
        if not self._done:
            self._done = True
            self.hide()
            self.cancelled.emit()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.search and event.type() == QEvent.KeyPress:
            e: QKeyEvent = event
            n, row = self.list.count(), self.list.currentRow()
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._choose(row)
                return True
            if e.key() == Qt.Key_Escape:
                self.cancel()
                return True
            if e.key() == Qt.Key_Down and n:
                self.list.setCurrentRow(min(n - 1, row + 1))
                return True
            if e.key() == Qt.Key_Up and n:
                self.list.setCurrentRow(max(0, row - 1))
                return True
        return super().eventFilter(obj, event)

    def changeEvent(self, e) -> None:  # noqa: N802
        if e.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active and self.isVisible():
                QTimer.singleShot(0, self.cancel)  # clicked elsewhere
        super().changeEvent(e)


def _has_login(v: EntryView) -> bool:
    """Profiles have no login to type (only Tab and Enter would be): their fields are typed one at a time."""
    return not v.is_identity or bool(v.username or v.password)


# The icons beside a field type just that field; a whole login in a password box types this.
ONE_FIELD = {"username": "{USERNAME}", "password": "{PASSWORD}", "otp": "{TOTP}"}
PASSWORD_BOX = "{PASSWORD}{ENTER}"


class AutoTypeFlow(QObject):
    """Glue between the main window, the picker and the typing thread."""
    typed = Signal(bool, str)  # from the typing thread: ok, why it stopped (no values)
    new_typed = Signal(bool, str, str)  # New login: ok, why it stopped, what was typed (no values)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.vc = win.vc
        self.note = CursorNote()
        self.picker = AutoTypePicker(win.vault_view.pixmap_for)
        self.picker.chosen.connect(self._picked)
        self.picker.cancelled.connect(self._picker_closed)
        self.picker.new_login.connect(lambda: QTimer.singleShot(0, self._new_from_picker))
        self._new: tuple | None = None  # (token, window, username, password) while the save runs
        self.vc.applied.connect(self._new_saved)
        self.watcher = sendkeys.ForegroundWatcher()
        win.activated.connect(self._show_fill_target)
        self._target = 0
        self._pending: tuple[int, float, bool] | None = None  # (window, when, Vaultline was hidden)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.typed.connect(self._typed)
        self.new_typed.connect(self._new_typed)
        self.vc.unlocked.connect(lambda *_: QTimer.singleShot(0, self._resume))
        for sig in (self.vc.locked, self.vc.pin_locked):
            sig.connect(lambda *_: self.stop())
        win.hidden_to_tray.connect(lambda: setattr(self, "_pending", None))

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        """Any lock: stop typing, close the picker, forget a waiting shortcut."""
        self._cancel.set()
        self._pending = None
        self._new = None  # a lock while a new login saves: don't type it afterwards
        if self.picker.isVisible():
            self.picker.cancel()

    def _say(self, title: str, body: str = "", kind: str = "error") -> None:
        self.note.show_message(title, body, kind)

    # ---- the global shortcut --------------------------------------------------------
    def hotkey_pressed(self) -> None:
        if self.busy or self.picker.isVisible():
            return
        target = sendkeys.foreground_window()
        if not target or sendkeys.is_own(target):
            self._say("Auto-Type fills another window",
                      "Switch to the login window and press the shortcut there, or pick an entry here "
                      "and press Fill (Ctrl+Shift+V).", "info")
            return
        if not sendkeys.is_fill_target(target):
            return
        if not self.vc.is_unlocked:
            hidden = not self.win.isVisible() or self.win.icon_mode
            self._pending = (target, time.monotonic(), hidden)
            self.win.bring_to_front()
            return
        self._match(target)

    def _resume(self) -> None:
        """After an unlock: carry on with a shortcut pressed while locked."""
        pending, self._pending = self._pending, None
        if pending is None:
            return
        target, when, hidden = pending
        if time.monotonic() - when > PENDING_SECONDS or not sendkeys.is_fill_target(target):
            return
        if hidden:
            self.win.hide_to_tray()  # it only came up for the unlock
        self._match(target)

    def _match(self, target: int) -> None:
        title = sendkeys.window_title(target)
        if sendkeys.runs_above_us(target):
            self._say_admin(title)
            return
        views = self.win.vault_view.views
        matches = find_matches(views, title)
        self._target = target
        if len(matches) == 1 and matches[0].how == "window":
            self._type(matches[0].entry, matches[0].sequence, target)
        else:  # a title match could come from any web page: confirm it
            self.picker.open(title, matches, views)

    def _picked(self, view: EntryView, sequence: str) -> None:
        self._type(view, sequence, self._target)

    def _picker_closed(self) -> None:
        self._target = 0

    # ---- Fill (from Vaultline) ----------------------------------------------------------
    def _show_fill_target(self) -> None:
        """Vaultline came to the front: show which window Fill would type into."""
        target = self.watcher.target()
        self.win.vault_view.set_fill_target(sendkeys.window_title(target) if target else "")

    def fill(self, view: EntryView, field: str = "") -> None:
        """Type `view` into the window you came from: the whole login, or one field from the icons
        beside the fields: "username", "password", "otp", or a format token from identity.fill_options
        ("identity_birthDate:mdy", "card_expiry:MM/YY"...), whose value is looked up only here."""
        if not self.vc.is_unlocked or self.busy or self.win.vault_view.in_bin:
            return
        if not view.autotype.enabled:
            self.win.toast.show_message("Auto-Type is off for this entry", "It's turned off in KeePass/KeePassXC "
                                        "(on the entry or its folder).", "warn", 4000)
            return
        if not field and not _has_login(view):
            self.win.toast.show_message("Fill one field at a time", "Use the keyboard icon beside each "
                                        "profile field in the details panel.", "info", 4500)
            return
        target = self.watcher.target()
        if not target:
            self.win.toast.show_message("Nothing to fill", "Click into the login box you want to fill, "
                                        "then come back and press Fill.", "warn", 4500)
            return
        literal = None
        if field in ONE_FIELD:
            sequence = ONE_FIELD[field]
        elif field:
            value = idlib.fill_value(view, field)
            if not value:
                self.win.toast.show_message("Nothing to type", "That field is empty.", "warn", 3500)
                return
            sequence, literal = "", [Step("text", value=value, part=idlib.fill_part(field))]
            del value
        else:
            title = sendkeys.window_title(target)
            sequence = next((seq or view.autotype.effective_sequence for pat, seq in view.autotype.associations
                             if window_matches(pat, title)), "")
        if self.win.icon_mode:
            self.win.collapse_panel()
        self._type(view, sequence, target, whole=not field, literal=literal)

    # ---- new login for this window --------------------------------------------------------
    def _new_from_picker(self) -> None:
        target, self._target = self._target, 0
        self.new_login(target)

    def newlogin_hotkey_pressed(self) -> None:
        target = sendkeys.foreground_window()
        if not target or sendkeys.is_own(target) or not sendkeys.is_fill_target(target):
            self._say("New login fills another window", "Press the shortcut on the sign-up page, or open the "
                      "quick search from there and pick “New login”.", "info")
            return
        self.new_login(target)

    def new_login(self, target: int = 0) -> None:
        """Make a login for the window you came from (a sign-up page), save it, and only once the save
        has worked type it there: the username, or in a password box the password (and again in a
        following password box, the confirm box). Nothing is typed into a box it can't tell is one."""
        if self.busy or self._new is not None or self.win._dialog is not None:
            return
        if not self.vc.is_unlocked or self.vc.snapshot is None:
            self.win.bring_to_front()
            self.win.toast.show_message("Unlock Vaultline first", "Then try New login again.", "warn", 4000)
            return
        target = target or self.watcher.target()
        if not target or not sendkeys.is_window(target) or sendkeys.is_own(target):
            self.win.toast.show_message("No window to sign up in", "Click into the sign-up page first, then "
                                        "come back and pick New login.", "warn", 4500)
            return
        title = sendkeys.window_title(target)
        if sendkeys.runs_above_us(target):
            self._say_admin(title)
            return
        from .new_login import NewLoginDialog  # noqa: PLC0415 (only needed here)
        url = self._site_of(target, title)
        snap, c = self.vc.snapshot, self.win.config
        profiles = sorted((v for v in snap.entries if v.is_identity), key=lambda v: (v.title or "").casefold())
        self.win.bring_to_front()
        dlg = NewLoginDialog(self.win, title, site_from_title(title), profiles, c.default_profile_uuid,
                             snap.groups, snap.root_uuid, c.newlogin_folder_uuid, lambda: passgen.from_config(c),
                             shared=self.win.shared_folders(), url=url, generator_config=c)
        if self.win.compact:
            self.win._fit_to_compact(dlg)
        accepted = self.win._run_dialog(dlg)
        edit, profile_uuid = dlg.result_edit, dlg.profile_uuid
        dlg.deleteLater()  # its boxes hold the new password: don't keep them around
        if not accepted or edit is None or not self.vc.is_unlocked:
            return
        profile = next((v for v in profiles if v.uuid == profile_uuid), None)
        token = ("newlogin", object())
        self._new = (token, target, idlib.form_values(profile, edit.username), edit.password)
        if not self.vc.apply([edit], token):
            self._new = None
            self.win.toast.show_message("Not saved", "Nothing was typed.", "error", 4000)

    @staticmethod
    def _site_of(target: int, title: str) -> str:
        """The site the browser is on, from its address bar (only the https://site part), or ""."""
        if not is_browser_title(title):
            return ""
        out: list[str] = []
        t = threading.Thread(target=lambda: out.append(sendkeys.browser_address(target)), daemon=True)
        t.start()
        t.join(2.0)
        return site_url(out[0]) if out else ""

    def _new_saved(self, token, ok: bool, message: str, _report=None) -> None:
        if self._new is None or token is not self._new[0]:
            return
        _token, target, values, password = self._new
        self._new = None
        diag(f"saved ok={ok}")
        if not ok:
            self.win.toast.show_message("New login not saved", f"Nothing was typed. {message}", "error", 6000)
            return
        self.win.toast.show_message("New login saved", "typing it into the sign-up page", "ok", 2500)
        if self.win.icon_mode:
            self.win.collapse_panel()
        self._type_new_login(target, values, password)

    def _type_new_login(self, target: int, values: dict[str, str], password: str) -> None:
        if self.busy or not self.vc.is_unlocked:
            diag(f"not typed: busy={self.busy} unlocked={self.vc.is_unlocked}")
            self._say("Saved, but nothing was typed", "Vaultline was busy or locked. Use the keyboard icons "
                      "beside the new login's fields.")
            return
        if not sendkeys.is_window(target) or sendkeys.is_own(target):
            diag("not typed: window gone or our own")
            self._say("Saved, but that window has closed", "The new login is in Vaultline.")
            return
        if sendkeys.runs_above_us(target):
            diag("not typed: admin window")
            self._say_admin(sendkeys.window_title(target))
            return
        front = sendkeys.foreground_window() == target
        if not front and not sendkeys.bring_to_front(target):
            diag("not typed: couldn't bring the window to the front")
            self._say("Saved, but couldn't switch to the window", "Click into it and use the keyboard icons.")
            return
        diag(f"window in front (already={front})")
        self._cancel.clear()
        cancel = self._cancel
        tab = [Step("key", vk=VK_TAB, part="tab")]

        def run():
            """Walk the form from the box you're in: each text box gets the profile value its label asks
            for, a drop-down labelled Month / Day / Year / Gender / Province / Country gets its value typed
            (the browser picks the matching option); anything else is skipped with Tab. A password box gets the
            password, and a password box right after it gets it again. It stops after the password, or
            after NEW_MAX_BOXES. Enter is never pressed."""
            def go(steps):
                return sendkeys.type_steps(steps, target, cancelled=cancel.is_set)

            def look():
                kind, label = sendkeys.focused_info(target, label=True)
                return kind, (idlib.field_for_label(label, choice=kind == "choice") if kind in ("text", "choice")
                              else "")
            result = sendkeys.Result(True)
            done: list[str] = []   # what was typed, by name only
            used: set[str] = set()
            why = ""
            passwords = 0
            try:
                time.sleep(NEW_SETTLE_SECONDS)
                kind, field = look()
                if not kind:  # the first question switches the browser's accessibility on: ask again
                    time.sleep(NEW_STEP_SECONDS)
                    kind, field = look()
                for n in range(NEW_MAX_BOXES):
                    diag(f"box {n}: {kind or 'other'} {field or '-'}")
                    if cancel.is_set():
                        break
                    if kind == "password":
                        result = go([Step("text", value=password, part="password")])
                        if not result.ok:
                            break
                        passwords += 1
                        done.append("the password" if passwords == 1 else "the password again")
                        if passwords == 2:
                            break
                    elif passwords:  # the box after the password isn't a confirm box: finished
                        break
                    elif kind in ("text", "choice") and field and field not in used and values.get(field):
                        result = go([Step("text", value=values[field], part=idlib.FORM_FIELD_NAMES[field])])
                        if not result.ok:
                            break
                        used.add(field)
                        done.append(idlib.FORM_FIELD_NAMES[field])
                    result = go(tab)
                    if not result.ok:
                        break
                    time.sleep(NEW_STEP_SECONDS)
                    kind, field = look()
                if result.ok and not passwords:
                    why = ("No password box was found (or Windows couldn't tell which one it is). Click into it "
                           "and use the keyboard icon beside the new login's password.")
            except Exception as exc:  # never silent: say it stopped (type of error only)
                diag(f"error {type(exc).__name__}")
                result = sendkeys.Result(False, "Something went wrong while typing.")
            finally:
                values.clear()
                diag(f"done ok={result.ok} typed={len(done)} passwords={passwords}")
                self.new_typed.emit(result.ok, result.message or why, ", ".join(done))

        self._thread = threading.Thread(target=run, name="autotype-new", daemon=True)
        self._thread.start()

    # ---- typing -------------------------------------------------------------------------
    def _say_admin(self, title: str) -> None:
        self._say("Can't type into this window",
                  f"“{title or 'This window'}” runs as administrator, and Windows blocks typing into it from "
                  "Vaultline. Copy and paste instead, or run Vaultline as administrator.")

    def _type(self, view: EntryView, sequence: str, target: int, whole: bool = True,
              literal: list[Step] | None = None) -> None:
        """Type in the background. `whole` (a whole login): if the box you're in turns out to be a
        password box, only the password and Enter are typed. `literal`: steps to type as they are
        (a formatted profile or card value), instead of building `sequence`."""
        if self.busy or not self.vc.is_unlocked:
            return
        if not sendkeys.is_window(target) or sendkeys.is_own(target):
            self._say("That window has closed")
            return
        title = sendkeys.window_title(target)
        if sendkeys.runs_above_us(target):
            self._say_admin(title)
            return
        code = (lambda: otplib.current(view.totp)[0]) if view.totp is not None else None
        try:
            steps = literal if literal is not None else build(sequence or view.autotype.effective_sequence, view, code)
            in_password = build(PASSWORD_BOX, view) if whole and literal is None else None
        except SequenceError as exc:
            self._say("Can't fill this entry", f"{view.title or 'This entry'}'s Auto-Type sequence: {exc}")
            return
        if sendkeys.foreground_window() != target and not sendkeys.bring_to_front(target):
            self._say("Couldn't switch to the window", "Click into it and try again.")
            return
        self._cancel.clear()
        cancel = self._cancel

        def run():
            time.sleep(SETTLE_SECONDS)
            kind = sendkeys.focused_box(target) if in_password else ""
            if in_password and not kind:  # a browser's first answer is often "can't tell": ask once more
                time.sleep(NEW_STEP_SECONDS)
                kind = sendkeys.focused_box(target)
            chosen = in_password if in_password and kind == "password" else steps
            result = sendkeys.type_steps(chosen, target, cancelled=cancel.is_set)
            steps.clear()
            if in_password:
                in_password.clear()
            self.typed.emit(result.ok, result.message)

        self._thread = threading.Thread(target=run, name="autotype", daemon=True)
        self._thread.start()

    def _new_typed(self, ok: bool, message: str, summary: str) -> None:
        """New login finished: always say what was typed, so a silent page is never a mystery."""
        self._thread = None
        if not summary:
            self._say("New login saved, nothing typed", message or "Use the keyboard icons beside its fields.",
                      "error" if not ok else "info")
        elif not ok or message:
            self._say(f"Typed {summary}", message or "", "info" if ok else "error")
        else:
            self._say(f"Typed {summary}", "Check the page, then finish signing up.", "info")

    def _typed(self, ok: bool, message: str) -> None:
        self._thread = None
        if not ok:
            self._say("Filling stopped", message)
