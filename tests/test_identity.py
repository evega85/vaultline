"""Sign-up profiles (identity_* fields) and typing profile and card fields in a choice of formats.
Made-up data only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pykeepass import PyKeePass, create_database

from vaultline import identity as idlib
from vaultline.autotype import AutoTypeInfo, find_matches
from vaultline.cli_server import entry_meta
from vaultline.vault import CustomField, EntryEdit, Vault

from test_autotype import fake_os  # noqa: F401  (fixture)
from test_gui import PW, app, setup, unlock, wait_for  # noqa: F401  (fixtures)

PROFILE = {idlib.FIRST: "Alex", idlib.LAST: "Tester", idlib.EMAIL: "alex@example.com",
           idlib.PHONE: "(416) 555-0123", idlib.BIRTH: "1990-01-05", idlib.ADDRESS: "1 Example St",
           idlib.CITY: "Toronto", idlib.STATE: "Ontario", idlib.POSTAL: "m5v2t6", idlib.COUNTRY: "Canada"}


def profile_custom(values=PROFILE):
    return [CustomField(k, v, k in idlib.PROTECTED) for k, v in values.items()]


@dataclass(eq=False)
class V:
    """Enough of an EntryView for identity.py."""
    custom: tuple = ()
    card_number: str = ""
    card_code: str = ""
    card_holder: str = ""
    card_expiry: tuple | None = None

    def get_field(self, key):
        return next((f.value for f in self.custom if f.key == key), "")


def opts(view, field):
    return {label: idlib.fill_value(view, tok) for label, tok in idlib.fill_options(view, field)}


# ---- formats ---------------------------------------------------------------------------------
def test_birth_date_formats():
    v = V(tuple(profile_custom()))
    got = opts(v, idlib.BIRTH)
    assert got == {"YYYY-MM-DD": "1990-01-05", "MM/DD/YYYY": "01/05/1990", "DD/MM/YYYY": "05/01/1990",
                   "Day (DD)": "05", "Day without a zero (D)": "5", "Month (MM)": "01",
                   "Month without a zero (M)": "1", "Month name": "January", "Year (YYYY)": "1990"}
    assert not any("1990" in label for label in got)  # menu labels never show the hidden date


@pytest.mark.parametrize("text,want", [("1990-01-31", date(1990, 1, 31)), ("1990/1/5", date(1990, 1, 5)),
                                       ("01/31/1990", None), ("1990-02-30", None), ("1800-01-01", None),
                                       ("2999-01-01", None), ("", None)])
def test_birth_date_parsing_refuses_ambiguous(text, want):
    assert idlib.parse_birth_date(text) == want


def test_phone_formats():
    assert [v for _t, v in idlib.phone_formats("(416) 555-0123")] == [
        "(416) 555-0123", "4165550123", "416-555-0123", "+1 416-555-0123", "+14165550123"]
    assert [v for _t, v in idlib.phone_formats("+1 416 555 0123")][1:] == [
        "4165550123", "416-555-0123", "+1 416-555-0123", "+14165550123"]
    # not North American: as saved and digits only (the + kept)
    assert [v for _t, v in idlib.phone_formats("+44 20 7946 0000")] == ["+44 20 7946 0000", "+442079460000"]
    assert idlib.phone_formats("") == []


def test_region_country_postal():
    assert [v for _t, v in idlib.region_formats("Ontario")] == ["ON", "Ontario"]
    assert [v for _t, v in idlib.region_formats("qc")] == ["QC", "Quebec"]
    assert [v for _t, v in idlib.region_formats("Bavaria")] == ["Bavaria"]
    assert [v for _t, v in idlib.country_formats("canada")] == ["CA", "Canada"]
    assert [v for _t, v in idlib.postal_formats("m5v2t6")] == ["M5V 2T6", "M5V2T6"]
    assert [v for _t, v in idlib.postal_formats("90210")] == ["90210"]


def test_one_line_only_and_unknown_tokens():
    v = V((CustomField(idlib.ADDRESS, "1 Example St\nUnit 2\t", False),))
    assert idlib.fill_value(v, f"{idlib.ADDRESS}:as") == "1 Example St Unit 2"  # no Enter, no Tab
    assert idlib.fill_value(v, f"{idlib.CITY}:as") == ""
    assert idlib.fill_value(v, "password:as") == "" and idlib.fill_value(v, "nonsense") == ""
    assert idlib.fill_part(f"{idlib.BIRTH}:mdy") == "date of birth" and idlib.fill_part("card_expiry:MM") == "card expiry"


def test_card_options():
    v = V(card_number="5500 0000 0000 0004", card_code="321", card_holder="A Tester", card_expiry=("03", "28"))
    assert opts(v, "card_number") == {"Card number": "5500000000000004"}
    assert opts(v, "card_code") == {"Card code": "321"}
    assert opts(v, "card_expiry") == {"MM/YY": "03/28", "MMYY (no slash)": "0328", "MM/YYYY": "03/2028",
                                      "Month (MM)": "03", "Year (YY)": "28", "Year (YYYY)": "2028"}
    assert idlib.fill_options(V(), "card_expiry") == []


def test_split_imported_name_and_address():
    """KeePassXC's Bitwarden import joins these (layout from its BitwardenReader)."""
    assert idlib.split_name("Mr Alex Q Tester") == ("Alex Q", "Tester")
    assert idlib.split_name("Alex") == ("Alex", "")
    got = idlib.split_address("1 Example St\nUnit 2\nToronto, ON M5V 2T6\nCanada")
    assert got == {idlib.ADDRESS: "1 Example St, Unit 2", idlib.CITY: "Toronto", idlib.STATE: "ON",
                   idlib.POSTAL: "M5V 2T6", idlib.COUNTRY: "Canada"}
    got = idlib.split_address("9 Sample Rd\nSpringfield, IL 62704-1234\nUnited States")
    assert got[idlib.STATE] == "IL" and got[idlib.POSTAL] == "62704-1234"


