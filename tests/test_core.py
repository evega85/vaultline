"""Tests for OTP handling and sync-safe vault saving. Run: python -m pytest -q"""
from __future__ import annotations

import time
from pathlib import Path

import pyotp
import pytest
from pykeepass import PyKeePass, create_database

from vaultline import otp as otplib
from vaultline import passgen
from vaultline.vault import CustomField, EntryEdit, FileUnreadable, IconUpdate, Vault, WrongCredentials

PW = "correct horse battery staple"
SECRET = "JBSWY3DPEHPK3PXP"


# ---- OTP ---------------------------------------------------------------------
def test_raw_base32_becomes_uri_with_same_codes():
    uri = otplib.normalize("jbsw y3dp ehpk 3pxp", "GitHub", "me@example.com")
    assert uri.startswith("otpauth://totp/GitHub:me%40example.com?") or uri.startswith("otpauth://totp/GitHub:me@example.com?")
    assert "secret=JBSWY3DPEHPK3PXP" in uri and "issuer=GitHub" in uri
    assert otplib.parse_uri(uri).now() == pyotp.TOTP(SECRET).now()


def test_uri_passthrough_keeps_custom_settings():
    uri = "otpauth://totp/X:y?secret=JBSWY3DPEHPK3PXP&period=60&digits=8&algorithm=SHA256"
    assert otplib.normalize(uri) == uri
    t = otplib.parse_uri(uri)
    assert t.interval == 60 and t.digits == 8


@pytest.mark.parametrize("bad", ["not base32 !!", "otpauth://hotp/X?secret=JBSWY3DPEHPK3PXP&counter=1",
                                 "otpauth://totp/X?nosecret=1"])
def test_bad_otp_rejected(bad):
    with pytest.raises(otplib.OtpError):
        otplib.normalize(bad)


def test_legacy_fields_read():
    t = otplib.from_fields({"TimeOtp-Secret-Base32": SECRET, "TimeOtp-Period": "30"})
    assert t is not None and t.now() == pyotp.TOTP(SECRET).now()
    t = otplib.from_fields({"TOTP Seed": SECRET, "TOTP Settings": "30;6"})
    assert t is not None and t.now() == pyotp.TOTP(SECRET).now()
    assert otplib.from_fields({"Title": "x"}) is None


def test_password_generator():
    pw = passgen.generate(20)
    assert len(pw) == 20
    assert any(c.islower() for c in pw) and any(c.isupper() for c in pw)
    assert any(c.isdigit() for c in pw) and any(c in passgen.SYMBOLS for c in pw)
    assert len({passgen.generate() for _ in range(50)}) == 50


# ---- Vault -------------------------------------------------------------------
@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "sync" / "vault.kdbx"
    path.parent.mkdir()
    kp = create_database(str(path), password=PW)
    kp.add_entry(kp.root_group, "Email", "me@example.com", "pw1")
    kp.save()
    return path


def make_vault(db: Path, tmp_path: Path) -> Vault:
    return Vault(str(db), PW, None, tmp_path / "backups", backups_to_keep=3)


def new(title, **kw):
    return EntryEdit(None, title, kw.get("username", ""), kw.get("password", "x"), "", "", kw.get("otp", ""))


def test_wrong_password(db, tmp_path):
    with pytest.raises(WrongCredentials):
        Vault(str(db), "nope", None, tmp_path / "b").open()


def test_add_entry_writes_standard_otp_field(db, tmp_path):
    v = make_vault(db, tmp_path)
    v.open()
    uri = otplib.normalize(SECRET, "GitHub", "me")
    snap, merged = v.save_entry(new("GitHub", username="me", otp=uri))
    entries = snap.entries
    assert not merged
    gh = next(e for e in entries if e.title == "GitHub")
    assert gh.totp is not None and gh.totp.now() == pyotp.TOTP(SECRET).now()

    # Re-open independently, as KeePassXC/KeePassium would.
    kp = PyKeePass(str(db), password=PW)
    e = kp.find_entries(title="GitHub", first=True)
    assert e.otp == uri
    val = next(s for s in e._element.findall("String") if s.find("Key").text == "otp").find("Value")
    assert val.get("Protected") == "True"
    # no temp file left behind in the synced folder; a backup was made locally
    assert [p.name for p in db.parent.iterdir()] == ["vault.kdbx"]
    assert len(list((tmp_path / "backups").glob("*.kdbx"))) == 1


