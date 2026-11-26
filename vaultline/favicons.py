"""Website icon download, modelled on KeePassXC's IconDownloader.

Requests go straight to the entry's own website (no third-party icon service).
Order tried for each host, then for its parent domain:
  1. icons declared in the home page (<link rel="icon" / "apple-touch-icon">)
  2. /favicon.ico
The best image is scaled to 64x64 PNG, which is what gets stored in the database.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import ssl
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

ICON_SIZE = 64
TIMEOUT = 8
MAX_PAGE = 768 * 1024
MAX_IMAGE = 512 * 1024
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
_TWO_LEVEL_TLDS = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz",
                   "co.jp", "com.br", "com.mx", "co.za", "co.in", "com.cn", "com.tr", "gc.ca"}
_CTX = ssl.create_default_context()


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[tuple[int, str]] = []  # (score, href)

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            raise _StopParsing
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        href = a.get("href", "").strip()
        if not href or not ({"icon", "apple-touch-icon", "apple-touch-icon-precomposed"} & set(rel)):
            return
        size = 0
        m = re.match(r"(\d+)x\d+", a.get("sizes", ""))
        if m:
            size = int(m.group(1))
        elif "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            size = 180
        is_svg = href.lower().split("?")[0].endswith(".svg") or "svg" in a.get("type", "")
        # Bigger is better up to ~192px (we scale down to 64); unsized icons are usually small.
        # SVG goes last because decoding it needs Qt's svg plugin.
        if is_svg:
            score = 5
        elif not size:
            score = 40
        else:
            score = size if size <= 192 else 192 - (size - 192) / 10
        self.icons.append((score, href))


class _StopParsing(Exception):
    pass


def _get(url: str, limit: int) -> tuple[bytes, str] | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as resp:
            if resp.status != 200:
                return None
            final = resp.geturl()
            try:
                data = resp.read(limit + 1)
            except http.client.IncompleteRead as exc:
                data = exc.partial  # dropped mid-body: the <head> usually arrived already
            return data[:limit], final
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, ssl.SSLError):
        return None


def host_of(url: str) -> str | None:
    """Host worth fetching an icon for, or None for local/odd URLs."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or "." not in host:
        return None
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return None
    except ValueError:
        if host.endswith((".local", ".lan", ".internal", ".home", ".arpa")):
            return None
    return host


def _parent_domain(host: str) -> str | None:
    labels = host.split(".")
    keep = 3 if ".".join(labels[-2:]) in _TWO_LEVEL_TLDS else 2
    if len(labels) <= keep:
        return None
    return ".".join(labels[-keep:])


def to_png(data: bytes) -> bytes | None:
    img = QImage.fromData(QByteArray(data))
    if img.isNull() or img.width() < 8 or img.height() < 8:
        return None
    if img.width() > ICON_SIZE or img.height() > ICON_SIZE:
        img = img.scaled(ICON_SIZE, ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    img = img.convertToFormat(QImage.Format_ARGB32)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def _try_host(host: str) -> bytes | None:
    candidates: list[str] = []
    page = _get(f"https://{host}/", MAX_PAGE)
    if page:
        html, final_url = page
        parser = _LinkParser()
        try:
            parser.feed(html.decode("utf-8", "replace"))
        except _StopParsing:
            pass
        except Exception:
            pass
        for _score, href in sorted(parser.icons, key=lambda t: -t[0]):
            candidates.append(urljoin(final_url, href))
        base = final_url
    else:
        base = f"https://{host}/"
    candidates += [urljoin(base, "/favicon.ico"), f"http://{host}/favicon.ico"]

    seen = set()
    for url in candidates:
        if url in seen or not url.startswith(("http://", "https://")):
            continue
        seen.add(url)
        got = _get(url, MAX_IMAGE)
        if got:
            png = to_png(got[0])
            if png:
                return png
    return None


def fetch_icon(url: str) -> bytes | None:
    """Return a 64px PNG for the website in `url`, or None."""
    host = host_of(url)
    if not host:
        return None
    tried = set()
    for h in (host, _parent_domain(host)):
        if h and h not in tried:
            tried.add(h)
            png = _try_host(h)
            if png:
                return png
    return None
