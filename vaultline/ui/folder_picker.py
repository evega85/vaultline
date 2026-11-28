"""Folder picker: a drop-down whose list is a tree. With a shared vault open, each vault is a top-level
row (Personal, Shared...) that folds open to show its folders, so "move to folder" is also "move to vault".

It is a QComboBox underneath (one flat item per row: findData / currentData / setCurrentIndex work as
usual, and mark_shared_folders can tint rows); only the list that opens is replaced by the tree.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QComboBox, QFrame, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout

from .. import icons, theme
from ..vault import GroupView

PARENT_ROLE = Qt.UserRole + 1   # flat index of the row this one sits under (-1 = top)
VAULT_ROLE = Qt.UserRole + 2    # True on a vault row (Personal / Shared)
MAX_H = 380


class FolderPicker(QComboBox):
    def __init__(self, groups, root_uuid: str, extras: list[tuple[str, object]] | None = None,
                 exclude: set[str] | None = None, main_name: str = "Personal", parent=None):
        super().__init__(parent)
        groups: list[GroupView] = list(groups or [])
        exclude = exclude or set()
        tops = [g for g in groups if g.vault and g.depth == 0 and g.parent_uuid == root_uuid]
        self.multi = bool(tops)
        names = {g.vault: g.name for g in tops}
        index: dict[tuple[str, str], int] = {}  # (vault, folder id) -> flat index

        def add(text: str, data, parent: int, vault_row: bool = False, closed_text: str | None = None) -> int:
            self.addItem(closed_text or text, data)
            i = self.count() - 1
            self.setItemData(i, text, Qt.UserRole + 3)   # the tree's label (the box shows closed_text)
            self.setItemData(i, parent, PARENT_ROLE)
            self.setItemData(i, vault_row, VAULT_ROLE)
            return i

        def folders(vault: str, label: str) -> None:
            for g in groups:
                if g.vault != vault or g.uuid in exclude or (vault and g.depth == 0):
                    continue
                parent = index.get((vault, g.parent_uuid), index[(vault, "")])
                closed = f"{g.name}  ·  {label}" if self.multi else g.name
                index[(vault, g.uuid)] = add(g.name, g.uuid, parent, closed_text=closed)

        if self.multi:
            index[("", root_uuid)] = index[("", "")] = add(main_name, root_uuid, -1, True, f"{main_name} (top)")
            folders("", main_name)
            for t in tops:
                index[(t.vault, t.uuid)] = index[(t.vault, "")] = add(t.name, t.uuid, -1, True, f"{t.name} (top)")
                folders(t.vault, names[t.vault])
        else:
            index[("", root_uuid)] = index[("", "")] = add("Top level", root_uuid, -1)
            folders("", "")
        self.extras = {add(text.lstrip("＋+ "), data, -1) for text, data in extras or []}

    def label(self, i: int) -> str:
        return self.itemData(i, Qt.UserRole + 3) or self.itemText(i)

    def showPopup(self) -> None:  # noqa: N802
        popup = _TreePopup(self)
        popup.adjustSize()
        w = max(self.width(), 300)
        h = min(MAX_H, popup.sizeHint().height())
        popup.resize(w, h)
        below = self.mapToGlobal(QPoint(0, self.height() + 2))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None and below.y() + h > screen.bottom():
            below = self.mapToGlobal(QPoint(0, -h - 2))
        popup.move(below)
        popup.show()
        popup.tree.setFocus()


class _TreePopup(QFrame):
    def __init__(self, box: FolderPicker):
        super().__init__(box, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.box = box
        self.setObjectName("FolderTree")
        self.setStyleSheet(f"#FolderTree {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER_STRONG};"
                           f" border-radius: 8px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.tree = tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setIndentation(16)
        tree.setIconSize(QSize(14, 14))
        tree.setUniformRowHeights(True)
        tree.setStyleSheet("QTreeWidget { background: transparent; border: none; font-size: 13px; outline: 0; }"
                           "QTreeWidget::item { padding: 5px 2px; border-radius: 5px; }"
                           f"QTreeWidget::item:hover {{ background: {theme.HOVER}; }}"
                           f"QTreeWidget::item:selected {{ background: {theme.HOVER}; color: {theme.ACCENT_SOFT}; }}"
                           f"QTreeWidget::branch:selected {{ background: {theme.HOVER}; }}")
        pal = tree.palette()  # the branch column takes the palette's highlight, not the style sheet's
        pal.setColor(QPalette.Highlight, QColor(theme.HOVER))
        pal.setColor(QPalette.HighlightedText, QColor(theme.ACCENT_SOFT))
        tree.setPalette(pal)
        lay.addWidget(tree)

        items: dict[int, QTreeWidgetItem] = {}
        current = box.currentIndex()
        for i in range(box.count()):
            parent = box.itemData(i, PARENT_ROLE)
            tip = box.itemData(i, Qt.ToolTipRole)
            it = QTreeWidgetItem([box.label(i) + ("  · shared with agents" if tip else "")])
            it.setData(0, Qt.UserRole, i)
            icon = box.itemData(i, Qt.DecorationRole)  # set by mark_shared_folders (amber)
            vault_row = bool(box.itemData(i, VAULT_ROLE))
            if icon is None:
                glyph = icons.LOCK if vault_row else icons.ADD if i in box.extras else icons.FOLDER_CLOSED
                icon = icons.glyph(glyph, theme.ACCENT_SOFT if vault_row else theme.TEXT_3, 14)
            it.setIcon(0, icon)
            if tip:
                it.setToolTip(0, tip)
            if vault_row:
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
            if parent is not None and parent >= 0 and parent in items:
                items[parent].addChild(it)
            else:
                tree.addTopLevelItem(it)
            items[i] = it

        # open: everything in one vault; with several, the vault holding the current choice
        def vault_of(i: int) -> int:
            while i >= 0:
                p = box.itemData(i, PARENT_ROLE)
                if p is None or p < 0:
                    break
                i = p
            return i
        cur_vault = vault_of(current) if current >= 0 else -1
        for i, it in items.items():
            if box.itemData(i, VAULT_ROLE):
                it.setExpanded(i == cur_vault)
            elif not box.multi or vault_of(i) == cur_vault:
                it.setExpanded(True)
        if current in items:
            tree.setCurrentItem(items[current])
            tree.scrollToItem(items[current])
        tree.itemClicked.connect(self._pick)
        tree.itemActivated.connect(self._pick)

        rows = 0
        it = QTreeWidgetItemIterator(tree, QTreeWidgetItemIterator.NotHidden)
        while it.value():
            rows += 1
            it += 1
        self._rows = rows

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(300, 12 + 30 * max(4, min(self._rows + 3, 40)))

    def _pick(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        self.box.setCurrentIndex(item.data(0, Qt.UserRole))
        self.close()
