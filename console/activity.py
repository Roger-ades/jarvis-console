"""What a Claude Code session did, read from its transcript files.

The CLI writes them as it goes: one for the lead, one per sub-agent (with a .meta.json naming its
type and the Agent call that started it). The stream only forwards a sub-agent's text, and nothing
of the background ones, so this is where every tool call is: the lead's, the sub-agents', MCP
included. Files are read incrementally (only what was appended since the last look).
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from . import library
from .config import Profile
from .permissions import summarize_target

_lock = threading.Lock()
_files: dict[str, "_File"] = {}


def server_key(name: str) -> str:
    """One spelling per MCP server: "claude.ai Claude Docs" (instructions) is claude_ai_Claude_Docs in tool names."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(name))


def _epoch(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class _File:
    """Running totals of one transcript file."""

    def __init__(self):
        self.offset = 0
        self.rest = b""
        self.calls: dict[str, tuple[str, dict]] = {}  # API call id -> (model, usage): a call is written once per block
        self.tool_ids: set[str] = set()
        self.tools: Counter = Counter()
        self.recent: deque = deque(maxlen=60)
        self.servers: set[str] = set()                # MCP servers the session was offered
        self.context = (0, 0.0)                       # lead's latest request size, when
        self.first = 0.0
        self.last = 0.0

    def feed(self, path: Path):
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size < self.offset:  # rewritten (session moved or compacted copy): start over
            self.__init__()
        if size == self.offset:
            return
        with open(path, "rb") as f:
            f.seek(self.offset)
            data = self.rest + f.read(size - self.offset)
        self.offset = size
        lines = data.split(b"\n")
        self.rest = lines.pop()  # an unfinished last line is read next time
        for raw in lines:
            if raw.strip():
                try:
                    self._entry(json.loads(raw))
                except (ValueError, TypeError, AttributeError):
                    pass

    def _entry(self, r: dict):
        ts = _epoch(r.get("timestamp") or "") if r.get("timestamp") else 0.0
        if ts:
            self.first = self.first or ts
            self.last = max(self.last, ts)
        att = r.get("attachment") or {}
        if r.get("type") == "attachment":
            if att.get("type") == "mcp_instructions_delta":
                self.servers.update(server_key(n) for n in att.get("addedNames") or [])
            elif att.get("type") in ("deferred_tools_delta", "deferred_tools_record"):
                for name in att.get("addedNames") or att.get("names") or []:
                    if str(name).startswith("mcp__") and str(name).count("__") >= 2:
                        self.servers.add(server_key(str(name).split("__")[1]))
            return
        if r.get("type") != "assistant":
            return
        m = r.get("message") or {}
        u = m.get("usage")
        if u:
            self.calls[m.get("id") or f"x{len(self.calls)}"] = (m.get("model") or "", u)
            if not r.get("isSidechain"):
                size = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                if size:
                    self.context = (size, ts or self.last)
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in self.tool_ids:
                self.tool_ids.add(b.get("id"))
                name = str(b.get("name") or "?")
                self.tools[name] += 1
                self.recent.append((ts, len(self.tool_ids), name, summarize_target(name, b.get("input") or {})[:200]))


def _state(path: Path) -> _File:
    key = str(path)
    st = _files.get(key)
    if st is None:
        st = _files[key] = _File()
    st.feed(path)
    return st


def transcript(profile: Profile, cwd: str, session_id: str) -> Path:
    return library.config_dir(profile) / "projects" / library.project_slug(cwd) / f"{session_id}.jsonl"


def session_activity(profile: Profile, cwd: str, session_id: str, hidden_servers=()) -> dict | None:
    """{context, models, agents, mcp, recent} of a session, or None when it has no transcript yet."""
    main = transcript(profile, cwd, session_id)
    if not main.is_file():
        return None
    subdir = main.with_suffix("") / "subagents"
    with _lock:
        files = [("chef", main, {})]
        for f in sorted(subdir.glob("*.jsonl")) if subdir.is_dir() else []:
            try:
                meta = json.loads(f.with_suffix(".meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            files.append((f.stem, f, meta))
        agents, models, recent, used = [], {}, [], Counter()
        lead = None
        for key, path, meta in files:
            st = _state(path)
            if key == "chef":
                lead = st
            per = Counter()
            for model, u in st.calls.values():
                acc = models.setdefault(model, Counter())
                acc["calls"] += 1
                for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                    acc[k] += int(u.get(k) or 0)
                    per[k] += int(u.get(k) or 0)
            names = sorted({m for m, _ in st.calls.values() if m})
            label = "chef" if key == "chef" else str(meta.get("agentType") or "sous-agent")
            for name, n in st.tools.items():
                if name.startswith("mcp__") and name.count("__") >= 2:
                    used[name.split("__")[1]] += n
            agents.append({
                "id": key, "type": label, "description": str(meta.get("description") or ""),
                "tool_use_id": meta.get("toolUseId") or "", "background": meta.get("requestShape") == "background",
                "models": names, "calls": len(st.calls), "read": per["input_tokens"] + per["cache_read_input_tokens"],
                "written": per["cache_creation_input_tokens"], "output": per["output_tokens"],
                "tools": dict(st.tools.most_common()), "first": st.first, "last": st.last,
            })
            recent.extend({"ts": ts, "n": n, "agent": label, "agent_id": key, "tool": name, "target": target}
                          for ts, n, name, target in st.recent)
        recent.sort(key=lambda x: (x["ts"], x["n"]), reverse=True)  # same millisecond: the order they were written
        size, at = lead.context if lead else (0, 0.0)
        servers = ((lead.servers if lead else set()) | set(used)) - set(hidden_servers)
        return {
            "context": {"tokens": size, "at": at},
            "models": {m: dict(c) for m, c in models.items() if m},
            "agents": agents,
            "mcp": {s: used.get(s, 0) for s in sorted(servers)},
            "recent": recent[:40],
        }
