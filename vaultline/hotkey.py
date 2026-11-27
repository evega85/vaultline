"""Global shortcut (off by default): opens the quick search from any program.

Uses Windows' RegisterHotKey on the main window, so WM_HOTKEY arrives in its nativeEvent even
while it's hidden in the tray. Windows refuses a shortcut another program already holds; the
caller reports that instead of failing silently.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

WM_HOTKEY = 0x0312
HOTKEY_ID = 0x5641  # any id 0..0xBFFF, private to our window
DEFAULT = "Ctrl+Alt+Space"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_SPECIAL = {Qt.Key_Space: 0x20, Qt.Key_Insert: 0x2D, Qt.Key_Home: 0x24, Qt.Key_End: 0x23,
            Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22}


def parse(text: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+Space' -> (Windows modifiers, virtual key), or None if it can't be a global
    shortcut (no key, more than one key, or no Ctrl/Alt/Win: plain or Shift-only keys would
    swallow ordinary typing everywhere)."""
    seq = QKeySequence.fromString(text or "", QKeySequence.PortableText)
    if seq.count() != 1:
        return None
    combo = seq[0]
    key, mods = combo.key(), combo.keyboardModifiers()
    winmods = 0
    if mods & Qt.ControlModifier:
        winmods |= MOD_CONTROL
    if mods & Qt.AltModifier:
        winmods |= MOD_ALT
    if mods & Qt.ShiftModifier:
        winmods |= MOD_SHIFT
    if mods & Qt.MetaModifier:
        winmods |= MOD_WIN
    if not winmods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None
    k = int(key)
    if Qt.Key_A <= k <= Qt.Key_Z or Qt.Key_0 <= k <= Qt.Key_9:
        vk = k  # Qt and Windows share ASCII codes for letters and digits
    elif Qt.Key_F1 <= k <= Qt.Key_F24:
        vk = 0x70 + (k - int(Qt.Key_F1))
    elif k in _SPECIAL:
        vk = _SPECIAL[k]
    else:
        return None
    return winmods, vk


def register(hwnd: int, text: str) -> bool:
    """(Re)register the shortcut on `hwnd`. False if it's invalid or another program holds it."""
    unregister(hwnd)
    parsed = parse(text)
    if parsed is None or sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    mods, vk = parsed
    return bool(ctypes.windll.user32.RegisterHotKey(ctypes.c_void_p(hwnd), HOTKEY_ID,
                                                    mods | MOD_NOREPEAT, vk))


def unregister(hwnd: int) -> None:
    if sys.platform == "win32" and hwnd:
        import ctypes
        ctypes.windll.user32.UnregisterHotKey(ctypes.c_void_p(hwnd), HOTKEY_ID)
