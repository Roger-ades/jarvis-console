"""Stand-in for `claude -p` speaking the stream-json control protocol.

The user message drives the scenario, one directive per line:
  TOOL <name> <json input>   call a tool (PreToolUse hook, then permission check)
  ASK                        AskUserQuestion round-trip
  SLEEP <seconds>
  ENV                        report CLAUDE_CONFIG_DIR in the result
  ARGS                       report argv in the result
  FAIL                       finish the turn with an error result
  FILES                      create an image, a PDF and a CSV, then answer with previews
Anything else is echoed back.
"""
import json
import os
import sys
import time
import uuid

sys.stdout.reconfigure(encoding="utf-8")  # the real CLI (Node) speaks UTF-8 on its pipes
sys.stdin.reconfigure(encoding="utf-8")
ARGS = sys.argv[1:]
_pending: list[dict] = []
_n = 0


def opt(name, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


MODE = opt("--permission-mode", "manual")
ALLOWED = [x for x in (opt("--allowedTools", "") or "").split(",") if x]
SESSION = opt("--resume") or opt("--session-id") or str(uuid.uuid4())


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read():
    if _pending:
        return _pending.pop(0)
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)


def request(sub: dict) -> dict:
    """Send a control request to the host and wait for its answer."""
    global _n
    _n += 1
    rid = f"cli_{_n}"
    out({"type": "control_request", "request_id": rid, "request": sub})
    while True:
        line = sys.stdin.readline()
        if not line:
            return {"_eof": True}  # the host closed our input: nobody can answer any more
        m = json.loads(line)
        if m.get("type") == "control_response" and m["response"].get("request_id") == rid:
            return m["response"].get("response") or {}
        _pending.append(m)


def use_tool(name: str, inp: dict) -> tuple[bool, str]:
    hook = request({"subtype": "hook_callback", "callback_id": "console_pretool",
                    "input": {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": inp,
                              "session_id": SESSION}})
    if hook.get("_eof"):  # what the real CLI tells the model when its host stopped answering
        return False, "The user doesn't want to take this action right now. STOP what you are doing and wait for the user to tell you how to proceed."
    decision = (hook.get("hookSpecificOutput") or {}).get("permissionDecision")
    if decision == "deny":
        return False, (hook["hookSpecificOutput"].get("permissionDecisionReason") or "hook deny")
    if decision != "allow" and MODE != "bypassPermissions" and name not in ALLOWED:
        if MODE == "dontAsk":
            return False, "dontAsk"
        perm = request({"subtype": "can_use_tool", "tool_name": name, "input": inp})
        if perm.get("behavior") != "allow":
            return False, perm.get("message", "deny")
    return True, "ok"


DEMO_TEXT = """## Synthèse

Trois devis **en brouillon** correspondent à la demande. Aucun n'a été confirmé.

| Client | Montant HT | État |
| --- | ---: | :---: |
| Dupont SA | 12 480 € | brouillon |
| Martin & fils | 3 200 € | brouillon |

- [x] Lecture du mail client
- [ ] Validation du devis par toi

```python
print("code copiable")
```

Source : https://example.com/doc."""


