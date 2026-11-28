"""More than one website per login (KeePassXC's KP2A_URL fields)."""
from __future__ import annotations

from pykeepass import PyKeePass, create_database

from vaultline.vault import CustomField, EntryEdit, MergeDuplicate, Vault

PW = "pw"


def make(tmp_path):
    db = tmp_path / "v.kdbx"
    kp = create_database(str(db), password=PW)
    e = kp.add_entry(kp.root_group, "Shop", "me@example.com", "pw1", url="https://shop.example")
    e.set_custom_property("KP2A_URL_2", "https://c.example")
    e.set_custom_property("KP2A_URL_1", "https://b.example")
    e.set_custom_property("Member no", "42")
    kp.save()
    v = Vault(str(db), PW, None, tmp_path / "bk")
    return db, v, v.open()


def test_read_in_order_and_not_custom(tmp_path):
    _db, _v, snap = make(tmp_path)
    e = snap.entries[0]
    assert e.extra_urls == ("https://b.example", "https://c.example")
    assert [f.key for f in e.custom] == ["Member no"]


def test_edit_keeps_rewrites_and_custom_edit_does_not_drop(tmp_path):
    db, v, snap = make(tmp_path)
    e = snap.entries[0]
    # custom fields edited, websites untouched (None): kept as they were
    v.save([EntryEdit(e.uuid, e.title, e.username, e.password, e.url, "", None,
                      custom=[CustomField("Member no", "43", False)])])
    props = PyKeePass(str(db), PW).entries[0].custom_properties
    assert props["KP2A_URL_1"] == "https://b.example" and props["Member no"] == "43"
    # same list: not rewritten (names stay); a new list: KeePassXC's names from KP2A_URL
    snap, _ = v.save([EntryEdit(e.uuid, e.title, e.username, e.password, e.url, "", None,
                                extra_urls=["https://b.example", "https://c.example"])])
    assert "KP2A_URL_2" in PyKeePass(str(db), PW).entries[0].custom_properties
    snap, _ = v.save([EntryEdit(e.uuid, e.title, e.username, e.password, e.url, "", None,
                                extra_urls=["https://c.example", " ", "https://d.example"])])
    props = PyKeePass(str(db), PW).entries[0].custom_properties
    assert {k: v for k, v in props.items() if k.startswith("KP2A")} == {
        "KP2A_URL": "https://c.example", "KP2A_URL_1": "https://d.example"}
    assert snap.entries[0].extra_urls == ("https://c.example", "https://d.example")


def test_merge_combines_websites(tmp_path):
    db, v, snap = make(tmp_path)
    kp = PyKeePass(str(db), PW)
    d = kp.add_entry(kp.root_group, "Shop", "me@example.com", "pw1", url="https://shop.example/",
                     force_creation=True)
    d.set_custom_property("KP2A_URL_1", "https://other.example")
    d.set_custom_property("KP2A_URL_2", "https://c.example")
    kp.save()
    keep = snap.entries[0].uuid
    v.save([MergeDuplicate(keep, d.uuid.hex)])
    assert v.last_report[0]["filled"] == ["more_urls"]
    e = next(x for x in v.snapshot().entries if x.uuid == keep)
    assert e.extra_urls == ("https://b.example", "https://c.example", "https://other.example")


def test_autotype_matches_any_website():
    from vaultline.autotype import find_matches
    from vaultline.vault import EntryView
    e = EntryView("a" * 32, "Shop login", "me", "pw", "https://shop.example", "", "", "",
                  extra_urls=("https://checkout.othershop.example",))
    found = find_matches([e], "Pay - Othershop - Helium")
    assert [m.entry.title for m in found] == ["Shop login"]


def test_form_plus_button_adds_rows_and_saves(qapp):
    from vaultline.ui.entry_dialog import EntryDialog
    from vaultline.vault import EntryView
    view = EntryView("b" * 32, "Shop", "me", "pw", "https://shop.example", "", "", "",
                     extra_urls=("https://b.example",))
    dlg = EntryDialog(None, view)
    assert [e.text() for _w, e in dlg._url_rows] == ["https://b.example"]
    dlg.add_url_btn.click()
    dlg._url_rows[-1][1].setText("https://new.example")
    dlg._accept()
    assert dlg.result_edit.extra_urls == ["https://b.example", "https://new.example"]

    dlg = EntryDialog(None, view)  # nothing changed: no save
    dlg._accept()
    assert dlg.result_edit is None


def test_merge_fills_empty_main_website(tmp_path):
    db = tmp_path / "w.kdbx"
    kp = create_database(str(db), password=PW)
    a = kp.add_entry(kp.root_group, "Shop", "me", "pw1")
    b = kp.add_entry(kp.root_group, "Shop", "me", "pw1", url="https://shop.example", force_creation=True)
    b.set_custom_property("KP2A_URL_1", "https://b.example")
    kp.save()
    v = Vault(str(db), PW, None, tmp_path / "bk")
    v.open()
    v.save([MergeDuplicate(a.uuid.hex, b.uuid.hex)])
    e = next(x for x in v.snapshot().entries if x.uuid == a.uuid.hex)
    assert e.url == "https://shop.example" and e.extra_urls == ("https://b.example",)
