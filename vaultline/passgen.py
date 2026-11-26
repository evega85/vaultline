"""Strong password generation using the OS CSPRNG."""
from __future__ import annotations

import secrets

# Ambiguous characters (Il1O0) are left out so passwords can be read aloud or retyped.
LOWER = "abcdefghijkmnopqrstuvwxyz"
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DIGITS = "23456789"
SYMBOLS = "!#$%&*+-=?@^_~"


def generate(length: int = 20) -> str:
    pools = [LOWER, UPPER, DIGITS, SYMBOLS]
    alphabet = "".join(pools)
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice(alphabet) for _ in range(max(length, len(pools)) - len(pools))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
