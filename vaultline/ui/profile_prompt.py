"""Approval prompt for `vaultline-cli profile add`: every value is shown, Cancel is the default,
Add needs a deliberate click after a short delay, and no answer means Cancel."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import identity as idlib
from .agent_prompt import ARM_MS, place_near

PROMPT_SECONDS = 120


class ProfilePrompt(QDialog):
    def __init__(self, request: dict, decide, parent: QWidget | None = None):
        super().__init__(parent, Qt.Dialog | Qt.WindowStaysOnTopHint)
        self._decide = decide
        self._done = False
        self.setWindowTitle("Vaultline: add a profile?")
        self.setFixedWidth(480)
        self.setAttribute(Qt.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        head = QLabel("The command line wants to add a sign-up profile")
        head.setObjectName("DialogTitle")
        head.setWordWrap(True)
        lay.addWidget(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        rows = [("Title", request["title"]), ("Folder", request.get("folder") or "Top level")]
        rows += [(idlib.LABELS[k], v) for k, v in request["fields"].items()]
        rows.append(("Asked by", request.get("client", "")))
        for i, (k, v) in enumerate(r for r in rows if r[1]):
            key = QLabel(k)
            key.setObjectName("Sub")
            val = QLabel(v)
            val.setTextFormat(Qt.PlainText)  # data, never markup
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(key, i, 0, Qt.AlignTop)
            grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.countdown = QLabel()
        self.countdown.setObjectName("Hint")
        lay.addWidget(self.countdown)
        row = QHBoxLayout()
        self.add = QPushButton("Add profile")
        self.add.setObjectName("Ghost")
        self.add.setAutoDefault(False)
        self.add.setEnabled(False)
        self.cancel = QPushButton("Cancel")
        self.cancel.setObjectName("Primary")
        self.cancel.setDefault(True)
        row.addWidget(self.add)
        row.addStretch()
        row.addWidget(self.cancel)
        lay.addLayout(row)
        self.add.clicked.connect(lambda: self._finish("add"))
        self.cancel.clicked.connect(lambda: self._finish("cancel"))
        self._left = PROMPT_SECONDS
        self._tick = QTimer(self, interval=1000)
        self._tick.timeout.connect(self._count)
        self._tick.start()
        self._count(start=True)
        QTimer.singleShot(ARM_MS, lambda: self.add.setEnabled(True))
        self.cancel.setFocus()

    def _count(self, start: bool = False) -> None:
        if not start:
            self._left -= 1
            if self._left <= 0:
                self._finish("cancel")
                return
        self.countdown.setText(f"Cancels automatically in {max(self._left, 0)} s. Add only if you started this.")

    def _finish(self, decision: str) -> None:
        if self._done:
            return
        self._done = True
        self._decide(decision)
        super().close()

    def close(self) -> bool:  # closed from outside (lock, caller gone): no decision from here
        self._done = True
        return super().close()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802  (X = Cancel)
        if not self._done:
            self._done = True
            self._decide("cancel")
        super().closeEvent(e)

    def reject(self) -> None:  # Esc = Cancel
        if not self._done:
            self._done = True
            self._decide("cancel")
        super().reject()


def show_profile_prompt(request: dict, decide, near: QWidget | None = None) -> ProfilePrompt:
    dlg = ProfilePrompt(request, decide)
    place_near(dlg, near)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
