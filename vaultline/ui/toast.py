"""Small fading notification pill shown at the bottom of the window."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QWidget

from .. import icons, theme
from .widgets import breakable


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
        self._detail.setTextFormat(Qt.PlainText)
        # errors: the whole message wraps and can be copied, so nothing is lost off the edge
        self.copy_btn = QPushButton(" Copy")
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.setIcon(icons.glyph(icons.COPY, theme.TEXT_2, 13))
        self.copy_btn.setToolTip("Copy this message")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setFocusPolicy(Qt.NoFocus)
        self.copy_btn.clicked.connect(self._copy)
        self.copy_btn.hide()
        self._message = ""
        lay.addWidget(self._dot, 0, Qt.AlignTop)
        lay.addWidget(self._label, 0, Qt.AlignTop)
        lay.addWidget(self._detail, 1)
        lay.addWidget(self.copy_btn, 0, Qt.AlignTop)

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
        error = kind == "error"
        self._message = f"{text}: {detail}" if detail else text
        self._detail.setText(breakable(detail) if error else detail)
        self._detail.setVisible(bool(detail))
        self._detail.setWordWrap(error)
        self.copy_btn.setText(" Copy")
        self.copy_btn.setVisible(error)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not error)
        if error:
            ms = max(ms, 8000)  # long enough to read, and it stays while the mouse is on it
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
        self.setMaximumWidth(max(200, p.width() - 32))
        self.adjustSize()
        if self._detail.wordWrap():  # wrapped text: size to the width it needs, then the height
            self.resize(self.maximumWidth() if self.sizeHint().width() > self.maximumWidth()
                        else self.sizeHint().width(), self.heightForWidth(self.width())
                        if self.hasHeightForWidth() else self.sizeHint().height())
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 52)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._message)  # an error message, never a secret value
        self.copy_btn.setText(" Copied")

    def enterEvent(self, event) -> None:
        if self.copy_btn.isVisible():
            self._hide_timer.stop()
            self._anim.stop()
            self._fx.setOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.copy_btn.isVisible() and self.isVisible():
            self._hide_timer.start(4000)
        super().leaveEvent(event)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(380)
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_done(self) -> None:
        if self._anim.endValue() == 0.0:
            self.hide()
