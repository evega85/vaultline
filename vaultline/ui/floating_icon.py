"""Icon mode: Vaultline shrunk to a small round button that floats above other windows.

Click it and it grows into the quick-search bar (the compact window) right where it sits; drag
it anywhere (it
sticks to the nearest screen edge when let go); right-click for a menu. It never takes the
keyboard focus, so clicking it doesn't steal focus from the panel or other programs.

The icon is its own small frameless tool window (no taskbar button). Being frameless, it has no
Windows minimum window size, which a window with a frame (the main window) always has.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .. import icons, theme
from .frameless import available as native_available

ICON_SIZE = 48      # logical px; the same height as the palette's bar
SNAP = 24           # it always sticks to the nearest edge; this close to a second one too (a corner)
EDGE_GAP = 6        # distance kept from a side or bottom edge it sticks to; the top is flush,
                    # like the bar it grows into
RADIUS = 8          # Windows 11's rounded window corner, as on the palette
DRAG_START = 4      # px the mouse must move before a press becomes a drag


def snap_point(pos: QPoint, size: int, area: QRect, snap: int = SNAP, gap: int = EDGE_GAP) -> QPoint:
    """Where an icon dropped at `pos` (its top-left) ends up: on the nearest screen edge of `area`
    (`gap` px in from it), and also flush with a second edge it was let go close to (a corner)."""
    lo_x, hi_x = area.left() + gap, area.right() - size + 1 - gap
    lo_y, hi_y = area.top(), area.bottom() - size + 1 - gap
    x = min(max(pos.x(), lo_x), hi_x)
    y = min(max(pos.y(), lo_y), hi_y)
    dist = {"left": x - lo_x, "right": hi_x - x, "top": y - lo_y, "bottom": hi_y - y}
    nearest = min(dist, key=dist.get)
    for edge, d in dist.items():
        if edge == nearest or d < snap:
            if edge == "left" and dist["left"] <= dist["right"]:
                x = lo_x
            elif edge == "right" and dist["right"] < dist["left"]:
                x = hi_x
            elif edge == "top" and dist["top"] <= dist["bottom"]:
                y = lo_y
            elif edge == "bottom" and dist["bottom"] < dist["top"]:
                y = hi_y
    return QPoint(x, y)


def panel_position(icon: QRect, width: int, max_height: int, area: QRect) -> QPoint:
    """Top-left of the panel, which grows out of the icon: its top-left corner where the icon's is
    (top-right when there's no room to the right), kept inside `area` (the icon's own screen)
    even when the panel grows to its full height."""
    x = icon.left() if area.right() - icon.left() + 1 >= width else icon.right() + 1 - width
    x = min(max(x, area.left()), area.right() - width + 1)
    y = min(icon.top(), area.bottom() - max_height + 1)
    return QPoint(x, max(y, area.top()))


def screen_area(rect: QRect) -> QRect:
    """Work area of the screen holding the middle of `rect` (or the primary screen)."""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


class FloatingIcon(QWidget):
    clicked = Signal()
    moved = Signal(QPoint)       # dropped somewhere (top-left, after snapping)
    open_full = Signal()
    open_compact = Signal()
    lock_requested = Signal()
    settings_requested = Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        # On Windows 11 the tile gets the palette's own rounded corners and thin border from
        # Windows (DWM); elsewhere it paints them itself on a see-through window.
        self._native = native_available()
        if not self._native:
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.setWindowTitle("Vaultline")
        self.setToolTip("Vaultline: click to search, drag to move, right-click for more")
        self.locked = True
        self.unlocked_vault = False  # True when "Lock" makes sense in the menu
        self._press: QPoint | None = None
        self._grab = QPoint()
        self._dragging = False
        self._hover = False

    # ---- state -----------------------------------------------------------------
    def set_locked(self, locked: bool) -> None:
        if locked != self.locked:
            self.locked = locked
            self.update()

    def place(self, pos: QPoint | None, fallback: QPoint) -> None:
        """Put the icon at a remembered spot, or `fallback` when that spot is on no screen now."""
        target = pos if pos is not None and QGuiApplication.screenAt(pos + QPoint(ICON_SIZE // 2, ICON_SIZE // 2)) \
            else fallback
        area = screen_area(QRect(target, self.size()))
        self.move(snap_point(target, ICON_SIZE, area))

    # ---- drawing -----------------------------------------------------------------
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self._native:
            try:
                import ctypes
                pref = ctypes.c_int(2)  # DWMWCP_ROUND, as the main window
                ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(self.winId())), 33,
                                                           ctypes.byref(pref), 4)
            except Exception:
                pass

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(theme.HOVER if self._hover else theme.BG)
        if self._native:
            p.fillRect(self.rect(), bg)
        else:
            p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(0.5, 0.5, ICON_SIZE - 1, ICON_SIZE - 1), RADIUS, RADIUS)
        logo = 28
        pm = icons.logo_pixmap(logo * 3)
        pm.setDevicePixelRatio(3)
        off = (ICON_SIZE - logo) / 2
        if self.locked:
            p.setOpacity(0.5)
        p.drawPixmap(QRectF(off, off, logo, logo), pm, QRectF(pm.rect()))
        p.setOpacity(1)
        if self.locked:  # small padlock badge on the logo's corner
            b = QRectF(off + logo - 11, off + logo - 11, 15, 15)
            p.setPen(QPen(bg, 2))
            p.setBrush(QColor(theme.WARN))
            p.drawEllipse(b)
            p.setPen(QPen(QColor(theme.BG), 1.3))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(b.x() + 5, b.y() + 3, 5, 5.5), 0, 180 * 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BG))
            p.drawRoundedRect(QRectF(b.x() + 4, b.y() + 6, 7, 5.5), 1, 1)
        p.end()

    def enterEvent(self, _e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover = False
        self.update()

    # ---- mouse: click, drag, menu ----------------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._grab = self._press - self.pos()
            self._dragging = False

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is None:
            return
        now = e.globalPosition().toPoint()
        if not self._dragging and (now - self._press).manhattanLength() >= DRAG_START:
            self._dragging = True
        if self._dragging:
            target = now - self._grab
            top = screen_area(QRect(target, self.size())).top()
            if target.y() < top + SNAP:  # sticks to the top while dragged, as the bar does
                target.setY(top)
            self.move(target)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            self.move(snap_point(self.pos(), ICON_SIZE, screen_area(self.frameGeometry())))
            self.moved.emit(self.pos())
        else:
            self.clicked.emit()

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        self.menu().exec(e.globalPos())

    def menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("Search", self.clicked.emit)
        m.addSeparator()
        m.addAction("Open full window\tCtrl+M", self.open_full.emit)
        m.addAction("Open compact window", self.open_compact.emit)
        if self.unlocked_vault:
            m.addSeparator()
            m.addAction("Settings…", self.settings_requested.emit)
            m.addAction("Lock", self.lock_requested.emit)
        return m
