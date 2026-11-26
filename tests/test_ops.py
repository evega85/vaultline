"""Vault operations added for settings, folders, cards and the command line."""
from __future__ import annotations

from pathlib import Path

import pytest
from pykeepass import PyKeePass, create_database

from vaultline import pin as pinlib
from vaultline.vault import (ChangeMasterPassword, CreateFolder, CustomField, DeleteEntries,
                             DeleteFolder, EntryEdit, MoveEntries, MoveFolder, RenameFolder, UpdateEntry, Vault,
                             VaultError, WrongCredentials)

PW = "pw-123"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    db = tmp_path / "v.kdbx"
    kp = create_database(str(db), password=PW)
    a = kp.add_group(kp.root_group, "A")
    kp.add_group(a, "A1")
    kp.add_entry(kp.root_group, "One", "u1", "p1")
    kp.add_entry(a, "Two", "u2", "p2")
    kp.save()
    v = Vault(str(db), PW, None, tmp_path / "bk")
    v.open()
    return v


def by_title(snap, title):
    return next(e for e in snap.entries if e.title == title)


def group(snap, path):
    return next(g for g in snap.groups if g.path == tuple(path.split("/")))


def test_change_master_password(vault):
    vault.save([ChangeMasterPassword("brand-new-pass")])
    with pytest.raises(Exception):
        PyKeePass(str(vault.path), password=PW)
    PyKeePass(str(vault.path), password="brand-new-pass")
    assert vault.check_password("brand-new-pass") and not vault.check_password(PW)
    # later saves keep working with the new password
    snap, _ = vault.save_entry(EntryEdit(None, "Three", "", "", "", "", ""))
    assert any(e.title == "Three" for e in snap.entries)
    with pytest.raises(WrongCredentials):
        Vault(str(vault.path), PW, None, vault._backup_dir).open()


def test_folder_ops(vault):
    snap = vault.snapshot()
    snap, _ = vault.save([CreateFolder("B/B1/B2"), RenameFolder(group(snap, "A/A1").uuid, "Renamed")])
    assert {g.display for g in snap.groups} >= {"B", "B / B1", "B / B1 / B2", "A / Renamed"}
    snap, _ = vault.save([MoveFolder(group(snap, "B/B1").uuid, group(snap, "A").uuid)])
    assert any(g.display == "A / B1 / B2" for g in snap.groups)
    with pytest.raises(VaultError, match="into itself"):
        vault.save([MoveFolder(group(snap, "A").uuid, group(snap, "A/B1/B2").uuid)])
    with pytest.raises(VaultError):
        vault.save([RenameFolder(group(snap, "A").uuid, "x/y")])
    snap = vault.snapshot()
    snap, _ = vault.save([DeleteFolder(group(snap, "A").uuid)])
    assert not any(g.display.startswith("A") for g in snap.groups)
    assert not any(e.title == "Two" for e in snap.entries)  # went to the recycle bin with A


def test_move_update_delete_entries(vault):
    snap = vault.snapshot()
    one, two = by_title(snap, "One"), by_title(snap, "Two")
    snap, _ = vault.save([MoveEntries([one.uuid, two.uuid], new_group="New/Place"),
                          UpdateEntry(one.uuid, title="Uno", url="https://x.example", tags_add=["t1", "t2"])])
    uno = by_title(snap, "Uno")
    assert uno.group == "New / Place" and by_title(snap, "Two").group == "New / Place"
    assert uno.url == "https://x.example" and set(uno.tags) == {"t1", "t2"}
    snap, _ = vault.save([UpdateEntry(uno.uuid, tags_remove=["t1"]),
                          MoveEntries([uno.uuid], group_uuid=snap.root_uuid)])
    uno = by_title(snap, "Uno")
    assert uno.tags == ("t2",) and uno.group == ""
    snap, _ = vault.save([DeleteEntries([uno.uuid])])
    assert not any(e.title == "Uno" for e in snap.entries)
    with pytest.raises(VaultError, match="No entry"):
        vault.save([DeleteEntries(["0" * 32])])


