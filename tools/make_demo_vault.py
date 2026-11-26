"""Build demo/sample.kdbx: a throwaway vault with made-up entries for trying Vaultline.

Master password: demo123. Never put real data in it.
Run from the repo root:  python tools/make_demo_vault.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from pykeepass import create_database

PASSWORD = "demo123"
OUT = Path(__file__).resolve().parents[1] / "demo" / "sample.kdbx"
# Public test secret from the RFC 6238 / pyotp examples; any authenticator shows the same codes.
DEMO_TOTP = "otpauth://totp/Demo%20GitHub:demo-user?secret=JBSWY3DPEHPK3PXP&issuer=Demo%20GitHub"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    kp = create_database(str(OUT), password=PASSWORD)
    personal = kp.add_group(kp.root_group, "Personal")
    work = kp.add_group(kp.root_group, "Work")
    finance = kp.add_group(kp.root_group, "Finance")

    kp.add_entry(work, "GitHub (demo)", "demo-user", "Demo-Pass-1234!", url="https://github.com",
                 notes="Has a working one-time code (TOTP). Compare it with any authenticator app.",
                 otp=DEMO_TOTP)
    kp.add_entry(personal, "Email (demo)", "demo@example.com", "correct-horse-battery-staple",
                 url="https://mail.example.com")
    wifi = kp.add_entry(personal, "Home Wi-Fi (demo)", "", "not-a-real-wifi-password")
    wifi.notes = "Network: DemoNet"
    api = kp.add_entry(work, "Weather API (demo)", "client-1234", "sk_demo_0000000000000000",
                       url="https://api.example.com")
    api._set_string_field("Environment", "staging", False)

    # A card in the same layout KeePassXC uses for cards imported from Bitwarden.
    # 4111 1111 1111 1111 is the standard Visa test number; it can't buy anything.
    card = kp.add_entry(finance, "Example Bank Visa", "", "online-banking-demo", url="https://bank.example.com")
    for key, value, protected in (
        ("card_number", "4111 1111 1111 1111", True), ("card_code", "123", True),
        ("card_expMonth", "12", False), ("card_expYear", "2030", False),
        ("card_cardholderName", "Sample Person", False), ("card_brand", "Visa", False),
        ("Phone", "1-800-555-0100", False), ("Phone (local)", "604-555-0100", False),
    ):
        card._set_string_field(key, value, protected)

    kp.save()
    print(f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT} (password: {PASSWORD})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
