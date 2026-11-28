"""Small custom-painted widgets."""
from __future__ import annotations

import zlib

import pyotp
from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QLabel,
                               QPushButton, QScrollBar, QSizePolicy, QWidget)

from .. import otp as otplib
from .. import theme


class ElidedLabel(QLabel):
    """Single-line label that shows an ellipsis instead of growing the row."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text
        super().setText(text)
        self.setToolTip(text if len(text) > 40 else "")

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(10, super().minimumSizeHint().height())

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        fm = QFontMetrics(self.font())
        text = fm.elidedText(self._full, Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(self.alignment()) | Qt.AlignVCenter, text)


_ICON_STYLE: dict[int, str] = {}


def icon_style(pm: QPixmap) -> str:
    """How to draw a website icon on the dark list:
    "plain"  - has a transparent background and is light enough: drawn as is, no tile;
    "tile"   - transparent but too dark to see on dark grey: keeps a light tile behind it;
    "opaque" - brings its own square background: drawn with rounded corners, no extra tile."""
    key = pm.cacheKey()
    if key not in _ICON_STYLE:
        img = pm.toImage().scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        lum_sum = weight = 0.0
        clear = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                a = c.alphaF()
                if a < 0.1:
                    clear += 1
                    continue
                lum_sum += a * (0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF())
                weight += a
        total = img.width() * img.height()
        if clear < total * 0.08:
            style = "opaque"
        elif weight and lum_sum / weight < 0.28:
            style = "tile"
        else:
            style = "plain"
        _ICON_STYLE[key] = style
    return _ICON_STYLE[key]


class Avatar(QWidget):
    """Website icon (no background when it has none of its own), or a coloured letter."""

    def __init__(self, text: str, parent: QWidget | None = None, size: int = 32,
                 pixmap: QPixmap | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = pixmap
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._letter = (text.strip()[:1] or "•").upper()
        idx = zlib.crc32(text.casefold().encode()) % len(theme.AVATAR_COLORS)
        self._color = QColor(theme.AVATAR_COLORS[idx])

    def set_size(self, size: int) -> None:
        if self.width() != size:
            self.setFixedSize(size, size)
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(self.rect())
        radius = self.height() * 0.25
        pm = self._pixmap
        if pm is not None and not pm.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            style = icon_style(pm)
            if style == "tile":  # dark logo: a light tile so it stays visible
                p.setBrush(QColor("#ececef"))
                p.drawRoundedRect(r, radius, radius)
                inset = self.height() * 0.17
            elif style == "opaque":  # its own background: just round the corners
                path = QPainterPath()
                path.addRoundedRect(r, radius, radius)
                p.setClipPath(path)
                inset = 0.0
            else:
                inset = self.height() * 0.06
            target = r.adjusted(inset, inset, -inset, -inset)
            scale = min(target.width() / pm.width(), target.height() / pm.height())
            w, h = pm.width() * scale, pm.height() * scale
            p.drawPixmap(QRectF(target.center().x() - w / 2, target.center().y() - h / 2, w, h),
                         pm, QRectF(pm.rect()))
            return
        p.setBrush(self._color)
        p.drawRoundedRect(r, radius, radius)
        f = QFont(theme.UI_FONT)
        f.setPixelSize(int(self.height() * 0.45))
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#e4e4e7"))
        p.drawText(r, Qt.AlignCenter, self._letter)


class OtpButton(QPushButton):
    """Shows the live TOTP code with a countdown ring; click copies it."""

    def __init__(self, totp: pyotp.TOTP, parent: QWidget | None = None):
        super().__init__(parent)
        self.totp = totp
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(98, 26)
        self.setToolTip("Copy one-time code (Ctrl+T)")
        self._hover = False

    def code(self) -> str:
        return otplib.current(self.totp)[0]

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _event) -> None:  # noqa: N802
        code, frac = otplib.current(self.totp)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        pressed = self.isDown()
        p.setPen(QPen(QColor(theme.ACCENT if pressed else (theme.BORDER_STRONG if self._hover else theme.CHIP_BORDER)), 1))
        p.setBrush(QColor(theme.ACCENT if pressed else (theme.HOVER if self._hover else "transparent")))
        p.drawRoundedRect(r, 7, 7)

        # countdown ring
        ring = QRectF(9, (self.height() - 12) / 2, 12, 12)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 2))
        p.drawEllipse(ring)
        low = frac * self.totp.interval <= 5
        p.setPen(QPen(QColor(theme.WARN if low else theme.ACCENT_SOFT), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 90 * 16, int(frac * 360 * 16))

        f = QFont(theme.MONO_FONT)
        f.setFamilies([theme.MONO_FONT, "Consolas"])
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("white" if pressed else (theme.WARN if low else theme.TEXT)))
        p.drawText(QRectF(26, 0, self.width() - 30, self.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   otplib.pretty(code))


SHARED_TIP = "Shared with agents: they can ask for passwords here (each request asks you)"


def mark_shared_folders(combo, shared: set[str]) -> None:
    """Mark the folders shared with agents in a folder picker (amber icon, "shared with agents"), and
    give the box an amber border while one of them is picked, so it's never chosen by accident."""
    from PySide6.QtCore import Qt as _Qt
    from .. import icons as _icons
    if not shared:
        return
    for i in range(combo.count()):
        if combo.itemData(i) in shared:
            # an amber icon and words (a text colour alone is overridden by the app's style sheet)
            combo.setItemText(i, combo.itemText(i) + "  · shared with agents")
            combo.setItemData(i, _icons.glyph(_icons.FOLDER_CLOSED, theme.WARN, 14), _Qt.DecorationRole)
            combo.setItemData(i, SHARED_TIP, _Qt.ToolTipRole)

    def look(_i=0):
        on = combo.currentData() in shared
        combo.setProperty("shared", on)  # theme: QComboBox[shared="true"] (only the closed box, not its list)
        combo.style().unpolish(combo)
        combo.style().polish(combo)
        combo.setToolTip(SHARED_TIP if on else "")
    combo.currentIndexChanged.connect(look)
    look()


