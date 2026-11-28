"""Bitwarden .json import (plain and password protected) into a new database. Made-up data only.
The encrypted exports are made here the way Bitwarden makes them, so no outside files are needed."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

import pytest
from pykeepass import PyKeePass

from vaultline import bitwarden as bw, identity as idlib
from vaultline.vault import ImportEntries, Vault, create_vault

from test_gui import PW, app, setup, wait_for  # noqa: F401  (fixtures)

SECRET = "JBSWY3DPEHPK3PXP"
EXPORT = {
    "encrypted": False,
    "folders": [{"id": "f1", "name": "Work"}, {"id": "f2", "name": "Work/Clients"}],
    "items": [
        {"type": 1, "name": "Example Mail", "folderId": "f1", "favorite": True, "notes": "line 1\nline 2",
         "creationDate": "2024-01-02T03:04:05.000Z", "revisionDate": "2025-06-07T08:09:10.000Z",
         "login": {"username": "alex@example.com", "password": "pw-one", "totp": SECRET,
                   "uris": [{"uri": "https://mail.example.com"}, {"uri": "https://example.com"}]},
         "fields": [{"name": "PIN", "value": "1234", "type": 1}, {"name": "Note", "value": "hi", "type": 0},
                    {"name": "Linked", "value": None, "type": 3}]},
        {"type": 3, "name": "Test Visa", "folderId": "f2",
         "card": {"cardholderName": "Alex Tester", "brand": "Visa", "number": "4111111111111111",
                  "expMonth": "3", "expYear": "2029", "code": "321"}},
        {"type": 4, "name": "Me", "folderId": None,
         "identity": {"title": "Mx", "firstName": "Alex", "middleName": "Q", "lastName": "Tester",
                      "address1": "1 Example St", "address2": "Unit 2", "city": "Toronto", "state": "ON",
                      "postalCode": "M5V 2T6", "country": "Canada", "email": "alex@example.com",
                      "phone": "416-555-0123", "ssn": "000-00-0000", "username": "alextester"}},
        {"type": 2, "name": "Secret note", "notes": "made-up note", "secureNote": {"type": 0}},
        {"type": 1, "name": "Passkey site", "login": {
            "username": "alex", "uris": [{"uri": "https://passkey.example"}],
            "fido2Credentials": [{"credentialId": "a3e15f8b-27c1-42ae-90cf-a615ad87854f",
                                  "keyValue": base64.urlsafe_b64encode(b"made-up key").decode().rstrip("="),
                                  "userName": "alex", "rpId": "passkey.example", "userHandle": "aGFuZGxl"}]}},
    ],
}


def _hkdf(prk: bytes, info: bytes) -> bytes:
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


def _seal(plain: bytes, enc: bytes, mac: bytes) -> str:
    from Cryptodome.Cipher import AES
    iv = os.urandom(16)
    pad = 16 - len(plain) % 16
    data = AES.new(enc, AES.MODE_CBC, iv).encrypt(plain + bytes([pad]) * pad)
    tag = hmac.new(mac, iv + data, hashlib.sha256).digest()
    return "2." + "|".join(base64.b64encode(x).decode() for x in (iv, data, tag))


def encrypt(export: dict, password: str, argon: bool = False) -> dict:
    """Make a password-protected export, as Bitwarden does."""
    salt = base64.b64encode(os.urandom(16)).decode()
    if argon:
        from argon2.low_level import Type, hash_secret_raw
        key = hash_secret_raw(password.encode(), hashlib.sha256(salt.encode()).digest(), time_cost=2,
                              memory_cost=8 * 1024, parallelism=1, hash_len=32, type=Type.ID)
        kdf = {"kdfType": 1, "kdfIterations": 2, "kdfMemory": 8, "kdfParallelism": 1}
    else:
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 1000, 32)
        kdf = {"kdfType": 0, "kdfIterations": 1000, "kdfMemory": None, "kdfParallelism": None}
    enc, mac = _hkdf(key, b"enc"), _hkdf(key, b"mac")
    return {"encrypted": True, "passwordProtected": True, "salt": salt, **kdf,
            "encKeyValidation_DO_NOT_EDIT": _seal(b"made-up check", enc, mac),
            "data": _seal(json.dumps(export).encode(), enc, mac)}


def write(tmp_path, data: dict, name: str = "export.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.parametrize("argon", [False, True])
def test_password_protected_export(tmp_path, argon):
    path = write(tmp_path, encrypt(EXPORT, "export-pw", argon))
    assert bw.is_encrypted(path)
    with pytest.raises(bw.BitwardenError, match="Wrong export password"):
        bw.load(path, "nope")
    with pytest.raises(bw.BitwardenError, match="password protected"):
        bw.load(path, "")
    assert len(bw.items(bw.load(path, "export-pw"))) == 5


def test_refuses_what_it_cant_read(tmp_path):
    with pytest.raises(bw.BitwardenError, match="account restricted"):
        bw.load(write(tmp_path, {"encrypted": True, "data": "2.x|y|z"}))
    with pytest.raises(bw.BitwardenError, match="isn't a Bitwarden"):
        bw.load(write(tmp_path, {"hello": 1}))
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(bw.BitwardenError, match="isn't a Bitwarden"):
        bw.load(tmp_path / "bad.json")
    assert "pw-one" not in repr(bw.items(EXPORT))  # never show values


def test_mapping():
    its = {i.title: i for i in bw.items(EXPORT)}
    mail = its["Example Mail"]
    f = {k: (v, p) for k, v, p in mail.fields}
    assert (mail.username, mail.password, mail.url, mail.folder) == ("alex@example.com", "pw-one",
                                                                      "https://mail.example.com", "Work")
    assert mail.otp.startswith("otpauth://totp/") and SECRET in mail.otp and mail.tags == ["Favorite"]
    assert f["KP2A_URL_1"] == ("https://example.com", False) and f["PIN"] == ("1234", True) and "Linked" not in f
    card = {k: (v, p) for k, v, p in its["Test Visa"].fields}
    assert its["Test Visa"].folder == "Work/Clients"
    assert card["card_number"] == ("4111111111111111", True) and card["card_code"] == ("321", True)
    me = {k: (v, p) for k, v, p in its["Me"].fields}
    assert me[idlib.FIRST][0] == "Alex Q" and me[idlib.ADDRESS][0] == "1 Example St, Unit 2"
    assert me["identity_ssn"] == ("000-00-0000", True) and its["Me"].username == "alextester"
    pk = {k: (v, p) for k, v, p in its["Passkey site"].fields}
    assert pk["KPEX_PASSKEY_CREDENTIAL_ID"][0] == "o-FfiyfBQq6Qz6YVrYeFTw" and its["Passkey site"].tags == ["Passkey"]
    assert pk["KPEX_PASSKEY_PRIVATE_KEY_PEM"][0].startswith("-----BEGIN PRIVATE KEY-----")


def test_imported_into_a_new_database(tmp_path):
    db = tmp_path / "new.kdbx"
    create_vault(str(db), PW)
    v = Vault(str(db), PW, None, tmp_path / "bk")
    v.open()
    snap, _ = v.save([ImportEntries(bw.items(EXPORT))])
    views = {e.title: e for e in snap.entries}
    assert set(views) == {"Example Mail", "Test Visa", "Me", "Secret note", "Passkey site"}
    assert views["Example Mail"].totp is not None and views["Example Mail"].group == "Work"
    assert views["Test Visa"].is_card and views["Test Visa"].card_expiry == ("03", "29")
    assert views["Test Visa"].group == "Work / Clients" and views["Me"].is_identity
    assert not any(f.key.startswith("KPEX_") for f in views["Passkey site"].custom)  # kept, not shown
    kp = PyKeePass(str(db), password=PW)  # and as KeePassXC would read it
    e = kp.find_entries(title="Example Mail", first=True)
    assert e.is_custom_property_protected("PIN") and e.tags == ["Favorite"]
    assert e.ctime.year == 2024 and e.mtime.year == 2025
    pk = kp.find_entries(title="Passkey site", first=True)
    assert pk.get_custom_property("KPEX_PASSKEY_RELYING_PARTY") == "passkey.example"


def test_create_and_import_from_the_app(setup, tmp_path, monkeypatch):
    """New database > Import: decrypts, creates, imports, opens, deletes the export."""
    app, win, vc, *_ = setup
    from vaultline.ui.new_database import NewDatabaseDialog
    export = write(tmp_path, encrypt(EXPORT, "export-pw"))
    dlg = NewDatabaseDialog()
    dlg.path.setText(str(tmp_path / "imported.kdbx"))
    dlg.password.setText("new-master-pw")
    dlg.confirm.setText("new-master-pw")
    monkeypatch.setattr("vaultline.ui.new_database.QFileDialog.getOpenFileName", lambda *a, **k: (str(export), ""))
    dlg._choose_export()
    assert dlg.export_password.isVisibleTo(dlg) and dlg.delete_export.isChecked()
    dlg.export_password.setText("wrong")
    dlg._accept()
    assert dlg.result() == 0 and "Wrong export password" in dlg.error.text()
    dlg.export_password.setText("export-pw")
    dlg._accept()
    assert dlg.result() == NewDatabaseDialog.Accepted and len(dlg.items) == 5
    said = []
    monkeypatch.setattr(win.toast, "show_message", lambda title, *a, **k: said.append(title))
    win._create(dlg.chosen_path(), "new-master-pw", list(dlg.items), dlg.delete_after)
    assert wait_for(app, lambda: vc.is_unlocked, 30)
    assert len(vc.snapshot.entries) == 5 and not export.exists()  # the plain-text risk is gone
    assert wait_for(app, lambda: "Imported 5 entries" in said, 3)