# ---- stored in the kdbx --------------------------------------------------------------------------
@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    db = tmp_path / "v.kdbx"
    create_database(str(db), password=PW).save()
    v = Vault(str(db), PW, None, tmp_path / "bk")
    v.open()
    return v


def test_profile_round_trip_and_privacy(vault):
    snap, _ = vault.save_entry(EntryEdit(None, "Personal", "", "", "", "", None, custom=profile_custom()))
    p = next(e for e in snap.entries if e.title == "Personal")
    assert p.is_identity and not p.is_card
    # plain custom fields for KeePassXC / KeePassium; only the date of birth is protected
    kp = PyKeePass(str(vault.path), password=PW)
    e = kp.find_entries(title="Personal", first=True)
    assert e.get_custom_property(idlib.FIRST) == "Alex" and e.get_custom_property(idlib.BIRTH) == "1990-01-05"
    assert e.is_custom_property_protected(idlib.BIRTH) and not e.is_custom_property_protected(idlib.PHONE)
    # the command line sees field names, never values
    meta = str(entry_meta(p))
    assert entry_meta(p)["type"] == "identity"
    for value in PROFILE.values():
        assert value not in meta
    # never offered for Auto-Type by title (it would type just Tab and Enter)
    assert find_matches(snap.entries, "Personal details - Example") == []