class WheelGuard(QObject):
    """Installed on the application: the mouse wheel over a drop-down, number box or slider scrolls
    whatever it sits in instead of changing its value (too easy to change a setting by accident).
    Open drop-down lists still scroll as usual: their list isn't one of these."""

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox, QAbstractSlider)) \
                and not isinstance(obj, QScrollBar):
            p = obj.parentWidget()
            while p is not None and not isinstance(p, QAbstractScrollArea):
                p = p.parentWidget()
            if p is not None:
                bar = p.horizontalScrollBar() if ev.angleDelta().y() == 0 else p.verticalScrollBar()
                QApplication.sendEvent(bar, ev)
            return True
        return False


def breakable(text: str) -> str:
    """Let a long path or name wrap: a zero-width space after slashes . _ - (for display only)."""
    return "".join(ch + "​" if ch in r"\/._-" else ch for ch in text)


class ErrorLabel(QWidget):
    """An error message that wraps (long paths too), can be selected, and has a Copy button,
    so nothing is cut off where it can't be read. setText/text work like QLabel's."""

    def __init__(self, text: str = "", center: bool = False):
        super().__init__()
        from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
        from .. import icons
        lay = QVBoxLayout(self) if center else QHBoxLayout(self)  # centred: Copy goes underneath
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2 if center else 6)
        self._text = ""
        self.label = QLabel()
        self.label.setObjectName("Error")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if center:
            self.label.setAlignment(Qt.AlignCenter)
        self.copy_btn = QPushButton()
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.setIcon(icons.glyph(icons.COPY, theme.TEXT_2, 13))
        self.copy_btn.setText(" Copy")
        self.copy_btn.setToolTip("Copy this message")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setFocusPolicy(Qt.NoFocus)
        self.copy_btn.clicked.connect(self._copy)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.copy_btn, 0, Qt.AlignHCenter if center else Qt.AlignTop)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._text = text or ""
        self.label.setText(breakable(self._text))
        self.copy_btn.setText(" Copy")

    def text(self) -> str:
        return self._text

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._text)  # an error message, never a secret value
        self.copy_btn.setText(" Copied")
