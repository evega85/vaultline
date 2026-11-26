"""PIN quick-unlock.

Only a slow scrypt hash of the PIN is stored (in config.json). The PIN never
unlocks the database file by itself: it only lets you back in while Vaultline is
still running and holds the master password in memory from a full unlock. After
too many wrong tries, or when the app restarts, the master password is required.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

MAX_TRIES = 3
_N, _R, _P = 2**15, 8, 1  # ~0.1 s per guess


def make(pin: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    return salt.hex(), _hash(pin, salt).hex()


def check(pin: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt, want = bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)
    except ValueError:
        return False
    return hmac.compare_digest(_hash(pin, salt), want)


def valid(pin: str) -> str | None:
    """Return an error message, or None if the PIN is acceptable."""
    if not pin.isdigit():
        return "Use digits only."
    if not 4 <= len(pin) <= 12:
        return "Use 4 to 12 digits."
    if len(set(pin)) == 1 or pin in "0123456789012" or pin in "9876543210987":
        return "Too easy to guess. Avoid repeats and runs like 1234."
    return None


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
