"""The unlocked screen: folder filter, search bar, entry list, detail panel, footer."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeyEvent, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget)

from .. import icons, theme
from ..autotype import find_matches, is_browser_title, is_signup_title, site_from_title
from ..vault import EntryView, GroupView, Snapshot
from .detail_panel import DetailPanel
from .entry_row import COMPACT_ROW_HEIGHT, ROW_HEIGHT, EntryRow
from .folder_popup import BIN, FolderPopup


def _top_button(glyph: str, tip: str) -> QPushButton:
    b = QPushButton()
    b.setObjectName("Ghost")
    b.setIcon(icons.glyph(glyph, theme.TEXT_2, 16))
    b.setIconSize(QSize(16, 16))
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.NoFocus)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(42, 42)
    return b


class VaultView(QWidget):
    copy_requested = Signal(str, object)   # kind, EntryView
    copy_text = Signal(str, str)           # label, value (detail panel fields)
    add_requested = Signal()
    edit_requested = Signal(object)        # EntryView
    lock_requested = Signal()
    delete_requested = Signal(object)      # EntryView
    restore_requested = Signal(object)     # EntryView in the recycle bin: move it back into a folder
    settings_requested = Signal()
    folder_create_requested = Signal()
    folder_rename_requested = Signal(str)  # folder uuid
    folder_delete_requested = Signal(str)
    pin_toggled = Signal(bool)             # compact window: keep on top
    content_changed = Signal()             # compact window: the results' height changed
    help_requested = Signal()
    mode_requested = Signal()              # Ctrl+M: palette <-> full window
    icon_requested = Signal()              # menu: shrink to the floating icon
    escape_unused = Signal()               # Esc with nothing left to step back from
    fill_requested = Signal(object)        # EntryView: type it into the window you came from
    fill_field_requested = Signal(object, str)  # EntryView, field: type just that field
    new_login_requested = Signal()         # quick box: new login for the window you came from

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._views: list[EntryView] = []
        self._rows: list[EntryRow] = []
        self._groups: list[GroupView] = []
        self._pixmaps: dict[str, QPixmap] = {}
        self._folder: GroupView | None = None
        self.compact = False
        self._last_uuid: str | None = None  # last selected entry, so a view switch can bring it back
        self.fill_target = ""  # title of the window Fill would type into ("" = none)
        self.suggest = True    # quick box, nothing typed: list the logins for that window (Settings)
        self._suggested: set[str] = set()  # their uuids
        self.shared_folders = lambda: set()  # folders shared with agents (set by MainWindow), tinted
        self._snap: Snapshot | None = None
        self.in_bin = False  # showing the recycle bin instead of the vault

        root = self._root = QVBoxLayout(self)
        root.setContentsMargins(16, 4, 16, 0)
        root.setSpacing(12)

        # top bar: MainWindow shows it inside the title bar (see titlebar.TitleBar.set_controls)
        self.top_bar = QWidget()
        bar = QHBoxLayout(self.top_bar)
        bar.setContentsMargins(0, 0, 8, 0)
        bar.setSpacing(8)
        self.folder_btn = QPushButton()
        self.folder_btn.setObjectName("FolderBtn")
        self.folder_btn.setIcon(icons.glyph(icons.FOLDER_CLOSED, theme.TEXT_2, 15))
        self.folder_btn.setIconSize(QSize(15, 15))
        self.folder_btn.setFocusPolicy(Qt.NoFocus)
        self.folder_btn.setCursor(Qt.PointingHandCursor)
        self.folder_btn.setFixedHeight(42)
        self.folder_btn.setToolTip("Show one folder")
        self.folder_btn.clicked.connect(self._folder_menu)
        bar.addWidget(self.folder_btn)

        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search vault…")
        self.search.addAction(icons.glyph(icons.SEARCH, theme.TEXT_3, 16), QLineEdit.LeadingPosition)
        self.clear_action = self.search.addAction(icons.glyph(icons.CLOSE, theme.TEXT_2, 12), QLineEdit.TrailingPosition)
        self.clear_action.setToolTip("Clear search (Esc)")
        self.clear_action.setVisible(False)
        self.clear_action.triggered.connect(self._clear_search)
        self.search.textChanged.connect(lambda t: self.clear_action.setVisible(bool(t)))
        self.search.textChanged.connect(self._apply_filter)
        self.search.installEventFilter(self)
        self.search.setMaximumWidth(640)  # leaves empty title bar to drag on wide windows
        bar.addWidget(self.search, 1)

        self.add_btn = _top_button(icons.ADD, "New entry (Ctrl+N)")
        self.add_btn.clicked.connect(self.add_requested)
        bar.addWidget(self.add_btn)
        self.lock_btn = _top_button(icons.LOCK, "Lock vault (Ctrl+L)")
        self.lock_btn.clicked.connect(self.lock_requested)
        bar.addWidget(self.lock_btn)

        self.pin_btn = _top_button(icons.PIN, "Keep on top")
        self.pin_btn.setCheckable(True)
        self.pin_btn.toggled.connect(self._pin_changed)
        bar.addWidget(self.pin_btn)

        # ☰ menu (full window): Settings and Help, so the search box gets the room
        self.menu_btn = _top_button(icons.MENU, "Menu")
        self.menu = QMenu(self.menu_btn)
        self.settings_action = self.menu.addAction(icons.glyph(icons.SETTINGS, theme.TEXT_2, 16), "Settings")
        self.settings_action.triggered.connect(self.settings_requested)
        self.help_action = self.menu.addAction(icons.glyph(icons.HELP, theme.TEXT_2, 16),
                                               "Help: other devices	F1")
        self.help_action.triggered.connect(self.help_requested)
        self.menu.addSeparator()
        self.icon_action = self.menu.addAction("Shrink to a floating icon")
        self.icon_action.triggered.connect(self.icon_requested)
        self.menu_btn.clicked.connect(lambda: self.menu.popup(
            self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomRight())
            - QPoint(self.menu.sizeHint().width(), -4)))
        bar.addWidget(self.menu_btn)
        bar.addStretch(0)

        # list
        self.list = QListWidget()
        self.list.setObjectName("Entries")
        self.list.setFocusPolicy(Qt.NoFocus)  # keyboard focus stays in the search box
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.verticalScrollBar().setSingleStep(24)
        self.list.setUniformItemSizes(True)
        self.list.setMinimumHeight(1)  # the compact window can be as short as its results
        self.list.itemClicked.connect(self._row_clicked)
        self.list.itemDoubleClicked.connect(lambda it: self._edit_row(self.list.row(it)))
        self.list.currentRowChanged.connect(self._follow_selection)
        self.list.currentRowChanged.connect(self._remember_row)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        root.addWidget(self.list, 1)

        # quick box opened from another window: make a new login for it (first on a sign-up page)
        self.new_login_btn = QPushButton()
        self.new_login_btn.setObjectName("Link")
        self.new_login_btn.setCursor(Qt.PointingHandCursor)
        self.new_login_btn.setFocusPolicy(Qt.NoFocus)
        self.new_login_btn.clicked.connect(self.new_login_requested)
        self.new_login_btn.hide()
        root.addWidget(self.new_login_btn, 0, Qt.AlignLeft)

        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"font-size: 13px; color: {theme.TEXT_3}; padding: 40px;")
        self.empty.hide()
        root.addWidget(self.empty, 1)

        # footer
        self.footer = QWidget()
        self.footer.setObjectName("Footer")
        self.footer.setAttribute(Qt.WA_StyledBackground)
        f = QHBoxLayout(self.footer)
        f.setContentsMargins(2, 8, 2, 10)
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {theme.SUCCESS}; font-size: 8px;")
        self.status = QLabel()
        self.status.setObjectName("Hint")
        hints = QLabel("↵ Pass  ·  Ctrl+B User  ·  Ctrl+T OTP  ·  Ctrl+Shift+V Fill  ·  Ctrl+I Details")
        hints.setObjectName("Hint")
        f.addWidget(self.status_dot)
        f.addWidget(self.status, 1)
        f.addWidget(hints)
        root.addWidget(self.footer)

        # detail panel floats over the right side of the list
        self.panel = DetailPanel(self)
        self.panel.copy_text.connect(self.copy_text)
        self.panel.edit_requested.connect(self.edit_requested)
        self.panel.delete_requested.connect(self.delete_requested)
        self.panel.restore_requested.connect(self.restore_requested)
        self.panel.fill_requested.connect(self.fill_requested)
        self.panel.fill_field_requested.connect(self.fill_field_requested)
        self.panel.closed.connect(self.focus_search)
        self.panel.closed.connect(self._content_changed)

        self._otp_timer = QTimer(self, interval=500)
        self._otp_timer.timeout.connect(self._tick_otp)
        self._update_folder_button()

    # ---- data ------------------------------------------------------------
    def set_snapshot(self, snap: Snapshot) -> None:
        """Replace the list, keeping search text, folder, selection and scroll position."""
        keep_uuid = self.current().uuid if self.current() else None
        scroll = self.list.verticalScrollBar().value()
        self._snap = snap
        self._groups = snap.groups
        restore, self._restore = getattr(self, "_restore", None), None
        if restore:
            text, folder = restore
            self._folder = next((g for g in snap.groups if g.uuid == folder), None)
            self.search.blockSignals(True)
            self.search.setText(text)
            self.search.blockSignals(False)
        if self._folder and not any(g.uuid == self._folder.uuid for g in snap.groups):
            self._folder = None
        elif self._folder:
            self._folder = next(g for g in snap.groups if g.uuid == self._folder.uuid)
        self._update_folder_button()

        self._pixmaps = {uid: pm for uid, pm in self._pixmaps.items() if uid in snap.icons}
        for uid, data in snap.icons.items():
            if uid not in self._pixmaps:
                pm = QPixmap()
                if pm.loadFromData(data):
                    self._pixmaps[uid] = pm

        self.list.setUpdatesEnabled(False)
        self.list.blockSignals(True)
        self.list.clear()
        shown = snap.binned if self.in_bin else snap.entries
        self._views, self._rows = shown, []
        for v in shown:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, self.row_height))
            row = EntryRow(v, self.pixmap_for(v))
            row.set_compact(self.compact)
            row.copy_requested.connect(lambda kind, it=item: self._row_copy(it, kind))
            row.fill_requested.connect(lambda it=item: self._row_fill(it))
            row.set_fill_target(self.fill_target)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self._rows.append(row)
        self.list.blockSignals(False)
        self._update_suggestions(refilter=False)
        self._apply_filter(keep_uuid=keep_uuid)
        self.list.verticalScrollBar().setValue(scroll)
        self.list.setUpdatesEnabled(True)
        self._otp_timer.start()

        if self.panel.isVisible():
            shown = self.panel.view.uuid if self.panel.view else None
            fresh = next((v for v in self._views if v.uuid == shown), None)
            if fresh is None:
                self.panel.close_panel()
            else:
                self.panel.show_entry(fresh, self.pixmap_for(fresh))

    def pixmap_for(self, v: EntryView) -> QPixmap | None:
        return self._pixmaps.get(v.icon_uuid) if v.icon_uuid else None

    def clear(self, remember: bool = False) -> None:
        """Drop all entry data from the UI. remember=True brings back search and folder after a PIN unlock."""
        self._restore = (self.search.text(), self.folder_uuid) if remember else None
        self._otp_timer.stop()
        self.panel.close_panel()
        self.list.clear()
        self._views, self._rows, self._groups, self._pixmaps = [], [], [], {}
        self._suggested = set()
        self._last_uuid = None
        self._folder = None
        self._snap = None
        self.in_bin = False
        self.panel.set_bin_mode(False)
        self._update_folder_button()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)

    def set_fill_target(self, title: str) -> None:
        self.fill_target = title
        self._update_suggestions()
        self._update_new_login()
        self.panel.set_fill_target(title)
        for row in self._rows:
            row.set_fill_target(title)

    @property
    def views(self) -> list[EntryView]:
        """The vault's entries (never the recycle bin's, even while it's shown)."""
        return self._snap.entries if self._snap is not None else self._views

    def current(self) -> EntryView | None:
        row = self.list.currentRow()
        if 0 <= row < len(self._views) and not self.list.isRowHidden(row):
            return self._views[row]
        return None

    @property
    def groups(self) -> list[GroupView]:
        return self._groups

    @property
    def folder_uuid(self) -> str | None:
        return self._folder.uuid if self._folder else None

    def set_status(self, text: str, color: str = theme.SUCCESS) -> None:
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 8px;")
        self.status.setText(text)

    def update_status(self) -> None:
        n = len(self._views)
        shown = sum(1 for i in range(n) if not self.list.isRowHidden(i))
        total = f"{n} entr{'y' if n == 1 else 'ies'}"
        text = total if shown == n else f"{shown} of {total}"
        if self._folder:
            text += f"  ·  {self._folder.name}"
        self.set_status(text)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ---- folders --------------------------------------------------------------
    def _update_folder_button(self) -> None:
        name = "Recycle bin" if self.in_bin else self._folder.name if self._folder else "All"
        self.folder_btn.setText("" if self.compact else f" {name if len(name) <= 16 else name[:15] + '…'}")
        self.folder_btn.setToolTip(f"Folder: {name}" if self.compact else "Show one folder")
        self.folder_btn.setProperty("active", self._folder is not None or self.in_bin)
        self.folder_btn.style().polish(self.folder_btn)

    def _folder_menu(self) -> None:
        entries = self._snap.entries if self._snap else []
        counts: dict[str, int] = {}
        for v in entries:
            counts[v.group_uuid] = counts.get(v.group_uuid, 0) + 1
        pop = FolderPopup(self, self._groups, counts, len(entries), BIN if self.in_bin else self.folder_uuid,
                          self.shared_folders(), len(self._snap.binned) if self._snap else -1)
        pop.selected.connect(self._folder_picked)
        pop.rename_requested.connect(self.folder_rename_requested)
        pop.delete_requested.connect(self.folder_delete_requested)
        pop.create_requested.connect(self.folder_create_requested)
        pop.popup_below(self.folder_btn)

    def _folder_picked(self, uuid) -> None:
        if uuid == BIN:
            self.show_bin(True)
            return
        if self.in_bin:
            self.show_bin(False)
        self.set_folder(next((g for g in self._groups if g.uuid == uuid), None))

    def show_bin(self, on: bool) -> None:
        """List the recycle bin (Restore puts an entry back) or go back to the vault."""
        if on == self.in_bin:
            return
        self.in_bin = on
        self._folder = None
        self.panel.close_panel()
        self.panel.set_bin_mode(on)
        if self._snap is not None:
            self.set_snapshot(self._snap)
        self._update_folder_button()
        self.focus_search()

    def set_folder(self, g: GroupView | None) -> None:
        self._folder = g
        self._update_folder_button()
        self._apply_filter()
        self.focus_search()

    def _in_folder(self, v: EntryView) -> bool:
        if self._folder is None:
            return True
        p = self._folder.display
        return v.group == p or v.group.startswith(p + " / ")

    # ---- compact window (a narrow window: the quick-search palette) ------------------
    def set_compact(self, on: bool) -> None:
        """One slim row (folder, search, Add, pin, lock), results only once you search or pick a
        folder, shorter rows, details over the results. Search, folder and selection stay as they are."""
        if on == self.compact:
            return
        keep = self.current().uuid if self.current() else self._last_uuid
        self.compact = on
        h = 36 if on else 42
        self.folder_btn.setFixedHeight(h)
        for b in (self.add_btn, self.pin_btn, self.lock_btn, self.menu_btn):
            b.setFixedSize(h, h)
        self.folder_btn.setMinimumWidth(h if on else 0)
        self.folder_btn.setMaximumWidth(h if on else 16777215)
        if on:
            self.search.setFixedHeight(h)
        else:  # natural height again: the style's padding decides, so text is never clipped
            self.search.setMinimumHeight(0)
            self.search.setMaximumHeight(16777215)
        self.search.setProperty("compact", on)
        self.search.style().polish(self.search)
        self._update_placeholder()
        self.menu_btn.setVisible(not on)
        self.top_bar.layout().setContentsMargins(0, 0, 0 if on else 8, 0)  # the title bar keeps a drag gap
        self.footer.setVisible(not on)
        self.empty.setStyleSheet(f"font-size: 13px; color: {theme.TEXT_3}; padding: {14 if on else 40}px;")
        m = 8 if on else 16
        self._root.setContentsMargins(m, 0 if on else 4, m, m if on else 0)
        rh = self.row_height
        for i, row in enumerate(self._rows):
            row.set_compact(on)
            self.list.item(i).setSizeHint(QSize(0, rh))
        self._update_folder_button()
        self._apply_filter(keep_uuid=keep)
        if self.panel.isVisible():
            QTimer.singleShot(0, lambda: self.panel.setGeometry(self._panel_rect()))

    @property
    def row_height(self) -> int:
        return COMPACT_ROW_HEIGHT if self.compact else ROW_HEIGHT

    def compact_content_height(self) -> int | None:
        """Height the compact window needs below its title bar; None = as tall as allowed."""
        if self.panel.isVisible():
            return None
        bottom = self._root.contentsMargins().bottom()
        if self.new_login_btn.isVisible():
            bottom += self.new_login_btn.sizeHint().height() + self._root.spacing()
        if self.list.isVisible():
            shown = sum(1 for i in range(self.list.count()) if not self.list.isRowHidden(i))
            return shown * (self.row_height + 2) + bottom  # +2: the item's 1px margins
        if self.empty.isVisible():
            return self.empty.sizeHint().height() + bottom
        return bottom

    def _content_changed(self) -> None:
        if self.compact:
            self.content_changed.emit()

    def set_pinned(self, on: bool) -> None:
        self.pin_btn.blockSignals(True)
        self.pin_btn.setChecked(on)
        self.pin_btn.blockSignals(False)
        self._pin_look(on)

    def _pin_changed(self, on: bool) -> None:
        self._pin_look(on)
        self.pin_toggled.emit(on)
        self.focus_search()

    def _pin_look(self, on: bool) -> None:
        # pinned: the filled head with the outline (and its needle) drawn over it, as Windows does
        self.pin_btn.setIcon(icons.layered([(icons.PIN_FILL, theme.ACCENT_SOFT), (icons.PIN, theme.ACCENT_SOFT)], 16)
                             if on else icons.glyph(icons.PIN, theme.TEXT_2, 16))
        self.pin_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(99, 102, 241, 0.22); border: 1px solid rgba(99, 102, 241, 0.45); }}"
            f"QPushButton:hover {{ background: rgba(99, 102, 241, 0.32); }}" if on else "")
        self.pin_btn.setToolTip("Kept on top (click to stop)" if on else "Keep on top")

    def _update_suggestions(self, refilter: bool = True) -> None:
        """The logins matching the window you came from, listed in the quick box before you type.
        Only listed: nothing is typed until you press Fill."""
        found = ({m.entry.uuid for m in find_matches(self.views, self.fill_target)}
                 if self.suggest and self.fill_target else set())
        changed = found != self._suggested
        self._suggested = found
        self._update_placeholder()
        if refilter and changed and self._idle():
            self._apply_filter()

    def _update_new_login(self) -> None:
        """The quick box's "New login for …" link: shown for a browser window while nothing is typed;
        above the suggestions when the page title looks like a sign-up page, below them otherwise."""
        # web pages only (or another program's own sign-up window), so it isn't offered for every window
        show = bool(self.fill_target) and self._idle() and (is_browser_title(self.fill_target)
                                                             or is_signup_title(self.fill_target))
        self.new_login_btn.setVisible(show)
        if not show:
            return
        site = site_from_title(self.fill_target) or "this window"
        if len(site) > 40:
            site = site[:39] + "…"
        signup = is_signup_title(self.fill_target)
        # QPushButton text is plain; & would be read as a shortcut marker
        self.new_login_btn.setText(f"＋ New login for “{site}”".replace("&", "&&"))
        self.new_login_btn.setToolTip("Saves a new login (your profile's email and a new password), "
                                      "then types it into the window you came from")
        want = 0 if signup else self._root.indexOf(self.list) + 1
        if self._root.indexOf(self.new_login_btn) != want:
            self._root.removeWidget(self.new_login_btn)
            self._root.insertWidget(want if signup else self._root.indexOf(self.list) + 1, self.new_login_btn,
                                    0, Qt.AlignLeft)

    def _update_placeholder(self) -> None:
        if not self.compact:
            text = "Search vault…"
        elif self._suggested:
            text = "Suggested for this window · type to search"
        else:
            text = "Search…"
        self.search.setPlaceholderText(text)

    def _idle(self) -> bool:
        if self.in_bin:
            return False
        """Compact window with nothing asked for yet: list nothing."""
        return self.compact and not self.search.text().strip() and self._folder is None

    def _apply_filter(self, _text: str = "", keep_uuid: str | None = None) -> None:
        terms = self.search.text().lower().split()
        idle = self._idle()
        first_visible, keep_row = -1, -1
        for i, v in enumerate(self._views):
            if idle:
                show = v.uuid in self._suggested
            else:
                show = self._in_folder(v) and (v.matches(terms) if terms else True)
            self.list.setRowHidden(i, not show)
            if show:
                if first_visible < 0:
                    first_visible = i
                if keep_uuid and v.uuid == keep_uuid:
                    keep_row = i
        target = keep_row if keep_row >= 0 else first_visible
        if target >= 0:
            self.list.setCurrentRow(target)
            self.list.scrollToItem(self.list.item(target))
        else:
            self.list.setCurrentRow(-1)
        has = first_visible >= 0
        self.list.setVisible(has)
        self.empty.setVisible(not has and not (idle and self._views))  # idle palette: just the bar
        if not has:
            self.panel.close_panel()
            if not self._views:
                self.empty.setText("Your vault is empty.\nPress Ctrl+N to add the first entry.")
            elif terms:
                self.empty.setText("No matches")
            else:
                self.empty.setText("This folder is empty")
        self.update_status()
        self._update_new_login()
        self._content_changed()

    # ---- filtering & navigation ---------------------------------------------

    def _move(self, step: int) -> None:
        n = self.list.count()
        row = self.list.currentRow()
        moved = 0
        i = row
        while 0 <= i + (1 if step > 0 else -1) < n and moved < abs(step):
            i += 1 if step > 0 else -1
            if not self.list.isRowHidden(i):
                row = i
                moved += 1
        if row >= 0:
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row))

    def _row_copy(self, item: QListWidgetItem, kind: str) -> None:
        self.list.setCurrentItem(item)
        self.copy_requested.emit(kind, self._views[self.list.row(item)])

    def _row_fill(self, item: QListWidgetItem) -> None:
        self.list.setCurrentItem(item)
        self.fill_requested.emit(self._views[self.list.row(item)])

    def _edit_row(self, row: int) -> None:
        if 0 <= row < len(self._views):
            self.edit_requested.emit(self._views[row])

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        v = self._views[self.list.row(item)]
        m = QMenu(self)
        if self.in_bin:  # deleted entries: put back, or look at them
            m.addAction("Restore…", lambda: self.restore_requested.emit(v))
            m.addAction("Details", lambda: self.open_details(v))
            m.exec(self.list.viewport().mapToGlobal(pos))
            return
        items = (("Copy card number", "card_number", v.card_number), ("Copy password", "password", v.password),
                 ("Copy card code", "card_code", v.card_code), ("Copy URL", "url", v.url)) if v.is_card else             (("Copy username", "username", v.username), ("Copy password", "password", v.password),
             ("Copy one-time code", "otp", v.totp), ("Copy URL", "url", v.url))
        for text, kind, ok in items:
            a = QAction(text, m)
            a.setEnabled(bool(ok))
            a.triggered.connect(lambda _=False, k=kind: self.copy_requested.emit(k, v))
            m.addAction(a)
        m.addSeparator()
        where = self.fill_target if len(self.fill_target) <= 40 else self.fill_target[:39] + "…"
        where = where.replace("&", "&&")  # not a menu accelerator
        fill = m.addAction(f"Fill into “{where}”	Ctrl+Shift+V" if where else "Fill	Ctrl+Shift+V",
                           lambda: self.fill_requested.emit(v))
        fill.setEnabled(v.autotype.enabled)
        m.addAction("Details", lambda: self.open_details(v))
        m.addAction("Edit…", lambda: self.edit_requested.emit(v))
        m.addSeparator()
        m.addAction("Delete…", lambda: self.delete_requested.emit(v))
        m.exec(self.list.viewport().mapToGlobal(pos))

    def _tick_otp(self) -> None:
        if not self.isVisible():
            return
        for i, row in enumerate(self._rows):
            if row.otp_btn is not None and not self.list.isRowHidden(i):
                row.otp_btn.update()

    # ---- detail panel ----------------------------------------------------------
    def _row_clicked(self, item: QListWidgetItem) -> None:
        v = self._views[self.list.row(item)]
        if self.panel.isVisible() and self.panel.view and self.panel.view.uuid == v.uuid:
            self.panel.close_panel()
        else:
            self.open_details(v)

    def open_details(self, v: EntryView | None = None) -> None:
        v = v or self.current()
        if v is None:
            return
        self.panel.show_entry(v, self.pixmap_for(v))
        self.panel.setGeometry(self._panel_rect())
        self.panel.open_at(self._panel_rect().topLeft(), animate=True)
        self.focus_search()
        self._content_changed()  # the compact window grows to make room

    def toggle_details(self) -> None:
        if self.panel.isVisible():
            self.panel.close_panel()
        else:
            self.open_details()

    def _remember_row(self, row: int) -> None:
        if 0 <= row < len(self._views):
            self._last_uuid = self._views[row].uuid

    def _follow_selection(self, row: int) -> None:
        if self.panel.isVisible() and 0 <= row < len(self._views) and not self.list.isRowHidden(row):
            v = self._views[row]
            if not self.panel.view or self.panel.view.uuid != v.uuid:
                self.panel.show_entry(v, self.pixmap_for(v))

    def _panel_rect(self):
        m = 8 if self.compact else 16
        top = self.list.geometry().top() if self.list.isVisible() else self.empty.geometry().top()
        bottom = self.footer.geometry().top() - 8 if self.footer.isVisible() else self.height() - m
        w = self.width() - 2 * m
        pw = w if w < 600 else max(360, min(440, int(w * 0.6)))
        r = self.rect()
        r.setLeft(self.width() - m - pw)
        r.setRight(self.width() - m)
        r.setTop(top)
        r.setBottom(bottom)
        return r

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self.panel.isVisible():
            QTimer.singleShot(0, lambda: self.panel.setGeometry(self._panel_rect()))

    # ---- keyboard ----------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.search and event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
            if self._handle_key(event, dry_run=event.type() == QEvent.ShortcutOverride):
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _handle_key(self, e: QKeyEvent, dry_run: bool) -> bool:
        key, mods = e.key(), e.modifiers() & ~Qt.KeypadModifier
        ctrl = mods == Qt.ControlModifier
        actions = {}
        if mods == Qt.NoModifier:
            actions = {
                Qt.Key_Down: lambda: self._move(1), Qt.Key_Up: lambda: self._move(-1),
                Qt.Key_PageDown: lambda: self._move(8), Qt.Key_PageUp: lambda: self._move(-8),
                Qt.Key_Return: lambda: self._copy_current("password"),
                Qt.Key_Enter: lambda: self._copy_current("password"),
                Qt.Key_Escape: self._escape,
                Qt.Key_F1: self.help_requested.emit,
            }
        elif mods == (Qt.ControlModifier | Qt.ShiftModifier):
            actions = {Qt.Key_V: self._fill_current}
        elif ctrl:
            actions = {
                Qt.Key_B: lambda: self._copy_current("username"),
                Qt.Key_T: lambda: self._copy_current("otp"),
                Qt.Key_N: self.add_requested.emit,
                Qt.Key_E: lambda: self._edit_row(self.list.currentRow()),
                Qt.Key_I: self.toggle_details,
                Qt.Key_L: self.lock_requested.emit,
                Qt.Key_M: self.mode_requested.emit,
            }
            # Ctrl+C copies the password unless the user selected text in the search box.
            if not self.search.hasSelectedText():
                actions[Qt.Key_C] = lambda: self._copy_current("password")
        fn = actions.get(key)
        if fn is None:
            return False
        if dry_run:
            return True  # claim the key so QLineEdit's own Ctrl+C etc. don't run
        fn()
        return True

    def _copy_current(self, kind: str) -> None:
        v = self.current()
        if v is not None:
            self.copy_requested.emit(kind, v)

    def _fill_current(self) -> None:
        v = self.current()
        if v is not None:
            self.fill_requested.emit(v)

    def _clear_search(self) -> None:
        self.search.clear()
        self.search.setFocus()

    def _escape(self) -> None:
        """Esc steps back: details, then the search, then the folder. It never hides the window
        (in icon mode, a last Esc shrinks the panel back to the icon: escape_unused)."""
        if self.panel.isVisible():
            self.panel.close_panel()
        elif self.search.text():
            self.search.clear()
        elif self._folder is not None:
            self.set_folder(None)
        else:
            self.escape_unused.emit()
