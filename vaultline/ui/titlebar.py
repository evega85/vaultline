"""The top bar that replaces the Windows title bar: logo, the current screen's controls, window buttons.

Empty space, the logo and labels count as title bar (drag, double-click, snap); buttons and the
search box are ordinary controls. `hit()` is what frameless.NativeFrame asks Windows to use.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QHBoxLayout, QLabel,
                               QLineEdit, QWidget)

from .. import APP_NAME, icons, theme

HEIGHT = 56
BUTTON_W = 46
COMPACT_HEIGHT = 48
COMPACT_BUTTON_W = 34  # a 32px rounded button, like the pin and lock beside it
COMPACT_FACE = 32

# Segoe Fluent Icons / MDL2 caption glyphs
_GLYPHS = {"min": "\uE921", "max": "\uE922", "restore": "\uE923", "close": "\uE8BB",
           "expand": "\uE92D", "collapse": "\uE92C"}  # ChromeFullScreen / ChromeBackToWindow
_INTERACTIVE = (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox)


class WindowButton(QAbstractButton):
    """Minimise / maximise / close. Hover and press can also be set from outside, because over the
    maximise button Windows (not Qt) receives the mouse so it can show the snap-layout flyout."""

    def __init__(self, kind: str, tip: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = kind
        self._hover = self._pressed = False
        self.rounded = False  # compact view: small rounded face instead of a full-height block
        self.setFixedSize(BUTTON_W, HEIGHT)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(tip)
        self.setAccessibleName(tip)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(BUTTON_W, HEIGHT)

    def set_hover(self, on: bool) -> None:
        if on != self._hover:
            self._hover = on
            self.update()

    def set_pressed(self, on: bool) -> None:
        if on != self._pressed:
            self._pressed = on
            self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.set_hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.set_hover(False)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        down = self._pressed or self.isDown()
        fg = QColor(theme.TEXT_2)
        face = QRectF(self.rect())
        if self.rounded:
            face = QRectF((self.width() - COMPACT_FACE) / 2, (self.height() - COMPACT_FACE) / 2,
                          COMPACT_FACE, COMPACT_FACE)

        def fill(color: str) -> None:
            if self.rounded:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color))
                p.drawRoundedRect(face, 8, 8)
            else:
                p.fillRect(face, QColor(color))

        if self.kind == "close" and (self._hover or down):
            fill("#c42b1c" if self._hover and not down else "#b22a1b")
            fg = QColor("white")
        elif down:
            fill(theme.BORDER)
            fg = QColor(theme.TEXT)
        elif self._hover:
            fill(theme.HOVER)
            fg = QColor(theme.TEXT)
        p.setFont(icons.glyph_font(10))
        p.setPen(fg)
        p.drawText(face, Qt.AlignCenter, _GLYPHS[self.kind])


class TitleBar(QWidget):
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    mode_clicked = Signal()  # palette <-> full window

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedHeight(HEIGHT)
        self._controls: QWidget | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 0, 0)
        lay.setSpacing(0)
        self.logo = QLabel()
        self.logo.setPixmap(icons.logo_pixmap(20))
        self.logo.setFixedWidth(20 + 12)
        lay.addWidget(self.logo)
        self.title = QLabel(APP_NAME)
        self.title.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 13px;")
        lay.addWidget(self.title)
        self._slot = QHBoxLayout()
        self._slot.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._slot, 1)
        self.drag_space = QWidget()  # always leaves room to grab the window, even with a wide search box
        self.drag_space.setMinimumWidth(36)
        lay.addWidget(self.drag_space)
        self.min_btn = WindowButton("min", "Minimise")
        self.mode_btn = WindowButton("collapse", "Compact view (Ctrl+M)")
        self.max_btn = WindowButton("max", "Maximise")
        self.close_btn = WindowButton("close", "Close")
        for b in (self.mode_btn, self.min_btn, self.max_btn, self.close_btn):  # view switch, then ─ □ ✕
            lay.addWidget(b)
        self.min_btn.clicked.connect(self.minimize_clicked)
        self.mode_btn.clicked.connect(self.mode_clicked)
        self.max_btn.clicked.connect(self.maximize_clicked)
        self.close_btn.clicked.connect(self.close_clicked)
        self.mode_btn.hide()  # shown with the vault list only (set_controls)
        self._lay = lay
        self._buttons_wanted = True
        self._brand = True
        self.compact = False

    def set_compact(self, on: bool) -> None:
        """Narrow window: lower bar, slimmer buttons, no logo and no maximise (double-click still works)."""
        self.compact = on
        h, bw = (COMPACT_HEIGHT, COMPACT_BUTTON_W) if on else (HEIGHT, BUTTON_W)
        self.setFixedHeight(h)
        for b in (self.min_btn, self.mode_btn, self.max_btn, self.close_btn):
            b.setFixedSize(bw, h)
            b.rounded = on
        self.mode_btn.kind = "expand" if on else "collapse"
        self.mode_btn.setToolTip("Full window (Ctrl+M)" if on else "Compact view (Ctrl+M)")
        self.mode_btn.update()
        self._lay.setContentsMargins(8 if on else 14, 0, 6 if on else 0, 0)
        self._show_brand()
        self.drag_space.setMinimumWidth(8 if on else 36)
        self.set_window_buttons(self._buttons_wanted)

    # ---- content ---------------------------------------------------------------
    def set_controls(self, w: QWidget | None) -> None:
        """Show one screen's controls (e.g. the vault's search row) in the bar, or just the app name."""
        if w is self._controls:
            return
        if self._controls is not None:
            self._slot.removeWidget(self._controls)
            self._controls.hide()
            self._controls.setParent(None)
        self._controls = w
        if w is not None:
            self._slot.addWidget(w)
            w.show()
        self._show_brand()
        self.mode_btn.setVisible(w is not None)  # only the vault list has two views

    def set_brand(self, on: bool) -> None:
        """Logo and app name; the lock screens show their own big logo, so the bar stays empty there."""
        self._brand = on
        self._show_brand()

    def _show_brand(self) -> None:
        w = self._controls
        self.title.setVisible(self._brand and w is None)
        self.logo.setVisible(self._brand and (w is None or not self.compact))

    def set_window_buttons(self, visible: bool) -> None:
        self._buttons_wanted = visible
        self.min_btn.setVisible(visible)
        self.max_btn.setVisible(visible and not self.compact)
        self.close_btn.setVisible(visible)

    def set_maximized(self, on: bool) -> None:
        self.max_btn.kind = "restore" if on else "max"
        self.max_btn.setToolTip("Restore down" if on else "Maximise")
        self.max_btn.update()

    # ---- hit testing --------------------------------------------------------------
    def hit(self, pos: QPoint) -> str | None:
        """'caption', 'max' or None (a normal control) for a point in this bar's coordinates."""
        if not self.rect().contains(pos):
            return None
        w = self.childAt(pos)
        if w is None:
            return "caption"
        node = w
        while node is not None and node is not self:
            if node is self.max_btn:
                return "max" if self.max_btn.isVisible() else None
            if isinstance(node, _INTERACTIVE) or node.property("interactive"):
                return None
            node = node.parentWidget()
        return "caption"
