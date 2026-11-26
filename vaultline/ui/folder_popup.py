"""Folder picker popup: fixed height, scrollable, with rename / delete / new folder."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from .. import icons, theme
from ..vault import GroupView

ROW_H = 34
MAX_LIST_H = 320


class _Row(QWidget):
    def __init__(self, popup: "FolderPopup", text: str, count: int, depth: int, uuid: str | None,
                 active: bool):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10 + depth * 16, 0, 6, 0)
        lay.setSpacing(6)
        ico = QLabel()
        glyph = icons.FOLDER_CLOSED if uuid else icons.SEARCH
        ico.setPixmap(icons.glyph(glyph, theme.ACCENT_SOFT if active else theme.TEXT_3, 14).pixmap(14, 14))
        lay.addWidget(ico)
        name = QLabel(text)
        name.setStyleSheet(f"font-size: 13px; color: {theme.TEXT if active else theme.TEXT_2};"
                           f"{' font-weight: 600;' if active else ''}")
        lay.addWidget(name, 1)
        self.count = QLabel(str(count))
        self.count.setObjectName("Hint")
        lay.addWidget(self.count)
        self.tools: list[QToolButton] = []
        if uuid:
            for glyph, tip, sig in ((icons.EDIT, "Rename", popup.rename_requested),
                                    (icons.DELETE, "Delete (to recycle bin)", popup.delete_requested)):
                b = QToolButton()
                b.setIcon(icons.glyph(glyph, theme.TEXT_2, 13))
                b.setIconSize(QSize(13, 13))
                b.setFixedSize(24, 24)
                b.setToolTip(tip)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, s=sig, u=uuid: (popup.close(), s.emit(u)))
                b.hide()
                lay.addWidget(b)
                self.tools.append(b)

    def enterEvent(self, e):  # noqa: N802
        for b in self.tools:
            b.show()
        self.count.hide() if self.tools else None
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        for b in self.tools:
            b.hide()
        self.count.show()
        super().leaveEvent(e)


class FolderPopup(QFrame):
    selected = Signal(object)          # folder uuid or None for all
    rename_requested = Signal(str)
    delete_requested = Signal(str)
    create_requested = Signal()

    def __init__(self, parent: QWidget, groups: list[GroupView], counts: dict[str, int], total: int,
                 current: str | None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        inner = QFrame(self)
        inner.setObjectName("FolderPopup")
        inner.setStyleSheet(f"#FolderPopup {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER_STRONG};"
                            f" border-radius: 10px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self.list = QListWidget()
        self.list.setObjectName("FolderList")
        self.list.setStyleSheet(
            f"#FolderList {{ background: transparent; border: none; }}"
            f"#FolderList::item {{ border-radius: 6px; }}"
            f"#FolderList::item:hover {{ background: {theme.HOVER}; }}"
            f"#FolderList::item:selected {{ background: {theme.SELECTED}; }}")
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setFocusPolicy(Qt.NoFocus)

        def add(text, count, depth, uuid):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ROW_H))
            item.setData(Qt.UserRole, uuid)
            self.list.addItem(item)
            self.list.setItemWidget(item, _Row(self, text, count, depth, uuid, uuid == current))

        add("All entries", total, 0, None)
        for g in groups:
            sub = sum(c for u, c in counts.items() if u in _descendants(g, groups))
            add(g.name, sub, g.depth, g.uuid)
        self.list.itemClicked.connect(self._pick)
        self.list.setFixedHeight(min(MAX_LIST_H, ROW_H * self.list.count() + 4))
        lay.addWidget(self.list)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        lay.addWidget(line)
        new = QPushButton("  New folder")
        new.setObjectName("Ghost")
        new.setIcon(icons.glyph(icons.ADD, theme.TEXT_2, 13))
        new.setCursor(Qt.PointingHandCursor)
        new.setStyleSheet("text-align: left; padding: 6px 10px;")
        new.clicked.connect(lambda: (self.close(), self.create_requested.emit()))
        lay.addWidget(new)
        self.setFixedWidth(280)

    def _pick(self, item: QListWidgetItem) -> None:
        uuid = item.data(Qt.UserRole)
        self.close()
        self.selected.emit(uuid)

    def popup_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))
        self.show()


def _descendants(g: GroupView, groups: list[GroupView]) -> set[str]:
    return {x.uuid for x in groups if x.path[: len(g.path)] == g.path}
