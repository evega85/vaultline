"""Identity profiles (for sign-up forms) and the typing formats for profile and card fields.

A profile is an ordinary entry with `identity_*` custom fields, so KeePassXC and KeePassium show it
as plain fields. The prefix and the key spellings follow KeePassXC's Bitwarden importer and
Bitwarden's own identity names (firstName, address1, postalCode...). The importer itself joins the
name and the address into one field each (`identity_name`, `identity_address`); those are still
read, and the profile form offers them split up, because a sign-up form wants each part on its own.

Fill: a keyboard icon sends a *token* such as "identity_birthDate:mdy" and the typing code looks
the value up with `fill_value`, so values never travel through the UI's signals. Nothing here
logs or raises with a value in it. No Qt, no Windows.
"""
from __future__ import annotations

import re
from datetime import date

PREFIX = "identity_"

FIRST = "identity_firstName"
LAST = "identity_lastName"
EMAIL = "identity_email"
PHONE = "identity_phone"
BIRTH = "identity_birthDate"     # stored as YYYY-MM-DD; Bitwarden has no birth date, KeePassXC shows it as-is
ADDRESS = "identity_address1"
CITY = "identity_city"
STATE = "identity_state"
POSTAL = "identity_postalCode"
COUNTRY = "identity_country"
GENDER = "identity_gender"       # not in Bitwarden; sign-up forms ask for it
KEYS = (FIRST, LAST, EMAIL, PHONE, BIRTH, GENDER, ADDRESS, CITY, STATE, POSTAL, COUNTRY)
PROTECTED = frozenset({BIRTH})   # hidden in the file and on screen, like a password

# Written by KeePassXC's Bitwarden import: read, and split up when saved from the profile form.
IMPORTED_NAME = "identity_name"
IMPORTED_ADDRESS = "identity_address"

LABELS = {FIRST: "First name", LAST: "Last name", EMAIL: "Email", PHONE: "Phone", BIRTH: "Date of birth",
          GENDER: "Gender",
          ADDRESS: "Street address", CITY: "City", STATE: "Province / state", POSTAL: "Postal / ZIP code",
          COUNTRY: "Country"}


def is_identity_key(key: str) -> bool:
    return key.startswith(PREFIX)


# ---- dates ---------------------------------------------------------------------------------
def parse_birth_date(text: str) -> date | None:
    """Only YYYY-MM-DD (or with / or .): 01/02/1990 could be either order, so it's refused."""
    m = re.fullmatch(r"\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*", text or "")
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return d if date(1900, 1, 1) <= d <= date.today() else None


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")

DATE_FORMATS = (  # token, menu label (patterns only: the date of birth is hidden)
    ("iso", "YYYY-MM-DD"),
    ("mdy", "MM/DD/YYYY"),
    ("dmy", "DD/MM/YYYY"),
    ("day", "Day (DD)"),
    ("day1", "Day without a zero (D)"),
    ("month", "Month (MM)"),
    ("month1", "Month without a zero (M)"),
    ("monthname", "Month name"),
    ("year", "Year (YYYY)"),
)


def format_date(d: date, fmt: str) -> str:
    return {
        "iso": f"{d.year:04d}-{d.month:02d}-{d.day:02d}",
        "mdy": f"{d.month:02d}/{d.day:02d}/{d.year:04d}",
        "dmy": f"{d.day:02d}/{d.month:02d}/{d.year:04d}",
        "day": f"{d.day:02d}",
        "day1": str(d.day),
        "month": f"{d.month:02d}",
        "month1": str(d.month),
        "monthname": _MONTHS[d.month - 1],
        "year": f"{d.year:04d}",
    }.get(fmt, "")


