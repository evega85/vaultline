"""Start Vaultline when the user signs in to Windows (HKCU Run key, no admin needed).

Only the packaged exe can do this: a Python run from source has no stable command to register.
A login start passes --background, so Vaultline waits in the tray instead of popping up.
"""
from __future__ import annotations

import os
import sys

from . import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
BACKGROUND_ARG = "--background"


def supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def command() -> str:
    return f'"{os.path.abspath(sys.executable)}" {BACKGROUND_ARG}'


def _read() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return str(value)
    except OSError:
        return None


def is_enabled() -> bool:
    """On only if the Run entry starts this exe (a moved exe counts as off, so ticking fixes it)."""
    if not supported():
        return False
    value = _read()
    return value is not None and os.path.normcase(command()) == os.path.normcase(value)


def set_enabled(on: bool) -> bool:
    """Add or remove the Run entry. Returns False if Windows refused."""
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
