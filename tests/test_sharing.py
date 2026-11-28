"""Sharing from the main database to managed vaults (sharing.py), with real files and made-up data."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import dispose
from pykeepass import PyKeePass, create_database

from vaultline import sharing
from vaultline.sharing import KEY_LINKS, KEY_SHARE, SetData
from vaultline.vault import EntryEdit, Vault, create_vault

PW, APW, BPW = "main pw", "team a pw long", "team b pw long"


@pytest.fixture
def world(tmp_path: Path):
    main = tmp_path / "Main.kdbx"
    kp = create_database(str(main), password=PW)
    design = kp.add_group(kp.root_group, "Design")
    kp.add_entry(design, "Figma", "studio@example.com", "figma-1", url="https://figma.example")
    kp.add_entry(design, "Fonts", "studio@example.com", "fonts-1")
    bank = kp.add_group(kp.root_group, "Bank")
    kp.add_entry(bank, "Biz bank", "123", "bank-1")
    kp.save()
    a, b = tmp_path / "A.kdbx", tmp_path / "B.kdbx"
    create_vault(a, APW)
    create_vault(b, BPW)
    vm = Vault(str(main), PW, None, tmp_path / "bk")
    va = Vault(str(a), APW, None, tmp_path / "bk")
    vb = Vault(str(b), BPW, None, tmp_path / "bk")
    snap = vm.open()
    va.open()
    vb.open()
    ids = sharing.ensure_ids({"a": va, "b": vb})
    w = {"main": main, "a": a, "b": b, "vm": vm, "va": va, "vb": vb, "ida": ids["a"], "idb": ids["b"],
         "groups": {g.name: g.uuid for g in snap.groups}, "entries": {e.title: e.uuid for e in snap.entries}}
    w["managed"] = {ids["a"]: ("Sam", va), ids["b"]: ("Alex", vb)}
    return w


def run(w, decisions=None):
    return sharing.sync(w["vm"], w["managed"], decisions or {})


def titles(path, pw, in_bin=False):
    kp = PyKeePass(str(path), pw)
    rb = kp.recyclebin_group
    out = []
    for e in kp.entries:
        binned = rb is not None and any(g.uuid == rb.uuid for g in _ancestors(e.group))
        if binned == in_bin:
            out.append(e.title)
    return sorted(out)


def _ancestors(g):
    while g is not None:
        yield g
        g = g.parentgroup


def share_folder(w, name, *vids):
    w["vm"].save([SetData(w["groups"][name], KEY_SHARE, json.dumps(list(vids)), group=True)])


def share_entry(w, title, *vids):
    w["vm"].save([SetData(w["entries"][title], KEY_SHARE, json.dumps(list(vids)))])


def edit_outside(path, pw, title, **changes):
    time.sleep(0.02)
    kp = PyKeePass(str(path), pw)
    e = kp.find_entries(title=title, first=True)
    for k, v in changes.items():
        setattr(e, k, v)
    kp.save()



def test_share_create_push_and_no_changes(world):
    w = world
    share_folder(w, "Design", w["ida"], w["idb"])
    share_entry(w, "Biz bank", w["ida"])
    saved, conflicts, report = run(w)
    assert conflicts == [] and saved == {"", w["ida"], w["idb"]}
    assert titles(w["a"], APW) == ["Biz bank", "Figma", "Fonts"]
    assert titles(w["b"], BPW) == ["Figma", "Fonts"]
    kp = PyKeePass(str(w["a"]), APW)
    assert kp.find_entries(title="Figma", first=True).group.name == "Design"  # same folder path
    assert kp.find_entries(title="Figma", first=True).password == "figma-1"
    # nothing new: nothing written
    assert run(w)[0] == set()

    # an edit in the main database goes out to both
    w["vm"].save([EntryEdit(w["entries"]["Figma"], "Figma", "studio@example.com", "figma-2",
                            "https://figma.example", "", None)])
    saved, conflicts, _ = run(w)
    assert conflicts == [] and {w["ida"], w["idb"]} <= saved
    assert PyKeePass(str(w["b"]), BPW).find_entries(title="Figma", first=True).password == "figma-2"
    assert len(PyKeePass(str(w["b"]), BPW).find_entries(title="Figma", first=True).history) == 1


def test_their_edit_waits_then_theirs_or_mine(world):
    w = world
    share_folder(w, "Design", w["ida"], w["idb"])
    run(w)
    edit_outside(w["a"], APW, "Figma", password="reset-by-sam")
    saved, conflicts, _ = run(w)
    assert saved == set()  # nothing applied on its own
    (c,) = conflicts
    assert (c.kind, c.title, c.vault, c.fields) == ("changed", "Figma", w["ida"], ("password",))
    assert "reset-by-sam" not in repr(c)
    # Use theirs: into the main database, then out to the other vault
    saved, conflicts, report = run(w, {(c.main_uuid, c.vault): "theirs"})
    assert conflicts == []
    assert PyKeePass(str(w["main"]), PW).find_entries(title="Figma", first=True).password == "reset-by-sam"
    assert PyKeePass(str(w["b"]), BPW).find_entries(title="Figma", first=True).password == "reset-by-sam"
    # Keep mine: theirs is overwritten (kept in that copy's history)
    edit_outside(w["b"], BPW, "Fonts", username="someone-else")
    (c,) = run(w)[1]
    run(w, {(c.main_uuid, c.vault): "mine"})
    e = PyKeePass(str(w["b"]), BPW).find_entries(title="Fonts", first=True)
    assert e.username == "studio@example.com" and e.history[-1].username == "someone-else"
    assert run(w)[1] == []


def test_unshare_and_delete_send_copies_to_the_bin(world):
    w = world
    share_folder(w, "Design", w["ida"], w["idb"])
    run(w)
    share_folder(w, "Design", w["ida"])  # untick B
    run(w)
    assert titles(w["b"], BPW) == [] and titles(w["b"], BPW, in_bin=True) == ["Figma", "Fonts"]
    from vaultline.vault import DeleteEntries
    w["vm"].save([DeleteEntries([w["entries"]["Fonts"]])])
    run(w)
    assert titles(w["a"], APW) == ["Figma"] and titles(w["a"], APW, in_bin=True) == ["Fonts"]


def test_deleted_there_put_back_or_stop(world):
    w = world
    share_folder(w, "Design", w["ida"])
    run(w)
    kp = PyKeePass(str(w["a"]), APW)
    kp.trash_entry(kp.find_entries(title="Figma", first=True))
    kp.trash_entry(kp.find_entries(title="Fonts", first=True))
    time.sleep(0.02)
    kp.save()
    conflicts = run(w)[1]
    assert sorted((c.kind, c.title) for c in conflicts) == [("deleted", "Figma"), ("deleted", "Fonts")]
    figma = next(c for c in conflicts if c.title == "Figma")
    fonts = next(c for c in conflicts if c.title == "Fonts")
    _s, left, _r = run(w, {(figma.main_uuid, w["ida"]): "restore", (fonts.main_uuid, w["ida"]): "unshare"})
    assert left == []
    assert titles(w["a"], APW) == ["Figma"]
    data = Vault(str(w["main"]), PW, None, w["main"].parent / "bk").open()
    fonts_view = next(e for e in data.entries if e.title == "Fonts")
    assert w["ida"] in json.loads(fonts_view.data[sharing.KEY_NOT])
    assert run(w)[1] == []  # stays unshared despite the folder


def test_their_new_entries_come_into_main(world):
    w = world
    share_folder(w, "Design", w["ida"])
    run(w)
    time.sleep(0.02)
    kp = PyKeePass(str(w["a"]), APW)
    kp.add_entry(kp.root_group, "Sam's portal", "sam@example.com", "portal-1")
    kp.save()
    saved, conflicts, report = run(w)
    assert conflicts == [] and "" in saved
    snap = Vault(str(w["main"]), PW, None, w["main"].parent / "bk").open()
    new = next(e for e in snap.entries if e.title == "Sam's portal")
    assert new.group == "Sam" and new.password == "portal-1"
    links = json.loads(new.data[KEY_LINKS])
    assert list(links) == [w["ida"]]
    assert titles(w["a"], APW).count("Sam's portal") == 1  # not copied back again
    assert run(w)[0] == set()


def test_reference_entry_is_reported_not_crashing(world):
    w = world
    kp = PyKeePass(str(w["main"]), PW)
    figma = kp.find_entries(title="Figma", first=True)
    kp.add_entry(kp.find_groups(name="Design", first=True), "Ref", "{REF:U@I:" + figma.uuid.hex.upper() + "}", "x")
    time.sleep(0.02)
    kp.save()
    share_folder(w, "Design", w["ida"])
    saved, conflicts, _ = run(w)
    assert any(c.kind == "error" for c in conflicts)


def test_window_share_review_and_filter(qapp, tmp_path, monkeypatch):
    from vaultline import config as cfgmod, controller as ctlmod
    from vaultline.clipboard import SecureClipboard
    from vaultline.config import Config
    from vaultline.ui import small_dialogs
    from vaultline.ui.main_window import MainWindow
    from vaultline.vault import EntryEdit as _E  # noqa: F401
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ctlmod, "BACKUP_DIR", tmp_path / "backups")
    main = tmp_path / "Main.kdbx"
    kp = create_database(str(main), password=PW)
    design = kp.add_group(kp.root_group, "Design")
    kp.add_entry(design, "Figma", "studio@example.com", "figma-1")
    kp.add_entry(kp.root_group, "Biz bank", "123", "bank-1")
    kp.save()
    config = Config(last_database=str(main), auto_download_icons=False)
    vc = ctlmod.VaultController(config, qapp)
    win = MainWindow(config, vc, SecureClipboard(20, qapp))

    def wait(cond, t=20):
        end = time.monotonic() + t
        while time.monotonic() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False
    try:
        vc.unlock(str(main), PW, None)
        assert wait(lambda: vc.snapshot is not None)
        target = tmp_path / "drive" / "Sam.kdbx"
        target.parent.mkdir()
        assert vc.add_vault(str(target), "Sam") is None
        assert wait(lambda: vc.managed_vaults() and vc.managed_vaults()[0].vault_id)
        vid = vc.managed_vaults()[0].vault_id
        pw = PyKeePass(str(main), PW).find_entries(title="Sam", first=True).password

        # share the folder and one entry through the dialogs
        monkeypatch.setattr(small_dialogs, "ask_share", lambda *a, **k: {vid})
        win.share_folder(next(g.uuid for g in vc.snapshot.groups if g.name == "Design"))
        assert wait(lambda: next(e for e in vc.snapshot.entries if e.title == "Figma").shares == (vid,))
        win.share_entry(next(e for e in vc.snapshot.entries if e.title == "Biz bank"))
        assert wait(lambda: time.sleep(0.4) or len(PyKeePass(str(target), pw).entries) == 2, 30)
        assert win.vault_view._share_groups[0].name == "Shared with Sam"
        win.vault_view.set_folder(win.vault_view._share_groups[0])
        assert sorted(v.title for v in win.vault_view._views if win.vault_view._in_folder(v)) == ["Biz bank", "Figma"]

        # Sam changes a password there: a banner, then Use theirs
        time.sleep(0.05)
        kp2 = PyKeePass(str(target), pw)
        kp2.find_entries(title="Figma", first=True).password = "sam-reset"
        kp2.save()
        assert wait(lambda: vc.conflicts, 30)
        assert not win.vault_view.share_banner.isHidden()
        c = vc.conflicts[0]
        vc.resolve(c.main_uuid, c.vault, "theirs")
        assert wait(lambda: next(e for e in vc.snapshot.entries if e.title == "Figma").password == "sam-reset", 30)
        assert win.vault_view.share_banner.isHidden()
    finally:
        dispose(vc, win)


def test_cli_share_op(qapp, tmp_path, monkeypatch):
    from vaultline import controller as ctlmod
    from vaultline.cli_server import CliError, CliServer
    from vaultline.config import Config
    monkeypatch.setattr(ctlmod, "BACKUP_DIR", tmp_path / "backups")
    main = tmp_path / "Main.kdbx"
    kp = create_database(str(main), password=PW)
    kp.add_entry(kp.root_group, "Figma", "studio@example.com", "figma-1")
    kp.save()
    vc = ctlmod.VaultController(Config(auto_download_icons=False, cli_enabled=True))
    server = CliServer(vc)

    def wait(cond, t=20):
        end = time.monotonic() + t
        while time.monotonic() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.05)
        return False
    try:
        vc.unlock(str(main), PW, None)
        assert wait(lambda: vc.snapshot is not None)
        target = tmp_path / "Jose.kdbx"
        vc.add_vault(str(target), "Jose")
        assert wait(lambda: vc.managed_vaults() and vc.managed_vaults()[0].vault_id)
        figma = next(e for e in vc.snapshot.entries if e.title == "Figma")
        with pytest.raises(CliError, match="Not a vault managed"):
            server._change(vc.snapshot, {"op": "share", "ids": [figma.uuid[:8]], "with": ["Nobody"]})
        changes, summary = server._change(vc.snapshot, {"op": "share", "ids": [figma.uuid[:8]], "with": ["jose"]})
        assert summary == "shared 1 with jose"
        vc.apply(changes)
        pw = PyKeePass(str(main), PW).find_entries(title="Jose", first=True).password
        assert wait(lambda: time.sleep(0.4) or bool(PyKeePass(str(target), pw).find_entries(title="Figma")), 30)
    finally:
        server.server.close()
        dispose(vc)
