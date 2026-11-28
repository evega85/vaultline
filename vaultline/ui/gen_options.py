"""The gear beside Generate: the password generator's options (the same ones as in Settings) in a small
popup. They're saved for every new password; closing the popup after a change makes a new one."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QSpinBox, QToolButton, QVBoxLayout, QWidget

from .. import icons, passgen, theme
from ..config import Config

SETS = (("A–Z", "pw_upper"), ("a–z", "pw_lower"), ("0–9", "pw_digits"), ("Symbols", "pw_symbols"))


class GeneratorPopup(QWidget):
    closed = Signal(bool)  # True when an option was changed (and saved)

    def __init__(self, parent: QWidget, config: Config):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.config = config
        self._start = self._values()
        inner = QFrame(self)
        inner.setObjectName("GenPopup")
        inner.setStyleSheet(f"#GenPopup {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER_STRONG};"
                            f" border-radius: 10px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        head = QLabel("Password generator")
        head.setStyleSheet(f"font-weight: 600; color: {theme.TEXT};")
        lay.addWidget(head)
        row = QHBoxLayout()
        row.addWidget(QLabel("Length (characters)"))
        row.addStretch()
        self.length = QSpinBox()
        self.length.setRange(passgen.MIN_LENGTH, passgen.MAX_LENGTH)
        self.length.setValue(max(passgen.MIN_LENGTH, min(passgen.MAX_LENGTH, config.pw_length or 20)))
        self.length.setFixedWidth(80)
        row.addWidget(self.length)
        lay.addLayout(row)
        sets = QHBoxLayout()
        sets.setSpacing(14)
        self.sets: list[QCheckBox] = []
        for text, attr in SETS:
            cb = QCheckBox(text)
            cb.setChecked(getattr(config, attr))
            cb.setProperty("attr", attr)
            cb.toggled.connect(lambda on, b=cb: self._toggled(b, on))
            sets.addWidget(cb)
            self.sets.append(cb)
        lay.addLayout(sets)
        hint = QLabel("Kept for every new password (also in Settings).")
        hint.setObjectName("Hint")
        lay.addWidget(hint)

    def _toggled(self, box: QCheckBox, on: bool) -> None:
        if not on and not any(b.isChecked() for b in self.sets):
            box.setChecked(True)  # at least one kind of character

    def _values(self) -> tuple:
        c = self.config
        return c.pw_length, c.pw_upper, c.pw_lower, c.pw_digits, c.pw_symbols

    def closeEvent(self, e) -> None:  # noqa: N802
        c = self.config
        c.pw_length = self.length.value()
        for cb in self.sets:
            setattr(c, cb.property("attr"), cb.isChecked())
        changed = self._values() != self._start
        if changed:
            c.save()
        self.closed.emit(changed)
        super().closeEvent(e)


def gear_button(config: Config, regenerate) -> QToolButton:
    """A gear for beside the Generate button; regenerate() is called after the options changed."""
    b = QToolButton()
    b.setIcon(icons.glyph(icons.SETTINGS, theme.TEXT_2, 15))
    b.setIconSize(QSize(15, 15))
    b.setToolTip("Password generator options")
    b.setFixedSize(34, 34)

    def show():
        pop = GeneratorPopup(b.window(), config)
        pop.closed.connect(lambda changed: changed and regenerate())
        pop.adjustSize()
        at = b.mapToGlobal(QPoint(b.width() - pop.width(), b.height() + 4))
        pop.move(at)
        pop.show()
        b._popup = pop  # for tests
    b.clicked.connect(show)
    return b