def test_save_merges_changes_made_elsewhere(db, tmp_path):
    v = make_vault(db, tmp_path)
    v.open()
    # "Phone" adds an entry and syncs it while we are open.
    phone = PyKeePass(str(db), password=PW)
    phone.add_entry(phone.root_group, "From phone", "p", "p")
    phone.save()
    time.sleep(0.01)

    snap, merged = v.save_entry(new("From PC"))
    assert merged
    titles = {e.title for e in snap.entries}
    assert {"Email", "From phone", "From PC"} <= titles
    on_disk = {e.title for e in PyKeePass(str(db), password=PW).entries}
    assert {"Email", "From phone", "From PC"} <= on_disk


def test_edit_keeps_history_and_untouched_legacy_otp(db, tmp_path):
    kp = PyKeePass(str(db), password=PW)
    e = kp.find_entries(title="Email", first=True)
    e.set_custom_property("TimeOtp-Secret-Base32", SECRET, protect=True)
    kp.save()

    v = make_vault(db, tmp_path)
    entries = v.open().entries
    email = next(x for x in entries if x.title == "Email")
    assert email.totp is not None
    v.save_entry(EntryEdit(email.uuid, "Email", "new@example.com", "pw2", "", "", None))

    e = PyKeePass(str(db), password=PW).find_entries(title="Email", first=True)
    assert e.username == "new@example.com" and e.password == "pw2"
    assert e.get_custom_property("TimeOtp-Secret-Base32") == SECRET  # left alone
    assert e.otp is None
    assert len(e.history) == 1 and e.history[0].password == "pw1"


def test_clearing_otp_removes_fields(db, tmp_path):
    v = make_vault(db, tmp_path)
    v.open()
    snap, _ = v.save_entry(new("Site", otp=otplib.normalize(SECRET, "Site")))
    site = next(x for x in snap.entries if x.title == "Site")
    snap, _ = v.save_entry(EntryEdit(site.uuid, "Site", "", "x", "", "", ""))
    assert next(x for x in snap.entries if x.title == "Site").totp is None