def test_profile_form_saves_layout_and_splits_imports(vault, app):
    from vaultline.ui.entry_dialog import EntryDialog
    kp = PyKeePass(str(vault.path), password=PW)
    e = kp.add_entry(kp.root_group, "Imported", "", "")
    e._set_string_field(idlib.IMPORTED_NAME, "Ms Alex Tester", False)
    e._set_string_field(idlib.IMPORTED_ADDRESS, "1 Example St\nToronto, ON M5V 2T6\nCanada", False)
    e._set_string_field("identity_ssn", "000-00-0000", True)
    kp.save()
    snap = vault.reload_if_changed()
    view = next(v for v in snap.entries if v.title == "Imported")
    assert view.is_identity
    dlg = EntryDialog(None, view, groups=snap.groups, root_uuid=snap.root_uuid)
    assert dlg._identity and dlg.id_hint.isVisibleTo(dlg)
    assert dlg.id_boxes[idlib.FIRST].text() == "Alex" and dlg.id_boxes[idlib.POSTAL].text() == "M5V 2T6"
    assert [r.key.text() for r in dlg._rows] == ["identity_ssn"]  # other identity fields kept as they are
    dlg.id_boxes[idlib.BIRTH].setText("01/05/1990")
    dlg._accept()
    assert dlg.result_edit is None and "YYYY-MM-DD" in dlg.error.text()
    dlg.id_boxes[idlib.BIRTH].setText("1990/1/5")
    dlg._accept()
    edit = dlg.result_edit
    keys = {f.key: f for f in edit.custom}
    assert keys[idlib.BIRTH].value == "1990-01-05" and keys[idlib.BIRTH].protected
    assert idlib.IMPORTED_NAME not in keys and idlib.IMPORTED_ADDRESS not in keys
    assert keys["identity_ssn"].protected and keys[idlib.LAST].value == "Tester"
    snap, _ = vault.save_entry(edit)
    again = next(v for v in snap.entries if v.title == "Imported")
    # opened again and saved untouched: nothing to write
    dlg = EntryDialog(None, again, groups=snap.groups, root_uuid=snap.root_uuid)
    assert not dlg.id_hint.isVisibleTo(dlg)
    dlg._accept()
    assert dlg.result_edit is None and dlg.result() == 0


def test_new_profile_from_the_identity_type(vault, app):
    from vaultline.ui.entry_dialog import EntryDialog
    snap = vault.snapshot()
    dlg = EntryDialog(None, None, groups=snap.groups, root_uuid=snap.root_uuid)
    dlg._apply_template("Identity")
    assert dlg._identity and not dlg.sec_password.isVisibleTo(dlg) and not dlg.sec_username.isVisibleTo(dlg)
    dlg.title.setText("Business")
    dlg._accept()
    assert dlg.result_edit is None and "at least one" in dlg.error.text()
    dlg.id_boxes[idlib.EMAIL].setText("biz@example.com")
    dlg.add_custom_row(idlib.EMAIL, "x")
    dlg._accept()
    assert dlg.result_edit is None and "reserved" in dlg.error.text()
    dlg.remove_custom_row(dlg._rows[-1])
    dlg._accept()
    assert [f.key for f in dlg.result_edit.custom] == [idlib.EMAIL]


# ---- the app: panel icons and typing ---------------------------------------------------------------
def _add_profile_and_card(vc, app):
    from vaultline.ui.entry_dialog import card_fields
    vc.save(EntryEdit(None, "Personal", "", "", "", "", None, custom=profile_custom()))
    vc.save(EntryEdit(None, "Visa", "", "", "", "", None, card=True,
                            custom=card_fields("4111 1111 1111 1111", "321", ("03", "28"), True, "Alex Tester", "Visa")))
    assert wait_for(app, lambda: {"Personal", "Visa"} <= {v.title for v in vc.snapshot.entries})


def test_panel_types_profile_and_card_fields(setup, fake_os, monkeypatch):
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    _add_profile_and_card(vc, app)
    flow = win.autotype
    monkeypatch.setattr(flow.watcher, "target", lambda: 500)
    view = win.vault_view
    panel = view.panel
    personal = next(v for v in vc.snapshot.entries if v.title == "Personal")
    panel.show_entry(personal, None)
    names = [b.property("fill_name") for b in panel._field_fills]
    assert names == ["first name", "last name", "email", "phone", "date of birth", "street address",
                     "city", "province / state", "postal / zip code", "country", "full name"]
    assert panel.fill_btn.isHidden()  # no whole-login Fill for a profile

    def typed_for(token):
        fake_os.typed.clear()
        panel.fill_field_requested.emit(panel.view, token)
        assert wait_for(app, lambda: fake_os.typed and not flow.busy)
        return fake_os.typed[0]

    assert typed_for(f"{idlib.BIRTH}:mdy") == (500, [("text", "01/05/1990")])
    assert typed_for(f"{idlib.PHONE}:dashes") == (500, [("text", "416-555-0123")])
    assert typed_for(f"{idlib.EMAIL}:as") == (500, [("text", "alex@example.com")])
    fake_os.box = "password"  # one field is typed as asked, whatever the box
    assert typed_for(f"{idlib.STATE}:code") == (500, [("text", "ON")])
    # whole-entry Fill on a profile types nothing
    fake_os.typed.clear()
    flow.fill(personal)
    wait_for(app, lambda: False, 0.2)
    assert fake_os.typed == []

    card = next(v for v in vc.snapshot.entries if v.title == "Visa")
    panel.show_entry(card, None)
    assert [b.property("fill_name") for b in panel._field_fills] == ["card number", "expiry", "card code",
                                                                    "cardholder"]
    fake_os.typed.clear()
    panel._field_fills[0].click()  # one format: types straight away
    assert wait_for(app, lambda: fake_os.typed and not flow.busy)
    assert fake_os.typed[0] == (500, [("text", "4111111111111111")])
    assert typed_for("card_expiry:MMYY") == (500, [("text", "0328")])
    assert typed_for("card_code:as") == (500, [("text", "321")])