def test_card_entry_and_search(vault):
    from vaultline.ui.entry_dialog import card_fields
    edit = EntryEdit(None, "Visa", "", "web-pw", "", "", None,
                     custom=card_fields("4111-1111-1111-9876", "321", ("01", "29"), True, "E V", "Visa"),
                     card=True)
    snap, _ = vault.save_entry(edit)
    visa = by_title(snap, "Visa")
    assert visa.is_card and visa.card_code == "321" and visa.card_last4 == "9876"
    assert visa.card_expiry == ("01", "29") and visa.card_holder == "E V"
    for q in (["9876"], ["4111111111119876"], ["4111", "1111"], ["visa"]):
        assert visa.matches(q), q
    assert not visa.matches(["5555"])
    assert not by_title(snap, "One").is_card
    e = PyKeePass(str(vault.path), password=PW).find_entries(title="Visa", first=True)
    assert e.get_custom_property("card_expYear") == "2029" and e.get_custom_property("card_expMonth") == "01"
    assert e.is_custom_property_protected("card_number") and e.is_custom_property_protected("card_code")


def test_bitwarden_import_card_layout(vault, qapp):
    """Same field names KeePassXC creates when importing cards from Bitwarden (values made up)."""
    kp = PyKeePass(str(vault.path), password=PW)
    e = kp.add_entry(kp.root_group, "Example Bank CC", "", "")
    fields = (("card_number", "5500000000000004", False), ("card_code", "777", True),
              ("card_expMonth", "3", False), ("card_expYear", "2028", False),
              ("card_brand", "Mastercard", False), ("card_cardholderName", "E V", False),
              ("Phone", "1-800-000-0000", False), ("Password_c724b", "bank-pw", True),
              ("KPEX_PASSKEY_PRIVATE_KEY_PEM", "-----BEGIN", True))
    for k, v, prot in fields:
        e._set_string_field(k, v, prot)
    kp.save()
    snap = vault.reload_if_changed()
    card = by_title(snap, "Example Bank CC")
    assert card.is_card and card.card_number == "5500000000000004" and card.card_last4 == "0004"
    assert card.card_expiry == ("03", "28") and card.card_code == "777" and card.card_brand == "Mastercard"
    assert card.password == "bank-pw" and card.password_alias == "Password_c724b"
    assert not any(f.key.startswith("KPEX_") for f in card.custom)  # passkey hidden
    assert card.matches(["mastercard"]) and card.matches(["0004"])

    # Opening it in the edit dialog and saving without changes writes nothing
    from vaultline.ui.entry_dialog import EntryDialog
    dlg = EntryDialog(None, card, groups=snap.groups, root_uuid=snap.root_uuid)
    assert dlg._card and dlg.card_number.text() == "5500000000000004"
    assert (dlg.exp_month.text(), dlg.exp_year.text()) == ("03", "28") and dlg.password.text() == "bank-pw"
    assert [r.key.text() for r in dlg._rows] == [] and dlg.card_phone.text() == "1-800-000-0000"
    dlg._accept()
    assert dlg.result_edit is None  # unchanged -> no save

    # A real edit: password moves into the Password field, layout and 4-digit year kept, passkey kept
    dlg = EntryDialog(None, card, groups=snap.groups, root_uuid=snap.root_uuid)
    dlg.exp_year.setText("30")
    dlg._accept()
    vault.save_entry(dlg.result_edit)
    e = PyKeePass(str(vault.path), password=PW).find_entries(title="Example Bank CC", first=True)
    assert e.password == "bank-pw" and e.get_custom_property("Password_c724b") is None
    assert e.get_custom_property("card_expYear") == "2030" and e.get_custom_property("card_expMonth") == "03"
    assert e.get_custom_property("card_number") == "5500000000000004"
    assert e.is_custom_property_protected("card_number")
    assert e.get_custom_property("KPEX_PASSKEY_PRIVATE_KEY_PEM") == "-----BEGIN"
    assert e.get_custom_property("Phone") == "1-800-000-0000"


def test_suspend_and_reopen(vault):
    vault.suspend()
    assert vault._kp is None and vault.check_password(PW)
    assert any(e.title == "One" for e in vault.open().entries)


