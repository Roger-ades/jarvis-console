"""Cloud routines (claude.ai): the scheduled tasks created from Cowork or Claude Code.

They live on claude.ai and are reached through Claude Code's own RemoteTrigger
tool, which carries the profile's claude.ai login. The console runs a minimal
relay session: one allowed tool, a PreToolUse hook that only lets through the
exact call requested, and a PostToolUse hook that captures the raw answer and
stops the session before the model reads it (a few thousand tokens per call).
"""
from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

from . import claude_cli

RELAY_PROMPT = ("Tu es un relais technique de la console JARVIS. Ta seule tâche : appeler l'outil RemoteTrigger "
                "une seule fois avec exactement les paramètres JSON du message (charge l'outil avec ToolSearch si "
                "besoin), puis t'arrêter sans commentaire.")
ALLOWED = {"list", "get", "run", "update", "list_runs"}
DAYS = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"]


class CloudError(Exception):
    pass


def _response_text(resp) -> str:
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for k in ("content", "output", "result", "text"):
            if k in resp:
                return _response_text(resp[k])
        return json.dumps(resp)
    if isinstance(resp, list):
        return "\n".join(_response_text(x.get("text", x) if isinstance(x, dict) else x) for x in resp)
    return str(resp)


def parse_http(text: str) -> tuple[int, object]:
    m = re.match(r"\s*HTTP (\d{3})\s*\n?(.*)", text, re.S)
    status, body = (int(m.group(1)), m.group(2)) if m else (200, text)
    start = min([i for i in (body.find("{"), body.find("[")) if i >= 0], default=-1)
    if start < 0:
        return status, body.strip()
    try:
        return status, json.JSONDecoder().raw_decode(body[start:])[0]
    except ValueError:
        return status, body.strip()


def relay(cli: list[str], env: dict, cwd: str, params: dict, timeout: float = 90) -> tuple[int, object]:
    """Run one RemoteTrigger call through a throw-away Claude Code session."""
    if params.get("action") not in ALLOWED:
        raise CloudError("Action non autorisée.")
    cmd = [*cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--model", "haiku", "--tools", "RemoteTrigger,ToolSearch", "--allowedTools", "RemoteTrigger,ToolSearch",
           "--permission-mode", "dontAsk", "--permission-prompt-tool", "stdio", "--strict-mcp-config",
           "--no-session-persistence", "--max-turns", "3", "--system-prompt", RELAY_PROMPT]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env,
                                **claude_cli.spawn_kwargs())
    except OSError as exc:
        raise CloudError(f"Impossible de lancer Claude Code : {exc}")
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout] + [lines.put(None)], daemon=True).start()
    threading.Thread(target=lambda: [None for _ in proc.stderr], daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def answer(rid, payload):
        send({"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": payload}})

    captured, failure = None, ""
    deadline = time.time() + timeout
    try:
        send({"type": "control_request", "request_id": "relay_init", "request": {"subtype": "initialize", "hooks": {
            "PreToolUse": [{"matcher": None, "hookCallbackIds": ["relay_guard"], "timeout": 60}],
            "PostToolUse": [{"matcher": "RemoteTrigger", "hookCallbackIds": ["relay_capture"], "timeout": 60}]}}})
        send({"type": "user", "session_id": "", "parent_tool_use_id": None,
              "message": {"role": "user", "content": json.dumps(params, ensure_ascii=False)}})
        while time.time() < deadline:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if line is None:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "control_request":
                req, rid = msg.get("request") or {}, msg.get("request_id", "")
                if req.get("subtype") == "hook_callback":
                    hin = req.get("input") or {}
                    tool, tin = hin.get("tool_name", ""), hin.get("tool_input") or {}
                    if hin.get("hook_event_name") == "PostToolUse" and tool == "RemoteTrigger":
                        captured = hin.get("tool_response")
                        answer(rid, {})
                        break
                    ok = tool == "ToolSearch" or (tool == "RemoteTrigger" and all(tin.get(k) == v for k, v in params.items()))
                    answer(rid, {} if ok else {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                                      "permissionDecisionReason": "Appel non demandé par la console."}})
                elif req.get("subtype") == "can_use_tool":
                    tool = req.get("tool_name", "")
                    answer(rid, {"behavior": "allow", "updatedInput": req.get("input") or {}} if tool in ("RemoteTrigger", "ToolSearch")
                           else {"behavior": "deny", "message": "Non autorisé."})
                else:
                    send({"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": "non pris en charge"}})
            elif msg.get("type") == "result":
                failure = str(msg.get("result") or "")
                break
    except (OSError, ValueError) as exc:
        failure = str(exc)
    finally:
        if proc.poll() is None:
            claude_cli.kill_tree(proc.pid)
    if captured is None:
        if re.search(r"not logged in|/login", failure, re.I):
            raise CloudError("Ce profil n'est pas connecté : Configuration → Profils → Se connecter.")
        raise CloudError("Claude Code n'a pas pu joindre les routines claude.ai" + (f" : {failure[:300]}" if failure else " (délai dépassé)."))
    return parse_http(_response_text(captured))


