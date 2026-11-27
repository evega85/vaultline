"""Auto-Type: KeePass-compatible keystroke sequences and finding the entry for a window.

Nothing here touches Windows or Qt: `sendkeys.py` does the typing and `ui/autotype_flow.py` the
steps around it. The kdbx's Auto-Type settings (entry flag, sequence, window associations, and the
folders' inherited flag and sequence) are only read, never written (see Vault._autotype_info).

Typed values are never logged, and errors never include them.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit

DEFAULT_SEQUENCE = "{USERNAME}{TAB}{PASSWORD}{ENTER}"

VK_TAB, VK_RETURN, VK_SPACE = 0x09, 0x0D, 0x20
DEFAULT_GAP_MS = 10     # between keystrokes; {DELAY=n} changes it
MAX_DELAY_MS = 10_000   # one {DELAY n}; a longer one is almost certainly a typo
MAX_REPEAT = 100        # {TAB n}


@dataclass(frozen=True)
class AutoTypeInfo:
    """An entry's Auto-Type settings as KeePass would use them (folders' settings applied)."""
    enabled: bool = True
    sequence: str = ""                              # "" = DEFAULT_SEQUENCE
    associations: tuple[tuple[str, str], ...] = ()  # (window pattern, sequence or "" for the entry's)
    expired: bool = False

    @property
    def effective_sequence(self) -> str:
        return self.sequence or DEFAULT_SEQUENCE


class SequenceError(ValueError):
    """The sequence can't be typed (unknown placeholder, KeePass modifier keys, missing OTP...)."""


# ---- sequences ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Step:
    """One thing to do. kind: "text" (value), "key" (vk), "delay" (ms), "gap" (ms between keys).
    `part` names the placeholder it came from, so a stop can say how far it got without values."""
    kind: str
    value: str = ""
    vk: int = 0
    ms: int = 0
    part: str = ""

    def __repr__(self) -> str:  # never show typed text in tracebacks or test output
        return f"Step({self.kind}, part={self.part!r}, vk={self.vk}, ms={self.ms}, len={len(self.value)})"


_ESCAPES = {"{": "{", "}": "}", "+": "+", "^": "^", "%": "%", "~": "~", "(": "(", ")": ")",
            "[": "[", "]": "]"}
_KEYS = {"TAB": VK_TAB, "ENTER": VK_RETURN, "SPACE": VK_SPACE}
_FIELDS = {"USERNAME": "username", "PASSWORD": "password", "TITLE": "title", "URL": "url"}
_S_STANDARD = {"title": "title", "username": "username", "password": "password", "url": "url",
               "notes": "notes"}
_DELAY = re.compile(r"DELAY\s+(\d+)$", re.I)
_DELAY_SET = re.compile(r"DELAY\s*=\s*(\d+)$", re.I)
_REPEAT = re.compile(r"(TAB|ENTER|SPACE)(?:\s+(\d+))?$", re.I)


