"""Vaultline: a minimal dark KeePass (.kdbx) client for Windows."""
import getpass
import os

APP_NAME = "Vaultline"
__version__ = "1.2.0"


def pipe_name(kind: str) -> str:
    """Local pipe names, per Windows user. VAULTLINE_INSTANCE gives tests their own private names,
    so a test can never talk to the real running app."""
    instance = os.environ.get("VAULTLINE_INSTANCE", "").strip()
    return "-".join(p for p in (APP_NAME, instance, kind, getpass.getuser()) if p)
