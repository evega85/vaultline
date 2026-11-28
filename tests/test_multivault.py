"""Several databases at once: AutoOpen entries, merged snapshots, routing, and the controller with real files."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import dispose
from pykeepass import PyKeePass, create_database

from vaultline import controller as ctlmod, multivault
from vaultline.config import Config
from vaultline.multivault import MAIN, Router
from vaultline.vault import (CreateFolder, DeleteEntries, DeleteFolder, EntryEdit, MoveEntries, MoveFolder, RenameFolder,
                             Vault, VaultError)

PW = "main pw"
SPW = "shared pw that is long"


def make_pair(tmp_path: Path, url: str | None = None, **autoopen):
    """A main database with an AutoOpen entry for a shared one beside it."""
    shared = tmp_path / "drive" / "Shared.kdbx"
    shared.parent.mkdir(parents=True, exist_ok=True)
    kp = create_database(str(shared), password=SPW)
    biz = kp.add_group(kp.root_group, "Business")
    kp.add_entry(biz, "Bank", "biz@example.com", "bank-pass")
    kp.add_entry(kp.root_group, "Supplier", "orders@example.com", "sup-pass")
    kp.save()

    main = tmp_path / "Personal.kdbx"
    kp = create_database(str(main), password=PW)
    work = kp.add_group(kp.root_group, "Work")
    kp.add_entry(work, "Email", "me@example.com", "mail-pass")
    ao = kp.add_group(kp.root_group, "AutoOpen")
    e = kp.add_entry(ao, autoopen.pop("title", "Shared"), autoopen.pop("username", ""), autoopen.pop("password", SPW),
                     url=url if url is not None else "{DB_DIR}/drive/Shared.kdbx")
    for k, v in autoopen.items():
        e.set_custom_property(k, v)
    kp.save()
    return main, shared


def snap_of(path, pw):
    return Vault(str(path), pw, None, path.parent / "bk").open()


# ---- AutoOpen ---------------------------------------------------------------------
def test_resolve_path_forms(tmp_path):
    main = str(tmp_path / "a" / "Personal.kdbx")
    want = str(tmp_path / "a" / "x" / "S.kdbx")
    assert multivault.resolve_path("{DB_DIR}/x/S.kdbx", main) == want
    assert multivault.resolve_path("x/S.kdbx", main) == want
    assert multivault.resolve_path("kdbx://{DB_DIR}/x/S.kdbx", main) == want
    assert multivault.resolve_path(Path(want).as_uri(), main) == want
    assert multivault.resolve_path("", main) == ""


def test_autoopen_items(tmp_path):
    main, shared = make_pair(tmp_path)
    items = multivault.autoopen_items(snap_of(main, PW), str(main))
    assert len(items) == 1
    it = items[0]
    assert it.name == "Shared" and it.password == SPW and it.keyfile == ""
    assert it.key == multivault.path_key(str(shared))


@pytest.mark.parametrize("rule,host,ok", [
    ("", "PC1", True), ("pc1", "PC1", True), ("PC2", "PC1", False),
    ("!PC1", "PC1", False), ("!PC2", "PC1", True), ("PC1, !PC2", "PC2", False),
])
def test_ifdevice(rule, host, ok):
    assert multivault._device_allowed(rule, host) is ok


def test_autoopen_skips_other_devices_self_and_nested(tmp_path):
    main, _ = make_pair(tmp_path, IfDevice="SOMEONE-ELSE")
    assert multivault.autoopen_items(snap_of(main, PW), str(main), host="MYPC") == []
    main2, _ = make_pair(tmp_path / "b", url="{DB_DIR}/Personal.kdbx")  # itself
    assert multivault.autoopen_items(snap_of(main2, PW), str(main2)) == []


# ---- merge and routing ----------------------------------------------------------
def merged_pair(tmp_path):
    main, shared = make_pair(tmp_path)
    ms, ss = snap_of(main, PW), snap_of(shared, SPW)
    key = multivault.path_key(str(shared))
    names = multivault.display_names(ms, {key: "Shared"})
    info = multivault.VaultInfo(key, names[key], str(shared), ss.root_uuid)
    return multivault.merge(ms, {key: (names[key], ss)}, [info]), ms, ss, key


def test_merge_shows_shared_as_top_folder(tmp_path):
    m, ms, ss, key = merged_pair(tmp_path)
    top = next(g for g in m.groups if g.uuid == multivault.top_id(key))
    assert top.path == ("Shared",) and top.depth == 0 and top.vault == key
    biz = next(g for g in m.groups if g.name == "Business")
    assert biz.path == ("Shared", "Business") and biz.depth == 1
    bank = next(e for e in m.entries if e.title == "Bank")
    assert bank.group == "Shared / Business" and bank.vault == key
    sup = next(e for e in m.entries if e.title == "Supplier")
    assert sup.group == "Shared" and sup.group_uuid == top.uuid
    assert next(e for e in m.entries if e.title == "Email").vault == MAIN
    assert m.root_uuid == ms.root_uuid
    # merging again doesn't prefix twice (the per-database snapshot is untouched)
    again = multivault.merge(ms, {key: ("Shared", ss)}, [])
    assert next(e for e in again.entries if e.title == "Bank").group == "Shared / Business"


def test_display_name_clash(tmp_path):
    main, _ = make_pair(tmp_path, title="Work")
    ms = snap_of(main, PW)
    assert multivault.display_names(ms, {"k": "Work"}) == {"k": "Work (vault)"}


def test_router(tmp_path):
    m, ms, ss, key = merged_pair(tmp_path)
    r = Router(m)
    bank = next(e for e in m.entries if e.title == "Bank")
    email = next(e for e in m.entries if e.title == "Email")
    biz = next(g for g in m.groups if g.name == "Business")
    work = next(g for g in m.groups if g.name == "Work")
    top = multivault.top_id(key)
    assert ss.root_uuid == m.root_uuid  # pykeepass's template: same root uuid in both files

    (k, [ch]), = r.route([EntryEdit(None, "t", "", "p", "", "", None, group_uuid=top)]).items()
    assert k == key and ch.group_uuid == ss.root_uuid

    assert r.route([CreateFolder("Shared/Clients")]) == {key: [CreateFolder("Clients")]}
    assert r.route([CreateFolder("Clients")]) == {MAIN: [CreateFolder("Clients")]}
    new = EntryEdit(None, "t", "", "p", "", "", None, group_uuid=biz.uuid)
    assert list(r.route([new])) == [key]
    new_top = EntryEdit(None, "t", "", "p", "", "", None, new_group="Shared")
    (k, [ch]), = r.route([new_top]).items()
    assert k == key and ch.group_uuid == ss.root_uuid and ch.new_group == ""
    routes = r.route([DeleteEntries([bank.uuid, email.uuid])])
    assert routes == {key: [DeleteEntries([bank.uuid])], MAIN: [DeleteEntries([email.uuid])]}
    (k, [mf]), = r.route([MoveFolder(biz.uuid, None)]).items()
    assert k == key and mf.parent_uuid == ss.root_uuid
    (k, [mv]), = r.route([MoveEntries([email.uuid], group_uuid=biz.uuid)]).items()
    assert k == multivault.MOVES and mv == multivault.CrossMove(MAIN, key, (email.uuid,), None, biz.uuid)
    routes = r.route([EntryEdit(email.uuid, "Email", "", "", "", "", None, group_uuid=top)])
    assert routes[MAIN][0].group_uuid is None
    assert routes[multivault.MOVES] == [multivault.CrossMove(MAIN, key, (email.uuid,), None, ss.root_uuid)]
    (k, [mv]), = r.route([MoveFolder(work.uuid, biz.uuid)]).items()
    assert mv.folder == work.uuid and mv.dest == key
    (k, [mv]), = r.route([MoveEntries([bank.uuid], new_group="Old/Bank")]).items()
    assert (mv.src, mv.dest, mv.new_group, mv.group_uuid) == (key, MAIN, "Old/Bank", None)
    for ch in (RenameFolder(top, "x"), DeleteFolder(top)):
        with pytest.raises(VaultError):
            r.route([ch])


# ---- controller with real files ------------------------------------------------------
@pytest.fixture
def vc(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ctlmod, "BACKUP_DIR", tmp_path / "backups")
    c = ctlmod.VaultController(Config(auto_download_icons=False))
    yield c
    dispose(c)


def wait_for(qapp, cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def titles(vc):
    return {e.title for e in vc.snapshot.entries} if vc.snapshot else set()


def test_controller_opens_both_and_routes_saves(qapp, vc, tmp_path):
    main, shared = make_pair(tmp_path)
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: {"Bank", "Email"} <= titles(vc))
    assert [i.name for i in vc.vault_infos()] == ["Shared"]
    biz = next(g for g in vc.snapshot.groups if g.name == "Business")

    vc.save(EntryEdit(None, "Payroll", "hr", "pay-pass", "", "", None, group_uuid=biz.uuid))
    assert wait_for(qapp, lambda: "Payroll" in titles(vc))
    assert [e.title for e in PyKeePass(str(shared), SPW).find_entries(title="Payroll")]
    assert not PyKeePass(str(main), PW).find_entries(title="Payroll")
    pay = next(e for e in vc.snapshot.entries if e.title == "Payroll")
    assert pay.group == "Shared / Business"

    got = []
    vc.applied.connect(lambda t, ok, msg, rep: got.append((t, ok, msg)))
    vc.apply([CreateFolder("Shared/Clients"), CreateFolder("Home")], token="f")
    assert wait_for(qapp, lambda: got)
    assert got[-1][1], got
    assert PyKeePass(str(shared), SPW).find_groups(name="Clients", first=True)
    assert PyKeePass(str(main), PW).find_groups(name="Home", first=True)
    assert not PyKeePass(str(main), PW).find_groups(name="Clients", first=True)



def test_controller_sees_changes_from_other_pc(qapp, vc, tmp_path):
    main, shared = make_pair(tmp_path)
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: "Bank" in titles(vc))
    kp = PyKeePass(str(shared), SPW)
    kp.add_entry(kp.root_group, "Added elsewhere", "x", "y")
    time.sleep(0.05)
    kp.save()
    assert wait_for(qapp, lambda: "Added elsewhere" in titles(vc), timeout=20)


def test_wrong_shared_password_keeps_personal_open(qapp, vc, tmp_path):
    main, _ = make_pair(tmp_path, password="wrong")
    failed = []
    vc.vault_failed.connect(lambda name, msg: failed.append((name, msg)))
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: failed)
    assert vc.is_unlocked and "Email" in titles(vc) and "Bank" not in titles(vc)
    assert vc.vault_infos()[0].error
    assert not any(g.name == "Shared" and g.vault for g in vc.snapshot.groups)


def test_removing_autoopen_entry_detaches(qapp, vc, tmp_path):
    main, _ = make_pair(tmp_path)
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: "Bank" in titles(vc))
    ao = next(e for e in vc.snapshot.entries if e.group == "AutoOpen")
    vc.apply([DeleteEntries([ao.uuid])])
    assert wait_for(qapp, lambda: "Bank" not in titles(vc))
    assert vc.vault_infos() == []


def test_pin_lock_reopens_shared(qapp, vc, tmp_path):
    from vaultline import pin as pinlib
    main, _ = make_pair(tmp_path)
    vc.config.pin_enabled = True
    vc.config.pin_salt, vc.config.pin_hash = pinlib.make("2580")
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: "Bank" in titles(vc))
    vc.lock()
    assert vc.is_pin_locked and vc.snapshot is None
    assert vc.pin_unlock("2580")
    assert wait_for(qapp, lambda: {"Bank", "Email"} <= titles(vc))


def test_add_vault_creates_and_attaches(qapp, vc, tmp_path):
    main = tmp_path / "Personal.kdbx"
    kp = create_database(str(main), password=PW)
    kp.add_entry(kp.root_group, "Email", "me", "x")
    kp.save()
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: "Email" in titles(vc))
    got = []
    vc.applied.connect(lambda t, ok, msg, rep: got.append((t, ok, msg)))
    target = tmp_path / "drive" / "Shared.kdbx"
    target.parent.mkdir()
    assert vc.add_vault(str(target), "Shared", token="new") is None
    assert wait_for(qapp, lambda: got and vc.vault_infos() and vc.vault_infos()[0].root_uuid)
    assert got[-1][1], got
    ao = PyKeePass(str(main), PW).find_entries(group=PyKeePass(str(main), PW).find_groups(name="AutoOpen", first=True),
                                               title="Shared", first=True)
    assert ao is not None and ao.url == str(target) and len(ao.password) == 40
    PyKeePass(str(target), ao.password)  # the saved password opens it
    # made here: managed from this database, so it isn't listed; entries are shared into it
    assert vc.vault_infos()[0].managed and not any(g.vault for g in vc.snapshot.groups)
    seen = []
    vc.entries_changed.connect(lambda snap, why: seen.append(snap))
    assert wait_for(qapp, lambda: vc.vault_infos()[0].vault_id)
    # the window is told (its Share with… needs the id), even though nothing was shared yet
    assert wait_for(qapp, lambda: any(v.vault_id for s in seen for v in s.vaults))
    email = next(e for e in vc.snapshot.entries if e.title == "Email")
    vc.share(email.uuid, [vc.vault_infos()[0].vault_id])

    def copied():  # read now and then: an open file can't be replaced (Windows), as with a sync client
        time.sleep(0.4)
        return PyKeePass(str(target), ao.password).find_entries(title="Email", first=True) is not None
    assert wait_for(qapp, copied, timeout=30)
    assert wait_for(qapp, lambda: next(e for e in vc.snapshot.entries if e.title == "Email").shares)

    # again: refused; an existing file with a wrong password: refused, nothing written
    assert vc.add_vault(str(target), "Again") is not None
    other = tmp_path / "Other.kdbx"
    create_database(str(other), password="right").save()
    assert vc.add_vault(str(other), "Other", "wrong", token="bad") is None
    assert wait_for(qapp, lambda: got[-1][0] == "bad")
    assert not got[-1][1] and "Wrong password" in got[-1][2]
    assert len(vc.vault_infos()) == 1


def test_window_banner_and_settings(qapp, tmp_path, monkeypatch):
    from vaultline import config as cfgmod
    from vaultline.clipboard import SecureClipboard
    from vaultline.ui.main_window import MainWindow
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ctlmod, "BACKUP_DIR", tmp_path / "backups")
    main, shared = make_pair(tmp_path, password="wrong")
    config = Config(last_database=str(main), auto_download_icons=False)
    vc = ctlmod.VaultController(config, qapp)
    win = MainWindow(config, vc, SecureClipboard(20, qapp))
    try:
        vc.unlock(str(main), PW, None)
        assert wait_for(qapp, lambda: vc.vault_infos() and vc.vault_infos()[0].error)
        assert not win.vault_view.vault_banner.isHidden()
        assert "Shared" in win.vault_view.vault_banner_text.text()
        win.open_settings()
        assert win.settings_page is not None
        win.close_settings()
    finally:
        dispose(vc, win)


def test_move_entries_and_folders_between_vaults(qapp, vc, tmp_path):
    main, shared = make_pair(tmp_path)
    kp = PyKeePass(str(main), PW)
    e = kp.find_entries(title="Email", first=True)
    e.otp = "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP"
    e.set_custom_property("PIN", "4321", protect=True)
    e.save_history()
    e.password = "mail-pass-2"
    e.add_attachment(kp.add_binary(b"made-up attachment"), "note.txt")
    kp.add_entry(kp.find_groups(name="Work", first=True), "Ref user", "{REF:U@I:" + e.uuid.hex.upper() + "}", "x")
    kp.save()
    got = []
    vc.applied.connect(lambda t, ok, msg, rep: got.append((t, ok, msg, rep)))
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: "Bank" in titles(vc))
    snap = vc.snapshot
    email = next(x for x in snap.entries if x.title == "Email")
    biz = next(g for g in snap.groups if g.name == "Business")

    # another entry refers to Email: refused, nothing changed
    vc.apply([MoveEntries([email.uuid], group_uuid=biz.uuid)], token="ref")
    assert wait_for(qapp, lambda: got and got[-1][0] == "ref")
    assert not got[-1][1] and "refers" in got[-1][2]
    assert not PyKeePass(str(shared), SPW).find_entries(title="Email")

    ref = next(x for x in vc.snapshot.entries if x.title == "Ref user")
    vc.apply([DeleteEntries([ref.uuid])], token="del")
    assert wait_for(qapp, lambda: got[-1][0] == "del")
    vc.apply([MoveEntries([email.uuid], group_uuid=biz.uuid)], token="mv")
    assert wait_for(qapp, lambda: got[-1][0] == "mv")
    assert got[-1][1], got[-1]
    skp = PyKeePass(str(shared), SPW)
    moved = skp.find_entries(title="Email", first=True)
    assert moved.group.name == "Business" and moved.uuid.hex != email.uuid
    assert moved.password == "mail-pass-2" and moved.otp and moved.get_custom_property("PIN") == "4321"
    assert len(moved.history) == 1 and moved.attachments[0].data == b"made-up attachment"
    mkp = PyKeePass(str(main), PW)
    assert mkp.find_entries(title="Email", first=True).group.uuid == mkp.recyclebin_group.uuid
    now = next(x for x in vc.snapshot.entries if x.title == "Email")
    assert now.group == "Shared / Business" and now.vault

    # a whole folder back to the personal vault's top level, via its path
    biz = next(g for g in vc.snapshot.groups if g.name == "Business")
    vc.apply([MoveFolder(biz.uuid, next(g for g in vc.snapshot.groups if g.name == "Work").uuid)], token="fold")
    assert wait_for(qapp, lambda: got[-1][0] == "fold")
    assert got[-1][1], got[-1]
    mkp = PyKeePass(str(main), PW)
    g = mkp.find_groups(name="Business", first=True)
    assert g.parentgroup.name == "Work" and {x.title for x in g.entries} == {"Bank", "Email"}
    assert PyKeePass(str(shared), SPW).find_groups(name="Business", first=True).parentgroup.name == "Recycle Bin"

    # the entry form moving an entry: fields saved, then moved
    sup = next(x for x in vc.snapshot.entries if x.title == "Supplier")
    vc.save(EntryEdit(sup.uuid, "Supplier", "orders@example.com", "sup-pass-2", "", "", None, new_group="Vendors"))
    assert wait_for(qapp, lambda: any(x.title == "Supplier" and not x.vault for x in vc.snapshot.entries))
    s2 = PyKeePass(str(main), PW).find_entries(title="Supplier", first=True)
    assert s2.password == "sup-pass-2" and s2.group.name == "Vendors"


def test_merge_duplicate_fills_otp_and_refuses_conflicts(tmp_path):
    from vaultline.vault import MergeDuplicate
    db = tmp_path / "S.kdbx"
    kp = create_database(str(db), password=SPW)
    a = kp.add_entry(kp.root_group, "Shop", "us@example.com", "same", url="https://shop.example", force_creation=True)
    b = kp.add_entry(kp.root_group, "Shop", "us@example.com", "same", tags=["mel"], force_creation=True,
                     otp="otpauth://totp/Shop?secret=JBSWY3DPEHPK3PXP")
    b.set_custom_property("Member no", "123")
    c = kp.add_entry(kp.root_group, "Shop", "us@example.com", "other", force_creation=True)
    d = kp.add_entry(kp.root_group, "Shop", "us@example.com", "same", force_creation=True)
    d.set_custom_property("Member no", "999")
    kp.save()
    v = Vault(str(db), SPW, None, tmp_path / "bk")
    v.open()
    v.save([MergeDuplicate(a.uuid.hex, b.uuid.hex)])
    assert v.last_report[0]["filled"] == ["Member no", "otp"]
    assert "same" not in repr(v.last_report) and "JBSW" not in repr(v.last_report)  # no values
    k = PyKeePass(str(db), SPW)
    kept = k.find_entries(uuid=a.uuid, first=True)
    assert kept.otp and kept.get_custom_property("Member no") == "123" and kept.tags == ["mel"]
    assert kept.url == "https://shop.example" and len(kept.history) == 1
    assert k.find_entries(uuid=b.uuid, first=True).group.uuid == k.recyclebin_group.uuid
    for other, why in ((c, "passwords differ"), (d, "Member no differ")):
        with pytest.raises(VaultError, match=why):
            v.save([MergeDuplicate(a.uuid.hex, other.uuid.hex)])


def test_folder_picker_shows_vaults_as_collapsible_tops(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from vaultline.ui.folder_picker import FolderPicker, _TreePopup
    m, ms, ss, key = merged_pair(tmp_path)
    top = multivault.top_id(key)
    biz = next(g for g in m.groups if g.name == "Business")
    work = next(g for g in m.groups if g.name == "Work")
    box = FolderPicker(m.groups, m.root_uuid, extras=[("New folder…", "__new__")])
    labels = [box.label(i) for i in range(box.count())]
    assert labels == ["Personal", "AutoOpen", "Work", "Shared", "Business", "New folder…"]
    assert box.itemText(box.findData(biz.uuid)) == "Business  ·  Shared"
    assert box.itemData(box.findData(biz.uuid), Qt.UserRole + 1) == box.findData(top)

    box.setCurrentIndex(box.findData(work.uuid))
    pop = _TreePopup(box)
    t = pop.tree
    tops = [t.topLevelItem(i) for i in range(t.topLevelItemCount())]
    assert [x.text(0) for x in tops] == ["Personal", "Shared", "New folder…"]
    assert tops[0].isExpanded() and not tops[1].isExpanded()  # the vault holding the current choice
    assert [tops[1].child(i).text(0) for i in range(tops[1].childCount())] == ["Business"]
    pop._pick(tops[1].child(0))
    assert box.currentData() == biz.uuid
    pop.deleteLater()

    one = FolderPicker(ms.groups, ms.root_uuid)  # one vault: as before
    assert [one.label(i) for i in range(one.count())] == ["Top level", "AutoOpen", "Work"]
    assert one.itemText(one.findData(work.uuid)) == "Work"

    no_biz = FolderPicker(m.groups, m.root_uuid, exclude={biz.uuid})
    assert no_biz.findData(biz.uuid) == -1 and no_biz.findData(top) >= 0


def test_move_to_menu_moves_entry_and_folder_between_vaults(qapp, tmp_path, monkeypatch):
    from vaultline import config as cfgmod
    from vaultline.clipboard import SecureClipboard
    from vaultline.ui import small_dialogs
    from vaultline.ui.main_window import MainWindow
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ctlmod, "BACKUP_DIR", tmp_path / "backups")
    main, shared = make_pair(tmp_path)
    config = Config(last_database=str(main), auto_download_icons=False)
    vc = ctlmod.VaultController(config, qapp)
    win = MainWindow(config, vc, SecureClipboard(20, qapp))
    try:
        vc.unlock(str(main), PW, None)
        assert wait_for(qapp, lambda: "Bank" in titles(vc))
        biz = next(g for g in vc.snapshot.groups if g.name == "Business")
        email = next(e for e in vc.snapshot.entries if e.title == "Email")
        asked = {}

        def pick(*a, **k):
            asked.update(k)
            return biz.uuid, False
        monkeypatch.setattr(small_dialogs, "ask_move", pick)
        win.move_entry(email)
        assert asked["current"] == email.group_uuid
        assert wait_for(qapp, lambda: any(e.title == "Email" and e.vault for e in vc.snapshot.entries))
        assert PyKeePass(str(shared), SPW).find_entries(title="Email", first=True).group.name == "Business"

        work = next(g for g in vc.snapshot.groups if g.name == "Work")
        monkeypatch.setattr(small_dialogs, "ask_move", lambda *a, **k: (asked.update(k), (vc.snapshot.root_uuid, False))[1])
        win.move_folder(biz.uuid)
        assert biz.uuid in asked["exclude"] and work.uuid not in asked["exclude"]
        assert wait_for(qapp, lambda: any(e.title == "Bank" and not e.vault for e in vc.snapshot.entries))
        g = PyKeePass(str(main), PW).find_groups(name="Business", first=True)
        assert g.parentgroup.is_root_group and {e.title for e in g.entries} == {"Bank", "Email"}
    finally:
        dispose(vc, win)


def test_copy_keeps_the_original(qapp, vc, tmp_path):
    from vaultline.vault import CopyEntries, CopyFolder
    main, shared = make_pair(tmp_path)
    got = []
    vc.applied.connect(lambda t, ok, msg, rep: got.append((t, ok, msg, rep)))
    vc.unlock(str(main), PW, None)
    assert wait_for(qapp, lambda: "Bank" in titles(vc))
    email = next(e for e in vc.snapshot.entries if e.title == "Email")
    top = multivault.top_id(vc.vault_infos()[0].key)
    vc.apply([CopyEntries([email.uuid], group_uuid=top)], token="c")
    assert wait_for(qapp, lambda: got and got[-1][0] == "c")
    assert got[-1][1] and got[-1][3][-1]["copied"] == 1, got[-1]
    both = [e for e in vc.snapshot.entries if e.title == "Email"]
    assert sorted(bool(e.vault) for e in both) == [False, True] and len({e.uuid for e in both}) == 2
    assert PyKeePass(str(main), PW).find_entries(title="Email", first=True).group.name == "Work"
    assert PyKeePass(str(shared), SPW).find_entries(title="Email", first=True).password == "mail-pass"

    biz = next(g for g in vc.snapshot.groups if g.name == "Business")
    vc.apply([CopyFolder(biz.uuid, vc.snapshot.root_uuid)], token="f")
    assert wait_for(qapp, lambda: got[-1][0] == "f")
    assert got[-1][1], got[-1]
    assert PyKeePass(str(main), PW).find_groups(name="Business", first=True).parentgroup.is_root_group
    assert PyKeePass(str(shared), SPW).find_groups(name="Business", first=True).parentgroup.is_root_group

    # by path from the command line's op, and within one vault
    vc.apply([CopyEntries([email.uuid], new_group="Shared/Clients")], token="p")
    assert wait_for(qapp, lambda: got[-1][0] == "p")
    assert PyKeePass(str(shared), SPW).find_groups(name="Clients", first=True).entries[0].title == "Email"
    vc.apply([CopyEntries([email.uuid], new_group="Work")], token="same")
    assert wait_for(qapp, lambda: got[-1][0] == "same")
    assert len(PyKeePass(str(main), PW).find_entries(title="Email")) == 2