# ---------------------------------------------------------------- presentation

def cron_label(expr: str) -> str:
    """French label for the usual cron forms (claude.ai schedules run in UTC)."""
    parts = (expr or "").split()
    if len(parts) != 5:
        return expr or ""
    mi, hr, dom, mon, dow = parts
    if not (mi.isdigit() and hr.isdigit()):
        if mi.isdigit() and hr == "*" and dom == mon == dow == "*":
            return f"toutes les heures à :{int(mi):02d}"
        return f"cron {expr} (UTC)"
    utc = datetime.now(timezone.utc).replace(hour=int(hr), minute=int(mi), second=0, microsecond=0)
    local = utc.astimezone().strftime("%H:%M")
    at = f"à {local}"
    if dom == "*" and mon == "*":
        if dow == "*":
            return f"tous les jours {at}"
        if dow in ("1-5", "MON-FRI"):
            return f"en semaine {at}"
        names = []
        for d in dow.split(","):
            if d.isdigit() and 0 <= int(d) <= 7:
                names.append(DAYS[int(d) % 7])
            else:
                return f"cron {expr} (UTC)"
        return f"chaque {', '.join(names)} {at}"
    if dom.isdigit() and mon == "*" and dow == "*":
        return f"le {dom} de chaque mois {at}"
    return f"cron {expr} (UTC)"


def summarize(t: dict) -> dict:
    ds = t.get("derived_state") or {}
    last = t.get("last_run") or {}
    return {
        "id": t.get("id"), "name": t.get("name") or "(sans nom)", "cron": t.get("cron_expression") or "",
        "schedule_label": cron_label(t.get("cron_expression") or ""), "enabled": bool(t.get("enabled")),
        "next_run_at": t.get("next_run_at"), "last_fired_at": t.get("last_fired_at"),
        "last_status": (last.get("status") if isinstance(last, dict) else None) or "",
        "kind": t.get("created_kind") or "", "created_at": t.get("created_at"), "updated_at": t.get("updated_at"),
        "suspension": t.get("suspension_reason") or "", "ended": t.get("ended_reason") or "",
        "device": (t.get("bound_device") or {}).get("display_name", ""),
        "model": ds.get("model") or "", "permission_mode": ds.get("permission_mode") or "",
        "folders": ds.get("folders") or [], "prompt": str(ds.get("prompt") or "")[:6000],
    }


def summarize_runs(body) -> list[dict]:
    items = body.get("data") if isinstance(body, dict) else body
    out = []
    for r in items or []:
        if isinstance(r, dict):
            out.append({k: r.get(k) for k in ("id", "title", "status", "created_at", "updated_at", "started_at",
                                              "last_activity_at", "url", "link") if k in r})
    return out
