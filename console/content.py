"""A second origin for HTML that is not the console's: mails, pages of a task's folders, results of tools.

The console page lives on http://127.0.0.1:<port>. HTML shown in a preview is served from
http://apercu.localhost:<port> instead (Chrome and Edge send every *.localhost name to the loopback):
another origin, so such a page can never reach the console's token, storage or API. Each page sits
behind an unguessable address that expires, runs without scripts (CSP sandbox), keeps its own styles
and loads nothing from the web until the user asks for its remote images (no tracking pixel, no
"read" receipt). Links open in a new tab of the browser, never inside the console.
"""
from __future__ import annotations

import html as html_mod
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HOST = "apercu.localhost"
TTL = 12 * 3600
MAX_ITEMS = 300
MAX_PAGE = 20 * 1024 * 1024
MAX_TOTAL = 120 * 1024 * 1024

_REFRESH = re.compile(r"<meta\b[^>]*\bhttp-equiv\s*=\s*['\"]?\s*refresh\b[^>]*>", re.I)
_HINTS = re.compile(r"<link\b[^>]*\brel\s*=\s*['\"]?[^'\">]*\b(?:dns-prefetch|preconnect|prefetch|prerender|preload|modulepreload)\b[^>]*>", re.I)
_REMOTE = re.compile(r"""(?:\b(?:src|srcset|background|poster|data)\s*=\s*["']?\s*|url\(\s*["']?\s*|@import\s+["']?)(?:https?:)?//""", re.I)
_REMOTE_CSS = re.compile(r"""<link\b[^>]*\bhref\s*=\s*["']?\s*(?:https?:)?//""", re.I)
_DOCTYPE = re.compile(r"^\s*<!doctype\b[^>]*>", re.I)
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", re.I)
MAIL_STYLE = ("<style>html{background:#fff;color:#1f1f1f;color-scheme:light}"
              "body{margin:14px 16px;font:14px/1.5 'Segoe UI',system-ui,Arial,sans-serif;overflow-wrap:anywhere}"
              "pre.plain{white-space:pre-wrap;font:inherit;margin:0}</style>")


def decode_html(raw: bytes) -> str:
    """Bytes of an HTML file as text: UTF-8 (BOM or not), else the charset it declares, else Windows-1252."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    m = _CHARSET.search(raw[:4096])
    try:
        return raw.decode(m.group(1).decode("ascii") if m else "cp1252", "replace")
    except LookupError:
        return raw.decode("cp1252", "replace")


def prepare(page: str, style: str = "") -> str:
    """The page with what the console adds in front: UTF-8, links in a new tab, an optional base style
    (before the page's own, which wins); without automatic redirections nor network hints."""
    page = _DOCTYPE.sub("", _HINTS.sub("", _REFRESH.sub("", page)))
    return f'<!doctype html><meta charset="utf-8"><base target="_blank">{style}' + page


def text_page(text: str) -> str:
    return f'<pre class="plain">{html_mod.escape(text)}</pre>'


def remote_refs(page: str) -> int:
    """How many web resources (images, styles, fonts) the page would load: 0 hides the button."""
    page = _HINTS.sub("", _REFRESH.sub("", page))
    return len(_REMOTE.findall(page)) + len(_REMOTE_CSS.findall(page))


def policy(origin: str, ancestors: str, remote: bool) -> str:
    """No script, no form, no frame; the page's own resources only, the web too once the user asked."""
    web = " https: http:" if remote else ""
    return (f"default-src 'none'; style-src {origin} 'unsafe-inline'{web}; img-src {origin} data:{web}; "
            f"font-src {origin} data:{web}; media-src {origin}{web}; form-action 'none'; base-uri {origin}; "
            f"frame-ancestors {ancestors}; sandbox allow-popups allow-popups-to-escape-sandbox")


@dataclass
class Entry:
    page: bytes
    remote: bool
    resolve: Callable[[str], Path | None] | None  # a file next to the page ("images/logo.png"), or None
    expires: float


class ContentStore:
    """Pages waiting to be shown, in memory only, each one behind its own random address."""

    def __init__(self):
        self._items: dict[str, Entry] = {}
        self._lock = threading.Lock()

    def put(self, page: str, *, remote: bool = False, resolve: Callable[[str], Path | None] | None = None,
            style: str = "") -> str:
        data = prepare(page, style).encode("utf-8")
        if len(data) > MAX_PAGE:
            raise ValueError("page trop volumineuse pour un aperçu (plus de 20 Mo)")
        cid = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._items = {k: e for k, e in self._items.items() if e.expires > now}
            self._items[cid] = Entry(data, remote, resolve, now + TTL)
            while len(self._items) > MAX_ITEMS or sum(len(e.page) for e in self._items.values()) > MAX_TOTAL:
                self._items.pop(next(iter(self._items)))  # the oldest first
        return cid

    def get(self, cid: str) -> Entry | None:
        with self._lock:
            e = self._items.get(cid or "")
        return e if e and e.expires > time.time() else None
