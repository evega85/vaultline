"""TOTP parsing and storage in the KeePassXC / KeePassium `otp` format.

Writes always use the `otp` string field holding an `otpauth://totp/...` URI,
which KeePassXC, KeePassium and Strongbox all read. Reads also accept the
KeePass 2 `TimeOtp-*` fields and the legacy KeePassXC `TOTP Seed` fields.
"""
from __future__ import annotations

import base64
import binascii
import re
import time
from urllib.parse import quote, urlencode

import pyotp
import pyotp.otp

# pyotp 2.10+ refuses secrets under 112 bits, but many sites issue 80-bit
# (16-character) secrets. We only use secrets a site already issued, so accept
# any length, as KeePassXC and KeePassium do.
pyotp.otp.OTP.min_secret_length_bits = 8

LEGACY_FIELDS = (
    "TOTP Seed", "TOTP Settings",
    "TimeOtp-Secret", "TimeOtp-Secret-Hex", "TimeOtp-Secret-Base32",
    "TimeOtp-Secret-Base64", "TimeOtp-Length", "TimeOtp-Period", "TimeOtp-Algorithm",
)

_ALGOS = {"SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512",
          "HMAC-SHA-1": "sha1", "HMAC-SHA-256": "sha256", "HMAC-SHA-512": "sha512"}


class OtpError(ValueError):
    pass


def clean_base32(secret: str) -> str:
    s = re.sub(r"[\s\-]", "", secret).upper().rstrip("=")
    if not s:
        raise OtpError("Secret is empty")
    try:
        base64.b32decode(s + "=" * (-len(s) % 8))
    except (binascii.Error, ValueError):
        raise OtpError("Not a valid Base32 secret") from None
    return s


def normalize(text: str, title: str = "", username: str = "") -> str:
    """Turn user input (raw Base32 or otpauth URI) into a stored otpauth URI."""
    text = (text or "").strip()
    if not text:
        return ""
    if text.lower().startswith("otpauth://"):
        totp = parse_uri(text)  # validates
        if totp is None:
            raise OtpError("Not a valid otpauth URI")
        return text
    secret = clean_base32(text)
    issuer = (title or "Vaultline").strip()
    label = f"{issuer}:{username}" if username else issuer
    query = urlencode({"secret": secret, "period": 30, "digits": 6, "issuer": issuer},
                      quote_via=quote)
    return f"otpauth://totp/{quote(label, safe=':@')}?{query}"


def parse_uri(uri: str) -> pyotp.TOTP | None:
    try:
        otp = pyotp.parse_uri(uri)
    except (ValueError, binascii.Error, TypeError) as exc:
        raise OtpError(f"Invalid otpauth URI: {exc}") from None
    if not isinstance(otp, pyotp.TOTP):
        raise OtpError("Only time-based (TOTP) codes are supported")
    clean_base32(otp.secret)
    return otp


def from_fields(fields: dict[str, str]) -> pyotp.TOTP | None:
    """Build a TOTP from an entry's string fields, or None if it has none."""
    try:
        uri = fields.get("otp")
        if uri:
            if uri.lower().startswith("otpauth://"):
                return parse_uri(uri)
            return pyotp.TOTP(clean_base32(uri))  # some tools store a bare secret
        if fields.get("TimeOtp-Secret-Base32"):
            algo = _ALGOS.get((fields.get("TimeOtp-Algorithm") or "").upper(), "sha1")
            import hashlib
            return pyotp.TOTP(
                clean_base32(fields["TimeOtp-Secret-Base32"]),
                digits=int(fields.get("TimeOtp-Length") or 6),
                interval=int(fields.get("TimeOtp-Period") or 30),
                digest=getattr(hashlib, algo),
            )
        if fields.get("TOTP Seed"):
            period, digits = 30, 6
            settings = (fields.get("TOTP Settings") or "").split(";")
            if len(settings) >= 2 and settings[0].isdigit():
                period = int(settings[0])
                digits = int(settings[1]) if settings[1].isdigit() else 6
            return pyotp.TOTP(clean_base32(fields["TOTP Seed"]), digits=digits, interval=period)
    except (OtpError, ValueError):
        return None
    return None


def current(totp: pyotp.TOTP) -> tuple[str, float]:
    """(code, fraction of the period remaining in 0..1)."""
    now = time.time()
    remaining = totp.interval - (now % totp.interval)
    return totp.at(now), remaining / totp.interval


def pretty(code: str) -> str:
    half = len(code) // 2
    return f"{code[:half]} {code[half:]}" if len(code) >= 6 else code
