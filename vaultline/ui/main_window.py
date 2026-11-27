"""Top-level window: switches between the lock screens and the vault list."""
from __future__ import annotations

import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, agent_access, hotkey, icons, otp as otplib, pin as pinlib, theme
from ..clipboard import SecureClipboard
from ..config import Config
from ..controller import VaultController
from ..vault import (CreateFolder, DeleteEntries, DeleteFolder, EntryEdit, EntryView, MoveEntries, RenameFolder,
                     Snapshot)
from . import small_dialogs
from .autotype_flow import AutoTypeFlow
from .entry_dialog import EntryDialog, known_usernames
from .floating_icon import ICON_SIZE, FloatingIcon, panel_position, screen_area
from .frameless import NativeFrame, available as native_frame_available
from .help_page import HelpPage
from .pin_view import PinView
from .settings_page import SettingsPage
from .titlebar import COMPACT_HEIGHT, TitleBar
from .toast import Toast
from .unlock_view import UnlockView
from .vault_view import VaultView

MIN_WIDTH = 560            # titles, folders and copy buttons need this much room
COMPACT_WIDTH = 720        # narrower than this, the window is the compact quick-search palette
COMPACT_MAX_HEIGHT = 480   # the palette grows with its results up to this, then scrolls
FULL_MIN_HEIGHT = 420
FULL_DEFAULT_HEIGHT = 600
FULL_DEFAULT_WIDTH = 820
COMPACT_DEFAULT_WIDTH = 600
EDGE_SWITCH = 60  # pull the bottom edge this far past the limit to switch views
                  # (and a palette side edge this far past MIN_WIDTH to shrink to the icon)
COLLAPSE_DELAY = 200  # ms after the panel loses focus before it shrinks back to the icon


class _CurrentStack(QStackedWidget):
    """Only the page on show decides the minimum size (so the idle palette can be just its bar)."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else QSize(0, 0)

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.currentWidget()
        return w.sizeHint() if w is not None else QSize(0, 0)

_LABELS = {"username": "Username", "password": "Password", "otp": "OTP", "url": "URL",
           "card_code": "Card code", "card_number": "Card number"}


class _EditorBar(QWidget):
    """Title bar while the entry form is open: its name and a pin that works like the list's."""

    def __init__(self, title: str, pin_btn: QPushButton):
        super().__init__()
        self._source = pin_btn
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 8, 0)
        lay.setSpacing(8)
        name = QLabel(title)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        lay.addWidget(name)
        lay.addStretch(1)
        self.pin = QPushButton()
        self.pin.setObjectName("Ghost")
        self.pin.setIconSize(pin_btn.iconSize())
        self.pin.setFixedSize(pin_btn.size() if pin_btn.width() > 0 else QSize(42, 42))
        self.pin.setFocusPolicy(Qt.NoFocus)
        self.pin.setCursor(Qt.PointingHandCursor)
        self.pin.clicked.connect(pin_btn.click)  # the list's pin does the work (and keeps the setting)
        pin_btn.toggled.connect(self.sync)
        lay.addWidget(self.pin)
        self.sync()

    def sync(self, *_):
        self.pin.setIcon(self._source.icon())
        self.pin.setStyleSheet(self._source.styleSheet())
        self.pin.setToolTip(self._source.toolTip())


