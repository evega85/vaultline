"""Icons drawn at runtime: Windows 11 Fluent glyphs and the app logo."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

from . import theme

ICON_FONT = "Segoe Fluent Icons"
ICON_FONT_FALLBACK = "Segoe MDL2 Assets"

SEARCH = "\uE721"
ADD = "\uE710"
LOCK = "\uE72E"
EYE = "\uE890"
EYE_OFF = "\uED1A"
DICE = "\uE8B1"
FOLDER = "\uE838"
KEY = "\uE8D7"
EDIT = "\uE70F"
CLOSE = "\uE711"
OPEN = "\uE8A7"
COPY = "\uE8C8"
MORE = "\uE712"
FOLDER_CLOSED = "\uE8B7"
CHEVRON_DOWN = "\uE70D"
GLOBE = "\uE774"
DELETE = "\uE74D"
SETTINGS = "\uE713"
CARD = "\uE8C7"
BACK = "\uE72B"
PIN = "\uE718"
PIN_FILL = "\uE841"
HELP = "\uE897"
MENU = "\uE700"  # GlobalNavigationButton (three lines)


def glyph_font(px: int) -> QFont:
    f = QFont(ICON_FONT)
    f.setFamilies([ICON_FONT, ICON_FONT_FALLBACK])
    f.setPixelSize(px)
    return f


def glyph(char: str, color: str = theme.TEXT_2, px: int = 16) -> QIcon:
    return layered([(char, color)], px)


def layered(layers: list[tuple[str, str]], px: int = 16) -> QIcon:
    """Glyphs drawn on top of each other, bottom first. Fluent "…Fill" glyphs are only the solid
    part (a pin's head without its needle); Windows draws the outline glyph over them."""
    icon = QIcon()
    for scale in (1, 2):
        size = px * scale
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(glyph_font(size))
        for char, color in layers:
            p.setPen(QColor(color))
            p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, char)
        p.end()
        pm.setDevicePixelRatio(scale)
        icon.addPixmap(pm)
    return icon


def _paint_logo(p: QPainter, s: float) -> None:
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92)
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0, QColor("#7c7ff7"))
    grad.setColorAt(1, QColor("#4338ca"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(r, s * 0.22, s * 0.22)
    # keyhole
    p.setBrush(QColor("white"))
    cx = s / 2
    p.drawEllipse(QPointF(cx, s * 0.40), s * 0.13, s * 0.13)
    path = QPainterPath()
    path.moveTo(cx - s * 0.07, s * 0.44)
    path.lineTo(cx + s * 0.07, s * 0.44)
    path.lineTo(cx + s * 0.11, s * 0.74)
    path.lineTo(cx - s * 0.11, s * 0.74)
    path.closeSubpath()
    p.drawPath(path)


def logo_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    _paint_logo(p, size)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(s))
    return icon


def locked_tray_icon() -> QIcon:
    """Tray icon variant shown while the vault is locked (dimmed)."""
    icon = QIcon()
    for s in (16, 20, 24, 32, 48):
        pm = logo_pixmap(s)
        p = QPainter(pm)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.fillRect(pm.rect(), QColor(0, 0, 0, 140))
        p.end()
        icon.addPixmap(pm)
    return icon


if __name__ == "__main__":  # python -m vaultline.icons assets/vaultline.ico
    import sys
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(sys.argv)
    out = sys.argv[1] if len(sys.argv) > 1 else "assets/vaultline.ico"
    ok = logo_pixmap(256).save(out, "ICO")
    print("wrote" if ok else "FAILED", out)
