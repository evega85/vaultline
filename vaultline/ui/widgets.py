"""Small custom-painted widgets."""
from __future__ import annotations

import zlib

import pyotp
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

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


class Avatar(QWidget):
    """Website icon on a light tile, or a coloured letter when there is no icon."""

    def __init__(self, text: str, parent: QWidget | None = None, size: int = 32,
                 pixmap: QPixmap | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = pixmap
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._letter = (text.strip()[:1] or "•").upper()
        idx = zlib.crc32(text.casefold().encode()) % len(theme.AVATAR_COLORS)
        self._color = QColor(theme.AVATAR_COLORS[idx])

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(self.rect())
        radius = self.height() * 0.25
        if self._pixmap is not None and not self._pixmap.isNull():
            # Favicons are drawn for light backgrounds, so they sit on a light tile.
            p.setBrush(QColor("#ececef"))
            p.drawRoundedRect(r, radius, radius)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            inset = self.height() * 0.17
            target = r.adjusted(inset, inset, -inset, -inset)
            pm = self._pixmap
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