def test_agents_cant_read_profiles(setup):
    """The `secret` gate refuses profiles like cards, before any prompt."""
    from vaultline import agent_access
    app, win, vc, *_ = setup
    unlock(app, win, vc)
    work = next(g for g in vc.snapshot.groups if g.name == "Work")
    vc.save(EntryEdit(None, "Shared profile", "someone", "pw", "", "", None, group_uuid=work.uuid,
                            custom=profile_custom()))
    assert wait_for(app, lambda: any(v.title == "Shared profile" for v in vc.snapshot.entries))
    win.config.cli_enabled = True
    win.config.agent_share_enabled, win.config.agent_folder_uuid = True, work.uuid
    gate = agent_access.AgentGate(vc, win.config, ask=lambda *a: pytest.fail("prompted"), parent=app)
    view = next(v for v in vc.snapshot.entries if v.title == "Shared profile")
    with pytest.raises(agent_access.AccessRefused, match="profiles"):
        gate._check(view, "password", view.title, "test")


# ---- `vaultline-cli profile add` (approved in Vaultline) ---------------------------------------------
@pytest.fixture
def cli_profile(setup, tmp_path, monkeypatch):
    import contextlib
    import io
    import json
    import threading
    from PySide6.QtCore import QTimer
    from vaultline import agent_access, cli
    from vaultline.cli_server import CliServer
    app, win, vc, *_ = setup
    monkeypatch.setattr(agent_access, "ACCESS_LOG", tmp_path / "access.log")
    asked, answers, closed = [], [], []

    def ask(request, decide):
        asked.append(request)
        answer = answers.pop(0) if answers else None
        if answer:
            QTimer.singleShot(0, lambda: decide(answer))

        class Prompt:
            def close(self):
                closed.append(1)
        return Prompt()
    server = CliServer(vc, app, ask_profile=ask)
    win.config.cli_enabled = True
    unlock(app, win, vc)

    def run(data):
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        out = {}

        def target():
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                out["code"] = cli.main(["profile", "add", str(path)])
            out["out"], out["err"] = buf.getvalue(), err.getvalue()
        t = threading.Thread(target=target)
        t.start()
        wait_for(app, lambda: not t.is_alive(), 60)
        path.unlink()
        return out
    yield app, vc, run, asked, answers, closed, tmp_path / "access.log"
    server.server.close()


PROFILE_JSON = {"title": "Personal", "fields": {"firstName": "Alex", "lastName": "Tester", "email": "alex@example.com",
                                                 "birthDate": "1990-01-05", "gender": "male", "city": "Toronto"}}


def test_cli_profile_add_after_approval(cli_profile):
    app, vc, run, asked, answers, closed, log = cli_profile
    answers.append("add")
    r = run(PROFILE_JSON)
    assert r["code"] == 0 and "Profile added" in r["out"]
    req = asked[0]
    assert req["title"] == "Personal" and req["fields"][idlib.BIRTH] == "1990-01-05"  # every value shown
    assert list(req["fields"]) == [idlib.FIRST, idlib.LAST, idlib.EMAIL, idlib.BIRTH, idlib.GENDER, idlib.CITY]
    assert wait_for(app, lambda: any(v.title == "Personal" for v in vc.snapshot.entries))
    p = next(v for v in vc.snapshot.entries if v.title == "Personal")
    assert p.is_identity and p.get_field(idlib.GENDER) == "male"
    assert next(f for f in p.custom if f.key == idlib.BIRTH).protected
    assert opts(p, idlib.GENDER) == {"Male": "Male", "M": "M"}
    text = log.read_text(encoding="utf-8")
    assert "profile add: approved" in text and "alex@example.com" not in text and "1990" not in text