def _call(name, inp, parent=None, result="ok"):
    tid = f"toolu_{uuid.uuid4().hex[:8]}"
    out({"type": "assistant", "parent_tool_use_id": parent,
         "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
    ok, why = use_tool(name, inp)
    time.sleep(0.15)
    out({"type": "user", "parent_tool_use_id": parent, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": not ok, "content": result if ok else why}]}})
    return tid


def _agent_start(kind, desc, parent=None):
    tid = f"toolu_{uuid.uuid4().hex[:8]}"
    inp = {"subagent_type": kind, "description": desc, "prompt": f"{desc}. Rends un résumé court."}
    out({"type": "assistant", "parent_tool_use_id": parent,
         "message": {"content": [{"type": "tool_use", "id": tid, "name": "Task", "input": inp}]}})
    use_tool("Task", inp)
    return tid


def _agent_end(tid, text, parent=None):
    out({"type": "user", "parent_tool_use_id": parent, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": False, "content": text}]}})


def _pdf(text):
    """A minimal valid one-page PDF."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return pdf + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def files():
    import shutil
    cwd = os.getcwd()
    png = os.path.join(cwd, "schema-reseau.png")
    shutil.copy(os.path.join(os.path.dirname(__file__), "..", "static", "img", "icon-512.png"), png)
    pdf = os.path.join(cwd, "devis-S00012.pdf")
    with open(pdf, "wb") as f:
        f.write(_pdf("Devis S00012 - 3 200 EUR HT"))
    csv = os.path.join(cwd, "tarifs.csv")
    with open(csv, "w", encoding="utf-8") as f:
        f.write("Référence;Désignation;Prix HT\nCAM-01;Caméra dôme 4 MP;189,00\nNVR-08;Enregistreur 8 voies;412,50\n")
    _call("Write", {"file_path": png, "content": "(image)"})
    _call("Read", {"file_path": pdf}, result="PDF 1 page")
    return (f"Voici les documents :\n\n- Schéma : `{png}`\n- Devis : {pdf}\n- Tarifs : [tableau des tarifs]({csv})\n\n"
            "Image trouvée sur le web : ![dés](https://upload.wikimedia.org/wikipedia/commons/4/47/"
            "PNG_transparency_demonstration_1.png)\n\n"
            "Page de référence : https://example.com")


def demo():
    todo = {"todos": [{"content": "Lire le mail client", "status": "completed"},
                      {"content": "Préparer le devis", "status": "in_progress"},
                      {"content": "Faire valider", "status": "pending"}]}
    out({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "thinking", "thinking": "Je relis le mail, puis je cherche le client et ses devis dans Odoo."}]}})
    _call("TodoWrite", todo)
    _call("Glob", {"pattern": "**/*.eml", "path": "."}, result="mail-client.eml")
    _call("Read", {"file_path": "mail-client.eml"}, result="Bonjour, pouvez-vous me faire un devis…")
    _call("Grep", {"pattern": "référence", "path": "."}, result="2 résultats")
    out({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "text", "text": "Le client demande **3 postes** de télésurveillance. Je lance deux recherches en parallèle."}]}})
    a1 = _agent_start("Explore", "Chercher les devis existants du client")
    a2 = _agent_start("general-purpose", "Vérifier les tarifs en vigueur")
    out({"type": "assistant", "parent_tool_use_id": a1, "message": {"content": [
        {"type": "text", "text": "Je cherche le client puis ses devis."}]}})
    _call("mcp__odoo__search_records", {"model": "res.partner", "domain": [["name", "ilike", "Dupont"]]}, a1, "[{\"id\": 7}]")
    _call("mcp__odoo__search_records", {"model": "sale.order", "domain": [["partner_id", "=", 7]]}, a1, "[{\"id\": 12}]")
    n1 = _agent_start("Explore", "Lire le détail du devis S00012", parent=a1)
    _call("mcp__odoo__get_record", {"model": "sale.order", "record_id": 12}, n1, "{\"amount_total\": 3200}")
    _agent_end(n1, "S00012 : 3 200 € HT, brouillon.", parent=a1)
    _call("WebFetch", {"url": "https://example.com/tarifs"}, a2, "Tarifs 2026")
    _call("Read", {"file_path": "grille-tarifaire.xlsx"}, a2, "…")
    _agent_end(a2, "Tarif unitaire : 1 066 € HT.")
    _agent_end(a1, "Deux devis trouvés, dont S00012 en brouillon.")
    time.sleep(0.3)


FAKE_TRIGGERS = {
    "list": {"data": [{"id": "trig_1", "name": "Rapport hebdo", "cron_expression": "0 8 * * 1", "enabled": True,
                       "next_run_at": "2026-10-05T08:05:00Z", "created_kind": "cowork_task", "api_token_hint": "SECRET_HINT",
                       "job_config": {"token": "SECRET_JOB"}, "bound_device": {"display_name": "LAPTOP"},
                       "derived_state": {"model": "claude-sonnet-5", "folders": ["C:\\Tarifs"], "prompt": "Produire le rapport."}}],
             "has_more": False},
    "run": {"id": "session_run_1", "status": "running"},
    "update": {"id": "trig_1", "enabled": False},
    "list_runs": {"data": [{"id": "cse_1", "title": "Rapport du lundi", "status": "succeeded", "created_at": "2026-09-28T08:05:00Z"}]},
}


def relay_turn(params: dict):
    """What Claude Code does for the console's RemoteTrigger relay."""
    out({"type": "system", "subtype": "init", "session_id": SESSION, "model": "haiku", "tools": ["RemoteTrigger", "ToolSearch"],
         "permissionMode": MODE, "mcp_servers": []})
    if "NOLOGIN" in json.dumps(params):
        out({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"})
        return
    ok, _ = use_tool("RemoteTrigger", params)
    if ok:
        request({"subtype": "hook_callback", "callback_id": "relay_capture", "input": {
            "hook_event_name": "PostToolUse", "tool_name": "RemoteTrigger", "tool_input": params,
            "tool_response": "HTTP 200\n" + json.dumps(FAKE_TRIGGERS.get(params.get("action"), {}))}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "fait" if ok else "refusé"})


def turn(text: str):
    if text.strip().startswith("{") and '"action"' in text:
        return relay_turn(json.loads(text))
    out({"type": "system", "subtype": "init", "session_id": SESSION, "model": "fake-model",
         "tools": ["Read", "Write", "Bash"], "permissionMode": MODE,
         "mcp_servers": [{"name": "odoo", "status": "connected"}]})
    # like the real CLI: the plan limits ride on every response
    out({"type": "rate_limit_event", "session_id": SESSION, "rate_limit_info": {
        "status": "allowed", "resetsAt": int(time.time()) + 7200, "rateLimitType": "five_hour", "isUsingOverage": False,
        "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled",
        "unifiedWindows": {"five_hour": {"utilization": 0.25, "resetsAt": int(time.time()) + 7200},
                           "seven_day": {"utilization": 0.6, "resetsAt": int(time.time()) + 3 * 86400}}}})
    lines, error = [], False
    for raw in text.splitlines():
        parts = raw.strip().split(" ", 2)
        cmd = parts[0] if parts else ""
        if cmd == "TOOL":
            name, inp = parts[1], json.loads(parts[2]) if len(parts) > 2 else {}
            tid = f"toolu_{uuid.uuid4().hex[:8]}"
            out({"type": "assistant", "parent_tool_use_id": None,
                 "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
            ok, why = use_tool(name, inp)
            out({"type": "user", "parent_tool_use_id": None, "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "is_error": not ok, "content": why}]}})
            lines.append(f"{name}:{'ok' if ok else 'refus'}")
        elif cmd == "ASK":
            q = {"questions": [{"question": "Quelle couleur ?", "header": "Couleur", "multiSelect": False,
                                "options": [{"label": "Bleu", "description": ""}, {"label": "Rouge", "description": ""}]}]}
            perm = request({"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": q})
            lines.append("réponses=" + json.dumps((perm.get("updatedInput") or {}).get("answers"), ensure_ascii=False))
        elif cmd == "SLEEP":
            time.sleep(float(parts[1]))
        elif cmd == "ENV":
            lines.append("CONFIG_DIR=" + os.environ.get("CLAUDE_CONFIG_DIR", "(aucun)"))
            lines.append("API_KEY=" + ("présente" if os.environ.get("ANTHROPIC_API_KEY") else "absente"))
        elif cmd == "ARGS":
            lines.append("ARGV=" + json.dumps(ARGS, ensure_ascii=False))
        elif cmd == "TEAM":
            path = opt("--agents")
            agents = json.load(open(path, encoding="utf-8")) if path else {}
            lines.append("AGENTS=" + json.dumps({k: v.get("model") for k, v in agents.items()}))
            lines.append("SUBMODEL=" + os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL", "(aucun)"))
            lines.append("TOOLS=" + (opt("--tools") or "(tous)"))
        elif cmd == "FAIL":
            error = True
        elif cmd == "BG":  # BG <seconds>: launch a background sub-agent that reports after this turn
            task_id = f"bg_{uuid.uuid4().hex[:6]}"
            out({"type": "system", "subtype": "task_started", "task_id": task_id, "description": "inventaire",
                 "is_backgrounded": True, "session_id": SESSION})
            out({"type": "system", "subtype": "background_tasks_changed", "session_id": SESSION,
                 "tasks": [{"task_id": task_id, "task_type": "local_agent", "description": "inventaire"},
                           {"task_id": "watch", "task_type": "monitor", "description": "veille", "ambient": True}]})
            BACKGROUND.append((float(parts[1]) if len(parts) > 1 else 1.0, task_id))
            lines.append("sous-agent lancé en arrière-plan")
        elif cmd == "LIMIT":  # LIMIT <five_hour used> <status>: the session window is reached
            out({"type": "rate_limit_event", "session_id": SESSION, "rate_limit_info": {
                "status": parts[2] if len(parts) > 2 else "allowed", "rateLimitType": "five_hour",
                "utilization": float(parts[1]), "resetsAt": int(time.time()) + 600}})
        elif cmd == "FILES":
            lines.append(files())
        elif cmd == "DEMO":
            demo()
            lines.append(DEMO_TEXT)
        elif raw.strip():
            lines.append("écho:" + raw.strip())
    result = "\n".join(lines) or "rien"
    for chunk in (result[:5], result[5:]):
        if chunk:
            out({"type": "stream_event", "parent_tool_use_id": None,
                 "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk}}})
    out({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": result}]}})
    out({"type": "result", "subtype": "success", "is_error": error, "result": result, "num_turns": 1,
         "total_cost_usd": 0.01, "duration_ms": 5, "session_id": SESSION,
         "usage": {"input_tokens": 10, "output_tokens": 5}})
    if BACKGROUND:
        background_work(*BACKGROUND.pop())


BACKGROUND: list = []


def background_work(seconds: float, task_id: str):
    """A background sub-agent still working after the lead's turn ended (team mode), then the lead
    taking over when it reports back, as the real CLI does."""
    time.sleep(seconds)
    ok, why = use_tool("Read", {"file_path": "rapport.md"})
    out({"type": "system", "subtype": "background_tasks_changed", "tasks": [], "session_id": SESSION})
    out({"type": "system", "subtype": "task_notification", "task_id": task_id, "status": "completed",
         "output_file": "", "summary": "inventaire", "session_id": SESSION})
    text = f"Rapport du sous-agent : Read:{'ok' if ok else 'refus'}" + ("" if ok else f" ({why[:40]})")
    out({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": text}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": 1,
         "total_cost_usd": 0.01, "duration_ms": 5, "session_id": SESSION, "usage": {"input_tokens": 1, "output_tokens": 1}})


def main():
    if "--input-format" not in ARGS and "-p" in ARGS:  # one-shot prompt, as used for the limits probe
        turn(ARGS[ARGS.index("-p") + 1])
        return 0
    while True:
        m = read()
        if m is None:
            return 0
        if m.get("type") == "control_request" and m["request"].get("subtype") == "initialize":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": m["request_id"], "response": {
                "commands": [{"name": "deep-research", "description": "Recherche approfondie", "argumentHint": ""}],
                "agents": [{"name": "claude", "description": "généraliste"}],
                "models": [{"value": "default", "displayName": "Défaut", "resolvedModel": "fake-model"}],
                "account": {"tokenSource": "claude.ai", "email": "test@example.com"}}}})
        elif m.get("type") == "control_request" and m["request"].get("subtype") == "mcp_status":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": m["request_id"],
                 "response": {"mcpServers": [{"name": "odoo", "status": "connected", "config": {"type": "stdio", "command": "odoo-mcp", "env": {"SECRET": "x"}}}]}}})
        elif m.get("type") == "user":
            turn(m["message"]["content"])


if __name__ == "__main__":
    sys.exit(main())
