"""Stand-in for `claude -p` speaking the stream-json control protocol.

The user message drives the scenario, one directive per line:
  TOOL <name> <json input>   call a tool (PreToolUse hook, then permission check)
  DO <name> <json input>     same with the call's id (as the real CLI), and Write / Edit / MultiEdit really write
  PAR <json [[name, input]…]> several DO calls in one message, their results together (parallel calls)
  DO_SANS_ID <name> <json>   DO without giving the call's id to the hook (older CLI)
  ASK                        AskUserQuestion round-trip
  ASK2                       AskUserQuestion with two questions, long option descriptions
  SLEEP <seconds>
  ENV                        report CLAUDE_CONFIG_DIR in the result
  ARGS                       report argv in the result
  FAIL                       finish the turn with an error result
  FILES                      create an image, a PDF and a CSV, then answer with previews
  IMAGES                     write two images in images/, then name them without their folder
  SHOW <path> | <path>…      call the console's own "afficher" tool (in-process MCP server)
  MAIL <subject>             a connector returns a mail (Office 365 format); BIGMAIL: too long, saved to a file
  RESULT <json arguments>    call the console's "afficher_resultat" tool
  PRESENT <json arguments>   call the console's "presenter" tool (a display made of blocks)
  PROPOSE <json arguments>   call the console's "proposer" tool (an action or a routine for the project)
  FIND <query>               call the console's "chercher_documents" tool (the local document index)
  VITRINE [ou]               a display with every kind of block (images, results, table, chart…)
  CTX <tokens>               the session's context now weighs that much (usage of the next calls)
  /compact                   compact the context, as Claude Code does
Anything else is echoed back.
"""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")  # the real CLI (Node) speaks UTF-8 on its pipes
sys.stdin.reconfigure(encoding="utf-8")
ARGS = sys.argv[1:]
_pending: list[dict] = []
SDK_SERVERS: dict[str, list[str]] = {}  # SDK-hosted MCP servers dialed at start -> tool names
# Like the real CLI: the context grows with each turn, total_cost_usd and modelUsage add up over the process.
STATE = {"ctx": 20000, "cost": 0.0, "usage": {}}


def spend(model="fake-model", read=0, written=500, output=5, cost=0.01, window=1_000_000):
    u = STATE["usage"].setdefault(model, {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
                                          "cacheCreationInputTokens": 0, "costUSD": 0.0, "contextWindow": window})
    u["inputTokens"] += 10
    u["outputTokens"] += output
    u["cacheReadInputTokens"] += read
    u["cacheCreationInputTokens"] += written
    u["costUSD"] = round(u["costUSD"] + cost, 6)
    STATE["cost"] = round(STATE["cost"] + cost, 6)