def test_cli_profile_add_cancel_and_refusals(cli_profile):
    app, vc, run, asked, answers, closed, log = cli_profile
    n = len(vc.snapshot.entries)
    answers.append("cancel")
    r = run(PROFILE_JSON)
    assert r["code"] == 1 and "Cancelled" in r["err"]
    for bad, why in (({"title": "X", "fields": {"password": "x"}}, "Unknown profile field"),
                     ({"title": "X", "fields": {"email": "{REF:P@I:0}"}}, "references"),
                     ({"title": "X", "fields": {"birthDate": "01/05/1990"}}, "YYYY-MM-DD"),
                     ({"title": "X", "fields": {"city": "a\nb"}}, "one line"),
                     ({"title": "", "fields": {"city": "a"}}, "title")):
        r = run(bad)
        assert r["code"] == 1 and why in r["err"], (bad, r)
    assert len(asked) == 1  # none of those reached a prompt
    vc.lock()  # a prompt still open when Vaultline locks: closed, nothing added
    app.processEvents()
    assert len(vc.snapshot.entries if vc.snapshot else []) in (0, n)


def test_cli_profile_prompt_closed_by_lock(cli_profile):
    import contextlib
    import io
    import json
    import threading
    from vaultline import cli
    app, vc, run, asked, answers, closed, log = cli_profile
    path = log.parent / "p2.json"
    path.write_text(json.dumps(PROFILE_JSON), encoding="utf-8")
    out = {}

    def target():
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            out["code"] = cli.main(["profile", "add", str(path)])
        out["err"] = err.getvalue()
    t = threading.Thread(target=target)
    t.start()
    assert wait_for(app, lambda: asked)  # the prompt is up; nobody answers
    vc.lock()
    assert wait_for(app, lambda: not t.is_alive(), 30)
    assert out["code"] == 1 and "locked" in out["err"] and closed
    assert vc.snapshot is None or not any(v.title == "Personal" for v in vc.snapshot.entries)


@pytest.mark.parametrize("label,field", [
    ("First name", "first"), ("Last name", "last"), ("Mobile number or email", "email"), ("Email address", "email"),
    ("Phone number", "phone"), ("Postal code", "postal"), ("ZIP", "postal"), ("City", "city"),
    ("State / Province", "state"), ("Country", "country"), ("Street address", "address"), ("Name", "full"),
    ("Username", "username"), ("Prénom", "first"), ("Courriel", "email"),
    ("Password", ""), ("Company", ""), ("Month", ""), ("Select your gender", ""), ("", "")])
def test_field_for_label(label, field):
    assert idlib.field_for_label(label) == field


@pytest.mark.parametrize("label,field", [("Month", "month"), ("Day", "day"), ("Year", "year"), ("Birthday", ""),
                                         ("Select your gender", "gender"), ("Province", "state_name"),
                                         ("Country", "country_name"), ("Title", "")])
def test_field_for_dropdown_label(label, field):
    assert idlib.field_for_label(label, choice=True) == field


def test_form_values():
    v = V(tuple(profile_custom()) + (CustomField(idlib.GENDER, "male", False),))
    got = idlib.form_values(v, "new@example.com")
    assert got["email"] == got["username"] == "new@example.com" and got["full"] == "Alex Tester"
    assert set(got) <= set(idlib.FORM_FIELD_NAMES)
    # the birth date only as drop-down picks; names for province and country drop-downs
    assert (got["month"], got["day"], got["year"]) == ("Jan", "5", "1990") and got["gender"] == "Male"
    assert got["state_name"] == "Ontario" and got["country_name"] == "Canada"
    assert not any("1990-01" in x or "01/05" in x for x in got.values())
    assert idlib.form_values(None, "u") == {"email": "u", "username": "u"}