class MainWindow(QMainWindow):
    hidden_to_tray = Signal()
    settings_saved = Signal()
    quit_requested = Signal()  # the X with "Quit" chosen: the tray does the real quit
    activated = Signal()       # came to the front (Fill shows the window it would type into)

    def __init__(self, config: Config, controller: VaultController, clipboard: SecureClipboard):
        super().__init__()
        self.config = config
        self.vc = controller
        self.clipboard = clipboard
        self.allow_close = False
        self.can_quit = False  # the tray handles quit_requested (without a tray the X really closes)
        self._dialog: QDialog | None = None
        self.settings_page: SettingsPage | None = None
        self.help_page: HelpPage | None = None
        self._drag_from_compact = False
        self._drag_start = (0, 0)  # (width, palette height) when a drag began
        self._icons_running = False
        self._icons_asked = False  # True when the user asked for icons (vs. the automatic fetch on save)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        if native_frame_available():  # our own title bar (see frameless.py); Windows still snaps/resizes it
            self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.resize(820, FULL_DEFAULT_HEIGHT)
        self.setMinimumWidth(MIN_WIDTH)
        self.compact = False
        self.on_top = False
        self.icon_mode = False  # shrunk to the floating icon (see floating_icon.py)
        if config.window_geometry:  # reopen where it was; Qt moves it back if that screen is gone
            self.restoreGeometry(QByteArray.fromBase64(config.window_geometry.encode("ascii")))

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        col = QVBoxLayout(root)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.titlebar = TitleBar()
        self.titlebar.set_window_buttons(native_frame_available())
        self.titlebar.minimize_clicked.connect(self._minimize)
        self.titlebar.maximize_clicked.connect(lambda: self.frame.toggle_maximize())
        self.titlebar.close_clicked.connect(self.close)
        col.addWidget(self.titlebar)
        self.stack = _CurrentStack()
        col.addWidget(self.stack, 1)
        self.frame = NativeFrame(self, self._hit, self.titlebar.max_btn, sizing_height=self._sizing_height,
                                 vertical_resize=lambda: not self.compact)
        self.frame.size_move_started = self._size_move_started
        self.titlebar.mode_clicked.connect(self.toggle_view)
        self.unlock_view = UnlockView(config.last_database, config.last_keyfile)
        self.unlock_view.set_recent(config.recent_databases)
        self.pin_view = PinView()
        self.vault_view = VaultView()
        for w in (self.unlock_view, self.pin_view, self.vault_view):
            self.stack.addWidget(w)
        self.toast = Toast(root)
        self.stack.currentChanged.connect(self._update_titlebar)
        self.stack.currentChanged.connect(lambda _i: self._fit_height())
        self._update_titlebar()

        # wiring
        self.unlock_view.unlock_requested.connect(self._unlock)
        self.unlock_view.create_requested.connect(self._create)
        self.pin_view.pin_entered.connect(self._pin_entered)
        self.pin_view.use_master.connect(lambda: self.vc.lock(full=True))
        vv = self.vault_view
        vv.copy_requested.connect(self.copy)
        vv.copy_text.connect(self.copy_text)
        vv.add_requested.connect(lambda: self.open_editor(None))
        vv.edit_requested.connect(self.open_editor)
        vv.delete_requested.connect(self.delete_entry)
        vv.lock_requested.connect(lambda: self.vc.lock())
        vv.settings_requested.connect(self.open_settings)
        vv.folder_create_requested.connect(self.create_folder)
        vv.folder_rename_requested.connect(self.rename_folder)
        vv.folder_delete_requested.connect(self.delete_folder)
        vv.restore_requested.connect(self.restore_entry)
        vv.pin_toggled.connect(self._pin_toggled_compact)
        vv.content_changed.connect(lambda: self._fit_height())
        vv.help_requested.connect(self.open_help)
        vv.mode_requested.connect(self.toggle_view)
        vv.icon_requested.connect(self.enter_icon_mode)
        vv.escape_unused.connect(self.collapse_panel)
        vv.set_pinned(config.compact_pinned)
        self._update_compact()

        # icon mode: the window shrunk to a floating search icon
        self._collapsed_at = 0.0
        self._shrinking = False
        self.hotkey_error = ""
        self.icon = FloatingIcon()
        self.icon.clicked.connect(self._icon_clicked)
        self.icon.moved.connect(self._icon_moved)
        self.icon.open_full.connect(lambda: self.leave_icon_mode(full=True))
        self.icon.open_compact.connect(lambda: self.leave_icon_mode(full=False))
        self.icon.lock_requested.connect(lambda: self.vc.lock())
        self.icon.settings_requested.connect(self.show_settings)
        for sig in (self.vc.unlocked, self.vc.locked, self.vc.pin_locked):
            sig.connect(lambda *_: self._update_icon_lock())
        self.settings_saved.connect(self.apply_hotkey)
        self.settings_saved.connect(self._apply_suggest)
        self._apply_suggest()
        self.autotype = AutoTypeFlow(self)
        vv.fill_requested.connect(self.autotype.fill)
        vv.fill_field_requested.connect(self.autotype.fill)
        vv.new_login_requested.connect(lambda: self.autotype.new_login())
        vv.shared_folders = self.shared_folders

        self.vc.unlocked.connect(self._on_unlocked)
        self.vc.unlock_failed.connect(self._on_unlock_failed)
        self.vc.entries_changed.connect(self._on_entries_changed)
        self.vc.save_failed.connect(self._on_save_failed)
        self.vc.locked.connect(self._on_locked)
        self.vc.pin_locked.connect(self._on_pin_locked)
        self.vc.pin_failed.connect(self.pin_view.wrong)
        self.vc.busy_changed.connect(self._on_busy)
        self.vc.icon_progress.connect(self._on_icon_progress)
        self.vc.applied.connect(self._on_applied)

    # ---- layout ----------------------------------------------------------------
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._update_compact()
        self.toast.reposition()

    def showEvent(self, e) -> None:  # noqa: N802
        self.frame.apply_styles()
        super().showEvent(e)
        self._apply_on_top(force=True)
        self.focus_input()

    # ---- compact view (by width) and keep-on-top -------------------------------------
    def _update_compact(self) -> None:
        if not hasattr(self, "vault_view"):  # a resize during construction
            return
        on = self.width() < COMPACT_WIDTH
        if on == self.compact:
            return
        normal = not (self.isMaximized() or self.isMinimized() or self.isFullScreen())
        if on and normal and self.height() >= FULL_MIN_HEIGHT:
            self.config.full_height = self.height()  # brought back when the window is widened again
            self.config.save()
        self.compact = on
        if not on and getattr(self, "icon_mode", False):
            self._drop_icon_mode()  # pulled wider than the palette: a normal window again
        self.titlebar.set_compact(on)
        self.vault_view.set_compact(on)
        self.frame.set_maximizable(not on)  # the palette snaps to the screen top instead of maximising
        self.frame.snap_to_top = on
        self.frame.bottom_resize = on       # pulling the bottom edge down opens the full window
        self._apply_on_top()
        if on:
            self._fit_height()
        elif normal and self.height() < FULL_MIN_HEIGHT:
            self.resize(self.width(), self._full_height())

    def _full_height(self) -> int:
        return max(self.config.full_height or FULL_DEFAULT_HEIGHT, FULL_MIN_HEIGHT)

    def compact_height(self) -> int:
        """The palette's height: just the bar while idle, growing with the results, capped."""
        if self.stack.currentWidget() is not self.vault_view:
            return COMPACT_MAX_HEIGHT  # lock screen, PIN, settings, help
        content = self.vault_view.compact_content_height()
        return COMPACT_MAX_HEIGHT if content is None else min(COMPACT_MAX_HEIGHT, COMPACT_HEIGHT + content)

    def _fit_height(self, again: bool = False) -> None:
        if not self.compact or self.isMaximized() or self.isMinimized() or self.isFullScreen():
            return
        self.stack.updateGeometry()  # shown/hidden rows change the minimum; let Qt see it first
        self.centralWidget().layout().activate()
        h = self.compact_height()
        if self.height() != h:
            self.resize(self.width(), h)
            if self.height() != h and not again:  # Qt's limits were still catching up
                QTimer.singleShot(0, lambda: self._fit_height(again=True))

    def _hit(self, pos) -> str | None:
        """What Windows should treat a point as: the bar's answer, and in the palette every empty
        strip (under the results, around the lock card) also moves the window."""
        tb = self.titlebar
        p = tb.mapFrom(self, pos)
        if tb.rect().contains(p):
            return tb.hit(p)
        if self.compact:
            w = self.childAt(pos)
            empty = (self.centralWidget(), self.stack, self.vault_view, self.vault_view.empty,
                     self.unlock_view, self.pin_view)
            if w is None or w in empty or w.objectName() == "LockPanel":
                return "caption"
        return None

    def _size_move_started(self) -> None:
        self._drag_from_compact = self.compact
        self._drag_start = (self.width(), self.compact_height())

    def _sizing_height(self, width: float, height: float, edge: int = 2):
        """Asked by Windows while the user drags an edge: the height to use (or (width, height) to
        switch views from the bottom edge: pull the palette down, or push the full window up)."""
        wanted = self.frame.wanted_width
        if self._drag_from_compact and wanted is not None and wanted < MIN_WIDTH - EDGE_SWITCH:
            self._pulled_past_minimum()
            return self.compact_height()
        if edge in (6, 7, 8):  # WMSZ_BOTTOM, BOTTOMLEFT, BOTTOMRIGHT
            start_w, start_h = self._drag_start
            if self._drag_from_compact:
                if height > start_h + EDGE_SWITCH:
                    return self._full_width(), max(height, FULL_MIN_HEIGHT)
                return start_w, self.compact_height()
            if height < FULL_MIN_HEIGHT - EDGE_SWITCH:
                return self._compact_width(), self.compact_height()
            if height < FULL_MIN_HEIGHT:
                return start_w, FULL_MIN_HEIGHT
            return None
        if width < COMPACT_WIDTH:
            return self.compact_height()
        if self._drag_from_compact:
            return self._full_height()  # widened out of the palette: the full view's own height
        return FULL_MIN_HEIGHT if height < FULL_MIN_HEIGHT else None

    def _full_width(self) -> int:
        return max(self.config.full_width or FULL_DEFAULT_WIDTH, COMPACT_WIDTH)

    def _compact_width(self) -> int:
        return min(max(self.config.compact_width or COMPACT_DEFAULT_WIDTH, MIN_WIDTH), COMPACT_WIDTH - 1)

    def toggle_view(self) -> None:
        """The view button (left of minimise) and Ctrl+M: palette <-> full window,
        each at the size it had last time. In icon mode: the full window."""
        if self.icon_mode:
            self.leave_icon_mode(full=True)
            return
        if self.isMaximized():
            self.frame.toggle_maximize()
            if self.isMaximized():
                self.showNormal()
        if self.compact:
            self.config.compact_width = self.width()
            w, h = self._full_width(), self._full_height()
        else:
            self.config.full_width, self.config.full_height = self.width(), max(self.height(), FULL_MIN_HEIGHT)
            w, h = self._compact_width(), self.height()
        self.config.save()
        self.resize(w, h)
        self._keep_on_screen()
        self.vault_view.focus_search()

    def _keep_on_screen(self) -> None:
        area = self.screen().availableGeometry()  # keep it on screen when it grows
        g = self.frameGeometry()
        x = min(max(g.x(), area.left()), max(area.left(), area.right() - g.width() + 1))
        y = min(max(g.y(), area.top()), max(area.top(), area.bottom() - g.height() + 1))
        if (x, y) != (g.x(), g.y()):
            self.move(x, y)

    def _pin_toggled_compact(self, on: bool) -> None:
        self.config.compact_pinned = on
        self.config.save()
        self._apply_on_top()

    def _apply_on_top(self, force: bool = False) -> None:
        """Keep on top (the pin button, in both views; stored as compact_pinned for older configs)."""
        want = self.config.compact_pinned or self.icon_mode
        if want == self.on_top and not force:
            return
        self.on_top = want
        if not self.isVisible():
            return  # showEvent applies it
        if not self.frame.set_topmost(want):  # off Windows: Qt's flag (re-shows the window)
            if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) != want:
                self.setWindowFlag(Qt.WindowStaysOnTopHint, want)
                self.show()

    def nativeEvent(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG" and sys.platform == "win32":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == hotkey.WM_HOTKEY and msg.wParam == hotkey.HOTKEY_ID:
                QTimer.singleShot(0, self.summon)
                return True, 0
            if msg.message == hotkey.WM_HOTKEY and msg.wParam == hotkey.AUTOTYPE_ID:
                QTimer.singleShot(0, self.autotype.hotkey_pressed)
            if msg.message == hotkey.WM_HOTKEY and msg.wParam == hotkey.NEWLOGIN_ID:
                QTimer.singleShot(0, self.autotype.newlogin_hotkey_pressed)
                return True, 0
        got = self.frame.handle(event_type, message)
        return got if got is not None else super().nativeEvent(event_type, message)

    def changeEvent(self, e) -> None:  # noqa: N802
        if e.type() == QEvent.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
        elif e.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.activated.emit()
        elif e.type() == QEvent.ActivationChange and self.icon_mode and not self.isActiveWindow():
            QTimer.singleShot(COLLAPSE_DELAY, self._collapse_if_away)
        super().changeEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape and self.icon_mode:  # lock screens: Esc shrinks back to the icon
            self.collapse_panel()
            return
        super().keyPressEvent(e)

    # ---- icon mode --------------------------------------------------------------------
    def start(self, background: bool = False) -> None:
        """First show at startup: the window, or the icon when it was left in icon mode. Either way a
        locked vault opens straight to the password box (the icon's panel opens beside it), except
        a Windows sign-in start (background), which waits in the tray or as the icon."""
        if self.config.icon_mode:
            self.enter_icon_mode()
            if not self.vc.is_unlocked and not background:
                self.open_panel()
        elif not background:  # a Windows sign-in start waits in the tray
            self.show()
        self.apply_hotkey()

    def enter_icon_mode(self) -> None:
        """Shrink to the floating icon (palette pulled past its minimum width, or the menus)."""
        if self.icon_mode:
            return
        if self.isVisible():
            if self.frame.is_maximized():
                self.frame.toggle_maximize()
            if self.compact:
                self.config.compact_width = self._drag_start[0] if self._shrinking else self.width()
            elif not self.isMaximized():
                self.config.full_width = self.width()
                self.config.full_height = max(self.height(), FULL_MIN_HEIGHT)
            self.remember_geometry()
        self._shrinking = False
        g = self.frameGeometry()
        if len(self.config.icon_pos) < 2:  # first time: where the window's top-right corner was
            self.config.icon_pos = [g.right() - ICON_SIZE + 1, g.top()]
        self.icon_mode = self.config.icon_mode = True
        self.config.save()
        self.hide()
        if not self.compact:  # the panel is always the palette
            self.resize(self._compact_width(), self.height())
        self.vault_view.pin_btn.setVisible(False)  # always on top while it's the icon
        self._apply_on_top()
        self._update_icon_lock()
        primary = QApplication.primaryScreen().availableGeometry()
        self.icon.place(QPoint(*self.config.icon_pos[:2]),
                        QPoint(primary.right() - ICON_SIZE - 40, primary.top() + 80))
        self.icon.show()
        self.icon.raise_()

    def _pulled_past_minimum(self) -> None:
        """A palette side edge was dragged well past the minimum width: end the drag, shrink."""
        if self._shrinking:
            return
        self._shrinking = True
        self.frame.end_drag()
        if self.icon_mode:  # the panel: just shrink back to the icon
            QTimer.singleShot(0, self.collapse_panel)
        else:
            QTimer.singleShot(0, self.enter_icon_mode)

    def _drop_icon_mode(self) -> None:
        self.icon_mode = self.config.icon_mode = False
        self.config.save()
        self.icon.hide()
        self.vault_view.pin_btn.setVisible(True)
        self._apply_on_top()

    def leave_icon_mode(self, full: bool = True) -> None:
        """Back to a normal window (full or compact), opened where the icon is."""
        if not self.icon_mode:
            return
        anchor = self.icon.frameGeometry()
        shown = self.isVisible()
        self._drop_icon_mode()
        if full:
            w, h = self._full_width(), self._full_height()
        else:
            w, h = self._compact_width(), self.compact_height()
        self.resize(w, h)
        if not shown:
            self.move(panel_position(anchor, w, h, screen_area(anchor)))
        self.bring_to_front()
        self._keep_on_screen()

    def open_panel(self) -> None:
        """Icon clicked: the quick-search palette opens beside it (on the icon's own screen)."""
        if not self.icon_mode:
            self.bring_to_front()
            return
        self._shrinking = False
        anchor = self.icon.frameGeometry()
        w = self._compact_width()
        if self.width() != w:
            self.resize(w, self.height())
        self._fit_height()
        pos = panel_position(anchor, w, COMPACT_MAX_HEIGHT, screen_area(anchor))
        self.move(pos)
        self.icon.hide()  # the icon grows into the bar; it's back when the bar closes
        self.show()
        self._apply_on_top(force=True)
        self.raise_()
        self.activateWindow()
        self.focus_input()
        # Opened on a screen with other scaling: Qt may settle the size and place a moment later.
        QTimer.singleShot(0, lambda: self._settle(pos))

    def _settle(self, pos: QPoint) -> None:
        if self.icon_mode and self.isVisible() and self.pos() != pos:
            self.move(pos)

    def collapse_panel(self) -> None:
        """Esc, a click elsewhere, ✕ or ─: the panel goes, the icon stays."""
        if not self.icon_mode or not self.isVisible() or self._dialog is not None:
            return
        self.close_settings()
        self.close_help()
        if self.compact:
            self.config.compact_width = self._drag_start[0] if self._shrinking else self.width()
        self._shrinking = False
        self.hide()
        self.icon.show()
        self._collapsed_at = time.monotonic()

    def _collapse_if_away(self) -> None:
        """The panel lost focus: shrink back unless focus went to one of our own windows
        (an editor, a menu, the agent prompt)."""
        if not self.icon_mode or not self.isVisible() or self.isActiveWindow():
            return
        if self._dialog is not None or QApplication.activePopupWidget() is not None \
                or QApplication.activeModalWidget() is not None or QApplication.activeWindow() is not None:
            return
        self.collapse_panel()

    def _icon_clicked(self) -> None:
        if self.isVisible():
            self.collapse_panel()
        elif time.monotonic() - self._collapsed_at > 0.4:  # (not the click that just closed it)
            self.open_panel()

    def _icon_moved(self, pos: QPoint) -> None:
        self.config.icon_pos = [pos.x(), pos.y()]
        self.config.save()

    def _update_icon_lock(self) -> None:
        self.icon.set_locked(not self.vc.is_unlocked)
        self.icon.unlocked_vault = self.vc.is_unlocked

    def _minimize(self) -> None:
        if self.icon_mode:
            self.collapse_panel()
        else:
            self.frame.minimize()

    # ---- global shortcut ----------------------------------------------------------------
    def apply_hotkey(self) -> None:
        """Register (or drop) the global shortcuts from Settings (search, Auto-Type); hotkey_error says
        why one failed."""
        c = self.config
        errors = []
        for on, text, hid, what in ((c.hotkey_enabled, c.hotkey, hotkey.HOTKEY_ID, "search"),
                                    (c.autotype_hotkey_enabled, c.autotype_hotkey, hotkey.AUTOTYPE_ID, "Auto-Type"),
                                    (c.newlogin_hotkey_enabled, c.newlogin_hotkey, hotkey.NEWLOGIN_ID, "New login")):
            if not on:
                if native_frame_available():
                    hotkey.unregister(int(self.winId()), hid)
                continue
            if hotkey.parse(text) is None:
                errors.append(f"{text} can't be a shortcut: use Ctrl, Alt or Win with a key.")
            elif hid != hotkey.HOTKEY_ID and (clash := self._shortcut_clash(hid, text)):
                if native_frame_available():
                    hotkey.unregister(int(self.winId()), hid)
                errors.append(f"{text} is already the {clash} shortcut. Pick another for {what} in Settings.")
            elif native_frame_available() and not hotkey.register(int(self.winId()), text, hid):
                errors.append(f"{text} ({what}) is already used by another program. Pick another in Settings.")
        self.hotkey_error = " ".join(errors)
        if self.hotkey_error:
            self.toast.show_message("Shortcut", self.hotkey_error, "error", 6000)

    def shared_folders(self) -> set[str]:
        """The folder shared with agents and its subfolders (tinted in folder pickers)."""
        return agent_access.shared_ids_for(self.config, self.vc.snapshot)

    def _shortcut_clash(self, hid: int, text: str) -> str:
        """Which shortcut registered before this one already uses `text` ("" if none)."""
        c = self.config
        if c.hotkey_enabled and hotkey.same(text, c.hotkey):
            return "search"
        if hid == hotkey.NEWLOGIN_ID and c.autotype_hotkey_enabled and hotkey.same(text, c.autotype_hotkey):
            return "Auto-Type"
        return ""

    def summon(self) -> None:
        """The global shortcut: open the search (or put it away if it's already in front)."""
        if self.isVisible() and self.isActiveWindow():
            self.hide_to_tray()
        elif self.icon_mode:
            self.open_panel()
        else:
            self.bring_to_front()

    # ---- help ----------------------------------------------------------------------
    def open_help(self) -> None:
        if self.help_page is not None or self.settings_page is not None or self._dialog is not None:
            return
        page = HelpPage()
        page.done.connect(self.close_help)
        self.help_page = page
        self.vault_view.panel.close_panel()
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        page.setFocus()

    def close_help(self) -> None:
        page, self.help_page = self.help_page, None
        if page is None:
            return
        if self.vc.is_unlocked:
            self.stack.setCurrentWidget(self.vault_view)
            self.vault_view.focus_search()
        self.stack.removeWidget(page)
        page.deleteLater()

    def _update_titlebar(self, _index: int = 0) -> None:
        """The vault's search row lives in the title bar; the entry form shows its name and the pin;
        other screens just show the app name."""
        current = self.stack.currentWidget()
        if current is self.vault_view:
            self.titlebar.set_controls(self.vault_view.top_bar)
        elif isinstance(current, EntryDialog):
            self.titlebar.set_controls(_EditorBar(current.windowTitle(), self.vault_view.pin_btn))
        else:
            self.titlebar.set_controls(None)
        self.titlebar.set_brand(self.stack.currentWidget() not in (self.unlock_view, self.pin_view))

    def focus_input(self) -> None:
        if self.settings_page is not None:
            self.settings_page.setFocus()
        elif self.help_page is not None:
            self.help_page.setFocus()
        elif self.vc.is_unlocked:
            self.vault_view.focus_search()
        elif self.vc.is_pin_locked:
            self.pin_view.focus()
        else:
            self.unlock_view.focus()

    # ---- window / tray behaviour ----------------------------------------------------
    def bring_to_front(self) -> None:
        if self.icon_mode:  # tray click, second start, shortcut: the panel beside the icon
            self.open_panel()
            return
        if self.isMinimized():  # back to how it was (maximised stays maximised)
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        self.focus_input()

    def remember_geometry(self) -> None:
        """Store the window's position and size so the next start opens it in the same place."""
        g = bytes(self.saveGeometry().toBase64()).decode("ascii")
        if g != self.config.window_geometry:
            self.config.window_geometry = g
            self.config.save()

    def hide_to_tray(self) -> None:
        if self.icon_mode:
            self.collapse_panel()
            return
        self.remember_geometry()
        self.hide()
        self.hidden_to_tray.emit()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        if self.allow_close:
            self.remember_geometry()
            e.accept()
        else:
            e.ignore()
            QTimer.singleShot(0, self._close_clicked)  # not inside closeEvent: it may open a dialog

    def _close_clicked(self) -> None:
        """The X (or Alt+F4): to the tray or quit, as set in Settings, asking the first time."""
        action = self.config.close_action
        if self.icon_mode or self._dialog is not None:
            action = "tray"  # the icon's panel just shrinks back to the icon
        elif action not in ("tray", "quit"):
            answer = small_dialogs.ask_close(self)
            if answer is None:
                return
            action, remember = answer
            if remember:
                self.config.close_action = action
                self.config.save()
        if action == "quit" and self.can_quit:
            self.quit_requested.emit()
        else:
            self.hide_to_tray()

    # ---- unlock / lock --------------------------------------------------------
    def _unlock(self, path: str, password: str, keyfile: str) -> None:
        self.vc.unlock(path, password, keyfile or None)

    def _create(self, path: str, password: str) -> None:
        self.unlock_view.show_error("")
        self.unlock_view.set_busy(True)
        self.vc.create(path, password)

    def _pin_entered(self, pin: str) -> None:
        self.vc.pin_unlock(pin)

    def _on_unlocked(self, snap: Snapshot) -> None:
        self.config.last_database = self.vc.path
        if self.unlock_view._path != self.vc.path:  # e.g. a database just created from the welcome screen
            self.unlock_view._path = self.vc.path
            self.unlock_view._refresh()
        self.config.last_keyfile = self.unlock_view._keyfile
        self.config.remember_database(self.vc.path, self.config.last_keyfile)
        self.unlock_view.set_recent(self.config.recent_databases)
        self.config.save()
        self.unlock_view.reset()
        self.pin_view.set_busy(False)
        self.vault_view.set_snapshot(snap)
        self.stack.setCurrentWidget(self.vault_view)
        self.vault_view.focus_search()
        shared = agent_access.shared_folder_path(self.config, snap) if self.config.cli_enabled else None
        if shared:  # a reminder, so a setting changed behind your back is noticed
            self.toast.show_message("Shared with agents", f"{shared} (each request asks you)", "info", 4000)

    def _on_unlock_failed(self, message: str) -> None:
        self.unlock_view.set_busy(False)
        self.unlock_view.show_error(message)
        self.unlock_view.password.selectAll()
        self.unlock_view.password.setFocus()

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.reject()

    def _on_locked(self, reason: str) -> None:
        self._close_dialog()
        self.close_settings()
        self.close_help()
        self.clipboard.wipe()
        self.vault_view.clear()
        self.unlock_view.reset(reason)
        self.stack.setCurrentWidget(self.unlock_view)

    def close_database(self) -> None:
        """Save settings, then lock completely: the unlock screen, where Change picks another file."""
        self.close_settings()
        if self.vc.is_unlocked or self.vc.is_pin_locked:
            self.vc.lock(full=True)

    def open_other_database(self, path: str, keyfile: str = "") -> None:
        """Close this database and get ready to open `path` (or browse for one when it's empty)."""
        self.close_database()
        if path:
            self.unlock_view.select_database(path, keyfile)
        else:
            self.unlock_view.browse()

    def switch_database(self) -> None:
        """Close the database completely (master password needed again) and offer the recent ones."""
        self.bring_to_front()
        if self.vc.is_unlocked or self.vc.is_pin_locked:
            self.vc.lock(full=True)
        self.unlock_view.open_chooser()

    def _on_pin_locked(self, reason: str) -> None:
        self._close_dialog()
        self.close_settings()
        self.close_help()
        self.clipboard.wipe()
        self.vault_view.clear(remember=True)
        self.pin_view.reset(Path(self.vc.path).name, reason)
        self.stack.setCurrentWidget(self.pin_view)

    def _on_busy(self, busy: bool) -> None:
        if not self.vc.is_unlocked or self._icons_running:
            return
        if busy:
            self.vault_view.set_status("Working…", theme.WARN)
        else:
            self.vault_view.update_status()

    # ---- entries ------------------------------------------------------------
    def _on_entries_changed(self, snap: Snapshot, reason: str) -> None:
        self.vault_view.set_snapshot(snap)
        if reason == "saved":
            self.toast.show_message("Saved")
        elif reason == "merged":
            self.toast.show_message("Saved", "merged with changes from another device", "info", 2600)
        elif reason == "reloaded":
            self.toast.show_message("Vault updated", "changes synced from another device", "info", 2400)

    def _on_save_failed(self, edit: EntryEdit | None, message: str) -> None:
        self.vault_view.update_status()
        if edit is None:
            self.toast.show_message("Not saved", message, "error", 4000)
            return
        self.toast.show_message("Not saved", "see the dialog", "error", 3000)
        view = next((v for v in self.vault_view._views if v.uuid == edit.uuid), None)
        self.open_editor(view, draft=edit, error=f"Could not save: {message}")

    def _run_dialog(self, dlg: QDialog) -> int:
        self._dialog = dlg
        try:
            return dlg.exec()
        finally:
            self._dialog = None
            if self.vc.is_unlocked:
                self.vault_view.focus_search()

    def open_editor(self, view: EntryView | None, draft: EntryEdit | None = None, error: str = "") -> None:
        if not self.vc.is_unlocked or self._dialog is not None:
            return
        if view is not None and draft is None and self.vault_view.in_bin:
            return  # in the recycle bin: Restore first
        snap = self.vc.snapshot
        dlg = EntryDialog(self, view, draft, error,
                          groups=snap.groups if snap else [], root_uuid=snap.root_uuid if snap else "",
                          default_group=self.vault_view.folder_uuid,
                          usernames=[n for n in known_usernames(snap.entries) if not self.config.username_hidden(n)]
                          if snap else [], hide_username=self._hide_username,
                          copy_secret=self._copy_generated, shared=self.shared_folders())
        dlg.finished.connect(lambda result, d=dlg: self._editor_done(d, result))
        self._show_editor(dlg)

    def _show_editor(self, dlg: QDialog) -> None:
        """The entry form opens inside the main window (a page, like Settings), so the window can still
        be moved, resized and pinned or unpinned while editing. Esc or Cancel goes back to the list."""
        dlg.setWindowFlags(Qt.Widget)
        dlg.setMinimumSize(0, 0)
        self._dialog = dlg
        self.vault_view.panel.close_panel()
        self.stack.addWidget(dlg)
        self.stack.setCurrentWidget(dlg)
        dlg.show()
        dlg.title.setFocus()

    def _editor_done(self, dlg: QDialog, result: int) -> None:
        if self._dialog is dlg:
            self._dialog = None
        if self.stack.currentWidget() is dlg:
            self.stack.setCurrentWidget(self.vault_view)
        self.stack.removeWidget(dlg)
        dlg.deleteLater()
        if result and dlg.result_edit is not None and self.vc.is_unlocked:
            if self.config.unhide_username(dlg.result_edit.username):  # used again: suggest it again
                self.config.save()
            self.vc.save(dlg.result_edit)
        if self.vc.is_unlocked:
            self.vault_view.focus_search()

    def _copy_generated(self, password: str) -> int:
        """The editor's dice: copy the new password (auto-clears, kept out of clipboard history)."""
        self.clipboard.copy(password)
        return self.clipboard.clear_seconds

    def _hide_username(self, name: str) -> None:
        self.config.hide_username(name)
        self.config.save()

    def _fit_to_compact(self, dlg: QDialog) -> None:
        """Open the entry form over the compact window, as wide as it, instead of a big dialog."""
        g = self.frameGeometry()
        area = self.screen().availableGeometry()
        dlg.setMinimumWidth(340)
        w = max(g.width(), 360)
        h = min(max(g.height(), 560), area.height())
        dlg.resize(w, h)
        x = min(max(g.x(), area.left()), area.right() - w + 1)
        y = min(max(g.y(), area.top()), area.bottom() - h + 1)
        dlg.move(x, y)

    def delete_entry(self, view: EntryView) -> None:
        if not self.vc.is_unlocked or self._dialog is not None or self.vault_view.in_bin:
            return
        if small_dialogs.confirm(self, "Delete entry",
                                 f"Move “{view.title or 'untitled'}” to the recycle bin? "
                                 "You can restore it from Recycle bin in the folder list.",
                                 "Delete", danger=True):
            self.vault_view.panel.close_panel()
            self.vc.apply([DeleteEntries([view.uuid])], token=("ui", "Moved to recycle bin"))

    def restore_entry(self, view: EntryView) -> None:
        """Out of the recycle bin, into the folder you pick."""
        snap = self.vc.snapshot
        if not self.vc.is_unlocked or self._dialog is not None or snap is None:
            return
        uuid = small_dialogs.ask_folder(self, "Restore entry", f"Put “{view.title or 'untitled'}” back in:",
                                        snap.groups, snap.root_uuid, self.shared_folders(), "Restore")
        if uuid:
            self.vault_view.panel.close_panel()
            self.vc.apply([MoveEntries([view.uuid], group_uuid=uuid)], token=("ui", "Restored"))

    # ---- folders -------------------------------------------------------------------
    def _group(self, uuid: str):
        snap = self.vc.snapshot
        return next((g for g in snap.groups if g.uuid == uuid), None) if snap else None

    def create_folder(self) -> None:
        if not self.vc.is_unlocked:
            return
        parent = self._group(self.vault_view.folder_uuid) if self.vault_view.folder_uuid else None
        hint = f"Created inside “{parent.name}”. Use / for deeper levels." if parent else "Use / for a subfolder, e.g. Work/Clients"
        name = small_dialogs.ask_text(self, "New folder", hint, placeholder="Folder name")
        if name:
            path = "/".join([*parent.path, name]) if parent else name
            self.vc.apply([CreateFolder(path)], token=("ui", "Folder created"))

    def rename_folder(self, uuid: str) -> None:
        g = self._group(uuid)
        if not g:
            return
        name = small_dialogs.ask_text(self, "Rename folder", "New name", g.name)
        if name and name != g.name:
            if "/" in name:
                self.toast.show_message("Folder names can't contain /", kind="warn")
                return
            self.vc.apply([RenameFolder(uuid, name)], token=("ui", "Folder renamed"))

    def delete_folder(self, uuid: str) -> None:
        g = self._group(uuid)
        snap = self.vc.snapshot
        if not g or not snap:
            return
        inside = sum(1 for v in snap.entries if v.group == g.display or v.group.startswith(g.display + " / "))
        what = f"its {inside} entr{'y' if inside == 1 else 'ies'} and subfolders" if inside else "its subfolders"
        if small_dialogs.confirm(self, "Delete folder",
                                 f"Move “{g.name}” with {what} to the recycle bin?", "Delete", danger=True):
            if self.vault_view.folder_uuid == uuid:
                self.vault_view.set_folder(None)
            self.vc.apply([DeleteFolder(uuid)], token=("ui", "Folder moved to recycle bin"))

    def _on_applied(self, token, ok: bool, msg: str, _report=None) -> None:
        if not isinstance(token, tuple):
            return
        if token[0] == "ui":
            if ok:
                self.toast.show_message(token[1])
            else:
                self.toast.show_message("Not saved", msg, "error", 4000)
        elif token[0] == "master":
            if ok:
                self.toast.show_message("Master password changed", "use the new one from now on", "ok", 3000)
            else:
                self.toast.show_message("Password not changed", msg, "error", 5000)

    # ---- settings ------------------------------------------------------------------
    def show_settings(self) -> None:
        """From the tray or the floating icon's menu: bring the window (or the panel) up on Settings."""
        self.bring_to_front()
        self.open_settings()  # does nothing while locked: the unlock screen shows instead
        if self.icon_mode:
            self._fit_height()

    def open_settings(self) -> None:
        if not self.vc.is_unlocked or self._dialog is not None or self.settings_page is not None:
            return
        self.close_help()
        snap = self.vc.snapshot
        page = SettingsPage(self.config, self.vc.path, snap.groups if snap else (),
                            profiles=[v for v in snap.entries if v.is_identity] if snap else (),
                            shared=self.shared_folders())
        page.change_master.connect(self._change_master)
        page.pin_toggled.connect(self._pin_toggled)
        page.change_pin.connect(self._set_pin)
        page.download_icons.connect(lambda missing: (self.close_settings(), self._download_icons(missing)))
        page.done.connect(self.close_settings)
        page.open_database.connect(self.open_other_database)
        page.close_database.connect(self.close_database)
        self.settings_page = page
        self.vault_view.panel.close_panel()
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        page.setFocus()

    def _apply_suggest(self) -> None:
        self.vault_view.suggest = self.config.autotype_suggest
        self.vault_view.set_fill_target(self.vault_view.fill_target)

    def close_settings(self) -> None:
        """Save the settings page and go back to the list (or wherever a lock sends us)."""
        page, self.settings_page = self.settings_page, None
        if page is None:
            return
        page.save()
        self.clipboard.clear_seconds = self.config.clipboard_clear_seconds
        self.settings_saved.emit()
        if self.vc.is_unlocked:
            self.stack.setCurrentWidget(self.vault_view)
            self.vault_view.focus_search()
        self.stack.removeWidget(page)
        page.deleteLater()

    def _change_master(self) -> None:
        vault = self.vc._vault
        got = small_dialogs.ask_master_change(self, lambda pw: bool(vault) and vault.check_password(pw))
        if got:
            err = self.vc.change_master_password(*got)
            if err:
                self.toast.show_message("Password not changed", err, "error", 4000)
            else:
                self.toast.show_message("Changing master password…", kind="info")

    def _set_pin(self) -> bool:
        pin = small_dialogs.ask_new_pin(self)
        if not pin:
            return False
        self.config.pin_salt, self.config.pin_hash = pinlib.make(pin)
        self.config.pin_enabled = True
        self.config.save()
        self.toast.show_message("PIN set", "locking now asks for the PIN")
        return True

    def _pin_toggled(self, on: bool) -> None:
        page = self.settings_page
        if on:
            ok = self._set_pin()
            if page is not None:
                page.set_pin_checked(ok)
        else:
            self.config.pin_enabled = False
            self.config.pin_salt = self.config.pin_hash = ""
            self.config.save()
            if page is not None:
                page.set_pin_checked(False)

    # ---- website icons -------------------------------------------------------------
    def _download_icons(self, missing_only: bool) -> None:
        snap = self.vc.snapshot
        if not snap:
            return
        todo = [e for e in snap.entries if e.url and not (missing_only and e.icon_uuid)]
        self._icons_asked = True
        if not self.vc.download_icons(todo):
            self._icons_asked = False
            self.toast.show_message("No icons to fetch", "entries need a website URL", "info")

    def _on_icon_progress(self, done: int, total: int) -> None:
        if total:
            self._icons_running = True
            self.vault_view.set_status(f"Downloading website icons  {done}/{total}", theme.ACCENT_SOFT)
            return
        self._icons_running = False
        self.vault_view.update_status()
        asked, self._icons_asked = self._icons_asked, False
        if done:
            self.toast.show_message(f"Found {done} website icon{'s' if done != 1 else ''}", "saving to vault", "info")
        elif asked:  # stay quiet when the automatic fetch on save finds nothing
            self.toast.show_message("No website icons found", kind="warn")

    # ---- clipboard --------------------------------------------------------------
    def copy_text(self, label: str, value: str) -> None:
        if not value:
            self.toast.show_message(f"{label} is empty", kind="warn")
            return
        self.clipboard.copy(value)
        self.toast.show_message(f"Copied {label}", f"clears in {self.clipboard.clear_seconds}s")

    def copy(self, kind: str, view: EntryView) -> None:
        if view.is_card and kind == "otp":
            kind = "card_code"  # Ctrl+T on a card copies its code
        if view.is_card and kind == "username":
            kind = "card_number"  # Ctrl+B on a card copies its number
        if view.is_card and kind == "password" and not view.password:
            kind = "card_number"  # Enter on a card without a password copies its number
        if kind == "otp":
            if view.totp is None:
                self.toast.show_message("No OTP for this entry", kind="warn")
                return
            value = otplib.current(view.totp)[0]
        else:
            value = getattr(view, kind, "")
        label = _LABELS[kind]
        if not value:
            self.toast.show_message(f"No {label.lower()} to copy", kind="warn")
            return
        self.clipboard.copy(value)
        self.toast.show_message(f"Copied {label}", f"clears in {self.clipboard.clear_seconds}s")


def enable_dark_title_bar(widget: QWidget) -> None:
    """Ask DWM for a dark title bar (Windows 10 1809+ / 11)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), 4) == 0:
                break
    except Exception:
        pass