def tokenize(sequence: str) -> list[tuple[str, str]]:
    """Split into ("text", literal) and ("ph", inside-the-braces) pieces. KeePass's bare modifier
    and grouping characters (+ ^ % ( )) are refused rather than typed wrongly; ~ is Enter."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(sequence)
    text = []
    while i < n:
        c = sequence[i]
        if c == "{":
            # {{} and {}} are literal braces
            if sequence.startswith("{{}", i) or sequence.startswith("{}}", i):
                text.append(sequence[i + 1])
                i += 3
                continue
            end = sequence.find("}", i + 1)
            if end < 0:
                raise SequenceError("A { has no matching }.")
            inner = sequence[i + 1:end]
            i = end + 1
            if inner in _ESCAPES:
                text.append(_ESCAPES[inner])
                continue
            if text:
                out.append(("text", "".join(text)))
                text = []
            out.append(("ph", inner))
        elif c == "}":
            raise SequenceError("A } has no matching {.")
        elif c == "~":
            if text:
                out.append(("text", "".join(text)))
                text = []
            out.append(("ph", "ENTER"))
            i += 1
        elif c in "+^%()":
            raise SequenceError(f"Key combinations ({c}) aren't supported in sequences yet.")
        else:
            text.append(c)
            i += 1
    if text:
        out.append(("text", "".join(text)))
    return out


def build(sequence: str, entry, otp_code: Callable[[], str] | None = None) -> list[Step]:
    """Turn a sequence into steps for `entry` (an EntryView or anything with the same fields).
    Raises SequenceError before anything is typed, so a sequence is never half-typed."""
    steps: list[Step] = []
    for kind, val in tokenize(sequence or DEFAULT_SEQUENCE):
        if kind == "text":
            steps.append(Step("text", value=val, part="text"))
            continue
        name = val.strip()
        upper = name.upper()
        if upper in _FIELDS:
            steps.append(Step("text", value=getattr(entry, _FIELDS[upper], "") or "", part=upper.lower()))
        elif upper in ("TOTP", "TIMEOTP"):
            if otp_code is None:
                raise SequenceError("The sequence types a one-time code, but this entry has no OTP.")
            steps.append(Step("text", value=otp_code(), part="one-time code"))
        elif m := _REPEAT.match(name):
            count = int(m.group(2) or 1)
            if count > MAX_REPEAT:
                raise SequenceError(f"{{{name}}} repeats too many times.")
            vk = _KEYS[m.group(1).upper()]
            steps.extend(Step("key", vk=vk, part=m.group(1).lower()) for _ in range(count))
        elif m := _DELAY_SET.match(name):
            steps.append(Step("gap", ms=min(int(m.group(1)), 1000), part="delay"))
        elif m := _DELAY.match(name):
            ms = int(m.group(1))
            if ms > MAX_DELAY_MS:
                raise SequenceError(f"{{{name}}} is longer than {MAX_DELAY_MS // 1000} seconds.")
            steps.append(Step("delay", ms=ms, part="delay"))
        elif upper.startswith("S:"):
            key = name[2:]
            attr = _S_STANDARD.get(key.casefold())
            if attr is not None:
                value = getattr(entry, attr, "") or ""
            else:
                value = next((f.value for f in getattr(entry, "custom", ()) if f.key == key), "")
            steps.append(Step("text", value=value, part=f"field {key}"))
        else:
            raise SequenceError(f"{{{name}}} isn't supported.")
    return steps


def describe_progress(done: list[Step]) -> str:
    """'after the username' style text for a stop, from the steps already typed (no values)."""
    for s in reversed(done):
        if s.part not in ("text", "delay", "tab", "enter", "space"):
            return f"after the {s.part}"
    return "before anything was typed" if not done else "partway"


# ---- matching ----------------------------------------------------------------------------
def window_matches(pattern: str, title: str) -> bool:
    """KeePass rules: the whole title, ignoring case; * matches anything, ? one character;
    //regex// is a regular expression (searched, ignoring case)."""
    pattern = (pattern or "").strip()
    if not pattern or not title:
        return False
    if len(pattern) > 4 and pattern.startswith("//") and pattern.endswith("//"):
        try:
            return re.search(pattern[2:-2], title, re.I) is not None
        except re.error:
            return False
    rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, title, re.I | re.S) is not None


def _word_in(needle: str, haystack: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack, re.I) is not None


_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "gc", "ne", "or", "go"}
MIN_NAME = 3


def site_names(url: str) -> list[str]:
    """Names a browser's page title might show for this URL: the host without www., and the site's
    own name ('github' for accounts.github.com, 'bbc' for www.bbc.co.uk). IPs and short names skipped."""
    url = (url or "").strip()
    if not url:
        return []
    if "://" not in url:
        url = "https://" + url
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return []
    if not host or host == "localhost":
        return []
    try:
        ipaddress.ip_address(host)
        return []
    except ValueError:
        pass
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    names = [host]
    if len(labels) >= 2:
        i = len(labels) - 2
        if len(labels) >= 3 and labels[i] in _SECOND_LEVEL and len(labels[-1]) == 2:
            i -= 1  # example.co.uk -> example
        names.append(labels[i])
    return [n for n in dict.fromkeys(names) if len(n) >= MIN_NAME]


_DASH = r"\s+[-–—]\s+"
_BROWSER_SUFFIX = re.compile(
    # Edge: "Page and 3 more pages - Microsoft​ Edge". A profile name ("- Work -") is left in:
    # it can't be told apart from the page's own " - Site" part.
    r"(?:\s+and\s+\d+\s+more\s+pages?)?" + _DASH + r"Microsoft​?\s?Edge\s*$"
    r"|" + _DASH + r"(?:Google Chrome|Chromium|Mozilla Firefox|Firefox Developer Edition|Firefox|Brave|"
    r"Opera|Vivaldi|Arc|Waterfox|LibreWolf|Tor Browser|Zen Browser|Helium|Thorium)\s*$", re.I)