# ---- phones --------------------------------------------------------------------------------
def _nanp(phone: str) -> str:
    """The 10 digits of a North American number (with or without the leading 1), else ""."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits if len(digits) == 10 and digits[0] not in "01" else ""


def phone_formats(phone: str) -> list[tuple[str, str]]:
    """(token, value) for each way a phone number can be typed; the first is as saved."""
    phone = (phone or "").strip()
    if not phone:
        return []
    out = [("as", phone)]
    n = _nanp(phone)
    if n:
        out += [("digits", n), ("dashes", f"{n[:3]}-{n[3:6]}-{n[6:]}"),
                ("intl", f"+1 {n[:3]}-{n[3:6]}-{n[6:]}"), ("e164", f"+1{n}")]
    else:
        digits = re.sub(r"\D", "", phone)
        out.append(("digits", ("+" if phone.startswith("+") else "") + digits))
    seen, unique = set(), []
    for tok, val in out:
        if val not in seen:
            seen.add(val)
            unique.append((tok, val))
    return unique


# ---- provinces, states, countries, postal codes --------------------------------------------
_REGIONS = {
    # Canada
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
    # United States
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_REGION_BY_NAME = {v.casefold(): k for k, v in _REGIONS.items()} | {"québec": "QC"}
_COUNTRIES = {"CA": "Canada", "US": "United States"}
_COUNTRY_BY_NAME = {"canada": "CA", "united states": "US", "united states of america": "US", "usa": "US",
                    "u.s.a.": "US", "u.s.": "US"}


def _code_and_name(value: str, by_code: dict, by_name: dict) -> list[tuple[str, str]]:
    value = (value or "").strip()
    if not value:
        return []
    code = value.upper() if value.upper() in by_code else by_name.get(value.casefold(), "")
    if not code:
        return [("as", value)]
    return [("code", code), ("name", by_code[code])]


def region_formats(value: str) -> list[tuple[str, str]]:
    return _code_and_name(value, _REGIONS, _REGION_BY_NAME)


def country_formats(value: str) -> list[tuple[str, str]]:
    return _code_and_name(value, _COUNTRIES, _COUNTRY_BY_NAME)


def postal_formats(value: str) -> list[tuple[str, str]]:
    value = (value or "").strip()
    if not value:
        return []
    m = re.fullmatch(r"([A-Za-z]\d[A-Za-z])\s*-?\s*(\d[A-Za-z]\d)", value)  # Canadian A1A 1A1
    if m:
        a, b = m.group(1).upper(), m.group(2).upper()
        return [("space", f"{a} {b}"), ("nospace", f"{a}{b}")]
    return [("as", value)]


def gender_formats(value: str) -> list[tuple[str, str]]:
    """As saved, plus the one-letter form for Male / Female (some forms use M / F)."""
    value = (value or "").strip()
    if not value:
        return []
    out = [("as", value)]
    letter = {"male": "M", "man": "M", "m": "M", "female": "F", "woman": "F", "f": "F"}.get(value.casefold())
    if letter:
        full = "Male" if letter == "M" else "Female"
        out = [("name", full), ("letter", letter)]
    return out


# ---- imported (joined) name and address ----------------------------------------------------
_HONORIFICS = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof"}


def split_name(full: str) -> tuple[str, str]:
    """KeePassXC's joined "title first middle last" -> (first and middle, last). A guess: shown in the
    profile form for checking, never saved without the user pressing Save."""
    words = (full or "").split()
    if words and words[0].casefold().rstrip(".") in _HONORIFICS:
        words = words[1:]
    if len(words) < 2:
        return (" ".join(words), "")
    return " ".join(words[:-1]), words[-1]


def split_address(text: str) -> dict[str, str]:
    """KeePassXC's joined address ("street lines\\ncity, state postal\\ncountry") -> profile keys."""
    lines = [ln.strip() for ln in (text or "").replace("\\n", "\n").splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    if len(lines) < 3:
        return {ADDRESS: ", ".join(ln for ln in lines if ln)} if any(lines) else {}
    country, place, street = lines[-1], lines[-2], [ln for ln in lines[:-2] if ln]
    out = {ADDRESS: ", ".join(street), COUNTRY: country}
    city, _, rest = place.partition(",")
    out[CITY] = city.strip()
    rest = rest.strip()
    m = re.fullmatch(r"(.*?)\s*([A-Za-z]\d[A-Za-z]\s*\d[A-Za-z]\d|\d{5}(?:-\d{4})?)?", rest)
    state, postal = (m.group(1) or "").strip(), (m.group(2) or "").strip()
    if not postal and " " in rest:  # some other postal code: take the last word
        state, postal = rest.rsplit(" ", 1)
    out[STATE], out[POSTAL] = state.strip(), postal
    return {k: v for k, v in out.items() if v}


# ---- a sign-up form's boxes, by their labels --------------------------------------------------
# First match wins, on the lower-cased label. "Mobile number or email" is an email box (checked first).
_LABEL_RULES = (
    ("email", re.compile(r"e-?mail|courriel")),
    ("username", re.compile(r"user\s*name|login\s*name|user\s*id|nom d'utilisateur")),
    ("first", re.compile(r"first\s*name|given\s*name|forename|pr[ée]nom")),
    ("last", re.compile(r"last\s*name|surname|family\s*name|nom de famille")),
    ("full", re.compile(r"full\s*name|your\s*name|^\s*name\s*\*?\s*$|^\s*nom\s*\*?\s*$")),
    ("phone", re.compile(r"phone|mobile|cell|t[ée]l[ée]phone")),
    ("postal", re.compile(r"postal|zip|post\s*code|code postal")),
    ("city", re.compile(r"\bcity\b|\btown\b|ville")),
    ("state", re.compile(r"province|\bstate\b|region")),
    ("country", re.compile(r"country|pays")),
    ("address", re.compile(r"address|street|adresse")),
)
# Drop-downs: typing picks the first option starting with what's typed (browsers' type-ahead).
_CHOICE_RULES = (
    ("month", re.compile(r"\bmonth\b|\bmois\b")),
    ("day", re.compile(r"\bday\b|\bjour\b")),
    ("year", re.compile(r"\byear\b|\bann[ée]e\b")),
    ("gender", re.compile(r"gender|\bsex\b|genre|sexe")),
    ("state_name", re.compile(r"province|\bstate\b|region")),
    ("country_name", re.compile(r"country|pays")),
)
FORM_FIELD_NAMES = {"month": "birth month", "day": "birth day", "year": "birth year", "gender": "gender",
                    "state_name": "province/state", "country_name": "country",
                    "email": "your email", "username": "the username", "first": "first name",
                    "last": "last name", "full": "full name", "phone": "phone", "postal": "postal code",
                    "city": "city", "state": "province/state", "country": "country", "address": "street address"}


def field_for_label(label: str, choice: bool = False) -> str:
    """Which profile value a text box (or with `choice`, a drop-down) asks for, from its label
    ("" if it isn't one we fill)."""
    low = (label or "").casefold()
    if not low.strip():
        return ""
    return next((name for name, rx in (_CHOICE_RULES if choice else _LABEL_RULES) if rx.search(low)), "")


def form_values(profile, username: str) -> dict[str, str]:
    """What a sign-up form may get from a profile, plus the new login's username for email/username
    boxes. The birth date only goes into drop-downs labelled Month / Day / Year."""
    get = (lambda k: profile.get_field(k).strip()) if profile is not None else (lambda k: "")
    first, last = get(FIRST), get(LAST)
    out = {"email": username, "username": username, "first": first, "last": last,
           "full": " ".join(p for p in (first, last) if p), "phone": get(PHONE), "postal": get(POSTAL),
           "city": get(CITY), "state": get(STATE), "country": get(COUNTRY), "address": get(ADDRESS),
           "gender": next((v for t, v in gender_formats(get(GENDER)) if t != "letter"), "")}
    d = parse_birth_date(get(BIRTH))
    if d:  # "Jul" picks both "Jul" and "July" options
        out.update(month=_MONTHS[d.month - 1][:3], day=str(d.day), year=str(d.year))
    region = region_formats(get(STATE))
    out["state_name"] = next((v for t, v in region if t == "name"), get(STATE))
    country = country_formats(get(COUNTRY))
    out["country_name"] = next((v for t, v in country if t == "name"), get(COUNTRY))
    return {k: re.sub(r"[\r\n\t]+", " ", v).strip() for k, v in out.items() if v and v.strip()}


# ---- fill options and values ---------------------------------------------------------------
def _date_of(view) -> date | None:
    return parse_birth_date(view.get_field(BIRTH))


def _expiry_values(view) -> list[tuple[str, str]]:
    exp = view.card_expiry
    if not exp:
        return []
    mm, yy = exp
    return [("MM/YY", f"{mm}/{yy}"), ("MMYY", f"{mm}{yy}"), ("MM/YYYY", f"{mm}/20{yy}"),
            ("MM", mm), ("YY", yy), ("YYYY", f"20{yy}")]


_EXPIRY_LABELS = {"MM/YY": "MM/YY", "MMYY": "MMYY (no slash)", "MM/YYYY": "MM/YYYY", "MM": "Month (MM)",
                  "YY": "Year (YY)", "YYYY": "Year (YYYY)"}


def _choices(view, field: str) -> list[tuple[str, str, str]]:
    """(format token, menu label, value) for one field. Labels of hidden fields carry no value."""
    if field == "name":
        first, last = view.get_field(FIRST).strip(), view.get_field(LAST).strip()
        full = " ".join(p for p in (first, last) if p)
        return [("full", full, full)] if full else []
    if field == BIRTH:
        d = _date_of(view)
        return [(tok, label, format_date(d, tok)) for tok, label in DATE_FORMATS] if d else []
    if field == PHONE:
        return [(tok, val, val) for tok, val in phone_formats(view.get_field(PHONE))]
    if field == STATE:
        return [(tok, val, val) for tok, val in region_formats(view.get_field(STATE))]
    if field == COUNTRY:
        return [(tok, val, val) for tok, val in country_formats(view.get_field(COUNTRY))]
    if field == POSTAL:
        return [(tok, val, val) for tok, val in postal_formats(view.get_field(POSTAL))]
    if field == GENDER:
        return [(tok, val, val) for tok, val in gender_formats(view.get_field(GENDER))]
    if field in (FIRST, LAST, EMAIL, ADDRESS, CITY):
        val = view.get_field(field).strip()
        return [("as", val, val)] if val else []
    if field == "card_number":
        digits = re.sub(r"\D", "", view.card_number or "")
        return [("digits", "Card number", digits)] if digits else []
    if field == "card_code":
        code = (view.card_code or "").strip()
        return [("as", "Card code", code)] if code else []
    if field == "card_holder":
        holder = (view.card_holder or "").strip()
        return [("as", holder, holder)] if holder else []
    if field == "card_expiry":
        return [(tok, _EXPIRY_LABELS[tok], val) for tok, val in _expiry_values(view)]
    return []


def fill_options(view, field: str) -> list[tuple[str, str]]:
    """(menu label, token) for the keyboard icon beside `field`. One option: no menu needed."""
    return [(label, f"{field}:{tok}") for tok, label, _val in _choices(view, field)]


def fill_value(view, token: str) -> str:
    """The text a token types, on one line (a newline or tab could submit the form or skip a box).
    "" when the field is empty or the token unknown."""
    field, _, tok = token.rpartition(":")
    val = next((v for t, _label, v in _choices(view, field) if t == tok), "")
    return re.sub(r"[\r\n\t]+", " ", val).strip()


def fill_part(token: str) -> str:
    """A name for the field a token types, for "stopped partway through the ..." (never a value)."""
    field = token.rpartition(":")[0]
    if field == "name":
        return "full name"
    if field in LABELS:
        return LABELS[field].lower()
    return field.replace("card_", "card ").replace("_", " ")