def test_reload_detects_external_change_and_ignores_partial_file(db, tmp_path):
    v = make_vault(db, tmp_path)
    v.open()
    assert v.reload_if_changed() is None

    phone = PyKeePass(str(db), password=PW)
    phone.add_entry(phone.root_group, "Synced", "s", "s")
    phone.save()
    snap = v.reload_if_changed()
    assert snap is not None and any(e.title == "Synced" for e in snap.entries)

    # A half-downloaded file must not replace what we have in memory.
    good = db.read_bytes()
    db.write_bytes(good[: len(good) // 2])
    with pytest.raises(FileUnreadable):
        v.reload_if_changed()
    assert any(e.title == "Synced" for e in v.snapshot().entries)
    db.write_bytes(good)


def test_backups_are_rotated(db, tmp_path):
    v = make_vault(db, tmp_path)
    v.open()
    for i in range(5):
        v.save_entry(new(f"E{i}"))
        time.sleep(1.05)  # backup names have one-second resolution
    assert len(list((tmp_path / "backups").glob("*.kdbx"))) == 3


def test_save_repairs_a_rename_the_drive_lost(db, tmp_path, monkeypatch):
    # rclone's VFS (1.76) leaves an empty file when a temp file is renamed over a cloud file
    import os
    import vaultline.vault as vaultmod

    real_replace = os.replace

    def lossy_replace(src, dst):
        real_replace(src, dst)
        Path(dst).write_bytes(b"")

    monkeypatch.setattr(vaultmod.os, "replace", lossy_replace)
    v = make_vault(db, tmp_path)
    v.open()
    v.save_entry(new("Bank"))
    kp = PyKeePass(str(db), password=PW)
    assert kp.find_entries(title="Bank", first=True) is not None
    assert [p.name for p in db.parent.iterdir()] == ["vault.kdbx"]


def test_save_fails_loudly_when_the_drive_keeps_losing_it(db, tmp_path, monkeypatch):
    import vaultline.vault as vaultmod
    from vaultline.vault import VaultError

    v = make_vault(db, tmp_path)
    v.open()
    monkeypatch.setattr(vaultmod.Vault, "_written_ok", lambda self, data: False)
    with pytest.raises(VaultError, match="did not save correctly"):
        v.save_entry(new("Bank"))
    assert len(list((tmp_path / "backups").glob("*.kdbx"))) == 1


def test_empty_database_file_names_the_backup(db, tmp_path):
    v = make_vault(db, tmp_path)
    v.open()
    v.save_entry(new("Bank"))
    db.write_bytes(b"")
    with pytest.raises(FileUnreadable, match="empty.*backups"):
        make_vault(db, tmp_path).open()


def test_recycle_bin_entries_hidden(db, tmp_path):
    kp = PyKeePass(str(db), password=PW)
    e = kp.add_entry(kp.root_group, "Trash me", "", "")
    kp.trash_entry(e)
    kp.save()
    titles = {x.title for x in make_vault(db, tmp_path).open().entries}
    assert "Trash me" not in titles and "Email" in titles


# ---- folders, custom fields, icons ------------------------------------------------
def test_folders_listed_and_entries_moved(db, tmp_path):
    kp = PyKeePass(str(db), password=PW)
    work = kp.add_group(kp.root_group, "Work")
    kp.add_group(work, "Clients")
    kp.save()
    v = make_vault(db, tmp_path)
    snap = v.open()
    assert [g.display for g in snap.groups] == ["Work", "Work / Clients"]
    assert [g.depth for g in snap.groups] == [0, 1]
    clients = snap.groups[1]
    email = next(e for e in snap.entries if e.title == "Email")

    # move into an existing folder
    snap, _ = v.save_entry(EntryEdit(email.uuid, "Email", email.username, email.password, "", "", None,
                                     group_uuid=clients.uuid))
    assert next(e for e in snap.entries if e.title == "Email").group == "Work / Clients"
    # new entry into a brand-new nested folder
    snap, _ = v.save_entry(EntryEdit(None, "Visa", "", "", "", "", "", new_group="Finance/Cards"))
    visa = next(e for e in snap.entries if e.title == "Visa")
    assert visa.group == "Finance / Cards"
    assert {g.display for g in snap.groups} >= {"Finance", "Finance / Cards"}
    kp = PyKeePass(str(db), password=PW)
    assert kp.find_entries(title="Visa", first=True).group.name == "Cards"


def test_custom_fields_roundtrip_and_internal_fields_kept(db, tmp_path):
    kp = PyKeePass(str(db), password=PW)
    e = kp.find_entries(title="Email", first=True)
    e.set_custom_property("KeePassXC-Browser Settings", "{}")
    e.set_custom_property("Recovery email", "old@example.com")
    kp.save()
    v = make_vault(db, tmp_path)
    email = next(x for x in v.open().entries if x.title == "Email")
    assert [f.key for f in email.custom] == ["Recovery email"]  # browser settings hidden

    fields = [CustomField("Card Number", "4111111111111111", True), CustomField("Expiry", "12/29")]
    snap, _ = v.save_entry(EntryEdit(email.uuid, "Email", email.username, email.password, "", "", None,
                                     custom=fields))
    email = next(x for x in snap.entries if x.title == "Email")
    assert list(email.custom) == fields
    e = PyKeePass(str(db), password=PW).find_entries(title="Email", first=True)
    assert e.get_custom_property("Recovery email") is None              # removed by user
    assert e.get_custom_property("KeePassXC-Browser Settings") == "{}"  # preserved
    assert e.is_custom_property_protected("Card Number")
    assert not e.is_custom_property_protected("Expiry")


def _png(color: str) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    from vaultline.favicons import to_png
    img = QImage(32, 32, QImage.Format_ARGB32)
    img.fill(QColor(color))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return to_png(bytes(ba))


def test_custom_icons_stored_like_keepassxc(db, tmp_path, qapp):
    v = make_vault(db, tmp_path)
    v.open()
    snap, _ = v.save_entry(new("Other"))
    a, b = (next(e for e in snap.entries if e.title == t) for t in ("Email", "Other"))
    red = _png("#ff0000")
    snap, _ = v.save([IconUpdate({a.uuid: red, b.uuid: red})])
    a, b = (next(e for e in snap.entries if e.title == t) for t in ("Email", "Other"))
    assert a.icon_uuid and a.icon_uuid == b.icon_uuid  # identical icons shared
    assert snap.icons[a.icon_uuid] == red
    # replacing both drops the now-unused icon
    blue = _png("#0000ff")
    snap, _ = v.save([IconUpdate({a.uuid: blue, b.uuid: blue})])
    assert list(snap.icons.values()) == [blue]
    root = PyKeePass(str(db), password=PW).tree.getroot()
    assert len(root.findall("Meta/CustomIcons/Icon")) == 1
    assert len(root.findall("Root//Entry/CustomIconUUID")) >= 2


def test_favicon_helpers(qapp):
    from PySide6.QtGui import QImage

    from vaultline import favicons
    assert favicons.host_of("github.com/login") == "github.com"
    assert favicons.host_of("https://accounts.google.com/x") == "accounts.google.com"
    for local in ("http://192.168.1.1", "localhost:8080", "https://nas.local", "cmd://calc", "", "router"):
        assert favicons.host_of(local) is None, local
    assert favicons._parent_domain("accounts.google.com") == "google.com"
    assert favicons._parent_domain("www.bbc.co.uk") == "bbc.co.uk"
    assert favicons._parent_domain("google.com") is None
    p = favicons._LinkParser()
    try:
        p.feed('<html><head><link rel="icon" href="/a16.png" sizes="16x16">'
               '<link rel="apple-touch-icon" href="/touch.png"><link rel="icon" href="/x.svg">'
               '</head><body><link rel="icon" href="/late.png"></body>')
    except favicons._StopParsing:
        pass
    best = [h for _s, h in sorted(p.icons, key=lambda t: -t[0])]
    assert best[0] == "/touch.png" and best[-1] == "/x.svg" and "/late.png" not in best
    assert favicons.to_png(b"not an image") is None
    assert QImage.fromData(_png("#00ff00")).width() == 32


def test_generator_uses_only_the_chosen_characters():
    pw = passgen.generate(16, upper=False, lower=True, digits=True, symbols=False)
    assert len(pw) == 16 and all(c in passgen.LOWER + passgen.DIGITS for c in pw)
    assert any(c in passgen.DIGITS for c in pw) and any(c in passgen.LOWER for c in pw)  # one of each set
    assert all(c in passgen.DIGITS for c in passgen.generate(8, False, False, True, False))
    assert len(passgen.generate(12, False, False, False, False)) == 12  # none chosen: all sets


def test_generator_settings_and_the_old_length(tmp_path, monkeypatch):
    import json
    from vaultline import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"newlogin_length": 28}))
    c = cfgmod.Config.load()
    assert c.pw_length == 28 and c.pw_upper and c.pw_symbols  # the earlier new-login length carries over
    c.pw_symbols, c.pw_upper, c.pw_length = False, False, 10
    pw = passgen.from_config(c)
    assert len(pw) == 10 and all(ch in passgen.LOWER + passgen.DIGITS for ch in pw)


def test_error_messages_wrap_and_copy_in_full(qapp):
    from PySide6.QtWidgets import QApplication, QWidget
    from vaultline.ui.toast import Toast
    from vaultline.ui.widgets import ErrorLabel
    msg = r"The database file is empty. The latest backup is D:\Example\Vaultline\backups\Some.Long-Name.kdbx"
    err = ErrorLabel(center=True)
    err.setText(msg)
    assert err.text() == msg and err.label.wordWrap()
    err.copy_btn.click()
    assert QApplication.clipboard().text() == msg and err.copy_btn.text().strip() == "Copied"

    host = QWidget()
    host.resize(400, 300)
    toast = Toast(host)
    toast.show_message("Not saved", msg, "error")
    assert toast.copy_btn.isVisibleTo(toast) and toast.width() <= 400 - 32
    toast.copy_btn.click()
    assert QApplication.clipboard().text() == f"Not saved: {msg}"
    toast.show_message("Copied", "password", "ok")  # ordinary notices: no button, clicks pass through
    assert not toast.copy_btn.isVisibleTo(toast)
