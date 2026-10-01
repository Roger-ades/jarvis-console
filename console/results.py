"""Results of tools shown to the user as they are (the console's tool "afficher_resultat").

Claude names a result it already received (a mail read through the Office 365 connector, an Odoo
record…) instead of copying it into its answer: the console finds that result in the task's recent
tool calls, or in the session's transcript, and shows it in a window. A mail keeps its layout, its
sender, recipients and date; HTML goes to the preview origin (content.py); anything else shows as
JSON or text. A result too long for Claude's context was saved to a file by Claude Code, and the
console reads it from there.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import content

SAVED = re.compile(r"saved to (.+?\.\w{1,6})\.?(?:\r?\n|\Z)")
MAX_SAVED = 50 * 1024 * 1024
HTML_KEYS = ("html", "body_html", "bodyHtml", "htmlBody", "html_body", "content_html")
_LOOKS_HTML = re.compile(r"<(?:html|body|div|table|p|span|br|td|h[1-6])\b", re.I)


def text_of(result) -> str:
    """The text blocks of a tool result (images left aside)."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b)
                         for b in result if not isinstance(b, dict) or b.get("type") in (None, "text"))
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return "" if result is None else str(result)


def image_of(result) -> str:
    """The first image of a tool result, as a data: address."""
    for b in result if isinstance(result, list) else []:
        src = b.get("source") if isinstance(b, dict) and b.get("type") == "image" else None
        if isinstance(src, dict) and src.get("type") == "base64" and re.fullmatch(r"image/(png|jpe?g|gif|webp)", str(src.get("media_type"))):
            return f"data:{src['media_type']};base64,{src.get('data', '')}"
    return ""


def saved_file(text: str, config_dir: Path) -> Path | None:
    """The file where Claude Code put a result too long for the context: only inside its own
    projects folder (a tool's text never makes the console read another file)."""
    m = SAVED.search(text or "") if len(text or "") < 4000 else None
    if not m:
        return None
    try:
        p = Path(os.path.realpath(m.group(1).strip()))
        root = Path(os.path.realpath(config_dir / "projects"))
        if p.parent.name == "tool-results" and root in p.parents and p.is_file() and p.stat().st_size <= MAX_SAVED:
            return p
    except (OSError, ValueError):
        pass
    return None


def parse_json(text: str) -> list | None:
    """JSON values of a text: one value, or several glued together ({…}{…}, one per line…)."""
    s = (text or "").strip()
    if not s or s[0] not in "{[":
        return None
    try:
        return [json.loads(s)]
    except ValueError:
        pass
    dec, out, i = json.JSONDecoder(), [], 0
    while i < len(s):
        while i < len(s) and s[i] in " \t\r\n,":
            i += 1
        if i >= len(s):
            break
        try:
            v, i = dec.raw_decode(s, i)
        except ValueError:
            return out or None
        out.append(v)
    return out or None


