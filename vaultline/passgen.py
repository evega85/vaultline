"""Strong password generation using the OS CSPRNG."""
from __future__ import annotations

import secrets

# Ambiguous characters (Il1O0) are left out so passwords can be read aloud or retyped.
LOWER = "abcdefghijkmnopqrstuvwxyz"
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DIGITS = "23456789"
SYMBOLS = "!#$%&*+-=?@^_~"


MIN_LENGTH, MAX_LENGTH = 8, 64


def generate(length: int = 20, upper: bool = True, lower: bool = True, digits: bool = True,
             symbols: bool = True) -> str:
    """At least one character from each chosen set; with none chosen, all of them."""
    pools = [p for p, on in ((LOWER, lower), (UPPER, upper), (DIGITS, digits), (SYMBOLS, symbols)) if on] \
        or [LOWER, UPPER, DIGITS, SYMBOLS]
    alphabet = "".join(pools)
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice(alphabet) for _ in range(max(length, len(pools)) - len(pools))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def from_config(c) -> str:
    """A password as set in Settings > Password generator."""
    return generate(max(MIN_LENGTH, min(MAX_LENGTH, c.pw_length or 20)), c.pw_upper, c.pw_lower, c.pw_digits,
                    c.pw_symbols)