def log(entry: dict, agent: str | None = None):
    """Like the real CLI: the session's transcript, one file per sub-agent next to it."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    if not base or "--no-session-persistence" in ARGS:
        return
    d = os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
    if agent:
        d = os.path.join(d, SESSION, "subagents")
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with open(os.path.join(d, f"agent-{agent}.jsonl" if agent else f"{SESSION}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": stamp, "sessionId": SESSION, **({"isSidechain": True} if agent else {}), **entry},
                           ensure_ascii=False) + "\n")


_n = 0


def opt(name, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


MODE = opt("--permission-mode", "manual")
ALLOWED = [x for x in (opt("--allowedTools", "") or "").split(",") if x]
SESSION = opt("--resume") or opt("--session-id") or str(uuid.uuid4())


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    if obj.get("type") == "assistant" and not obj.get("parent_tool_use_id"):
        log({"type": "assistant", "message": obj.get("message") or {}})


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


def use_tool(name: str, inp: dict, tool_use_id: str | None = None) -> tuple[bool, str]:
    ids = {"tool_use_id": tool_use_id} if tool_use_id else {}
    hook = request({"subtype": "hook_callback", "callback_id": "console_pretool", **ids,
                    "input": {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": inp,
                              "session_id": SESSION, **ids}})
    if hook.get("_eof"):  # what the real CLI tells the model when its host stopped answering
        return False, "The user doesn't want to take this action right now. STOP what you are doing and wait for the user to tell you how to proceed."
    decision = (hook.get("hookSpecificOutput") or {}).get("permissionDecision")
    if decision == "deny":
        return False, (hook["hookSpecificOutput"].get("permissionDecisionReason") or "hook deny")
    if decision != "allow" and MODE != "bypassPermissions" and name not in ALLOWED:
        if MODE == "dontAsk":
            return False, "dontAsk"
        perm = request({"subtype": "can_use_tool", "tool_name": name, "input": inp, **ids})
        if perm.get("behavior") != "allow":
            return False, perm.get("message", "deny")
    return True, "ok"


def apply_tool(name: str, inp: dict) -> tuple[bool, str]:
    """What the real file tools do once allowed."""
    path = inp.get("file_path") or ""
    try:
        if name == "Write":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(inp.get("content", ""))
            return True, f"File created successfully at: {path}"
        if name in ("Edit", "MultiEdit"):
            with open(path, encoding="utf-8", newline="") as f:
                text = f.read()
            for e in inp.get("edits") or [inp]:
                if e["old_string"] not in text:
                    return False, "String to replace not found in file."
                text = text.replace(e["old_string"], e["new_string"], 1)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            return True, f"The file {path} has been updated."
    except OSError as exc:
        return False, str(exc)
    return True, "ok"


def do_tools(calls: list, give_ids: bool = True) -> list[str]:
    """Tool calls of one assistant message, each checked then run, their results in one message."""
    ids = [f"toolu_{uuid.uuid4().hex[:8]}" for _ in calls]
    out({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": i, "name": n, "input": inp} for i, (n, inp) in zip(ids, calls)]}})
    results, lines = [], []
    for i, (name, inp) in zip(ids, calls):
        ok, why = use_tool(name, inp, i if give_ids else None)
        if ok:
            ok, why = apply_tool(name, inp)
        results.append({"type": "tool_result", "tool_use_id": i, "is_error": not ok, "content": why})
        lines.append(f"{name}:{'ok' if ok else 'refus'}")
    out({"type": "user", "parent_tool_use_id": None, "message": {"content": results}})
    return lines


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
         "message": {"id": f"msg_{uuid.uuid4().hex[:8]}", "model": "fake-model",
                     "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                     "usage": {"input_tokens": 10, "cache_read_input_tokens": STATE["ctx"], "cache_creation_input_tokens": 200, "output_tokens": 20}}})
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



def images():
    import shutil
    folder = os.path.join(os.getcwd(), "images")
    os.makedirs(folder, exist_ok=True)
    for name, src in (("logo-jarvis.png", "icon-192.png"), ("banniere.png", "icon-maskable-512.png")):
        dst = os.path.join(folder, name)
        shutil.copy(os.path.join(os.path.dirname(__file__), "..", "static", "img", src), dst)
        _call("Write", {"file_path": dst, "content": "(image)"})
    return "J'ai généré deux images dans ton projet :\n\n- **logo-jarvis.png** (192 × 192)\n- `banniere.png` (512 × 512)"


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


def dial_sdk_servers(names):
    """Like the real CLI: initialize each SDK-hosted server, then list its tools."""
    for name in names or []:
        init = request({"subtype": "mcp_message", "server_name": name, "message": {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "1"}}}})
        if not (init.get("mcp_response") or {}).get("result"):
            continue
        request({"subtype": "mcp_message", "server_name": name,
                 "message": {"jsonrpc": "2.0", "method": "notifications/initialized"}})
        tools = request({"subtype": "mcp_message", "server_name": name,
                         "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}})
        SDK_SERVERS[name] = [t["name"] for t in ((tools.get("mcp_response") or {}).get("result") or {}).get("tools") or []]


def background_team(delay: float):
    """Two background sub-agents (team mode): their tool calls only reach their transcripts, as with the
    real CLI, whose stream forwards nothing of them."""
    crew = (("eclaireur", "Inventaire du firmware", "claude-haiku-4-5-20251001",
             [("mcp__codegraph__codegraph_explore", {"query": "onboarding firmware"}), ("Read", {"file_path": "main.c"}),
              ("Grep", {"pattern": "firmware", "path": "."})]),
            ("executant", "Étape choix du boîtier", "claude-sonnet-5-5",
             [("Read", {"file_path": "Onboarding.kt"}), ("Edit", {"file_path": "Onboarding.kt"}),
              ("Bash", {"command": "gradlew test"})]))
    for kind, desc, model, tools in crew:
        tid = f"toolu_{uuid.uuid4().hex[:8]}"
        inp = {"subagent_type": kind, "description": desc, "prompt": f"{desc}.", "run_in_background": True}
        out({"type": "assistant", "parent_tool_use_id": None, "message": {"id": f"msg_{uuid.uuid4().hex[:8]}", "model": "fake-model",
             "content": [{"type": "tool_use", "id": tid, "name": "Agent", "input": inp}]}})
        out({"type": "user", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": False, "content": "Agent lancé en arrière-plan."}]}})
        agent = uuid.uuid4().hex[:16]
        d = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", "."), "projects", re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()),
                         SESSION, "subagents")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"agent-{agent}.meta.json"), "w", encoding="utf-8") as f:
            json.dump({"agentType": kind, "description": desc, "toolUseId": tid, "requestShape": "background"}, f)
        for name, tin in tools:
            time.sleep(delay)
            log({"type": "assistant", "message": {"id": f"msg_{uuid.uuid4().hex[:8]}", "model": model,
                 "content": [{"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:8]}", "name": name, "input": tin}],
                 "usage": {"input_tokens": 10, "cache_read_input_tokens": 30000, "cache_creation_input_tokens": 900, "output_tokens": 60}}},
                agent)
    return "2 sous-agents lancés en arrière-plan"


def showcase(where: str) -> str:
    images()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 90"><rect x="4" y="20" width="90" height="50" rx="8" '
           'fill="none" stroke="#3987e5" stroke-width="2"/><text x="49" y="50" font-size="13" text-anchor="middle" '
           'fill="#3987e5">Caméras</text><path d="M94 45h60" stroke="#888" stroke-width="2"/><rect x="154" y="20" width="90" '
           'height="50" rx="8" fill="none" stroke="#199e70" stroke-width="2"/><text x="199" y="50" font-size="13" '
           'text-anchor="middle" fill="#199e70">NVR</text><path d="M244 45h40" stroke="#888" stroke-width="2"/>'
           '<circle cx="300" cy="45" r="14" fill="none" stroke="#c98500" stroke-width="2"/></svg>')
    return console_tool("presenter", {"titre": "Point télésurveillance — client Dupont", "ou": where, "blocs": [
        {"type": "chiffres", "elements": [
            {"libelle": "CA septembre", "valeur": "48 200 €", "evolution": "+12 %", "detail": "vs août"},
            {"libelle": "Devis ouverts", "valeur": 7, "evolution": "-2"},
            {"libelle": "Délai moyen", "valeur": "3,4 j", "evolution": "+0,3 j"}]},
        {"type": "texte", "texte": "Le client demande **3 postes** de télésurveillance. Voici les éléments utiles."},
        {"type": "images", "titre": "Visuels", "images": [
            {"source": "images/logo-jarvis.png", "legende": "Logo"}, {"source": "images/banniere.png", "legende": "Bannière"},
            {"source": "https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png",
             "legende": "Image du web"}]},
        {"type": "resultats", "titre": "Recherche : caméras dôme 4 MP", "elements": [
            {"titre": "Caméra dôme IP 4 MP — fiche technique", "url": "https://example.com/camera-dome",
             "extrait": "Vision nocturne 30 m, IP67, PoE, compression H.265.", "source": "example.com"},
            {"titre": "Comparatif 2026 des caméras de surveillance", "url": "https://www.example.org/comparatif",
             "extrait": "Douze modèles testés en conditions réelles.", "source": "example.org"}]},
        {"type": "graphique", "titre": "Ventes par trimestre", "forme": "barres", "unite": "k€",
         "etiquettes": ["T1", "T2", "T3", "T4"],
         "series": [{"nom": "2025", "valeurs": [32, 41, 38, 45]}, {"nom": "2026", "valeurs": [36, 44, 48, None]}]},
        {"type": "graphique", "titre": "Appels reçus", "forme": "courbe", "etiquettes": ["lun", "mar", "mer", "jeu", "ven"],
         "series": [{"nom": "Alarmes", "valeurs": [12, 18, 9, 22, 15]}, {"nom": "Levées de doute", "valeurs": [5, 7, 4, 9, 6]}]},
        {"type": "graphique", "titre": "Répartition du parc", "forme": "secteurs",
         "etiquettes": ["Caméras", "Détecteurs", "Sirènes", "Claviers"], "series": [{"nom": "Parc", "valeurs": [48, 31, 12, 9]}]},
        {"type": "tableau", "titre": "Tarifs", "colonnes": ["Référence", "Désignation", "Prix HT"],
         "lignes": [["CAM-01", "Caméra dôme 4 MP", 189], ["NVR-08", "Enregistreur 8 voies", 412.5], ["SIR-02", "Sirène extérieure", 96]]},
        {"type": "fiche", "titre": "Client", "champs": [{"libelle": "Nom", "valeur": "Dupont SARL"},
                                                         {"libelle": "Contact", "valeur": "M. Dupont"},
                                                         {"libelle": "Devis", "valeur": "S00012 — 3 200 € HT"}],
         "lien": "https://example.com/odoo/sales/12"},
        {"type": "chronologie", "titre": "Agenda", "elements": [
            {"quand": "2026-10-02T09:00", "titre": "Visite technique", "texte": "Sur site, 1 h"},
            {"quand": "2026-10-06T14:30", "titre": "Rendez-vous signature"}]},
        {"type": "progression", "valeur": 65, "texte": "Installation : 2 postes sur 3"},
        {"type": "schema", "titre": "Architecture", "svg": svg},
        {"type": "choix", "question": "Quelle offre proposer ?", "options": ["Standard", "Premium", "Sur mesure"]},
        {"type": "actions", "boutons": [{"libelle": "Préparer le devis", "message": "Prépare le devis Premium."},
                                        {"libelle": "Ouvrir la fiche", "url": "https://example.com/odoo/sales/12"}]},
    ]})


def show(items):
    """The model calls mcp__jarvis__afficher: PreToolUse hook, then tools/call to the in-process server."""
    return console_tool("afficher", {"fichiers": items})


def console_tool(tool: str, inp: dict) -> str:
    name = f"mcp__jarvis__{tool}"
    tid = f"toolu_{uuid.uuid4().hex[:8]}"
    out({"type": "assistant", "parent_tool_use_id": None,
         "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
    if tool not in SDK_SERVERS.get("jarvis", []):
        text, err = f"No such tool available: {name}", True
    else:
        ok, text = use_tool(name, inp)
        err = not ok
        if ok:
            r = request({"subtype": "mcp_message", "server_name": "jarvis", "message": {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": inp}}})
            res = (r.get("mcp_response") or {}).get("result") or {}
            text = " ".join(c.get("text", "") for c in res.get("content") or [])
            err = bool(res.get("isError"))
    out({"type": "user", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": text}]}})
    return ("affichage refusé : " if err else "") + text


def mail(subject: str, big: bool = False) -> str:
    """A connector's tool returns a mail, as the Office 365 one does (read_resource); a long one is
    saved by the CLI in the session's tool-results folder, the model only gets its path."""
    m = {"id": "AAMk" + uuid.uuid4().hex, "subject": subject, "bodyPreview": "Bonjour, voici le devis.",
         "body": {"contentType": "html", "content": '<html><head><style>.t{color:#c00}</style></head><body>'
                  '<p class="t">Bonjour,</p><p style="font-weight:bold">Voici le devis.</p>'
                  '<img src="https://pixel.example.com/open.gif"><img src="cid:logo@x"></body></html>'},
         "sender": {"name": "Alice Martin", "address": "alice@example.com"},
         "toRecipients": [{"name": "Roger", "address": "roger@example.com"}], "ccRecipients": [],
         "receivedDateTime": "2026-09-30T08:15:00Z", "hasAttachments": True,
         "attachments": [{"name": "devis.pdf", "size": 48213, "isInline": False}, {"name": "logo.png", "isInline": True}],
         "webLink": "https://outlook.office365.com/owa/?ItemID=AAMk"}
    name, inp = "mcp__o365__read_resource", {"uri": "mail:///messages/AAMk"}
    tid = f"toolu_{uuid.uuid4().hex[:8]}"
    out({"type": "assistant", "parent_tool_use_id": None,
         "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
    text = json.dumps(m, ensure_ascii=False)
    if big:
        d = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", "."), "projects", re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()),
                         SESSION, "tool-results")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"mcp-o365-read_resource-{int(time.time() * 1000)}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        text = (f"Error: result ({len(text)} characters across 1 line) exceeds maximum allowed tokens. Output has been "
                f"saved to {path}.\nFormat: Plain text\nUse offset and limit parameters to read specific portions of the file")
    result = {"type": "user", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": big, "content": [{"type": "text", "text": text}]}]}}
    out(result)
    log(result)
    return f"mail lu : {subject}"


def title_turn(text: str):
    """The console asks for a title (one-shot): the first words of the first request, dressed up the
    way a model might answer (quotes, "Titre :", final period)."""
    extracts = text.split("<extraits>", 1)[1]
    first = extracts.split("Première demande :", 1)[-1].strip().splitlines()[0] if extracts.strip() else ""
    if "TITRE_ECHEC" in first:
        out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Erreur simulée"})
        return
    out({"type": "rate_limit_event", "session_id": SESSION, "rate_limit_info": {
        "status": "allowed", "resetsAt": int(time.time()) + 7200, "rateLimitType": "five_hour", "utilization": 0.3}})
    words = " ".join(first.split()[:5])
    out({"type": "result", "subtype": "success", "is_error": False, "result": f"Titre : « {words} ».", "num_turns": 1,
         "session_id": SESSION, "usage": {"input_tokens": len(text) // 4, "output_tokens": 6}})


def turn(text: str):
    if text.strip().startswith("{") and '"action"' in text:
        return relay_turn(json.loads(text))
    if text.strip() == "/compact":
        pre, STATE["ctx"] = STATE["ctx"], 12000
        out({"type": "system", "subtype": "compact_boundary", "session_id": SESSION,
             "compact_metadata": {"trigger": "manual", "pre_tokens": pre, "post_tokens": 12000}})
        spend(read=pre, written=12000, output=800)
        out({"type": "result", "subtype": "success", "is_error": False, "result": "", "num_turns": 0,
             "total_cost_usd": STATE["cost"], "modelUsage": STATE["usage"], "duration_ms": 5, "session_id": SESSION,
             "usage": {"input_tokens": 10, "output_tokens": 800}})
        return
    if "<extraits>" in text:
        return title_turn(text)
    log({"type": "attachment", "attachment": {"type": "mcp_instructions_delta", "addedNames": ["odoo", "codegraph"]}})
    out({"type": "system", "subtype": "init", "session_id": SESSION, "model": "fake-model",
         "tools": ["Read", "Write", "Bash", *(f"mcp__{s}__{t}" for s, ts in SDK_SERVERS.items() for t in ts)],
         "permissionMode": MODE, "mcp_servers": [{"name": "odoo", "status": "connected"},
                                                 *({"name": s, "status": "connected", "source": "sdk"} for s in SDK_SERVERS)]})
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
        elif cmd in ("DO", "DO_SANS_ID"):
            lines += do_tools([(parts[1], json.loads(parts[2]) if len(parts) > 2 else {})], give_ids=cmd == "DO")
        elif cmd == "PAR":
            lines += do_tools([tuple(c) for c in json.loads(raw.strip()[4:])])
        elif cmd in ("ASK", "ASK2"):
            q = {"questions": [{"question": "Quelle couleur ?", "header": "Couleur", "multiSelect": False,
                                "options": [{"label": "Bleu", "description": ""}, {"label": "Rouge", "description": ""}]}]}
            if cmd == "ASK2":
                long = "Une description d'option assez longue pour occuper plusieurs lignes dans une fenêtre étroite. " * 2
                q = {"questions": [
                    {"question": "Où et quand la routine doit-elle tourner ?", "header": "Routine", "multiSelect": False,
                     "options": [{"label": f"Option {i}", "description": long} for i in (1, 2, 3)]},
                    {"question": "Que faire du serveur JS ?", "header": "Nettoyage", "multiSelect": False,
                     "options": [{"label": "Les supprimer", "description": long}, {"label": "Les garder", "description": long}]}]}
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
        elif cmd == "AGENTS":
            lines.append(background_team(float(parts[1]) if len(parts) > 1 else 0.8))
        elif cmd == "CTX":
            STATE["ctx"] = int(parts[1])
        elif cmd == "FILES":
            lines.append(files())
        elif cmd == "IMAGES":
            lines.append(images())
        elif cmd == "SHOW":
            lines.append(show([x.strip() for x in raw.strip()[4:].split("|") if x.strip()]))
        elif cmd in ("MAIL", "BIGMAIL"):
            lines.append(mail(raw.strip()[len(cmd):].strip() or "Devis", big=cmd == "BIGMAIL"))
        elif cmd == "PRESENT":
            lines.append(console_tool("presenter", json.loads(raw.strip()[7:].strip() or "{}")))
        elif cmd == "PROPOSE":
            lines.append(console_tool("proposer", json.loads(raw.strip()[7:].strip() or "{}")))
        elif cmd == "FIND":
            lines.append(console_tool("chercher_documents", {"requete": raw.strip()[4:].strip()}))
        elif cmd == "VITRINE":
            lines.append(showcase(parts[1] if len(parts) > 1 else "conversation"))
        elif cmd == "RESULT":
            lines.append(console_tool("afficher_resultat", json.loads(raw.strip()[6:].strip() or "{}")))
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
    STATE["ctx"] += 1500
    call = {"input_tokens": 10, "cache_read_input_tokens": STATE["ctx"] - 500, "cache_creation_input_tokens": 500, "output_tokens": 5}
    out({"type": "assistant", "parent_tool_use_id": None,
         "message": {"id": f"msg_{uuid.uuid4().hex[:8]}", "model": "fake-model", "content": [{"type": "text", "text": result}], "usage": call}})
    spend(read=STATE["ctx"] - 500)
    if "TEAM" in text or "BG" in text:  # sub-agents on cheaper models
        spend("claude-haiku-4-5-20251001", read=40000, written=8000, output=300, cost=0.004, window=200000)
    out({"type": "result", "subtype": "success", "is_error": error, "result": result, "num_turns": 1,
         "total_cost_usd": STATE["cost"], "modelUsage": STATE["usage"], "duration_ms": 5, "session_id": SESSION,
         "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": call["cache_read_input_tokens"],
                   "cache_creation_input_tokens": call["cache_creation_input_tokens"]}})
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
            dial_sdk_servers(m["request"].get("sdkMcpServers"))
        elif m.get("type") == "control_request" and m["request"].get("subtype") == "mcp_status":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": m["request_id"],
                 "response": {"mcpServers": [{"name": "odoo", "status": "connected", "config": {"type": "stdio", "command": "odoo-mcp", "env": {"SECRET": "x"}}}]}}})
        elif m.get("type") == "user":
            turn(m["message"]["content"])


if __name__ == "__main__":
    sys.exit(main())
