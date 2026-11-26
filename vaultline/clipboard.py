"""Clipboard copy with auto-wipe, kept out of Windows clipboard history and cloud sync."""
from __future__ import annotations

import sys

from PySide6.QtCore import QMimeData, QObject, QTimer
from PySide6.QtGui import QGuiApplication

_WIN_FMT = 'application/x-qt-windows-mime;value="{}"'


class SecureClipboard(QObject):
    def __init__(self, clear_seconds: int = 20, parent: QObject | None = None):
        super().__init__(parent)
        self.clear_seconds = clear_seconds
        self._last: str | None = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.wipe)

    def copy(self, text: str) -> None:
        mime = QMimeData()
        mime.setText(text)
        if sys.platform == "win32":
            # Same markers KeePassXC uses: skip clipboard history (Win+V),
            # cloud clipboard sync and clipboard monitor apps.
            mime.setData(_WIN_FMT.format("ExcludeClipboardContentFromMonitorProcessing"), b"\x01\x00\x00\x00")
            mime.setData(_WIN_FMT.format("CanIncludeInClipboardHistory"), b"\x00\x00\x00\x00")
            mime.setData(_WIN_FMT.format("CanUploadToCloudClipboard"), b"\x00\x00\x00\x00")
        QGuiApplication.clipboard().setMimeData(mime)
        self._last = text
        self._timer.start(self.clear_seconds * 1000)

    def wipe(self) -> None:
        """Clear the clipboard, but only if it still holds what we put there."""
        self._timer.stop()
        if self._last is None:
            return
        cb = QGuiApplication.clipboard()
        if cb.text() == self._last:
            cb.clear()
        self._last = None