def test_pin_helpers():
    salt, h = pinlib.make("482916")
    assert pinlib.check("482916", salt, h) and not pinlib.check("482917", salt, h)
    for bad in ("12", "1111", "1234", "abcd", "9876"):
        assert pinlib.valid(bad), bad
    assert pinlib.valid("482916") is None


# ---- card fixes (vaultline-cli cards fix) ---------------------------------------------
def _imported_card(kp, title, alias=True, year="2028", month="3"):
    e = kp.add_entry(kp.root_group, title, "", "")
    for k, v, prot in (("card_number", "5500000000000004", False), ("card_code", "777", False),
                       ("card_expMonth", month, False), ("card_expYear", year, False),
                       ("card_brand", "Mastercard", False)):
        e._set_string_field(k, v, prot)
    if alias:
        e._set_string_field("Password_c724b", "bank-pw", True)
    return e


def test_normalize_imported_cards(vault):
    from vaultline.vault import NormalizeCards, card_fixes
    kp = PyKeePass(str(vault.path), password=PW)
    _imported_card(kp, "Bank A")
    _imported_card(kp, "Bank B", alias=False, year="28", month="11")
    kp.save()
    snap = vault.reload_if_changed()
    bank_a, bank_b = by_title(snap, "Bank A"), by_title(snap, "Bank B")
    assert card_fixes(bank_a) == ["pad expiry month to two digits", "hide card number", "hide card code",
                               "move Password_c724b into Password"]
    assert card_fixes(bank_b) == ["hide card number", "hide card code"]
    assert card_fixes(by_title(snap, "One")) == []  # not a card

    snap, _ = vault.save([NormalizeCards([bank_a.uuid, bank_b.uuid, by_title(snap, "One").uuid])])
    assert [r["title"] for r in vault.last_report] == ["Bank A", "Bank B"]
    assert all("5500" not in str(r) and "777" not in str(r) for r in vault.last_report)  # no values
    kp = PyKeePass(str(vault.path), password=PW)
    e = kp.find_entries(title="Bank A", first=True)
    assert e.password == "bank-pw" and e.get_custom_property("Password_c724b") is None
    assert e.is_custom_property_protected("card_number") and e.is_custom_property_protected("card_code")
    assert e.get_custom_property("card_expMonth") == "03" and e.get_custom_property("card_expYear") == "2028"
    assert len(e.history) == 1  # previous version kept in history
    t = kp.find_entries(title="Bank B", first=True)
    assert t.get_custom_property("card_expYear") == "28" and t.get_custom_property("card_expMonth") == "11"
    # running it again changes nothing
    assert all(card_fixes(v) == [] for v in snap.entries)


def test_normalize_legacy_card(vault):
    from vaultline.vault import NormalizeCards, card_fixes
    kp = PyKeePass(str(vault.path), password=PW)
    e = kp.add_entry(kp.root_group, "Old Visa", "4111 1111 1111 1234", "pw", tags=["Card"])
    e._set_string_field("Card code", "123", True)
    e._set_string_field("Expiry", "12/29", False)
    kp.save()
    snap = vault.reload_if_changed()
    old = by_title(snap, "Old Visa")
    assert "move card number from username to card_number" in card_fixes(old)
    snap, _ = vault.save([NormalizeCards([old.uuid])])
    e = PyKeePass(str(vault.path), password=PW).find_entries(title="Old Visa", first=True)
    assert not e.username and e.get_custom_property("card_number") == "4111 1111 1111 1234"
    assert e.get_custom_property("card_code") == "123" and e.get_custom_property("Card code") is None
    assert (e.get_custom_property("card_expMonth"), e.get_custom_property("card_expYear")) == ("12", "2029")
    assert e.get_custom_property("Expiry") is None and "Card" not in (e.tags or [])
    assert e.password == "pw"
    assert card_fixes(by_title(snap, "Old Visa")) == []


