"""Existing Claude Code sessions of a profile, whoever created them.

Two sources, both local:
- the CLI transcripts: <config dir>/projects/<project>/<session id>.jsonl, written
  by every Claude Code session (Claude Desktop's Code tab, the CLI, this console);
- the Claude Desktop metadata of its Code tab: <app dir>/claude-code-sessions/**/
  local_*.json (title, folder, archived flag), linked through cliSessionId.

Claude.ai conversations, Projects and Cowork live on Anthropic's servers and are
not reachable from here.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .config import Profile, expand_path

UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_cache: dict[str, tuple[float, int, dict]] = {}
_lock = threading.Lock()


def config_dir(profile: Profile) -> Path:
    return Path(expand_path(profile.config_dir) or str(Path.home() / ".claude"))


def desktop_dir(profile: Profile) -> Path | None:
    p = expand_path(profile.mcp.desktop_config)
    return Path(p).parent if p else None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _is_prompt(obj: dict) -> str:
    """The user's own words in a transcript line, or ''."""
    if obj.get("type") != "user" or obj.get("isSidechain") or obj.get("isMeta"):
        return ""
    msg = obj.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
        return ""
    text = _text_of(content).strip()
    if not text or text.startswith(("<command-", "<local-command", "<system-reminder", "Caveat:")):
        return ""
    return text


def _ts(value) -> float | None:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        from datetime import datetime
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _summarize(path: Path) -> dict:
    st = path.stat()
    key = str(path)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
    meta = {"id": path.stem, "cwd": "", "title": "", "first_prompt": "", "last_prompt": "", "prompts": 0,
            "messages": 0, "started": None, "updated": st.st_mtime, "entrypoint": "", "cost_usd": None,
            "file_size": st.st_size}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            typ = obj.get("type")
            if not meta["cwd"] and obj.get("cwd"):
                meta["cwd"] = obj["cwd"]
            if not meta["entrypoint"] and obj.get("entrypoint"):
                meta["entrypoint"] = obj["entrypoint"]
            if meta["started"] is None and obj.get("timestamp"):
                meta["started"] = _ts(obj["timestamp"])
            if typ == "custom-title" and obj.get("customTitle"):
                meta["title"] = obj["customTitle"]
            elif typ == "summary" and obj.get("summary") and not meta["title"]:
                meta["title"] = obj["summary"]
            elif typ == "last-prompt" and obj.get("lastPrompt"):
                meta["last_prompt"] = str(obj["lastPrompt"])[:300]
            elif typ == "cost-state" and obj.get("totalCostUSD") is not None:
                meta["cost_usd"] = obj.get("totalCostUSD")
            elif typ in ("user", "assistant") and not obj.get("isSidechain"):
                meta["messages"] += 1
                prompt = _is_prompt(obj)
                if prompt:
                    meta["prompts"] += 1
                    if not meta["first_prompt"]:
                        meta["first_prompt"] = prompt[:300]
    with _lock:
        _cache[key] = (st.st_mtime, st.st_size, meta)
    return meta


def _desktop_sessions(profile: Profile) -> dict[str, dict]:
    root = desktop_dir(profile)
    out: dict[str, dict] = {}
    if not root or not (root / "claude-code-sessions").is_dir():
        return out
    for f in (root / "claude-code-sessions").rglob("local_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cli = d.get("cliSessionId")
        if not cli:
            continue
        out[cli] = {"title": d.get("title") or "", "cwd": d.get("cwd") or "", "origin_cwd": d.get("originCwd") or "",
                    "archived": bool(d.get("isArchived")), "updated": _ts(d.get("lastActivityAt")),
                    "created": _ts(d.get("createdAt")), "model": d.get("model") or "", "turns": d.get("completedTurns")}
    return out


def _origin(meta: dict, desktop: dict | None) -> str:
    if desktop:
        return "desktop"
    if meta["title"].startswith("JARVIS - "):
        return "console"
    return "cli"


def list_sessions(profile: Profile, limit: int = 500) -> list[dict]:
    projects = config_dir(profile) / "projects"
    desktop = _desktop_sessions(profile)
    rows = []
    if projects.is_dir():
        for f in projects.glob("*/*.jsonl"):
            if not UUID.match(f.stem):
                continue
            try:
                meta = _summarize(f)
            except OSError:
                continue
            if not meta["messages"]:
                continue
            dk = desktop.pop(f.stem, None)
            cwd = meta["cwd"] or (dk or {}).get("cwd", "")
            if dk and not Path(cwd).is_dir() and Path(dk.get("origin_cwd") or "").is_dir():
                cwd = dk["origin_cwd"]
            title = (dk or {}).get("title") or meta["title"] or meta["first_prompt"] or "(sans titre)"
            rows.append({
                "id": f.stem, "profile": profile.id, "origin": _origin(meta, dk),
                "title": title.removeprefix("JARVIS - ")[:200], "first_prompt": meta["first_prompt"],
                "last_prompt": meta["last_prompt"], "cwd": cwd, "cwd_exists": bool(cwd) and Path(cwd).is_dir(),
                "project": f.parent.name, "started": meta["started"], "updated": (dk or {}).get("updated") or meta["updated"],
                "prompts": meta["prompts"], "messages": meta["messages"], "archived": bool((dk or {}).get("archived")),
                "model": (dk or {}).get("model", ""), "entrypoint": meta["entrypoint"], "cost_usd": meta["cost_usd"],
                "resumable": bool(cwd) and Path(cwd).is_dir(),
            })
    rows.sort(key=lambda r: r["updated"] or 0, reverse=True)
    return rows[:limit]


def find_transcript(profile: Profile, session_id: str) -> Path | None:
    if not UUID.match(session_id or ""):
        return None
    projects = config_dir(profile) / "projects"
    for f in projects.glob(f"*/{session_id}.jsonl"):
        return f
    return None


def read_transcript(profile: Profile, session_id: str, limit: int = 400) -> list[dict]:
    """The conversation as simple items: prompts, answers, tool calls (no tool output bodies)."""
    f = find_transcript(profile, session_id)
    if not f:
        return []
    items: list[dict] = []
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") not in ("user", "assistant") or obj.get("isMeta"):
                continue
            ts = _ts(obj.get("timestamp"))
            side = bool(obj.get("isSidechain"))
            if obj["type"] == "user":
                prompt = _is_prompt(obj)
                if prompt:
                    items.append({"role": "user", "text": prompt[:20000], "ts": ts})
                continue
            for block in (obj.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    items.append({"role": "assistant", "text": block["text"][:20000], "ts": ts, "sidechain": side})
                elif block.get("type") == "tool_use":
                    from .permissions import summarize_target
                    items.append({"role": "tool", "name": block.get("name", ""), "ts": ts, "sidechain": side,
                                  "target": summarize_target(block.get("name", ""), block.get("input") or {})[:300]})
    return items[-limit:]
