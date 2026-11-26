"""Small fading notification pill shown at the bottom of the window."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from .. import theme


class Toast(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setObjectName("Toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)
        self._dot = QLabel("●")
        self._label = QLabel()
        self._label.setStyleSheet(f"font-size: 13px; color: {theme.TEXT}; font-weight: 600;")
        self._detail = QLabel()
        self._detail.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_2};")
        lay.addWidget(self._dot)
        lay.addWidget(self._label)
        lay.addWidget(self._detail)

        self._fx = QGraphicsOpacityEffect(self, opacity=0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hide_timer = QTimer(self, singleShot=True)
        self._hide_timer.timeout.connect(self._fade_out)
        self._anim.finished.connect(self._on_anim_done)
        self.hide()

    def show_message(self, text: str, detail: str = "", kind: str = "ok", ms: int = 1800) -> None:
        color = {"ok": theme.SUCCESS, "warn": theme.WARN, "error": theme.DANGER, "info": theme.ACCENT_SOFT}[kind]
        self._dot.setStyleSheet(f"color: {color}; font-size: 9px;")
        border = theme.DANGER if kind == "error" else theme.BORDER_STRONG
        self.setStyleSheet(f"#Toast {{ background: {theme.RAISED}; border: 1px solid {border}; border-radius: 10px; }}")
        self._label.setText(text)
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setDuration(140)
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hide_timer.start(ms)

    def reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 52)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(380)
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_done(self) -> None:
        if self._anim.endValue() == 0.0:
            self.hide()
