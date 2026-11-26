"""The unlocked screen: folder filter, search bar, entry list, detail panel, footer."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeyEvent, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget)

from .. import icons, theme
from ..vault import EntryView, GroupView, Snapshot
from .detail_panel import DetailPanel
from .entry_row import ROW_HEIGHT, EntryRow
from .folder_popup import FolderPopup


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
    hide_requested = Signal()
    delete_requested = Signal(object)      # EntryView
    settings_requested = Signal()
    folder_create_requested = Signal()
    folder_rename_requested = Signal(str)  # folder uuid
    folder_delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._views: list[EntryView] = []
        self._rows: list[EntryRow] = []
        self._groups: list[GroupView] = []
        self._pixmaps: dict[str, QPixmap] = {}
        self._folder: GroupView | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 0)
        root.setSpacing(12)

        # top bar
        bar = QHBoxLayout()
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
        self.search.textChanged.connect(self._apply_filter)
        self.search.installEventFilter(self)
        bar.addWidget(self.search, 1)

        self.add_btn = QPushButton("  Add")
        self.add_btn.setObjectName("Primary")
        self.add_btn.setIcon(icons.glyph(icons.ADD, "white", 14))
        self.add_btn.setToolTip("New entry (Ctrl+N)")
        self.add_btn.setFocusPolicy(Qt.NoFocus)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setFixedHeight(42)
        self.add_btn.clicked.connect(self.add_requested)
        bar.addWidget(self.add_btn)

        self.settings_btn = _top_button(icons.SETTINGS, "Settings")
        self.settings_btn.clicked.connect(self.settings_requested)
        bar.addWidget(self.settings_btn)
        self.lock_btn = _top_button(icons.LOCK, "Lock vault (Ctrl+L)")
        self.lock_btn.clicked.connect(self.lock_requested)
        bar.addWidget(self.lock_btn)
        root.addLayout(bar)

        # list
        self.list = QListWidget()
        self.list.setObjectName("Entries")
        self.list.setFocusPolicy(Qt.NoFocus)  # keyboard focus stays in the search box
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.verticalScrollBar().setSingleStep(24)
        self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(self._row_clicked)
        self.list.itemDoubleClicked.connect(lambda it: self._edit_row(self.list.row(it)))
        self.list.currentRowChanged.connect(self._follow_selection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        root.addWidget(self.list, 1)

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
        hints = QLabel("↵ Pass  ·  Ctrl+B User  ·  Ctrl+T OTP  ·  Ctrl+I Details  ·  Ctrl+N New")
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
        self.panel.closed.connect(self.focus_search)

        self._otp_timer = QTimer(self, interval=500)
        self._otp_timer.timeout.connect(self._tick_otp)
        self._update_folder_button()

    # ---- data ------------------------------------------------------------
    def set_snapshot(self, snap: Snapshot) -> None:
        """Replace the list, keeping search text, folder, selection and scroll position."""
        keep_uuid = self.current().uuid if self.current() else None
        scroll = self.list.verticalScrollBar().value()
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
        self._views, self._rows = snap.entries, []
        for v in snap.entries:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            row = EntryRow(v, self.pixmap_for(v))
            row.copy_requested.connect(lambda kind, it=item: self._row_copy(it, kind))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self._rows.append(row)
        self.list.blockSignals(False)
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
        self._folder = None
        self._update_folder_button()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)

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
        name = self._folder.name if self._folder else "All"
        self.folder_btn.setText(f" {name if len(name) <= 16 else name[:15] + '…'}")
        self.folder_btn.setProperty("active", self._folder is not None)
        self.folder_btn.style().polish(self.folder_btn)

    def _folder_menu(self) -> None:
        counts: dict[str, int] = {}
        for v in self._views:
            counts[v.group_uuid] = counts.get(v.group_uuid, 0) + 1
        pop = FolderPopup(self, self._groups, counts, len(self._views), self.folder_uuid)
        pop.selected.connect(lambda uuid: self.set_folder(next((g for g in self._groups if g.uuid == uuid), None)))
        pop.rename_requested.connect(self.folder_rename_requested)
        pop.delete_requested.connect(self.folder_delete_requested)
        pop.create_requested.connect(self.folder_create_requested)
        pop.popup_below(self.folder_btn)

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

    # ---- filtering & navigation ---------------------------------------------
    def _apply_filter(self, _text: str = "", keep_uuid: str | None = None) -> None:
        terms = self.search.text().lower().split()
        first_visible, keep_row = -1, -1
        for i, v in enumerate(self._views):
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
        self.empty.setVisible(not has)
        if not has:
            self.panel.close_panel()
            if not self._views:
                self.empty.setText("Your vault is empty.\nPress Ctrl+N to add the first entry.")
            elif terms:
                self.empty.setText("No matches")
            else:
                self.empty.setText("This folder is empty")
        self.update_status()

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
        items = (("Copy card number", "card_number", v.card_number), ("Copy password", "password", v.password),
                 ("Copy card code", "card_code", v.card_code), ("Copy URL", "url", v.url)) if v.is_card else             (("Copy username", "username", v.username), ("Copy password", "password", v.password),
             ("Copy one-time code", "otp", v.totp), ("Copy URL", "url", v.url))
        for text, kind, ok in items:
            a = QAction(text, m)
            a.setEnabled(bool(ok))
            a.triggered.connect(lambda _=False, k=kind: self.copy_requested.emit(k, v))
            m.addAction(a)
        m.addSeparator()
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

    def toggle_details(self) -> None:
        if self.panel.isVisible():
            self.panel.close_panel()
        else:
            self.open_details()

    def _follow_selection(self, row: int) -> None:
        if self.panel.isVisible() and 0 <= row < len(self._views) and not self.list.isRowHidden(row):
            v = self._views[row]
            if not self.panel.view or self.panel.view.uuid != v.uuid:
                self.panel.show_entry(v, self.pixmap_for(v))

    def _panel_rect(self):
        top = self.list.geometry().top() if self.list.isVisible() else self.empty.geometry().top()
        bottom = self.footer.geometry().top() - 8
        w = self.width() - 32
        pw = w if w < 600 else max(360, min(440, int(w * 0.6)))
        r = self.rect()
        r.setLeft(self.width() - 16 - pw)
        r.setRight(self.width() - 16)
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
            }
        elif ctrl:
            actions = {
                Qt.Key_B: lambda: self._copy_current("username"),
                Qt.Key_T: lambda: self._copy_current("otp"),
                Qt.Key_N: self.add_requested.emit,
                Qt.Key_E: lambda: self._edit_row(self.list.currentRow()),
                Qt.Key_I: self.toggle_details,
                Qt.Key_L: self.lock_requested.emit,
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

    def _escape(self) -> None:
        if self.panel.isVisible():
            self.panel.close_panel()
        elif self.search.text():
            self.search.clear()
        elif self._folder is not None:
            self.set_folder(None)
        else:
            self.hide_requested.emit()