def _unwrap(v, depth: int = 0):
    """MCP resources ({"contents": [{"text": "<json>"}]}) and JSON held in strings, opened up."""
    if depth > 6:
        return v
    if isinstance(v, str) and v[:1] in "{[" and len(v) < 30_000_000:
        parsed = parse_json(v)
        if parsed:
            return _unwrap(parsed[0] if len(parsed) == 1 else parsed, depth + 1)
        return v
    if isinstance(v, dict):
        return {k: _unwrap(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_unwrap(x, depth + 1) for x in v]
    return v


def is_mail(v) -> bool:
    return isinstance(v, dict) and "subject" in v and any(k in v for k in ("body", "bodyPreview", "uniqueBody", "sender", "from"))


def documents(v, depth: int = 0) -> list:
    """Mails found in a result (a search returns several), in order."""
    if depth > 6:
        return []
    if is_mail(v):
        return [v]
    out = []
    for x in (v.values() if isinstance(v, dict) else v if isinstance(v, list) else []):
        out.extend(documents(x, depth + 1))
    return out


def _person(p) -> str:
    if isinstance(p, dict) and isinstance(p.get("emailAddress"), dict):
        p = p["emailAddress"]
    if isinstance(p, dict):
        name, addr = str(p.get("name") or "").strip(), str(p.get("address") or p.get("email") or "").strip()
        return f"{name} <{addr}>" if name and addr and name.lower() != addr.lower() else name or addr
    return str(p or "").strip()


def _people(v) -> str:
    return ", ".join(x for x in (_person(p) for p in (v if isinstance(v, list) else [v] if v else [])) if x)


def _contains(v, needle: str) -> bool:
    return not needle or needle in json.dumps(v, ensure_ascii=False, default=str).lower()


def _html_field(v) -> str:
    if isinstance(v, dict):
        for k in HTML_KEYS:
            if isinstance(v.get(k), str) and _LOOKS_HTML.search(v[k]):
                return v[k]
    return ""


def view(result, *, needle: str = "", config_dir: Path | None = None) -> dict:
    """What to show for a tool result: {kind, title, meta, page | text | image, weblink}. kind: mail,
    html, json, text or image. needle picks the element of a list (a search's mails)."""
    needle = (needle or "").strip().lower()
    img = image_of(result)
    text = text_of(result)
    saved = saved_file(text, config_dir) if config_dir else None
    if saved:
        text = content.decode_html(saved.read_bytes())
    if img and not text.strip():
        return {"kind": "image", "title": "Image", "image": img, "meta": {}}
    values = parse_json(text)
    data = _unwrap(values[0] if values and len(values) == 1 else values) if values else None
    mails = documents(data) if data is not None else []
    if mails:
        m = next((x for x in mails if _contains(x, needle)), mails[0])
        body = m.get("body") if m.get("body") not in (None, "") else m.get("uniqueBody")
        kind = str((body or {}).get("contentType") or "").lower() if isinstance(body, dict) else ""
        raw = (body.get("content") if isinstance(body, dict) else body) or ""
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False)
        if not raw:
            raw, kind = str(m.get("bodyPreview") or ""), "text"
        html_body = kind == "html" or (kind != "text" and bool(_LOOKS_HTML.search(raw)))
        page = raw if html_body else content.text_page(raw)
        files = [{"name": str(a.get("name") or "pièce jointe"), "size": a.get("size")}
                 for a in m.get("attachments") or [] if isinstance(a, dict) and not a.get("isInline")]
        link = str(m.get("webLink") or "")
        return {"kind": "mail", "title": str(m.get("subject") or "(sans objet)"), "page": page, "count": len(mails),
                "weblink": link if link.lower().startswith("https://") else "",
                "inline_images": len(re.findall(r"""src\s*=\s*["']?\s*cid:""", page, re.I)),
                "meta": {"from": _person(m.get("from") or m.get("sender")), "to": _people(m.get("toRecipients")),
                         "cc": _people(m.get("ccRecipients")), "date": str(m.get("receivedDateTime") or m.get("sentDateTime") or ""),
                         "attachments": files}}
    if data is not None:
        items = data if isinstance(data, list) else [data]
        pick = next((x for x in items if _contains(x, needle)), items[0]) if needle and len(items) > 1 else data
        title = next((str(pick[k])[:200] for k in ("display_name", "name", "title", "subject")
                      if isinstance(pick, dict) and isinstance(pick.get(k), (str, int))), "")
        page = _html_field(pick)
        if page:
            return {"kind": "html", "title": title or "Page", "page": page, "meta": {}}
        return {"kind": "json", "title": title or "Résultat", "meta": {},
                "text": json.dumps(pick, ensure_ascii=False, indent=2, default=str)}
    stripped = text.lstrip()
    if stripped[:15].lower().startswith(("<!doctype html", "<html")):
        return {"kind": "html", "title": "Page", "page": text, "meta": {}}
    return {"kind": "text", "title": "Résultat", "text": text, "meta": {}, **({"image": img} if img else {})}


def from_transcript(paths: list[Path]) -> list[dict]:
    """Tool calls and their results in transcript files, oldest first: {id, name, input, content, is_error}."""
    calls: dict[str, dict] = {}
    for path in paths:
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                for b in (rec.get("message") or {}).get("content") or [] if isinstance(rec, dict) else []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and b.get("id"):
                        calls.setdefault(b["id"], {"id": b["id"], "name": b.get("name", ""), "input": b.get("input") or {},
                                                   "content": None, "is_error": False})
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                        c = calls[b["tool_use_id"]]
                        c["content"], c["is_error"] = b.get("content"), bool(b.get("is_error"))
    return [c for c in calls.values() if c["content"] is not None]
