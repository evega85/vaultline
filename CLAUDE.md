# Vaultline: notes for contributors and coding agents

How to run, test and build is in README.md.

Design rules to keep:
- The database must stay readable by KeePassXC and KeePassium. TOTP is written only to the `otp` field as an otpauth URI. Legacy OTP fields are read but never rewritten unless the user changes the secret.
- All writes go through `Vault.save`: re-read the disk, merge if it changed, verify by decrypting again, back up locally, then write a temp file and use `os.replace`. Never write the .kdbx any other way. pykeepass's own `save(path)` falls back to copying over the file in place on Windows.
- New databases are made only by `vault.create_vault`: pykeepass's blank KDBX 4 template (AES-256, Argon2d) with a fresh root-folder UUID, checked by decrypting it, then renamed into place. It never overwrites an existing file.
- pykeepass work runs only on the controller's single worker thread. The UI uses `EntryView` snapshots.
- pyotp's 112-bit minimum secret length is turned off in `otp.py` on purpose, because real sites issue 80-bit secrets.
- Custom icons are written by hand into `Meta/CustomIcons` (UUID + Data only, valid in KDBX 4.0 and 4.1) with `CustomIconUUID` on the entry, because pykeepass has no API for them. Identical icons are shared, and unused ones are removed.
- Favicons are fetched directly from each site, never through a third-party icon service. Local or private hosts are skipped.
- Custom fields: anything `is_internal_field` reports (standard, OTP, KeePassXC-Browser, KeePassXC passkeys) is never shown or edited, only preserved.
- PIN unlock is Vaultline's own (not Windows Hello). A PIN lock drops the parsed database but keeps the credentials in memory; 3 wrong tries or a restart means a full lock.
- The CLI never reads or sets secret values (passwords, card numbers/codes/expiry, notes, OTP secrets, custom field values). `cli_server.entry_meta` is the only place entries are serialised for it. Card fixes run inside the app and report without values. It also refuses `{REF:...}` in anything it writes, because references are resolved when read.
- The one exception is `secret` (agent_access.py): off by default, only for entries in the one folder the user shares (by UUID, subfolders included; no cards or reference fields), only while fully unlocked, and every request is approved in a prompt where Deny is the default. The prompt can grant a pass (this entry for 15 min, or the whole shared folder for the time set in Settings: 15 min by default, 1 hour, until locked, or not offered); each read under a pass is still checked and logged. Any lock, a settings change or the tray's Stop agent access clears allowances. The access log never holds values. The CLI can't move entries or folders into the shared folder.
- Cards use the field names KeePassXC creates when importing from Bitwarden: `card_number` and `card_code` (protected), `card_expMonth`, `card_expYear`, `card_cardholderName`, `card_brand`, plus `Phone` and `Phone (local)`. The website is the normal URL field. Older layouts are still read.
- The window draws its own title bar (`ui/frameless.py`, `ui/titlebar.py`): Windows keeps the caption/thick-frame styles and is told what each pixel is through WM_NCHITTEST, so snapping, resizing and the snap-layout flyout stay native. Don't change Qt window flags at runtime (that resets those styles); "keep on top" uses SetWindowPos. A window narrower than 720px is the compact view.
- Tests use private pipe names (`VAULTLINE_INSTANCE`, set in tests/conftest.py) so they can never talk to a running Vaultline.
- Tests and the demo vault use made-up data only.
