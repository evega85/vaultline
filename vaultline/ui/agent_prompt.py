"""The approval prompt for an agent's `secret` request. Deny is the default; Allow needs a deliberate click."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QCursor, QGuiApplication
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..agent_access import ALLOW_MINUTES, PROMPT_SECONDS

_FIELD_NAMES = {"password": "password", "username": "username", "otp": "one-time code"}
ARM_MS = 1200  # Allow stays disabled briefly so a stray Enter or click can't approve


def folder_button_text(request) -> str:
    """ "All of Work, 15 min" / "All of Work until locked": names what the pass would cover."""
    name = (request.shared_folder.rsplit("/", 1)[-1] or request.shared_folder) if request.shared_folder else "folder"
    if len(name) > 24:
        name = name[:23] + "…"
    sep = " " if request.folder_pass == "until locked" else ", "
    return f"All of {name}{sep}{request.folder_pass}"


class AgentPrompt(QDialog):
    def __init__(self, request, decide, parent: QWidget | None = None):
        super().__init__(parent, Qt.Dialog | Qt.WindowStaysOnTopHint)
        self._decide = decide
        self._done = False
        self.setWindowTitle("Vaultline: agent request")
        self.setFixedWidth(560 if request.folder_pass else 460)
        self.setAttribute(Qt.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        head = QLabel(f"An agent is asking for a {_FIELD_NAMES.get(request.field, request.field)}")
        head.setObjectName("DialogTitle")
        head.setWordWrap(True)
        lay.addWidget(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        rows = [("Entry", request.title or "(no title)"), ("Username", request.username),
                ("Website", request.url), ("Folder", request.folder or "/"), ("Asked by", request.client)]
        for i, (k, v) in enumerate(r for r in rows if r[1]):
            key = QLabel(k)
            key.setObjectName("Sub")
            val = QLabel(v)
            val.setTextFormat(Qt.PlainText)  # titles and URLs are data, never markup
            val.setWordWrap(True)
            grid.addWidget(key, i, 0, Qt.AlignTop)
            grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)  # labels hug their values
        lay.addLayout(grid)
        self.countdown = QLabel()
        self.countdown.setObjectName("Hint")
        lay.addWidget(self.countdown)

        row = QHBoxLayout()
        self.deny = QPushButton("Deny")
        self.deny.setObjectName("Primary")
        self.deny.setDefault(True)
        self.deny.setAutoDefault(True)
        self.once = QPushButton("Allow once")
        self.once.setObjectName("Ghost")
        self.entry = QPushButton(f"This entry for {ALLOW_MINUTES} min")
        self.entry.setObjectName("Ghost")
        self.entry.setToolTip(f"Allow this entry without asking for {ALLOW_MINUTES} minutes (until a lock)")
        self.folder = QPushButton(folder_button_text(request))
        self.folder.setObjectName("Caution")  # the widest grant: looks different from the others
        shared = request.shared_folder or "the shared folder"
        self.folder.setToolTip(f"Allow any entry in {shared} (subfolders included) without asking, "
                               + ("until Vaultline locks" if request.folder_pass == "until locked"
                                  else f"for {request.folder_pass} (or until a lock)")
                               + ". Stop it early from the tray icon.")
        self.folder.setVisible(bool(request.folder_pass))
        self._allow = (self.once, self.entry, self.folder)
        for b in self._allow:
            b.setAutoDefault(False)
            b.setEnabled(False)
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.deny)
        lay.addLayout(row)
        self.deny.clicked.connect(lambda: self._finish("deny"))
        self.once.clicked.connect(lambda: self._finish("once"))
        self.entry.clicked.connect(lambda: self._finish("entry"))
        self.folder.clicked.connect(lambda: self._finish("folder"))

        self._left = PROMPT_SECONDS
        self._tick = QTimer(self, interval=1000)
        self._tick.timeout.connect(self._count)
        self._tick.start()
        self._count(start=True)
        QTimer.singleShot(ARM_MS, self._arm)
        self.deny.setFocus()

    def _arm(self) -> None:
        for b in self._allow:
            b.setEnabled(True)

    def _count(self, start: bool = False) -> None:
        if not start:
            self._left -= 1
        self.countdown.setText(f"Denies automatically in {max(self._left, 0)} s. "
                               "Allow only if you started this.")

    def _finish(self, decision: str) -> None:
        if self._done:
            return
        self._done = True
        self._decide(decision)
        super().close()

    def close(self) -> bool:  # called by the gate (timeout, lock, caller gone): no decision from here
        self._done = True
        return super().close()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802  (X or Esc = Deny)
        if not self._done:
            self._done = True
            self._decide("deny")
        super().closeEvent(e)

    def reject(self) -> None:  # Esc, and QDialog.closeEvent: decide Deny once, then really hide
        # Never call close() from here: Qt calls reject() while already closing, and a nested
        # close() is refused, which left the prompt on screen after a decision.
        if not self._done:
            self._done = True
            self._decide("deny")
        super().reject()


def place_near(dlg: QWidget, near: QWidget | None) -> None:
    """Centre `dlg` over `near` when it's on screen, else on the screen `near` was last on
    (or the one with the mouse), kept inside that screen's work area."""
    dlg.adjustSize()
    shown = near is not None and near.isVisible() and not near.isMinimized()
    screen = (near.screen() if near is not None else None) or QGuiApplication.screenAt(QCursor.pos()) \
        or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    centre = near.frameGeometry().center() if shown else area.center()
    if shown:
        screen = QGuiApplication.screenAt(centre) or screen
        area = screen.availableGeometry()
    size = dlg.frameGeometry().size()
    x = min(max(centre.x() - size.width() // 2, area.left()), area.right() - size.width())
    y = min(max(centre.y() - size.height() // 2, area.top()), area.bottom() - size.height())
    dlg.move(QPoint(x, y))


def show_prompt(request, decide, near: QWidget | None = None) -> AgentPrompt:
    """Show the prompt as its own window (it must appear even while Vaultline is in the tray),
    placed over the Vaultline window, or on the screen that window was last on."""
    dlg = AgentPrompt(request, decide)
    place_near(dlg, near)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