def page_title(window_title: str) -> str:
    """The page's own title from a browser window title: the browser's name (and Edge's profile and
    'and N more pages') taken off the end, so an entry called "Google" doesn't match every Chrome window."""
    return _BROWSER_SUFFIX.sub("", window_title or "").strip()


# Sign-up pages, from the page title. Only a hint (any page can call itself anything): it puts
# "New login for this window" first, it never does anything by itself.
_SIGNUP = re.compile(r"(?<!\w)(?:sign[\s-]?up|register|registration|create\s+(?:an?\s+|your\s+)?(?:new\s+)?"
                     r"account|new\s+account|join(?:\s+(?:now|free|us|today))?|get\s+started|enrol(?:l)?|"
                     r"inscription|s'inscrire|cr[ée]er\s+(?:un|votre)\s+compte)(?!\w)", re.I)
_TITLE_PARTS = re.compile(r"\s+[-|·•—–:]\s+|\s*[|·•—–]\s*|:\s+")


def site_url(address: str) -> str:
    """The site part of what a browser's address bar shows ("facebook.com/r.php?x=1" ->
    "https://facebook.com"): the path and query are dropped (they can hold sign-up codes).
    "" for anything that isn't a web address (a search being typed, file:, chrome:...)."""
    text = (address or "").strip()
    if not text or any(c.isspace() for c in text):
        return ""
    if "://" not in text:  # browsers hide the scheme: https, except on the local network
        host = text.split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
        local = host == "localhost" or host.replace(".", "").isdigit() or ":" in host
        text = ("http://" if local else "https://") + text
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not host or "." not in host and host != "localhost":
        return ""
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def is_browser_title(window_title: str) -> bool:
    """The window title ends with a known browser's name."""
    return bool(_BROWSER_SUFFIX.search(window_title or ""))


def is_signup_title(window_title: str) -> bool:
    return bool(_SIGNUP.search(page_title(window_title)))


def site_from_title(window_title: str) -> str:
    """A name for a new entry from the window title: the browser's name taken off, then the shortest
    part that isn't a sign-up phrase ("Sign up · GitHub" -> "GitHub"). Only a suggestion to edit."""
    parts = [p.strip() for p in _TITLE_PARTS.split(page_title(window_title)) if p.strip()]
    if not parts:
        return ""
    names = [p for p in parts if not _SIGNUP.search(p)] or parts
    return min(reversed(names), key=len)[:80]


@dataclass
class Match:
    entry: object          # EntryView
    sequence: str
    how: str               # "window" (association), "title" or "url"


def find_matches(entries: Iterable, title: str) -> list[Match]:
    """Entries for a window title, from the first of these that finds any: window associations,
    the entry title in the window title, the URL's site name in the window title. Entries with
    Auto-Type turned off (on the entry or its folder), expired entries and profiles are skipped."""
    title = (title or "").strip()
    if not title:
        return []
    # Sign-up profiles have no login to type (the default sequence would be just Tab and Enter):
    # their fields are typed one at a time from the details panel.
    usable = [e for e in entries if not getattr(e, "is_identity", False)
              and (getattr(e, "autotype", None) is None or (e.autotype.enabled and not e.autotype.expired))]
    info = lambda e: getattr(e, "autotype", None) or AutoTypeInfo()  # noqa: E731

    found = []
    for e in usable:
        for pattern, seq in info(e).associations:
            if window_matches(pattern, title):
                found.append(Match(e, seq or info(e).effective_sequence, "window"))
                break
    if found:
        return found
    title = page_title(title)  # titles and site names: only the page's part, never the browser's name
    if not title:
        return []
    found = [Match(e, info(e).effective_sequence, "title") for e in usable
             if len((e.title or "").strip()) >= MIN_NAME and _word_in(e.title.strip(), title)]
    if found:
        return found
    return [Match(e, info(e).effective_sequence, "url") for e in usable
            if any(_word_in(n, title) for n in site_names(e.url))]