def test_card_website_and_phone_fixes(vault):
    from vaultline.vault import NormalizeCards, card_fixes
    kp = PyKeePass(str(vault.path), password=PW)
    a = _imported_card(kp, "Moves", alias=False, month="03")
    a._set_string_field("Website", "https://bank.example/login", False)
    a._set_string_field("Phone_59f63", "1-800-111-2222", False)
    b = _imported_card(kp, "Dupes", alias=False, month="03")
    b.url = "https://other.example"
    b._set_string_field("Website", "https://other.example", False)
    b._set_string_field("Phone", "1-800-333-4444", False)
    b._set_string_field("Phone_38583", "1-800-333-4444", False)
    c = _imported_card(kp, "Keeps", alias=False, month="03")
    c.url = "https://one.example"
    c._set_string_field("Website", "https://two.example", False)
    c._set_string_field("Phone", "1-800-555-0000", False)
    c._set_string_field("Phone_c4785", "1-800-555-9999", False)
    kp.save()
    snap = vault.reload_if_changed()
    fx = {t: card_fixes(by_title(snap, t)) for t in ("Moves", "Dupes", "Keeps")}
    assert "move Website into URL" in fx["Moves"] and "move Phone_59f63 into Phone" in fx["Moves"]
    assert "remove Website (same as URL)" in fx["Dupes"] and "remove Phone_38583 (same number as another phone field)" in fx["Dupes"]
    assert not any("Website" in f or "Phone" in f for f in fx["Keeps"])  # different values: both kept

    vault.save([NormalizeCards([by_title(snap, t).uuid for t in ("Moves", "Dupes", "Keeps")])])
    kp = PyKeePass(str(vault.path), password=PW)
    m = kp.find_entries(title="Moves", first=True)
    assert m.url == "https://bank.example/login" and m.get_custom_property("Website") is None
    assert m.get_custom_property("Phone") == "1-800-111-2222" and m.get_custom_property("Phone_59f63") is None
    d = kp.find_entries(title="Dupes", first=True)
    assert d.get_custom_property("Website") is None and d.get_custom_property("Phone_38583") is None
    k = kp.find_entries(title="Keeps", first=True)
    assert k.get_custom_property("Website") == "https://two.example"
    assert k.get_custom_property("Phone_c4785") == "1-800-555-9999"
    snap = vault.snapshot()
    assert by_title(snap, "Moves").card_phone == "1-800-111-2222"
    assert all(card_fixes(v) == [] for v in snap.entries)


def test_toll_free_and_local_phone_sorting(vault):
    from vaultline.vault import NormalizeCards, card_fixes
    kp = PyKeePass(str(vault.path), password=PW)
    a = _imported_card(kp, "LocalFirst", alias=False, month="03")   # Phone holds the local number
    a._set_string_field("Phone", "+505 2255 9595", False)
    a._set_string_field("Phone_59f63", "1800-2255", False)
    b = _imported_card(kp, "TollFirst", alias=False, month="03")    # Phone already toll-free
    b._set_string_field("Phone", "1-800-769-2511", False)
    b._set_string_field("Phone_38583", "604-877-7000", False)
    c = _imported_card(kp, "Unclear", alias=False, month="03")      # neither toll-free
    c._set_string_field("Phone", "604-111-1111", False)
    c._set_string_field("Phone_c4785", "604-222-2222", False)
    kp.save()
    snap = vault.reload_if_changed()
    assert not any("Phone" in f for f in card_fixes(by_title(snap, "Unclear")))  # can't tell: no phone change
    vault.save([NormalizeCards([by_title(snap, t).uuid for t in ("LocalFirst", "TollFirst", "Unclear")])])
    kp = PyKeePass(str(vault.path), password=PW)
    lf = kp.find_entries(title="LocalFirst", first=True)
    assert lf.get_custom_property("Phone") == "1800-2255"
    assert lf.get_custom_property("Phone (local)") == "+505 2255 9595"
    assert lf.get_custom_property("Phone_59f63") is None
    tf = kp.find_entries(title="TollFirst", first=True)
    assert tf.get_custom_property("Phone") == "1-800-769-2511"
    assert tf.get_custom_property("Phone (local)") == "604-877-7000"
    un = kp.find_entries(title="Unclear", first=True)
    assert un.get_custom_property("Phone_c4785") == "604-222-2222"  # left alone
    snap = vault.snapshot()
    assert by_title(snap, "LocalFirst").card_local_phone == "+505 2255 9595"
    assert all(card_fixes(v) == [] for v in snap.entries)
