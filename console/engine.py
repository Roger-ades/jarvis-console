"""Task engine: queue, worker pool, Claude Code processes, human approvals.

Each task runs `claude -p` with streaming JSON on both stdin and stdout. The
console registers a PreToolUse hook and answers the CLI's permission prompts
itself over that control channel, so every tool call (subagents included) is
checked by the task's Policy and, when the policy says so, parked until the
user approves or refuses it in the task window.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import activity as activity_mod
from . import attachments as att
from . import changes as changes_mod
from . import display as display_mod
from . import claude_cli, cloud, content, library, mcp, presence
from . import project_tools
from . import regard as regard_mod
from . import results as results_mod
from .config import (Config, ConfigStore, InputConstraint, Preset, Profile, ProjectRule, ToolRule, dump,
                     expand_path, trusted_url, web_domain)
from .permissions import (INTERACTIVE_TOOLS, Policy, cli_permission_args, is_mcp, norm, parse_rule,
                          policy_context, project_rule_problem, suggest_rules, summarize_target, within)
from . import team as team_mod
from .routines import Routine
from .store import Store

ACTIVE = {"queued", "running", "awaiting"}
TERMINAL = {"done", "error", "cancelled", "interrupted"}
HOOK_ID = "console_pretool"
# Keeping a session's prompt cache warm: the cache lasts about an hour on Claude plans and each read
# restarts it, so a throwaway copy of the session reads it every 50 minutes while the session is idle.
WARM_EVERY = 50 * 60
WARM_MAX_HOURS = 12
WARM_PROMPT = "Maintien du cache de la console : réponds seulement « ok », sans utiliser d'outil."
# The console's own MCP server, hosted in this process (the CLI reaches it over the control protocol).
CONSOLE_MCP = "jarvis"
# Its tools stay in Claude's prompt, never deferred behind tool search: a deferred tool shows only its name,
# and a model that calls it without loading it guesses the parameters (afficher called with « content »).
ALWAYS_LOAD = {"anthropic/alwaysLoad": True}
SHOW_TOOL = f"mcp__{CONSOLE_MCP}__afficher"
SHOW_SPEC = {
    "_meta": ALWAYS_LOAD,
    "name": "afficher",
    "description": (
        "Ouvre des fichiers ou des pages web dans des fenêtres d'aperçu de la console JARVIS, que l'utilisateur "
        "voit à l'écran (images, PDF, HTML, texte, Markdown, CSV, JSON… ; les autres types proposent de s'ouvrir "
        "avec leur application). À utiliser dès que l'utilisateur demande d'afficher, de montrer, d'ouvrir ou de "
        "voir un fichier, et pour lui présenter un fichier que tu viens de créer quand il veut le voir. Chemins "
        "absolus, ou relatifs au dossier de travail ; un simple nom de fichier est cherché dans les dossiers de la "
        "tâche. Une adresse https:// d'un domaine approuvé par l'utilisateur s'ouvre dans un aperçu web ; pour les "
        "autres, la console lui propose de l'ouvrir et il décide. Pour montrer le résultat d'un outil déjà reçu "
        "(un mail, un enregistrement…), utilise plutôt afficher_resultat ; pour composer un affichage (galerie, "
        "résultats de recherche, tableau, graphique, fiche, choix à cliquer…), presenter."),
    "inputSchema": {
        "type": "object",
        "properties": {"fichiers": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12,
                                    "description": "Chemins des fichiers, ou adresses https://, à afficher."}},
        "required": ["fichiers"],
    },
}
RESULT_TOOL = f"mcp__{CONSOLE_MCP}__afficher_resultat"
RESULT_SPEC = {
    "_meta": ALWAYS_LOAD,
    "name": "afficher_resultat",
    "description": (
        "Montre à l'utilisateur, dans une fenêtre de la console JARVIS, le résultat d'un outil que tu as déjà reçu "
        "(un mail lu avec le connecteur Office 365, un enregistrement Odoo, une page renvoyée par un connecteur…), "
        "tel quel : ne recopie pas son contenu, désigne-le seulement. Un mail s'affiche avec sa mise en forme, son "
        "expéditeur, ses destinataires et sa date ; du HTML comme une page ; le reste en JSON ou en texte. Marche aussi "
        "pour un résultat trop long que tu n'as pas pu lire en entier. Sans paramètre : le dernier résultat reçu."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "outil": {"type": "string", "description": "Nom, ou partie du nom, de l'outil qui a donné le résultat "
                                                       "(ex. « read_resource », « get_record »)."},
            "contient": {"type": "string", "description": "Un texte que contient le résultat voulu, ou l'élément voulu "
                                                          "d'une liste (sujet ou identifiant d'un mail, nom d'un devis…)."},
            "rang": {"type": "integer", "minimum": 1, "maximum": 50,
                     "description": "1 = le plus récent des résultats qui correspondent (par défaut), 2 = le précédent…"},
            "id": {"type": "string", "description": "Identifiant de l'appel d'outil (toolu_…), si tu le connais."},
        },
    },
}
PRESENT_TOOL = f"mcp__{CONSOLE_MCP}__presenter"
PRESENT_SPEC = {"_meta": ALWAYS_LOAD, **display_mod.TOOL_SPEC}
PROPOSE_TOOL = f"mcp__{CONSOLE_MCP}__proposer"
PROPOSE_SPEC = {"_meta": ALWAYS_LOAD, **project_tools.PROPOSE_SPEC}
CONSOLE_TOOLS = {SHOW_TOOL, RESULT_TOOL, PRESENT_TOOL, PROPOSE_TOOL}
KEEP_CALLS = 60  # tool results kept in memory per task, for afficher_resultat


class TaskError(Exception):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


# ---------------------------------------------------------------- event bus

class Bus:
    """Thread-safe fan-out to SSE subscribers living on the asyncio loop."""

    def __init__(self):
        self._subs: set = set()
        self._lock = threading.Lock()

    def subscribe(self, loop) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10000)
        with self._lock:
            self._subs.add((loop, q))
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}

    def publish(self, kind: str, data):
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, (kind, data))
            except RuntimeError:  # loop closed
                self.unsubscribe(q)


def _offer(q: asyncio.Queue, item):
    if q.full():
        try:
            q.get_nowait()  # drop the oldest; the client resyncs from the store
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(item)


# ---------------------------------------------------------------- runtime state

@dataclass
class Approval:
    id: str
    request_id: str
    kind: str                 # hook | permission | question | plan | proposal
    tool: str
    input: dict
    reason: str
    created: float = field(default_factory=time.time)
    event: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None
    message: str = ""
    answers: dict | None = None
    by: str = "utilisateur"
    suggest: list = field(default_factory=list)  # rules for "toujours pour ce projet"
    _claim_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def claim(self, decision: str, message: str = "", by: str = "utilisateur", answers: dict | None = None) -> bool:
        """Take the decision once: the user, the watchdog and a cancel may race."""
        with self._claim_lock:
            if self.decision is not None:
                return False
            self.decision, self.message, self.by, self.answers = decision, message, by, answers
        self.event.set()
        return True

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "tool": self.tool, "reason": self.reason,
                "target": summarize_target(self.tool, self.input),
                "input": _clip_json(self.input, 20000), "created": self.created, "suggest": self.suggest}


@dataclass
class Run:
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    sent: int = 0
    results: int = 0
    approvals: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    awaiting_since: float | None = None
    awaiting_total: float = 0.0
    stdin_closed: bool = False
    stop_status: str | None = None
    stop_reason: str = ""
    files: list = field(default_factory=list)
    stderr: deque = field(default_factory=lambda: deque(maxlen=40))
    policy: Policy | None = None
    last_error: bool = False
    got_result: bool = False
    # background work of the CLI (team mode sub-agents, background shells): the session must stay
    # open and keep answering their permission checks after the lead's turn has ended
    background: set = field(default_factory=set)
    had_background: bool = False
    last_output: float = field(default_factory=time.time)
    # the CLI's total_cost_usd and modelUsage add up over the life of its process: the task's totals
    # are what earlier processes (earlier follow-ups) used plus the latest figures of this one
    cost_base: float = 0.0
    usage_base: dict = field(default_factory=dict)
    # files that Claude's file tools are about to write, as they are now (tool_use_id -> Snapshot)
    snaps: dict = field(default_factory=dict)


def _ktok(n: int) -> str:
    return f"{n / 1000:.1f} k".replace(".", ",") if n < 10000 else f"{round(n / 1000)} k"


USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "costUSD")


def _merge_usage(base: dict, cur: dict) -> dict:
    """Per-model usage of earlier processes plus the current one (modelUsage of the CLI's result)."""
    out = {m: dict(v) for m, v in (base or {}).items()}
    for model, u in (cur or {}).items():
        if not isinstance(u, dict):
            continue
        acc = out.setdefault(model, {})
        for k in USAGE_KEYS:
            acc[k] = round(acc.get(k, 0) + (u.get(k) or 0), 6) if k == "costUSD" else int(acc.get(k, 0) + (u.get(k) or 0))
        acc["contextWindow"] = max(int(acc.get("contextWindow") or 0), int(u.get("contextWindow") or 0))
    return out


def _clip_json(value, limit: int):
    s = json.dumps(value, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return value
    return {"_tronqué": True, "aperçu": s[:limit]}


def _clip(s: str, limit: int) -> str:
    s = s or ""
    return s if len(s) <= limit else s[:limit] + f"\n… ({len(s) - limit} caractères de plus)"


def _clean_title(text: str) -> str:
    """The title Claude proposed, without markdown, quotes, "Titre :" or a final period."""
    for line in str(text or "").splitlines():
        line = re.sub(r"^\s*(?:#+\s*|[-*•]\s+)", "", line)
        line = line.strip().strip("*_`").strip()
        line = re.sub(r"^(?:titre|title)\s*:\s*", "", line, flags=re.I).strip()
        line = re.sub(r"\s+", " ", line).strip("\"'«»“”‘’`*_ \u00a0\u202f.;:,!").strip()
        if line:
            return line if len(line) <= 80 else line[:80].rsplit(" ", 1)[0]
    return ""


def _title(prompt: str) -> str:
    line = prompt.strip().splitlines()[0] if prompt.strip() else "Tâche"
    line = line.lstrip("/").strip()
    if len(line) <= 60:
        return line
    cut = line[:60].rsplit(" ", 1)[0]
    return (cut or line[:60]) + "…"


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(c, ensure_ascii=False)[:500])
            else:
                parts.append(str(c))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False, default=str) if content is not None else ""


# ---------------------------------------------------------------- engine

class Engine:
    def __init__(self, cfg_store: ConfigStore, store: Store, data_dir: Path, port: int,
                 cli_command: list[str] | None = None, start_threads: bool = True):
        self.cfg_store = cfg_store
        self.store = store
        self.data_dir = Path(data_dir)
        self.runtime = self.data_dir / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        for leftover in self.runtime.glob("*"):
            leftover.unlink(missing_ok=True)
        self.port = port
        self.bus = Bus()
        self._cli_override = cli_command
        self._lock = threading.RLock()
        self._warm_checked = 0.0
        self._warming: set[str] = set()
        self._wake = threading.Event()
        self._stop = False
        self.tasks: dict[str, dict] = {}
        self.runs: dict[str, Run] = {}
        self.queue: list[str] = []
        self._seq: dict[str, int] = {}
        self.emergency = bool(store.kv_get("emergency_stop", False))
        self.probes: dict = store.kv_get("probes", {}) or {}
        self.limits: dict = store.kv_get("limits", {}) or {}  # plan usage limits, per profile
        self._limits_busy: set[str] = set()
        self.contents = content.ContentStore()  # pages served on the preview origin
        self._calls: dict[str, dict[str, dict]] = {}  # task -> recent tool calls and their results
        self._calls_lock = threading.Lock()
        self._displays: dict[str, dict[str, dict]] = {}  # task -> displays composed by Claude (tool "presenter")
        self._displays_lock = threading.Lock()
        self.changes = changes_mod.Changes(self.data_dir / "modifications")  # what Claude changed in files
        self._snaps_lock = threading.Lock()
        self.routines: dict[str, Routine] = {}
        for raw in store.kv_get("routines", []) or []:
            try:
                r = Routine.model_validate(raw)
                self.routines[r.id] = r
            except ValueError:
                continue
        self._recover()
        self._purge_old()
        self._schedule_routines(startup=True)
        if start_threads:
            threading.Thread(target=self._scheduler, name="scheduler", daemon=True).start()
            threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()
            threading.Thread(target=self._routine_loop, name="routines", daemon=True).start()
            if self.cfg.general.limits_on_start:
                threading.Thread(target=self._refresh_stale_limits, name="limits", daemon=True).start()

    # -------------------------------------------------------- helpers
    @property
    def cfg(self) -> Config:
        return self.cfg_store.config

    def cli(self) -> list[str] | None:
        if self._cli_override:
            return list(self._cli_override)
        p = claude_cli.find_cli(self.cfg.general.cli_path)
        return [p] if p else None

    def _live_profile(self, t: dict) -> dict:
        """The account as configured now: a color or a name changed later applies to old discussions too."""
        prof = self.cfg.profile(t.get("profile") or "")
        return {"color": prof.color, "profile_name": prof.name} if prof else {}

    def public(self, t: dict) -> dict:
        out = {k: v for k, v in t.items() if k not in ("spec", "queued_messages")}
        out.update(self._live_profile(t))
        out["queued_messages"] = len(t.get("queued_messages") or [])
        spec = t.get("spec") or {}
        pre = spec.get("preset") or {}
        out["preset_mode"] = pre.get("mode")
        out["resumable"] = bool(t.get("session_started"))
        return out

    def _save(self, t: dict, publish: bool = True):
        self.store.save_task(t)
        if publish:
            self.bus.publish("task", self.public(t))

    def _next_seq(self, tid: str) -> int:
        if tid not in self._seq:
            self._seq[tid] = self.store.last_seq(tid)
        self._seq[tid] += 1
        return self._seq[tid]

    def _event(self, tid: str, kind: str, data: dict, persist: bool = True):
        t = self.tasks.get(tid)
        ts = time.time()
        seq = None
        if persist and t is not None:
            size = len(json.dumps(data, ensure_ascii=False, default=str))
            limit = self.cfg.general.max_output_kb * 1024
            heavy = kind in ("text", "thinking", "tool", "tool_result")
            if heavy and t.get("output_bytes", 0) + size > limit:
                if not t.get("truncated"):
                    t["truncated"] = True
                    self._event(tid, "info", {"text": f"Sortie tronquée : limite de {self.cfg.general.max_output_kb} Ko atteinte (Configuration → Général)."})
                persist = False
            else:
                t["output_bytes"] = t.get("output_bytes", 0) + size
        if persist:
            seq = self._next_seq(tid)
            self.store.add_event(tid, seq, ts, kind, data)
        self.bus.publish("ev", {"task_id": tid, "seq": seq, "ts": ts, "kind": kind, "data": data})
        if kind in ("status", "approval", "approval_done"):
            self.bus.publish("state", self.state())   # top bar counters and the emblem follow every transition

    def _audit(self, kind: str, detail: dict, t: dict | None = None):
        if not self.cfg.security.audit:
            return
        self.store.audit(kind, detail, task_id=t["id"] if t else None,
                         profile=t["profile"] if t else None, preset=t["preset"] if t else None)

    def state(self) -> dict:
        with self._lock:
            active = [t for t in self.tasks.values() if t["status"] in ACTIVE]
        cli = self.cli()
        return {
            "emergency_stop": self.emergency,
            "running": sum(1 for t in active if t["status"] == "running"),
            "awaiting": sum(1 for t in active if t["status"] == "awaiting"),
            "queued": sum(1 for t in active if t["status"] == "queued"),
            "cli": {"path": cli[-1] if cli else None,
                    "version": claude_cli.cli_version(cli[0]) if cli and not self._cli_override else "test"},
            "probes": {pid: {k: p.get(k) for k in ("ok", "logged_in", "account", "checked", "error")}
                       for pid, p in self.probes.items()},
        }

    # -------------------------------------------------------- startup / shutdown
    def _recover(self):
        for t in self.store.list_tasks(limit=2000):
            if t["status"] == "queued" and t.get("not_before") and not t.get("started"):
                self.tasks[t["id"]] = t
                self.queue.append(t["id"])
                continue
            if t["status"] in ACTIVE:
                t["status"] = "interrupted"
                t["error"] = "Interrompue : le serveur s'est arrêté pendant l'exécution."
                t["ended"] = t.get("ended") or time.time()
                t["pending"] = []
                t["queued_messages"] = []
                self.store.save_task(t)
                self.tasks[t["id"]] = t
                self._event(t["id"], "status", {"status": "interrupted", "text": t["error"]})
            self.tasks[t["id"]] = t
        # discussions launched by a project action before they kept its name
        scanned: dict[str, set] = {}
        for t in self.tasks.values():
            act = "action" not in t and project_tools.action_of(t.get("prompt") or "")
            proj = act and self._project(t.get("workdir"))
            if proj:
                key = norm(proj.folder)
                if key not in scanned:
                    scanned[key] = {a["name"] for a in project_tools.scan(proj.folder)} if os.path.isdir(proj.folder) else set()
                if act[0] in scanned[key]:
                    t["action"] = act[0]
                    self.store.save_task(t)

    def _purge_old(self):
        days = self.cfg.history.retention_days
        n = self.store.purge(time.time() - days * 86400, sorted(TERMINAL))
        with self._lock:
            alive = {t["id"] for t in self.store.list_tasks(limit=100000)}
            if n:
                self.tasks = {k: v for k, v in self.tasks.items() if k in alive}
        self.changes.sweep(alive)

    def shutdown(self):
        self._stop = True
        with self._lock:
            runs = list(self.runs.items())
        for tid, run in runs:
            run.stop_status, run.stop_reason = "interrupted", "Interrompue : arrêt du serveur."
            self._release_approvals(run, "Arrêt du serveur.")
            if run.proc and run.proc.poll() is None:
                claude_cli.kill_tree(run.proc.pid)

    # -------------------------------------------------------- task creation
    def _profile_alias(self, alias: str) -> str | None:
        alias = alias.lower()
        profiles = self.cfg.profiles
        for p in profiles:
            if alias in (p.id.lower(), p.name.lower()):
                return p.id
        special = {"perso": "personal", "pro": "work", "travail": "work", "boulot": "work"}
        if alias in special and self.cfg.profile(special[alias]):
            return special[alias]
        hits = [p.id for p in profiles if p.id.lower().startswith(alias) or p.name.lower().startswith(alias)]
        return hits[0] if len(hits) == 1 else None

    def _workdir(self, prof: Profile, pre: Preset, requested: str | None) -> str:
        wd = expand_path(requested or prof.workdir)
        p = Path(wd)
        if not requested:
            p.mkdir(parents=True, exist_ok=True)
        if not p.is_dir():
            raise TaskError(f"Dossier de travail introuvable : {wd}")
        wd = str(p.resolve())
        ctx = policy_context(wd, [], self.cfg.security.forbidden_paths, str(self.data_dir), [])
        hit = Policy(pre, [], [], ctx).forbidden_hit({"path": wd}, "LS")
        if hit:
            raise TaskError(f"Dossier de travail interdit par la configuration de sécurité : {wd} ({hit}).")
        if pre.require_dedicated_workdir:
            home = str(Path.home())
            bad = (norm(wd) == norm(home) or len(Path(wd).parts) <= 2 or within(home, [wd])
                   or within(str(self.data_dir), [wd]))
            if bad:
                raise TaskError(f"Le preset « {pre.name} » exige un dossier de travail dédié "
                                "(ni le dossier utilisateur, ni la racine d'un disque, ni le dossier de la console).")
        return wd

    def create_task(self, prompt: str, profile: str | None = None, model: str | None = None,
                    preset: str | None = None, workdir: str | None = None, effort: str | None = None,
                    confirmed: bool = False, retry_of: str | None = None, resume: str | None = None,
                    fork: bool = True, history: list | None = None, origin: str = "",
                    routine: dict | None = None, closed: bool = False, team: bool = False,
                    attachments: list[str] | None = None, context: list[dict] | None = None,
                    extra_dirs: list[str] | None = None, not_before: float | None = None,
                    regard: dict | None = None) -> dict:
        if self.emergency:
            raise TaskError("Arrêt d'urgence actif : réactive la console avant de lancer une tâche.", 423)
        cfg = self.cfg
        text = (prompt or "").strip()
        m = re.match(r"^@([\w-]+)\s+", text)
        if m:
            pid = self._profile_alias(m.group(1))
            if pid:
                profile = pid
                text = text[m.end():].strip()
        if not text and not attachments and not context:
            raise TaskError("Demande vide.")
        text = text or ("Voici des pièces jointes." if attachments else "Reprends le contexte joint.")
        prof = cfg.profile(profile or cfg.general.default_profile)
        if not prof:
            raise TaskError(f"Profil inconnu : {profile}")
        pre = cfg.preset(preset or prof.default_preset)
        if not pre:
            raise TaskError(f"Preset inconnu : {preset}")
        if not pre.enabled:
            raise TaskError(f"Le preset « {pre.name} » est désactivé (Configuration → Autorisations).", 403)
        if (pre.require_confirm or prof.confirm_launch) and not confirmed:
            raise TaskError(f"Confirmer le lancement sur le profil « {prof.name} » avec le preset « {pre.name} » ?",
                            409, need_confirm=True, profile=prof.name, preset=pre.name, color=prof.color)
        wd = self._workdir(prof, pre, workdir)
        model = (model or pre.model or prof.default_model or "default").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]{1,80}", model):
            raise TaskError("Nom de modèle invalide.")
        effort = prof.effort if effort is None else effort
        if effort not in ("", "low", "medium", "high", "xhigh", "max"):
            raise TaskError("Niveau d'effort invalide.")
        add_dirs = [d for d in (expand_path(x) for x in prof.add_dirs) if d and Path(d).is_dir()]
        add_dirs += [d for d in (extra_dirs or []) if d and Path(d).is_dir() and d not in add_dirs]
        if resume and not re.fullmatch(r"[0-9a-fA-F-]{36}", resume):
            raise TaskError("Identifiant de session invalide.")
        team_agents, team_models, team_prompt = ({}, {}, "")
        if team:
            resolved = {m.get("value"): m.get("resolved") for m in (self.probes.get(prof.id) or {}).get("models") or []
                        if m.get("value") and m.get("resolved")}
            team_agents, team_models, team_prompt = team_mod.build(model, cfg.team, resolved)
        tid = secrets.token_hex(4)
        now = time.time()
        root = self.attachments_root()
        adir = att.task_dir(root, tid, now)
        try:
            files = att.claim(root, attachments, adir) if attachments else []
        except att.AttachmentError as e:
            raise TaskError(str(e), e.status) from e
        if context:
            files += self._context_files(prof, context, adir)
        seen, seen_block = self._regard(regard, "", prof.id)
        t = {
            "id": tid, "title": _title(text), "prompt": text, "profile": prof.id,
            "profile_name": prof.name, "color": prof.color, "model": model, "effort": effort,
            "preset": pre.id, "preset_name": pre.name, "workdir": wd, "add_dirs": add_dirs,
            "status": "queued", "created": now, "started": None, "ended": None,
            "session_id": str(uuid.uuid4()), "session_started": False, "turns": 0,
            "cost_usd": 0.0, "duration_ms": 0, "result": "", "error": "", "is_error": False,
            "pinned": False, "closed": bool(closed), "retry_of": retry_of, "pending": [], "mcp": [],
            "todos": [], "usage": {}, "output_bytes": 0, "truncated": False, "model_resolved": "",
            "queued_messages": [att.message(text + (f"\n\n{seen_block}" if seen_block else ""), files)],
            "origin": origin, "routine": routine, "regard": seen,
            "attachments_dir": str(adir), "attachments": files,
            "not_before": float(not_before) if not_before and not_before > now else None,
            "resumed_from": resume, "fork_next": bool(resume and fork), "action": self._action_link(text, wd),
            "team": bool(team_agents), "team_agents": team_models,
            "spec": {"profile": dump_model(prof), "preset": dump_model(pre),
                     "rules": [dump_model(r) for r in cfg.tool_rules],
                     "constraints": [dump_model(c) for c in cfg.constraints],
                     "forbidden": list(cfg.security.forbidden_paths),
                     "security_instructions": cfg.general.security_instructions,
                     "max_turns": pre.max_turns or cfg.general.max_turns,
                     "team": {"agents": team_agents, "prompt": team_prompt,
                              "subagent_default": cfg.team.subagent_default} if team_agents else None},
        }
        if resume:
            t["session_id"], t["session_started"] = resume, True
        with self._lock:
            self.tasks[tid] = t
            self._save(t)
            if history:
                self._event(tid, "history", {"session": resume, "items": history[-40:], "total": len(history)})
            self._event(tid, "user", {"text": text, "first": True, **({"files": files} if files else {}),
                                      **({"regard": regard_mod.public(seen)} if seen else {})})
            if t["not_before"]:
                self._event(tid, "status", {"status": "queued", "text": "Programmée pour "
                            + time.strftime("%d/%m %H:%M", time.localtime(t["not_before"]))
                            + " : réinitialisation de la limite du compte."})
            self._audit("tâche créée", {"demande": _clip(text, 2000), "modèle": model, "preset": pre.name,
                                        **({"pièces jointes": [f["path"] for f in files]} if files else {}),
                                        **({"regard": regard_mod.label(seen)} if seen else {}),
                                        "dossier": wd, "effort": effort or "défaut",
                                        **({"équipe": team_models} if team_agents else {}),
                                        **({"reprise": resume, "copie": fork} if resume else {}),
                                        **({"routine": routine.get("name")} if routine else {})}, t)
            self.queue.append(tid)
        self._wake.set()
        return self.public(t)

    def retry(self, tid: str, confirmed: bool = False) -> dict:
        t = self._get(tid)
        return self.create_task(t["prompt"], profile=t["profile"], model=t["model"], preset=t["preset"],
                                workdir=t["workdir"], effort=t["effort"], confirmed=confirmed, retry_of=tid,
                                team=bool(t.get("team")), attachments=self._restage(t), regard=t.get("regard"))

    def duplicate(self, tid: str, profile: str, confirmed: bool = False) -> dict:
        t = self._get(tid)
        prof = self.cfg.profile(profile)
        if not prof:
            raise TaskError(f"Profil inconnu : {profile}")
        preset = t["preset"] if self.cfg.preset(t["preset"]) else None
        return self.create_task(t["prompt"], profile=prof.id, model=t["model"], preset=preset,
                                effort=t["effort"], confirmed=confirmed, retry_of=tid, team=bool(t.get("team")),
                                attachments=self._restage(t), regard=t.get("regard"))

    def fork(self, tid: str, prompt: str, confirmed: bool = False) -> dict:
        """A new discussion that starts with this one's whole context; the original stays as it is."""
        t = self._get(tid)
        if not t.get("session_started"):
            raise TaskError("Cette discussion n'a pas encore démarré : rien à reprendre.", 409)
        prof = self.cfg.profile(t["profile"])
        if not prof:
            raise TaskError("Profil inconnu.", 404)
        history = [{"role": i["role"], "text": (i.get("text") or f"{i.get('name')} · {i.get('target', '')}")[:1500]}
                   for i in library.read_transcript(prof, t["session_id"], limit=60) if not i.get("sidechain")]
        n = self.create_task(prompt, profile=prof.id, model=t["model"], preset=t["preset"] if self.cfg.preset(t["preset"]) else None,
                             workdir=t["workdir"], effort=t["effort"], confirmed=confirmed, resume=t["session_id"], fork=True,
                             history=history, origin="copie", team=bool(t.get("team")),
                             extra_dirs=[t["attachments_dir"]] if t.get("attachments_dir") else None)
        self._audit("discussion copiée", {"depuis": tid}, self.tasks[n["id"]])
        return n

    # -------------------------------------------------------- moving a session into a project
    def move_session(self, pid: str, sid: str, folder: str) -> dict:
        """The same session, moved into another folder: its transcript is filed where Claude Code
        looks for it from there, Claude Desktop's record and the console's windows follow."""
        prof = self.cfg.profile(pid)
        if not prof:
            raise TaskError("Profil inconnu.", 404)
        if not library.UUID.match(sid or ""):
            raise TaskError("Identifiant de session invalide.")
        _, target = self._ws_folder(prof.id, folder)
        with self._lock:
            linked = [t for t in self.tasks.values() if t.get("session_id") == sid and t.get("profile") == prof.id]
            if any(t["id"] in self.runs for t in linked):
                raise TaskError("Cette discussion est en cours : attends qu'elle se termine pour la déplacer.", 409)
        try:
            res = library.move_session(prof, sid, target)
        except FileNotFoundError as e:
            raise TaskError("Transcription de la session introuvable.", 404) from e
        except FileExistsError as e:
            raise TaskError("Une session du même identifiant existe déjà dans ce projet.", 409) from e
        with self._lock:
            for t in linked:
                t["workdir"] = target
                self._save(t)
                self._event(t["id"], "info", {"text": f"Discussion déplacée dans le projet {Path(target).name} ({target})."})
        self._audit("session déplacée dans un projet", {"session": sid, "vers": target,
                                                        "claude desktop": "mis à jour" if res["desktop"] else "non concerné"})
        return {"session": sid, "folder": target, "desktop": res["desktop"], "tasks": [t["id"] for t in linked]}

    def move_task(self, tid: str, folder: str) -> dict:
        t = self._get(tid)
        if tid in self.runs:
            raise TaskError("Cette discussion est en cours : attends qu'elle se termine pour la déplacer.", 409)
        if t.get("session_started"):
            self.move_session(t["profile"], t["session_id"], folder)
        else:
            _, target = self._ws_folder(t["profile"], folder)
            with self._lock:
                t["workdir"] = target
                self._save(t)
        return self.public(self._get(tid))

    def moved_copies(self, pid: str | None = None) -> list[dict]:
        """Duplicates left by the former "move" (a copy per move): listed so they can be merged back."""
        rows = []
        for prof in self.cfg.profiles:
            if pid in (None, "", prof.id):
                rows += [{**r, "profile": prof.id, "profile_name": prof.name} for r in library.moved_copies(prof)]
        return rows

    def merge_moved_copies(self, pid: str | None = None) -> dict:
        merged, skipped = [], []
        for row in self.moved_copies(pid):
            prof = self.cfg.profile(row["profile"])
            with self._lock:
                busy = any(t.get("session_id") in (row["original"], row["copy"]) and t["id"] in self.runs for t in self.tasks.values())
            if busy:
                skipped.append({**row, "reason": "discussion en cours"})
                continue
            try:
                res = library.merge_moved_copy(prof, row["original"], row["copy"])
            except (OSError, ValueError) as e:
                skipped.append({**row, "reason": str(e)})
                continue
            with self._lock:
                for t in self.tasks.values():
                    if t.get("profile") == prof.id and t.get("session_id") in (row["original"], row["copy"]):
                        t["session_id"] = row["original"]
                        t["workdir"] = str(Path(res["folder"]).resolve()) if res["folder"] else t["workdir"]
                        t["resumed_from"] = t.get("resumed_from") and row["original"]
                        self._save(t)
            merged.append({**row, **res})
        if merged:
            self._audit("copies de sessions réunies", {"réunies": [m["original"] for m in merged]})
        return {"merged": merged, "skipped": skipped}

    def _context_files(self, prof: Profile, sources: list[dict], dest: Path) -> list[dict]:
        """Other discussions of the same account, written as Markdown next to the attachments."""
        if len(sources) > 5:
            raise TaskError("5 discussions au plus comme contexte.")
        out = []
        for src in sources:
            if (src.get("profile") or prof.id) != prof.id:
                raise TaskError("Contexte limité aux discussions du même compte.", 403)
            title = str(src.get("title") or "discussion").strip()[:80] or "discussion"
            md = library.transcript_markdown(prof, str(src.get("session") or ""), title)
            if not md:
                raise TaskError(f"Discussion introuvable pour le contexte : {title}.", 404)
            out.append({**att.write_text(dest, f"contexte - {title}.md", md), "context": True, "title": title})
        return out

    def _regard(self, raw, here: str, pid: str) -> tuple[dict | None, str]:
        """What the user looks at (regard.py) and the text added to the message. A display or a tool's
        result of another discussion of the same account comes with its content: the receiving
        discussion has never seen it."""
        r = regard_mod.clean(raw)
        if not r:
            return None, ""
        excerpt = ""
        src_id = r.get("task") or ""
        if src_id and src_id != here and ("key" in r or "call" in r):
            src = self.tasks.get(src_id) or self.store.get_task(src_id)
            if src and src.get("profile") == pid:
                try:
                    excerpt = self._regard_excerpt(src, r)
                except Exception:  # noqa: BLE001 - an unreadable excerpt never stops the message
                    excerpt = ""
        return r, regard_mod.block(r, here, excerpt)

    def _regard_excerpt(self, src: dict, r: dict) -> str:
        if "key" in r:
            d = self._display(src["id"], r["key"])
            return json.dumps({k: v for k, v in d["doc"].items() if k != "ou"}, ensure_ascii=False) if d else ""
        call, _ = self._find_call(src, {"id": r["call"]})
        if not call:
            return ""
        v = self._view(src, call, r.get("contient", ""))
        if v["kind"] == "mail":
            m = v.get("meta") or {}
            head = [f"Objet : {v['title']}", *(f"{k} : {m[f]}" for k, f in (("De", "from"), ("À", "to"), ("Cc", "cc"),
                                                                             ("Date", "date")) if m.get(f))]
            return "\n".join(head) + "\n\n" + regard_mod.plain(v.get("page") or "")
        if v.get("text") is not None:
            return v["text"]
        return regard_mod.plain(v["page"]) if v.get("page") else ""

    def _restage(self, t: dict) -> list[str] | None:
        root = self.attachments_root()
        ids = [i for i in (att.stage_copy(root, f["path"]) for f in t.get("attachments") or []) if i]
        return ids or None

    # -------------------------------------------------------- attachments
    def attachments_root(self) -> Path:
        root = Path(expand_path(self.cfg.general.attachments_dir)
                    or str(Path.home() / "ClaudeConsole" / "pieces-jointes")).resolve()
        if within(str(root), [str(self.data_dir)]):
            raise TaskError("Le dossier des pièces jointes ne peut pas être dans les données de la console "
                            "(Configuration → Général).")
        return root

    def discard_upload(self, uid: str):
        att.discard(self.attachments_root(), uid)

    def _get(self, tid: str) -> dict:
        t = self.tasks.get(tid) or self.store.get_task(tid)
        if not t:
            raise TaskError("Tâche inconnue.", 404)
        return t

    # -------------------------------------------------------- follow-ups
    def followup(self, tid: str, text: str, attachments: list[str] | None = None, compact: bool = False,
                 regard: dict | None = None) -> dict:
        """compact: have Claude Code compact the session first (/compact), then send the message if any.
        regard: what the user looks at in the console (regard.py), added to the message."""
        text = (text or "").strip()
        if not text and not attachments and not compact:
            raise TaskError("Message vide.")
        if compact and not text and not attachments:
            return self._send_or_queue(tid, ["/compact"], "Compactage du contexte demandé.")
        text = text or "Voici des pièces jointes."
        if self.emergency:
            raise TaskError("Arrêt d'urgence actif.", 423)
        with self._lock:
            t = self._get(tid)
            run = self.runs.get(tid)
            files = []
            if attachments:
                root = self.attachments_root()
                adir = t.get("attachments_dir") or str(att.task_dir(root, tid, t.get("created") or time.time()))
                t["attachments_dir"] = adir
                try:
                    files = att.claim(root, attachments, Path(adir))
                except att.AttachmentError as e:
                    raise TaskError(str(e), e.status) from e
                t["attachments"] = [*(t.get("attachments") or []), *files]
                if run and run.policy and adir not in run.policy.ctx.add_dirs:
                    run.policy.ctx.add_dirs.append(adir)  # the live session may read it at once
            seen, seen_block = self._regard(regard, tid, t["profile"])
            notes = self._change_notes(t)
            sent = att.message(text + "".join(f"\n\n{b}" for b in (seen_block, notes) if b), files)
            if compact:
                self._event(tid, "info", {"text": "Compactage du contexte avant d'envoyer la suite…"})
            self._event(tid, "user", {"text": text, **({"files": files} if files else {}),
                                      **({"regard": regard_mod.public(seen)} if seen else {})})
            self._audit("message de suite", {"texte": _clip(text, 2000), **({"compactage": True} if compact else {}),
                                             **({"pièces jointes": [f["path"] for f in files]} if files else {}),
                                             **({"regard": regard_mod.label(seen)} if seen else {})}, t)
            self._deliver(tid, t, run, ["/compact", sent] if compact else [sent])
            self._save(t)
        self._wake.set()
        return self.public(t)

    def _send_or_queue(self, tid: str, messages: list[str], info: str) -> dict:
        with self._lock:
            t = self._get(tid)
            if not t.get("session_started"):
                raise TaskError("Rien à compacter : la session n'a pas encore démarré.")
            self._event(tid, "info", {"text": info})
            self._audit("compactage demandé", {}, t)
            self._deliver(tid, t, self.runs.get(tid), messages)
            self._save(t)
        self._wake.set()
        return self.public(t)

    def _deliver(self, tid: str, t: dict, run: Run | None, messages: list[str]):
        """Into the live session, else queued for the next run (which resumes the session)."""
        if run and run.proc and not run.stdin_closed and t["status"] in ("running", "awaiting"):
            for m in messages:
                self._send_user(run, m)
            return
        t.setdefault("queued_messages", []).extend(messages)
        if t["status"] in TERMINAL:
            t["status"] = "queued"
            t["error"] = ""
            self.queue.append(tid)
            self._event(tid, "status", {"status": "queued"})

    def _send_user(self, run: Run, text: str):
        msg = {"type": "user", "session_id": "", "parent_tool_use_id": None,
               "message": {"role": "user", "content": text}}
        run.sent += 1
        self._write(run, msg)

    def _write(self, run: Run, obj: dict) -> bool:
        with run.lock:
            if run.stdin_closed or not run.proc or run.proc.stdin is None:
                return False
            try:
                # ASCII-escaped JSON: immune to whatever encoding the child reads its stdin with.
                run.proc.stdin.write(json.dumps(obj) + "\n")
                run.proc.stdin.flush()
                return True
            except (OSError, ValueError):
                run.stdin_closed = True
                return False

    def _close_stdin(self, run: Run):
        with run.lock:
            if not run.stdin_closed and run.proc and run.proc.stdin:
                run.stdin_closed = True
                try:
                    run.proc.stdin.close()
                except OSError:
                    pass

    # -------------------------------------------------------- cancel / emergency
    def cancel(self, tid: str, reason: str = "Annulée par l'utilisateur.", status: str = "cancelled") -> dict:
        with self._lock:
            t = self._get(tid)
            if t["status"] == "queued" and tid not in self.runs:
                if tid in self.queue:
                    self.queue.remove(tid)
                t["status"], t["error"], t["ended"] = status, reason, time.time()
                t["queued_messages"] = []
                self._event(tid, "status", {"status": status, "text": reason})
                self._audit("tâche annulée", {"raison": reason}, t)
                self._save(t)
                return self.public(t)
            run = self.runs.get(tid)
            if not run:
                raise TaskError(f"La tâche n'est pas en cours ({t['status']}).", 409)
            run.stop_status, run.stop_reason = status, reason
            t.setdefault("queued_messages", []).clear()
        self._release_approvals(run, reason)
        if run.proc and run.proc.poll() is None:
            claude_cli.kill_tree(run.proc.pid)
        return self.public(t)

    def _release_approvals(self, run: Run, reason: str):
        for appr in list(run.approvals.values()):
            appr.claim("deny", reason, "console")

    def set_emergency(self, on: bool) -> dict:
        with self._lock:
            self.emergency = bool(on)
            self.store.kv_set("emergency_stop", self.emergency)
            active = [t["id"] for t in self.tasks.values() if t["status"] in ACTIVE]
        self._audit("arrêt d'urgence", {"actif": self.emergency, "tâches arrêtées": len(active) if on else 0})
        if on:
            for tid in active:
                try:
                    self.cancel(tid, "Arrêtée par l'arrêt d'urgence.")
                except TaskError:
                    pass
        self.bus.publish("state", self.state())
        self._wake.set()
        return self.state()

    # -------------------------------------------------------- approvals
    def decide(self, tid: str, aid: str, decision: str, message: str = "", answers: dict | None = None,
               remember: list[str] | None = None) -> dict:
        if decision not in ("allow", "deny"):
            raise TaskError("Décision invalide.")
        run = self.runs.get(tid)
        appr = run.approvals.get(aid) if run else None
        if not appr or appr.decision is not None:
            raise TaskError("Cette validation n'est plus en attente.", 409)
        added = []
        if decision == "allow" and remember:
            added = self.add_project_rules(self._get(tid)["workdir"], [str(x) for x in remember][:10], tool=appr.tool)
        if not appr.claim(decision, (message or "").strip()[:2000], "utilisateur",
                          answers if isinstance(answers, dict) else None):
            raise TaskError("Cette validation n'est plus en attente.", 409)
        return {"ok": True, "remembered": added}

    # -------------------------------------------------------- search (Ctrl+K)
    def search(self, q: str, limit: int = 30) -> dict:
        """Discussions of the console (title, requests, answers) and Claude Code sessions (Desktop, CLI)."""
        q = (q or "").strip()
        if len(q) < 2:
            return {"tasks": [], "sessions": []}
        n = q.lower()
        with self._lock:
            tasks = sorted(self.tasks.values(), key=lambda t: t.get("created") or 0, reverse=True)
        found: dict[str, str] = {}
        for t in tasks:
            head = f"{t.get('title', '')} {t.get('prompt', '')}"
            if n in head.lower():
                found[t["id"]] = library.snippet(t.get("prompt") or t.get("title"), q)
        for e in self.store.search_events(q, 300):
            tid = e["task_id"]
            if tid not in found and tid in self.tasks:
                found[tid] = library.snippet(e["data"].get("text") or "", q)
        out = []
        for t in tasks:
            if t["id"] in found:
                out.append({k: t.get(k) for k in ("id", "title", "profile", "profile_name", "color", "status", "created", "workdir", "not_before")}
                           | self._live_profile(t)
                           | {"snippet": found[t["id"]]})
            if len(out) >= limit:
                break
        known = {t.get("session_id") for t in tasks}
        sessions = []
        for prof in self.cfg.profiles:
            for r in library.search_sessions(prof, q, limit=limit, skip=known):
                sessions.append({**r, "profile_name": prof.name, "color": prof.color})
        sessions.sort(key=lambda r: r.get("updated") or 0, reverse=True)
        return {"tasks": out, "sessions": sessions[:limit]}

    # -------------------------------------------------------- projects (named folders with defaults)
    def projects(self) -> list[dict]:
        with self._lock:
            tasks = list(self.tasks.values())
        out = []
        for p in self.cfg.projects:
            key = norm(p.folder)
            mine = [t for t in tasks if norm(t.get("workdir") or "") == key]
            exists = os.path.isdir(p.folder)
            out.append({**p.model_dump(), "exists": exists, "discussions": len(mine),
                        "actions": self.project_actions(p.folder) if exists else [],
                        "last": max((t.get("created") or 0 for t in mine), default=None),
                        "rules": len([r for r in self.cfg.project_rules if norm(r.folder) == key])})
        out.sort(key=lambda x: (not x["pinned"], -(x["last"] or x["created"] or 0)))
        return out

    def save_project(self, data: dict) -> dict:
        from .config import Project
        _, folder = self._ws_folder(data.get("profile") or None, str(data.get("folder") or ""))
        try:
            proj = Project.model_validate({**data, "folder": folder})
        except ValueError as e:
            raise TaskError(f"Projet invalide : {e}") from e
        if proj.profile and not self.cfg.profile(proj.profile):
            raise TaskError("Compte inconnu.")
        if proj.preset and not self.cfg.preset(proj.preset):
            raise TaskError("Preset inconnu.")
        cfg = self.cfg.model_copy(deep=True)
        old = next((p for p in cfg.projects if norm(p.folder) == norm(folder)), None)
        proj.created = old.created if old else time.time()
        cfg.projects = [p for p in cfg.projects if norm(p.folder) != norm(folder)] + [proj]
        self.cfg_store.save(cfg, f"projet {proj.name}")
        self.bus.publish("projects", {"projects": self.projects()})
        return proj.model_dump()

    def delete_project(self, folder: str):
        cfg = self.cfg.model_copy(deep=True)
        keep = [p for p in cfg.projects if norm(p.folder) != norm(folder)]
        if len(keep) == len(cfg.projects):
            raise TaskError("Projet introuvable.", 404)
        cfg.projects = keep
        self.cfg_store.save(cfg, f"projet retiré : {Path(folder).name}")  # the folder itself is untouched
        self.bus.publish("projects", {"projects": self.projects()})

    def _project(self, folder: str | None):
        key = norm(folder or "")
        return next((p for p in self.cfg.projects if key and norm(p.folder) == key), None)

    # -------------------------------------------------------- notes (general or of a project, with a reminder)
    def notes(self, folder: str | None = None) -> list[dict]:
        """Every note, newest first; with a folder, only that project's notes ("" = the general ones)."""
        out = self.store.list_notes()
        if folder is not None:
            key = norm(folder)
            out = [n for n in out if norm(n.get("folder") or "") == key]
        return out

    def save_note(self, data: dict, note_id: str | None = None) -> dict:
        """Create a note, or change the fields given of an existing one."""
        old = self.store.get_note(note_id) if note_id else None
        if note_id and not old:
            raise TaskError("Note introuvable.", 404)
        note = dict(old or {"id": uuid.uuid4().hex[:12], "created": time.time(), "text": "",
                            "profile": "", "folder": "", "remind_at": None, "reminded": False})
        if "text" in data:
            note["text"] = str(data.get("text") or "").strip()
        if not note["text"]:
            raise TaskError("La note est vide.")
        if len(note["text"]) > 20000:
            raise TaskError("La note est trop longue (20 000 caractères au plus).")
        if "folder" in data:
            folder = str(data.get("folder") or "")
            proj = self._project(folder) if folder else None
            if folder and not proj:
                raise TaskError("Projet inconnu.")
            note["folder"] = proj.folder if proj else ""
        if "profile" in data:
            note["profile"] = str(data.get("profile") or "")
        if not note["profile"]:  # a project note follows its project's account by default
            proj = self._project(note["folder"])
            note["profile"] = (proj.profile if proj and proj.profile else "") or                 (self.cfg.profiles[0].id if self.cfg.profiles else "")
        if note["profile"] and not self.cfg.profile(note["profile"]):
            raise TaskError("Compte inconnu.")
        if "remind_at" in data:
            at = data.get("remind_at")
            try:
                note["remind_at"] = float(at) if at not in (None, "", 0) else None
            except (TypeError, ValueError) as e:
                raise TaskError("Date de rappel invalide.") from e
            note["reminded"] = False  # a new date rings again
        if "reminded" in data:
            note["reminded"] = bool(data.get("reminded"))
        note["updated"] = time.time()
        self.store.save_note(note)
        self.bus.publish("notes", {"note": note})
        return note

    def delete_note(self, note_id: str):
        if not self.store.delete_note(note_id):
            raise TaskError("Note introuvable.", 404)
        self.bus.publish("notes", {"deleted": note_id})

    # -------------------------------------------------------- project actions (its commands and skills)
    def project_actions(self, folder: str, content: bool = False, profile: str | None = None) -> list[dict]:
        """The actions of a project folder, each one "ok" (validated as it is), "nouvelle" or "modifiee",
        with the model and effort chosen for it ("" = the project's). With `content`: also its files,
        the model it inherits and its latest launches."""
        pins = (self.store.kv_get("action_pins", {}) or {}).get(norm(folder), {})
        prefs = self._action_prefs(folder)
        proj = self._project(folder) if content else None
        inherited = self._inherited_model(proj, profile) if proj else None
        out = []
        for a in project_tools.scan(folder) if folder and os.path.isdir(folder) else []:
            pin = pins.get(a["name"])
            a["status"] = "ok" if pin == a["hash"] else ("modifiee" if pin else "nouvelle")
            a.update({"model": "", "effort": "", **prefs.get(a["name"], {})})
            if content:
                a["inherited"] = inherited
                a["runs"] = self.action_runs(folder, a["name"])
            else:
                a.pop("content", None)
                a.pop("files", None)
            out.append(a)
        return out

    def _action_link(self, prompt: str, workdir: str) -> str:
        """The project action a discussion runs ("/nom …" in the folder of a project that has it), or ""."""
        act = project_tools.action_of(prompt)
        proj = self._project(workdir) if act else None
        if not proj or not os.path.isdir(proj.folder):
            return ""
        return act[0] if any(a["name"] == act[0] for a in project_tools.scan(proj.folder)) else ""

    def action_runs(self, folder: str, name: str, limit: int = 30) -> list[dict]:
        """The discussions launched with an action (button, routine, Ctrl+K or typed), newest first."""
        key = norm(folder)
        with self._lock:
            mine = [t for t in self.tasks.values() if t.get("action") == name and norm(t.get("workdir") or "") == key]
        mine.sort(key=lambda t: t.get("created") or 0, reverse=True)
        return [{k: t.get(k) for k in ("id", "title", "prompt", "status", "created", "ended", "model", "model_resolved",
                                       "effort", "origin", "cost_usd", "profile_name", "color")} for t in mine[:limit]]

    def _action_prefs(self, folder: str) -> dict:
        return (self.store.kv_get("action_prefs", {}) or {}).get(norm(folder), {})

    def _inherited_model(self, proj, profile: str | None = None) -> dict:
        """The model and effort a launch of the project uses when its action has none of its own, and where they come from."""
        prof = self.cfg.profile(proj.profile or profile or self.cfg.general.default_profile)
        if not prof:
            return {"model": "default", "source": "compte", "resolved": "", "effort": "", "effort_source": "compte"}
        pre = self.cfg.preset(proj.preset or prof.default_preset)
        model, source = next(((v, s) for v, s in ((proj.model, "projet"), (pre.model if pre else "", "autorisations"),
                                                   (prof.default_model, "compte")) if v), ("default", "compte"))
        resolved = next((m.get("resolved") for m in (self.probes.get(prof.id) or {}).get("models") or []
                         if m.get("value") == model and m.get("resolved")), "")
        effort, esrc = (proj.effort, "projet") if proj.effort else (prof.effort, "compte")
        return {"model": model, "source": source, "resolved": resolved or "", "profile": prof.id,
                "profile_name": prof.name, "effort": effort, "effort_source": esrc}

    def set_action_prefs(self, folder: str, name: str, model: str = "", effort: str = "") -> dict:
        """The model and effort of an action's launches (kept by the console: the action's file is untouched)."""
        proj = self._project(folder)
        if not proj:
            raise TaskError("Projet introuvable.", 404)
        if not any(a["name"] == name for a in self.project_actions(proj.folder)):
            raise TaskError(f"Action introuvable : /{name}", 404)
        model, effort = (model or "").strip(), (effort or "").strip()
        if model and not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]{1,80}", model):
            raise TaskError("Nom de modèle invalide.")
        if effort not in ("", "low", "medium", "high", "xhigh", "max"):
            raise TaskError("Niveau d'effort invalide.")
        prefs = self.store.kv_get("action_prefs", {}) or {}
        mine = prefs.setdefault(norm(proj.folder), {})
        if model or effort:
            mine[name] = {"model": model, "effort": effort}
        else:
            mine.pop(name, None)
        self.store.kv_set("action_prefs", prefs)
        self._audit("réglages d'action", {"projet": proj.name, "action": f"/{name}", "modèle": model or "celui du projet",
                                          "effort": effort or "celui du projet"})
        self.bus.publish("projects", {"projects": self.projects()})
        return {"model": model, "effort": effort}

    def _pin_action(self, folder: str, name: str, fp: str):
        pins = self.store.kv_get("action_pins", {}) or {}
        pins.setdefault(norm(folder), {})[name] = fp
        self.store.kv_set("action_pins", pins)
        self.bus.publish("projects", {"projects": self.projects()})

    def approve_action(self, folder: str, name: str, fp: str) -> dict:
        """The user validated an action as shown: the fingerprint must still be that of the files."""
        proj = self._project(folder)
        if not proj:
            raise TaskError("Projet introuvable.", 404)
        a = next((x for x in self.project_actions(proj.folder, content=True) if x["name"] == name), None)
        if not a:
            raise TaskError("Action introuvable.", 404)
        if a["hash"] != fp:
            raise TaskError("L'action a changé depuis son affichage : relis-la avant de la valider.", 409,
                            need_approval=True, action=a)
        self._pin_action(proj.folder, name, fp)
        self._audit("action de projet validée", {"projet": proj.name, "action": f"/{name}", "fichier": a["path"],
                                                  "empreinte": fp})
        return {**a, "status": "ok"}

    def run_action(self, folder: str, name: str, arguments: str = "", confirmed: bool = False,
                   approve: str = "", profile: str | None = None) -> dict:
        """A project action launched from its button: a normal discussion of the project ("/nom arguments"),
        with the project's account and preset, the model and effort chosen for the action (else the
        project's), under the same policy."""
        proj = self._project(folder)
        if not proj:
            raise TaskError("Projet introuvable.", 404)
        a = next((x for x in self.project_actions(proj.folder, content=True) if x["name"] == name), None)
        if not a:
            raise TaskError(f"Action introuvable : /{name}", 404)
        if a["status"] != "ok":
            if approve != a["hash"]:
                why = "a changé depuis sa validation" if a["status"] == "modifiee" else "n'a pas encore été validée"
                raise TaskError(f"L'action « /{name} » {why} : relis-la avant de la lancer.", 409,
                                need_approval=True, action=a)
            self.approve_action(proj.folder, name, approve)
        args = " ".join(str(arguments or "").split())[:4000]
        pref = self._action_prefs(proj.folder).get(name, {})
        return self.create_task(f"/{name} {args}".strip(), profile=proj.profile or profile or None,
                                model=pref.get("model") or proj.model or None, preset=proj.preset or None,
                                workdir=proj.folder, effort=pref.get("effort") or proj.effort or None,
                                confirmed=confirmed, origin="action")

    def project_routines(self, folder: str) -> list[dict]:
        key = norm(folder or "")
        return [r.public() for r in sorted(self.routines.values(), key=lambda r: (r.name, r.id))
                if r.workdir and norm(r.workdir) == key]

    def _routine_action_problem(self, r: Routine) -> str | None:
        """A routine that launches a project action runs it only as the user validated it."""
        act = project_tools.action_of(r.prompt)
        proj = self._project(r.workdir) if act and r.workdir else None
        if not proj:
            return None
        a = next((x for x in self.project_actions(proj.folder) if x["name"] == act[0]), None)
        if a and a["status"] != "ok":
            return (f"L'action « /{a['name']} » {'a changé depuis sa validation' if a['status'] == 'modifiee' else 'n’est pas validée'} : "
                    "ouvre le projet pour la relire et la valider.")
        return None

    # -------------------------------------------------------- what Claude proposes to add to a project
    def _propose(self, tid: str, run: Run, rid: str, msg: dict):
        """Tool "proposer": checked here, then held on a card until the user decides; nothing is written or
        enabled before that click."""
        mid = msg.get("id")

        def reply(text: str, failed: bool = False):
            self._respond(run, rid, {"mcp_response": {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": failed}}})

        try:
            prop = self._proposal(tid, (msg.get("params") or {}).get("arguments") or {})
        except TaskError as exc:
            return reply("Rien de proposé : " + exc.message, True)
        what = "une action" if prop["quoi"] == "action" else "une routine"
        appr = Approval(id=secrets.token_hex(4), request_id=rid, kind="proposal", tool=PROPOSE_TOOL, input=prop,
                        reason=f"Claude propose d'ajouter {what} au projet « {prop['projet']} ».")

        def answer(a: Approval):
            if a.decision != "allow":
                return reply("L'utilisateur n'a pas retenu la proposition" + (f" : {a.message}" if a.message else ".")
                             + " N'insiste pas et ne l'écris pas toi-même.")
            try:
                reply(self._apply_proposal(tid, prop, a.answers or {}))
            except TaskError as exc:
                reply("Acceptée, mais pas enregistrée : " + exc.message, True)
        self._park(tid, run, appr, answer)

    def _proposal(self, tid: str, args: dict) -> dict:
        t = self._get(tid)
        proj = self._project(t["workdir"])
        if not proj:
            raise TaskError("cette discussion n'est pas dans un projet de la console (le dossier de travail doit "
                            "être celui d'un projet).")
        args = args if isinstance(args, dict) else {}

        def text(key: str, limit: int, required: bool = False) -> str:
            v = args.get(key)
            v = "" if v is None else str(v).strip()
            if required and not v:
                raise TaskError(f"« {key} » est requis.")
            if len(v) > limit:
                raise TaskError(f"« {key} » est trop long ({limit} caractères au plus).")
            return v

        quoi = text("quoi", 20, True).lower()
        base = {"quoi": quoi, "projet": proj.name, "dossier": proj.folder, "description": text("description", 300, True)}
        if quoi == "action":
            name = text("nom", 40, True).lower()
            if not project_tools.NAME.match(name):
                raise TaskError("nom d'action invalide : minuscules, chiffres et tirets, 40 caractères au plus.")
            existing = {a["name"]: a for a in self.project_actions(proj.folder, content=True)}
            if existing.get(name, {}).get("kind") == "skill":
                raise TaskError(f"le projet a déjà un skill « {name} » : choisis un autre nom.")
            path = Path(proj.folder) / ".claude" / "commands" / f"{name}.md"
            label, hint = text("libelle", 60), text("parametre", 200)
            body = text("consigne", 20000, True)
            old = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
            return {**base, "nom": name, "libelle": label, "parametre": hint, "consigne": body, "fichier": str(path),
                    "remplace": old, "contenu": project_tools.command_file(name, label, base["description"], hint, body)}
        if quoi != "routine":
            raise TaskError("« quoi » vaut action ou routine.")
        from .routines import Schedule
        action = text("action", 40).lower().lstrip("/")
        if action:
            if not any(a["name"] == action for a in self.project_actions(proj.folder)):
                raise TaskError(f"le projet n'a pas d'action « /{action} » (propose-la d'abord).")
            prompt = f"/{action} {' '.join(text('arguments', 4000).split())}".strip()
        else:
            prompt = text("consigne", 20000, True)
        try:
            sched = Schedule.model_validate(project_tools.schedule(args.get("planification")))
        except ValueError as exc:
            raise TaskError(f"planification invalide : {exc}") from exc
        model = t["model"] if t.get("model") and t["model"] != "default" else ""
        r = {"name": text("nom", 80, True), "prompt": prompt, "profile": t["profile"], "preset": t["preset"],
             "model": model, "effort": t.get("effort") or "", "workdir": proj.folder, "schedule": sched.model_dump(),
             "open_window": True}
        try:
            self._check_routine(Routine.model_validate(r))
        except ValueError as exc:
            raise TaskError(f"routine invalide : {exc}") from exc
        return {**base, "nom": r["name"], "consigne": prompt, "routine": r, "planification": sched.label(),
                "compte": t["profile_name"], "preset": t["preset_name"], "modele": model or "par défaut"}

    def _apply_proposal(self, tid: str, prop: dict, answers: dict) -> str:
        t = self._get(tid)
        if prop["quoi"] == "action":
            path = Path(prop["fichier"])
            now = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
            if now != prop["remplace"]:
                raise TaskError("le fichier de l'action a changé pendant la proposition.")
            self._write_doc(path, prop["contenu"])
            a = next((x for x in self.project_actions(prop["dossier"]) if x["name"] == prop["nom"]), None)
            if not a:
                raise TaskError("l'action écrite est introuvable.")
            self._pin_action(prop["dossier"], a["name"], a["hash"])
            self._audit("action de projet ajoutée", {"projet": prop["projet"], "action": f"/{a['name']}",
                                                      "fichier": str(path), "remplace": prop["remplace"] is not None}, t)
            return (f"Action « /{a['name']} » {'remplacée' if prop['remplace'] is not None else 'ajoutée'} au projet "
                    f"« {prop['projet']} » ({path}) : elle apparaît comme un bouton « {a['label']} » dans la console.")
        enabled = str(answers.get("activer", "oui")).lower() not in ("non", "false", "0")
        r = self.save_routine({**prop["routine"], "enabled": enabled})
        self._audit("routine proposée par Claude", {"projet": prop["projet"], "nom": r["name"], "active": enabled}, t)
        when = (" Prochaine exécution : " + time.strftime("%d/%m/%Y %H:%M", time.localtime(r["next_run"])) + "."
                if enabled and r.get("next_run") else "")
        return (f"Routine « {r['name']} » ({r['schedule_label']}) ajoutée au projet "
                f"{'et activée' if enabled else 'mais désactivée : l’utilisateur l’activera'}.{when}")

    # -------------------------------------------------------- rules remembered for a project
    def project_allow(self, folder: str) -> list[str]:
        key = norm(folder or "")
        return [r.pattern for r in self.cfg.project_rules if norm(r.folder) == key]

    def add_project_rules(self, folder: str, patterns: list[str], tool: str = "") -> list[str]:
        clean = []
        for pat in patterns:
            try:
                rule = ProjectRule(folder=folder, pattern=pat, created=time.time())
            except ValueError as e:
                raise TaskError(f"Règle invalide : {pat}.") from e
            why = project_rule_problem(rule.pattern, tool)
            if why:
                raise TaskError(f"Règle refusée ({rule.pattern}) : {why}.")
            clean.append(rule)
        cfg = self.cfg.model_copy(deep=True)
        known = set(self.project_allow(folder))
        new = [r for r in clean if r.pattern not in known]
        if not new:
            return []
        cfg.project_rules.extend(new)
        self.cfg_store.save(cfg, f"règle mémorisée pour {Path(folder).name} : {', '.join(r.pattern for r in new)}")
        with self._lock:  # discussions already running in this folder use it at once
            for tid, run in self.runs.items():
                t = self.tasks.get(tid)
                if run.policy and t and norm(t["workdir"]) == norm(folder):
                    run.policy.project_allow = self.project_allow(folder)
        self._audit("règle mémorisée pour un projet", {"dossier": folder, "règles": [r.pattern for r in new]})
        return [r.pattern for r in new]

    def delete_project_rule(self, folder: str, pattern: str):
        cfg = self.cfg.model_copy(deep=True)
        before = len(cfg.project_rules)
        cfg.project_rules = [r for r in cfg.project_rules if not (norm(r.folder) == norm(folder) and r.pattern == pattern)]
        if len(cfg.project_rules) == before:
            raise TaskError("Règle introuvable.", 404)
        self.cfg_store.save(cfg, f"règle retirée pour {Path(folder).name} : {pattern}")
        with self._lock:
            for tid, run in self.runs.items():
                t = self.tasks.get(tid)
                if run.policy and t and norm(t["workdir"]) == norm(folder):
                    run.policy.project_allow = self.project_allow(folder)
        self._audit("règle de projet retirée", {"dossier": folder, "règle": pattern})

    def _park(self, tid: str, run: Run, appr: Approval, respond):
        """Hold a tool call until the user decides; answer the CLI from a side thread."""
        t = self.tasks[tid]
        with self._lock:
            run.approvals[appr.id] = appr
            if run.awaiting_since is None:
                run.awaiting_since = time.time()
            t["pending"] = [a.public() for a in run.approvals.values()]
            t["status"] = "awaiting"
            self._event(tid, "approval", appr.public())
            self._save(t)

        def waiter():
            appr.event.wait()
            try:
                respond(appr)
            finally:
                with self._lock:
                    run.approvals.pop(appr.id, None)
                    t["pending"] = [a.public() for a in run.approvals.values()]
                    if not run.approvals and run.awaiting_since is not None:
                        run.awaiting_total += time.time() - run.awaiting_since
                        run.awaiting_since = None
                    if t["status"] == "awaiting" and not run.approvals:
                        t["status"] = "running"
                    self._event(tid, "approval_done", {"id": appr.id, "decision": appr.decision,
                                                       "message": appr.message, "by": appr.by, "tool": appr.tool})
                    self._audit("validation", {"outil": appr.tool, "décision": appr.decision,
                                               "par": appr.by, "message": appr.message,
                                               "cible": summarize_target(appr.tool, appr.input)}, t)
                    self._save(t)

        threading.Thread(target=waiter, name=f"approval-{appr.id}", daemon=True).start()

    # -------------------------------------------------------- scheduling
    def _scheduler(self):
        while not self._stop:
            self._wake.wait(1.0)
            self._wake.clear()
            with self._lock:
                if self.emergency:
                    continue
                busy = [t for t in self.tasks.values() if t["id"] in self.runs]
                limit = self.cfg.general.max_concurrent
                for tid in list(self.queue):
                    t = self.tasks.get(tid)
                    if not t or t["status"] != "queued" or tid in self.runs:
                        if t is None or t["status"] != "queued":
                            self.queue.remove(tid)
                        continue
                    if t.get("not_before") and t["not_before"] > time.time():
                        continue  # scheduled for later (the account's limit resets then)
                    if len(busy) >= limit:
                        break
                    per = (t["spec"]["profile"] or {}).get("max_concurrent", 2)
                    if sum(1 for b in busy if b["profile"] == t["profile"]) >= per:
                        continue
                    self.queue.remove(tid)
                    run = Run(cost_base=float(t.get("cost_usd") or 0),
                              usage_base={m: dict(u) for m, u in (t.get("model_usage") or {}).items()})
                    self.runs[tid] = run
                    busy.append(t)
                    t["status"] = "running"
                    t["started"] = t.get("started") or time.time()
                    t["ended"] = None
                    self._event(tid, "status", {"status": "running"})
                    self._save(t)
                    threading.Thread(target=self._run, args=(tid, run), name=f"task-{tid}", daemon=True).start()

    def _watchdog(self):
        while not self._stop:
            time.sleep(1.0)
            g = self.cfg.general
            now = time.time()
            with self._lock:
                runs = list(self.runs.items())
            if now - self._warm_checked >= 30:
                self._warm_checked = now
                self._keep_warm_tick(now)
            for tid, run in runs:
                waiting = run.awaiting_total + ((now - run.awaiting_since) if run.awaiting_since else 0)
                if run.stop_status is None and now - run.started - waiting > g.task_timeout_min * 60:
                    try:
                        self.cancel(tid, f"Durée maximale dépassée ({g.task_timeout_min} min).", status="error")
                    except TaskError:
                        pass
                for appr in list(run.approvals.values()):
                    if appr.decision is None and now - appr.created > g.approval_timeout_min * 60:
                        appr.claim("deny", f"Délai de validation dépassé ({g.approval_timeout_min} min).", "console")
                # Background work is over and the lead said nothing since: the session can close.
                t = self.tasks.get(tid) or {}
                if (run.had_background and not run.background and not run.stdin_closed and run.got_result
                        and run.results >= run.sent and not run.approvals and not t.get("queued_messages")
                        and now - run.last_output > 45):
                    self._close_stdin(run)

    # -------------------------------------------------------- one run = one CLI process
    @staticmethod
    def _preset_for(spec: dict) -> Preset:
        """The task's preset; in team mode the delegation tool is offered and allowed
        (every action of a subagent still goes through the same checks)."""
        pre = Preset.model_validate(spec["preset"])
        if spec.get("team"):
            tools = [*pre.tools, "Agent"] if pre.tools and "Agent" not in pre.tools else pre.tools
            pre = pre.model_copy(update={"tools": tools, "allow": [*pre.allow, "Agent", "Task"]})
        return pre

    def _command(self, t: dict, run: Run) -> list[str]:
        cli = self.cli()
        if not cli:
            raise TaskError("Claude Code introuvable : installe-le ou indique son chemin (Configuration → Général).")
        spec = t["spec"]
        prof = Profile.model_validate(spec["profile"])
        pre = self._preset_for(spec)
        rules = [ToolRule.model_validate(r) for r in spec["rules"]]
        args = [*cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--forward-subagent-text",
                "--permission-prompt-tool", "stdio"]
        args += cli_permission_args(pre, rules)
        if t["model"] and t["model"] != "default":
            args += ["--model", t["model"]]
        if t.get("effort"):
            args += ["--effort", t["effort"]]
        args += ["--max-turns", str(spec.get("max_turns") or 60)]
        for d in [*(t.get("add_dirs") or []), *([t["attachments_dir"]] if t.get("attachments_dir") else [])]:
            args += ["--add-dir", d]
        team = spec.get("team") or {}
        proj = self._project(t["workdir"])
        where = presence.prompt(t, prof, pre, proj.name if proj else "", ask_user=self.cfg.general.ask_user_questions,
                                actions=[a for a in self.project_actions(proj.folder) if a["status"] == "ok"] if proj else None,
                                routines=self.project_routines(proj.folder) if proj else None,
                                apps=mcp.web_apps(prof))
        system = "\n\n".join(x for x in (where, spec.get("security_instructions", ""), prof.instructions, team.get("prompt", ""))
                             if x and x.strip())
        if team.get("agents"):
            f = self.runtime / f"{t['id']}-{secrets.token_hex(3)}.agents.json"
            f.write_text(json.dumps(team["agents"], ensure_ascii=False), encoding="utf-8")
            run.files.append(str(f))
            args += ["--agents", str(f)]
        if system:
            f = self.runtime / f"{t['id']}-{secrets.token_hex(3)}.prompt.md"
            f.write_text(system, encoding="utf-8")
            run.files.append(str(f))
            args += ["--append-system-prompt-file", str(f)]
        mcp_file = mcp.write_config(prof, self.runtime, f"{t['id']}-{secrets.token_hex(3)}",
                                    extra={CONSOLE_MCP: {"type": "sdk", "name": CONSOLE_MCP}})
        if mcp_file:
            run.files.append(mcp_file)
            args += ["--mcp-config", mcp_file]
        if prof.mcp.strict:
            args.append("--strict-mcp-config")
        if prof.chrome:
            args.append("--chrome")
        if t.get("session_started"):
            args += ["--resume", t["session_id"]]
            if t.get("fork_next"):
                # Continue a Claude Desktop / CLI session in a copy: the original stays untouched.
                args.append("--fork-session")
        else:
            t["session_id"] = str(uuid.uuid4())
            args += ["--session-id", t["session_id"]]
        name = re.sub(r"[^\w .,:'-]", "", f"JARVIS - {t['title']}")[:80]
        args += ["--name", name]
        return args

    def _run(self, tid: str, run: Run):
        t = self.tasks[tid]
        spec = t["spec"]
        try:
            prof = Profile.model_validate(spec["profile"])
            pre = self._preset_for(spec)
            adir = t.get("attachments_dir")
            if adir:
                Path(adir).mkdir(parents=True, exist_ok=True)
            run.policy = Policy(pre, [ToolRule.model_validate(r) for r in spec["rules"]],
                                [InputConstraint.model_validate(c) for c in spec["constraints"]],
                                policy_context(t["workdir"], [*(t.get("add_dirs") or []), *([adir] if adir else [])],
                                               spec["forbidden"], str(self.data_dir), [self.port]),
                                project_allow=self.project_allow(t["workdir"]))
            cmd = self._command(t, run)
            env = claude_cli.build_env(prof, self.cfg.general)
            if spec.get("team"):
                # the built-in subagents (general-purpose, Explore…) no longer inherit the chief's model
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = spec["team"].get("subagent_default") or "sonnet"
            resumed = bool(t.get("session_started"))
            self._event(tid, "info", {"text": ("Reprise de la session" if resumed else "Démarrage")
                                      + f" · {prof.name} · {pre.name} · {t['model']}"})
            self._audit("exécution", {"reprise": resumed, "mode": pre.mode, "commande": [Path(cmd[0]).name, *cmd[1:]]}, t)
            run.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                        errors="replace", cwd=t["workdir"], env=env,
                                        bufsize=1, **claude_cli.spawn_kwargs())
        except TaskError as exc:
            run.stop_status, run.stop_reason = "error", exc.message
            self._finish(tid, run)
            return
        except OSError as exc:
            run.stop_status, run.stop_reason = "error", f"Impossible de lancer Claude Code : {exc}"
            self._finish(tid, run)
            return
        except Exception as exc:  # noqa: BLE001 - never leave a task stuck in "running"
            run.stop_status, run.stop_reason = "error", f"Préparation impossible : {exc}"
            self._finish(tid, run)
            return

        threading.Thread(target=self._pump_stderr, args=(run,), daemon=True).start()
        self._write(run, {"type": "control_request", "request_id": f"init_{tid}",
                          "request": {"subtype": "initialize", "sdkMcpServers": [CONSOLE_MCP], "hooks": {
                              "PreToolUse": [{"matcher": None, "hookCallbackIds": [HOOK_ID], "timeout": 86400}]}}})
        with self._lock:
            pending = list(t.get("queued_messages") or [])
            t["queued_messages"] = []
        for text in pending:
            self._send_user(run, text)
        try:
            for line in run.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                run.last_output = time.time()
                try:
                    msg = json.loads(line)
                except ValueError:
                    self._event(tid, "info", {"text": _clip(line, 500)})
                    continue
                try:
                    self._handle(tid, run, msg)
                except Exception as exc:  # noqa: BLE001 - one bad message must not kill the task
                    self._event(tid, "info", {"text": f"Message ignoré ({exc.__class__.__name__}: {exc})"})
        finally:
            try:
                run.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                claude_cli.kill_tree(run.proc.pid)
            self._finish(tid, run)

    def _pump_stderr(self, run: Run):
        try:
            for line in run.proc.stderr:
                if line.strip():
                    run.stderr.append(line.rstrip()[:500])
        except (OSError, ValueError):
            pass

    def _finish(self, tid: str, run: Run):
        for f in run.files:
            mcp.remove_config(f)
        adir = self.tasks[tid].get("attachments_dir")
        if adir:
            try:
                Path(adir).rmdir()  # only when empty: no stray folder for tasks without attachments
            except OSError:
                pass
        self._release_approvals(run, "La tâche s'est arrêtée.")
        with self._lock:
            t = self.tasks[tid]
            self.runs.pop(tid, None)
            rc = run.proc.returncode if run.proc else None
            if run.stop_status:
                status, err = run.stop_status, run.stop_reason
            elif run.got_result:
                status = "error" if run.last_error else "done"
                err = "" if status == "done" else (t.get("error") or "")
            else:
                tail = " | ".join(list(run.stderr)[-4:])
                status, err = "error", f"Claude Code s'est arrêté sans résultat (code {rc})." + (f" {tail}" if tail else "")
            t["status"], t["error"] = status, err
            t["ended"] = time.time()
            t["pending"] = []
            if err:
                self._event(tid, "error" if status == "error" else "status", {"status": status, "text": err})
            else:
                self._event(tid, "status", {"status": status})
            self._audit("tâche terminée", {"statut": status, "coût_usd": round(t.get("cost_usd", 0), 4),
                                           "tours": t.get("turns", 0), "erreur": err}, t)
            if t.get("queued_messages") and status not in ("cancelled",) and not self.emergency:
                t["status"] = "queued"
                self.queue.append(tid)
                self._event(tid, "status", {"status": "queued"})
            self._save(t)
            if t.get("routine"):
                self._routine_done(t)
        self.bus.publish("state", self.state())
        self._wake.set()

    # -------------------------------------------------------- stream handling
    def _handle(self, tid: str, run: Run, msg: dict):
        t = self.tasks[tid]
        typ = msg.get("type")
        if typ == "control_request":
            try:
                self._control(tid, run, msg)
            except Exception as exc:  # noqa: BLE001 - the CLI waits for an answer: never leave it hanging
                reason = f"Erreur interne de la console ({exc.__class__.__name__}: {exc}) : action refusée par sécurité."
                sub = (msg.get("request") or {}).get("subtype")
                rid = msg.get("request_id", "")
                if sub == "hook_callback":
                    self._respond(run, rid, _hook("deny", reason))
                elif sub == "can_use_tool":
                    self._respond(run, rid, {"behavior": "deny", "message": reason})
                else:
                    self._respond(run, rid, error=reason)
                self._event(tid, "error", {"text": reason})
        elif typ == "control_response":
            self._on_init(t, msg.get("response") or {})
        elif typ == "system":
            self._on_system(tid, t, msg)
        elif typ == "rate_limit_event":
            self._on_rate_limit(t["profile"], msg.get("rate_limit_info") or {})
        elif typ == "stream_event":
            ev = msg.get("event") or {}
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    self._event(tid, "delta", {"text": d["text"], "parent": msg.get("parent_tool_use_id")},
                                persist=False)
        elif typ == "assistant":
            self._on_assistant(tid, t, msg)
        elif typ == "user":
            self._on_user(tid, msg)
        elif typ == "result":
            self._on_result(tid, t, run, msg)

    def _on_init(self, t: dict, resp: dict):
        r = resp.get("response") or {}
        if resp.get("subtype") != "success" or not r:
            return
        acc = r.get("account") or {}
        src = acc.get("tokenSource")
        logged = bool(src and src != "none") or bool(acc.get("email") or acc.get("emailAddress"))
        cached = self.probes.setdefault(t["profile"], {})
        cached.update({
            "commands": [{"name": c.get("name"), "description": c.get("description", ""),
                          "hint": c.get("argumentHint", "")} for c in r.get("commands") or []],
            "agents": [{"name": a.get("name"), "description": a.get("description", "")} for a in r.get("agents") or []],
            "models": [{"value": m.get("value"), "label": m.get("displayName") or m.get("value"),
                        "resolved": m.get("resolvedModel"), "efforts": m.get("supportedEffortLevels") or []}
                       for m in r.get("models") or []],
            "logged_in": logged,
            "account": {k: v for k, v in acc.items() if "token" not in k.lower() or k == "tokenSource"},
        })
        self.store.kv_set("probes", self.probes)
        self.bus.publish("probe", {"profile": t["profile"], "probe": cached})

    # -------------------------------------------------------- plan usage limits
    @staticmethod
    def _fraction(v) -> float | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f / 100 if f > 1.5 else f  # the CLI gives 0..1; percentages are tolerated

    def _on_rate_limit(self, pid: str, info: dict):
        """Every response of Claude carries the account's limits (5-hour session, week…): keep the latest."""
        if not isinstance(info, dict) or not info:
            return
        with self._lock:
            cur = dict(self.limits.get(pid) or {})
            windows = dict(cur.get("windows") or {})
            for name, w in (info.get("unifiedWindows") or {}).items():
                used = self._fraction((w or {}).get("utilization"))
                if used is not None:
                    windows[name] = {"used": used, "resets_at": (w or {}).get("resetsAt")}
            kind, used = info.get("rateLimitType"), self._fraction(info.get("utilization"))
            if kind and used is not None:  # the documented fields: the window that currently applies
                windows[kind] = {"used": used, "resets_at": info.get("resetsAt") or (windows.get(kind) or {}).get("resets_at")}
            cur.update({"status": info.get("status") or cur.get("status"), "type": kind or cur.get("type"),
                        "resets_at": info.get("resetsAt") or cur.get("resets_at"), "windows": windows,
                        "updated": time.time(), "error": ""})
            if any(k in info for k in ("overageStatus", "overageDisabledReason", "isUsingOverage")):
                cur["overage"] = {"status": info.get("overageStatus"), "reason": info.get("overageDisabledReason"),
                                  "using": bool(info.get("isUsingOverage"))}
            self.limits[pid] = cur
            self.store.kv_set("limits", self.limits)
        self.bus.publish("limits", {"profile": pid, "limits": cur})

    def limit_block(self, pid: str) -> float | None:
        """When the account's limit is reached: the time it resets (epoch), else None."""
        cur = self.limits.get(pid) or {}
        if cur.get("status") != "rejected":
            return None
        now = time.time()
        full = [w["resets_at"] for k, w in (cur.get("windows") or {}).items()
                if (w.get("resets_at") or 0) > now and ((w.get("used") or 0) >= 0.999 or k == cur.get("type"))]
        if not full and (cur.get("resets_at") or 0) > now:
            full = [cur["resets_at"]]
        return max(full) if full else None  # blocked until the last full window resets

    def refresh_limits(self, pid: str) -> dict:
        """Ask Claude for the account's limits now: one tiny Haiku request (the answer is discarded)."""
        prof = self.cfg.profile(pid)
        if not prof:
            raise TaskError("Profil inconnu.", 404)
        cli = self.cli()
        if not cli:
            raise TaskError("Claude Code introuvable.", 404)
        with self._lock:
            if pid in self._limits_busy:
                return {"started": False}
            self._limits_busy.add(pid)

        def work():
            try:
                wd = expand_path(prof.workdir)
                Path(wd).mkdir(parents=True, exist_ok=True)
                res = claude_cli.limits_probe(cli, claude_cli.build_env(prof, self.cfg.general), wd)
                for info in res.get("events") or []:
                    self._on_rate_limit(pid, info)
                if res.get("error"):
                    with self._lock:
                        cur = dict(self.limits.get(pid) or {})
                        cur.update({"error": res["error"], "checked": time.time()})
                        self.limits[pid] = cur
                        self.store.kv_set("limits", self.limits)
                    self.bus.publish("limits", {"profile": pid, "limits": cur})
            finally:
                with self._lock:
                    self._limits_busy.discard(pid)

        threading.Thread(target=work, name=f"limits-{pid}", daemon=True).start()
        return {"started": True}

    def _refresh_stale_limits(self, max_age: float = 3 * 3600):
        time.sleep(5)  # let the console start first
        for prof in self.cfg.profiles:
            cur = self.limits.get(prof.id) or {}
            if not self._stop and time.time() - (cur.get("updated") or 0) > max_age:
                try:
                    self.refresh_limits(prof.id)
                except TaskError:
                    pass

    def _on_background(self, run: Run, msg: dict):
        """Live background tasks, from the CLI's level signal (and its start/end bookends as a fallback).
        "Ambient" tasks (watchers that never end) do not keep the session open."""
        st = msg.get("subtype")
        if st == "background_tasks_changed":
            run.background = {x.get("task_id") for x in msg.get("tasks") or [] if x.get("task_id") and not x.get("ambient")}
        elif st == "task_started" and msg.get("is_backgrounded"):
            run.background.add(msg.get("task_id"))
        elif st == "task_updated" and (msg.get("patch") or {}).get("status") in ("completed", "failed", "killed"):
            run.background.discard(msg.get("task_id"))
        elif st == "task_notification":
            run.background.discard(msg.get("task_id"))
        if run.background:
            run.had_background = True

    def _on_system(self, tid: str, t: dict, msg: dict):
        st = msg.get("subtype")
        if st in ("background_tasks_changed", "task_started", "task_updated", "task_notification"):
            run = self.runs.get(tid)
            if run:
                self._on_background(run, msg)
        if st == "init":
            with self._lock:
                t["session_id"] = msg.get("session_id") or t["session_id"]
                t["session_started"] = True
                t["fork_next"] = False
                t["model_resolved"] = msg.get("model") or ""
                t["mcp"] = [{"name": s.get("name"), "status": s.get("status")} for s in msg.get("mcp_servers") or []
                            if s.get("name") != CONSOLE_MCP]
                self._save(t)
            self._event(tid, "init", {"model": msg.get("model"), "mcp": t["mcp"],
                                      "tools": len(msg.get("tools") or []),
                                      "permission_mode": msg.get("permissionMode"),
                                      "skills": len(msg.get("slash_commands") or msg.get("skills") or [])})
        elif st == "compact_boundary":
            meta = msg.get("compact_metadata") or {}
            pre, post = int(meta.get("pre_tokens") or 0), int(meta.get("post_tokens") or 0)
            how = "automatiquement" if meta.get("trigger") == "auto" else "à ta demande"
            if post:
                with self._lock:
                    t["context_tokens"], t["context_at"] = post, time.time()
                    self._save(t)
            size = f" : {_ktok(pre)} → {_ktok(post)} tokens." if pre and post else f" ({_ktok(pre)} tokens avant)." if pre else "."
            self._event(tid, "info", {"text": f"Contexte compacté {how}{size}"})
        elif st and "retry" in st:
            self._event(tid, "info", {"text": f"Nouvelle tentative de l'API ({msg.get('attempt', '?')})…"})

    def _on_assistant(self, tid: str, t: dict, msg: dict):
        parent = msg.get("parent_tool_use_id")
        u = (msg.get("message") or {}).get("usage") or {}
        size = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if size and not parent and size != t.get("context_tokens"):
            with self._lock:
                t["context_tokens"], t["context_at"] = size, time.time()
                self._save(t)
        for block in (msg.get("message") or {}).get("content") or []:
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                self._event(tid, "text", {"text": block["text"], "parent": parent})
            elif kind == "thinking" and block.get("thinking"):
                self._event(tid, "thinking", {"text": _clip(block["thinking"], 6000), "parent": parent})
            elif kind in ("tool_use", "server_tool_use"):
                name, inp = block.get("name", ""), block.get("input") or {}
                self._remember_call(tid, block.get("id"), name, inp)
                self._event(tid, "tool", {"id": block.get("id"), "name": name, "parent": parent,
                                          "target": _clip(summarize_target(name, inp), 400),
                                          "input": _clip_json(inp, 8000)})
                if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                    with self._lock:
                        t["todos"] = [{"content": str(x.get("content", ""))[:300], "status": x.get("status")}
                                      for x in inp["todos"] if isinstance(x, dict)]
                        self._save(t)

    def _on_user(self, tid: str, msg: dict):
        content = (msg.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                self._remember_result(tid, block.get("tool_use_id"), block.get("content"), bool(block.get("is_error")))
                self._event(tid, "tool_result", {"id": block.get("tool_use_id"),
                                                 "is_error": bool(block.get("is_error")),
                                                 "preview": _clip(_tool_result_text(block.get("content")), 4000),
                                                 "parent": msg.get("parent_tool_use_id")})
                self._record_change(tid, str(block.get("tool_use_id") or ""), bool(block.get("is_error")),
                                    msg.get("parent_tool_use_id") or "")

    # -------------------------------------------------------- file changes (differences, undo: changes.py)
    def _snap(self, run: Run, t: dict, tool: str, tin: dict, cid: str = ""):
        """The file a file tool is about to write, as it is now. Taken again by each later check of the
        same call (an approval may wait a while): the copy is the file just before the write."""
        if tool not in changes_mod.TOOLS:
            return
        snap = changes_mod.snapshot(tool, tin, t["workdir"])
        if not snap:
            return
        with self._snaps_lock:
            key = cid if cid else next((k for k, s in reversed(run.snaps.items()) if s.tool == tool and s.path == snap.path), "")
            if key in run.snaps:
                snap.ts = run.snaps[key].ts  # (its place among the pending writes of the same file)
            run.snaps[key or f"?{secrets.token_hex(4)}"] = snap
            while len(run.snaps) > 64 or sum(len(s.before or b"") for s in run.snaps.values()) > 64 * 1024 * 1024:
                run.snaps.pop(next(iter(run.snaps)))

    def _record_change(self, tid: str, cid: str, failed: bool, parent: str = ""):
        """A file tool's result: what it changed is kept (before / after) if the file really changed."""
        run = self.runs.get(tid)
        if not run or not run.snaps or not cid:
            return
        with self._calls_lock:
            call = (self._calls.get(tid) or {}).get(cid) or {}
        tool = call.get("name", "")
        with self._snaps_lock:
            snap = run.snaps.pop(cid, None)
            if snap is None and tool in changes_mod.TOOLS:
                # a CLI that does not give the call's id to the hook: the oldest pending write of that file
                path = changes_mod.target(tool, call.get("input") or {}, self.tasks[tid]["workdir"])
                key = next((k for k, s in run.snaps.items() if k.startswith("?") and s.tool == tool and s.path == path), None)
                snap = run.snaps.pop(key) if key else None
            # writes of the same file still pending (parallel calls): this one ends where the next begins
            later = sorted((s for s in run.snaps.values() if snap and s.path == snap.path and s.ts > snap.ts),
                           key=lambda s: s.ts)
        if snap is None or failed:
            return
        try:
            after = later[0].before if later else changes_mod.read(snap.path)
            row = self.changes.record(tid, snap, after, cid, parent)
        except (changes_mod.ChangeError, OSError):
            return
        if not row:
            return
        with self._lock:
            t = self.tasks[tid]
            t["changed_files"] = len({r["path"] for r in self.changes.rows(tid)})
            self._save(t)
        self._event(tid, "change", changes_mod.Changes.public(row, with_status=False))

    def _change_error(self, e: changes_mod.ChangeError) -> TaskError:
        return TaskError(str(e), e.status, **({"conflict": True} if e.conflict else {}))

    def file_changes(self, tid: str) -> dict:
        self._get(tid)
        return {"changes": self.changes.listing(tid)}

    def file_change(self, tid: str, cid: str) -> dict:
        self._get(tid)
        try:
            return self.changes.diff(tid, cid)
        except changes_mod.ChangeError as e:
            raise self._change_error(e) from e

    def _change_allowed(self, t: dict, cid: str) -> dict:
        try:
            row = self.changes.get(t["id"], cid)
        except changes_mod.ChangeError as e:
            raise self._change_error(e) from e
        if self._task_guard(t).forbidden_hit({"file_path": row["path"]}, "Write"):
            raise TaskError("Ce fichier est protégé par la configuration de sécurité : la console n'y écrit pas.", 403)
        return row

    def undo_change(self, tid: str, cid: str, force: bool = False, redo: bool = False) -> dict:
        """The user undoes one of Claude's changes (redo: undoes the undo). Claude learns it with the next
        message, so that it does not take the file for what it wrote."""
        t = self._get(tid)
        self._change_allowed(t, cid)
        try:
            row = (self.changes.redo if redo else self.changes.undo)(tid, cid, force)
        except changes_mod.ChangeError as e:
            raise self._change_error(e) from e
        what = "rétablie" if redo else "annulée"
        with self._lock:
            t = self._get(tid)
            notes = [n for n in t.get("change_notes") or [] if n.get("path") != row["path"]]
            t["change_notes"] = [*notes, {"path": row["path"], "state": row["state"]}][-20:]
            self._save(t)
        self._event(tid, "change_state", {"id": cid, "state": row["state"], "path": row["path"],
                                          **({"forced": True} if row.get("forced") and not redo else {})})
        self._audit(f"modification {what}", {"chemin": row["path"], "outil": row["tool"],
                                             **({"forcée": True} if force else {})}, t)
        return changes_mod.Changes.public(row)

    def undo_file(self, tid: str, path: str) -> dict:
        """Every change of one file, newest first: the file as it was before the discussion touched it."""
        rows = [r for r in self.changes.rows(tid) if r["path"] == path and r.get("state") != "annule"]
        if not rows:
            raise TaskError("Aucune modification de ce fichier à annuler.", 404)
        done = []
        for r in reversed(rows):
            try:
                done.append(self.undo_change(tid, r["id"]))
            except TaskError as e:
                if not done:
                    raise
                return {"undone": done, "stopped": e.message}
        return {"undone": done}

    def _change_notes(self, t: dict) -> str:
        """What the user undid (or redid) since Claude's last message, for the next message."""
        notes = t.pop("change_notes", None) or []
        undone = [n["path"] for n in notes if n.get("state") == "annule"]
        redone = [n["path"] for n in notes if n.get("state") == "fait"]
        lines = []
        if undone:
            lines.append("L'utilisateur a annulé tes modifications de ces fichiers depuis la console (ils sont revenus "
                         "à leur état d'avant ; relis-les avant de les modifier) :\n" + "\n".join(f"- {p}" for p in undone))
        if redone:
            lines.append("L'utilisateur a rétabli tes modifications de ces fichiers :\n" + "\n".join(f"- {p}" for p in redone))
        return "\n\n".join(lines)

    def _on_result(self, tid: str, t: dict, run: Run, msg: dict):
        run.results += 1
        run.got_result = True
        is_error = bool(msg.get("is_error")) or (msg.get("subtype") or "success") != "success"
        run.last_error = is_error
        text = msg.get("result") or ""
        hint = ""
        if is_error and re.search(r"not logged in|/login|invalid api key|oauth", text, re.I):
            hint = "Ce profil n'est pas connecté : Configuration → Profils → Se connecter."
            self.probes.setdefault(t["profile"], {})["logged_in"] = False
            self.store.kv_set("probes", self.probes)
        elif is_error and re.search(r"usage limit|rate limit|limit reached|quota", text, re.I):
            hint = f"Limite d'usage atteinte sur le profil « {t['profile_name']} ». Aucune bascule automatique vers un autre compte."
        subtype_msg = {"error_max_turns": "Nombre maximal de tours atteint.",
                       "error_during_execution": "Erreur pendant l'exécution."}.get(msg.get("subtype"), "")
        with self._lock:
            t["turns"] += int(msg.get("num_turns") or 0)
            t["cost_usd"] = round(run.cost_base + float(msg.get("total_cost_usd") or 0), 6)
            mu = msg.get("modelUsage")
            if isinstance(mu, dict) and mu:
                t["model_usage"] = _merge_usage(run.usage_base, mu)
                win = int((mu.get(t.get("model_resolved") or "") or {}).get("contextWindow") or 0)
                k = self.cfg.general.compact_at_k * 1000
                t["context_limit"] = min(x for x in (win, k) if x) if (win or k) else 0
            t["duration_ms"] += int(msg.get("duration_ms") or 0)
            if text or is_error or not t.get("result"):  # a /compact ends with an empty result: keep the last answer
                t["result"] = _clip(text, 200000)
            t["is_error"] = is_error
            if is_error:
                t["error"] = hint or subtype_msg or _clip(text, 500)
            u = msg.get("usage") or {}
            acc = t.setdefault("usage", {})
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                acc[k] = acc.get(k, 0) + int(u.get(k) or 0)
            self._save(t)
        denials = [{"tool": d.get("tool_name"), "target": summarize_target(d.get("tool_name", ""), d.get("tool_input") or {})}
                   for d in msg.get("permission_denials") or []]
        self._event(tid, "result", {"text": _clip(text, 200000), "is_error": is_error,
                                    "subtype": msg.get("subtype"), "hint": hint or subtype_msg,
                                    "cost": msg.get("total_cost_usd"), "duration_ms": msg.get("duration_ms"),
                                    "turns": msg.get("num_turns"), "denials": denials})
        if run.results >= run.sent:
            with self._lock:
                more = list(t.get("queued_messages") or [])
                t["queued_messages"] = []
            for m in more:
                self._send_user(run, m)
            if not more and not run.background:
                self._close_stdin(run)
            elif not more:
                self._event(tid, "info", {"text": f"Travail en arrière-plan en cours ({len(run.background)}) : "
                                                  "la session reste ouverte jusqu'à son retour."})

    # -------------------------------------------------------- control protocol
    def _respond(self, run: Run, rid: str, payload: dict | None = None, error: str | None = None):
        if error is not None:
            resp = {"subtype": "error", "request_id": rid, "error": error}
        else:
            resp = {"subtype": "success", "request_id": rid, "response": payload or {}}
        self._write(run, {"type": "control_response", "response": resp})

    def _control(self, tid: str, run: Run, msg: dict):
        t = self.tasks[tid]
        rid = msg.get("request_id", "")
        req = msg.get("request") or {}
        sub = req.get("subtype")
        pol = run.policy
        pre_unlisted = (t["spec"]["preset"] or {}).get("unlisted", "ask")
        if sub == "hook_callback":
            hin = req.get("input") or {}
            if hin.get("hook_event_name", "PreToolUse") != "PreToolUse":
                return self._respond(run, rid, {})
            tool, tin = hin.get("tool_name", ""), hin.get("tool_input") or {}
            if tool in CONSOLE_TOOLS:
                # Only shows things to the user; each file is checked like a preview (task folders, protected
                # paths) and a web address outside the approved domains waits for the user's click.
                return self._respond(run, rid, _hook("allow", "Affichage dans la console."))
            v = pol.evaluate(tool, tin)
            target = summarize_target(tool, tin)
            self._audit("appel d'outil", {"outil": tool, "cible": _clip(target, 500), "décision": v.decision,
                                          "raison": v.reason}, t)
            if v.decision == "deny":
                self._event(tid, "policy", {"tool": tool, "decision": "deny", "reason": v.reason,
                                            "target": _clip(target, 300)})
                return self._respond(run, rid, _hook(v.decision, v.reason))
            if tool in INTERACTIVE_TOOLS:
                return self._respond(run, rid, {})
            cid = str(req.get("tool_use_id") or hin.get("tool_use_id") or "")
            self._snap(run, t, tool, tin, cid)
            if v.decision == "ask":
                appr = Approval(id=secrets.token_hex(4), request_id=rid, kind="hook", tool=tool, suggest=suggest_rules(tool, tin),
                                input=tin, reason=v.reason)

                def answer(a: Approval, rid=rid):
                    reason = (f"Refusé par l'utilisateur{' : ' + a.message if a.message else '.'}"
                              if a.decision == "deny" else "Approuvé par l'utilisateur.")
                    if a.decision == "allow":
                        self._snap(run, t, tool, tin, cid)  # the file may have changed while the approval waited
                    self._respond(run, rid, _hook("allow" if a.decision == "allow" else "deny", reason))
                return self._park(tid, run, appr, answer)
            if v.decision == "allow" and is_mcp(tool):
                return self._respond(run, rid, _hook("allow", v.reason))
            if v.decision == "default" and pre_unlisted == "deny":
                # Enforced here too: in dontAsk / bypass modes the CLI may never ask.
                reason = f"Outil non autorisé par le preset « {t['preset_name']} »."
                self._event(tid, "policy", {"tool": tool, "decision": "deny", "reason": reason,
                                            "target": _clip(target, 300)})
                return self._respond(run, rid, _hook("deny", reason))
            return self._respond(run, rid, {})

        if sub == "can_use_tool":
            tool, tin = req.get("tool_name", ""), req.get("input") or {}
            if tool == "AskUserQuestion":
                appr = Approval(id=secrets.token_hex(4), request_id=rid, kind="question", tool=tool,
                                input=tin, reason="Claude te pose une question.")

                def answer_q(a: Approval, rid=rid, tin=tin):
                    if a.decision == "allow":
                        self._respond(run, rid, {"behavior": "allow",
                                                 "updatedInput": {**tin, "answers": a.answers or {}}})
                    else:
                        self._respond(run, rid, {"behavior": "deny",
                                                 "message": a.message or "L'utilisateur n'a pas répondu."})
                return self._park(tid, run, appr, answer_q)
            v = pol.evaluate(tool, tin)
            cid = str(req.get("tool_use_id") or "")
            if v.decision == "deny":
                self._event(tid, "policy", {"tool": tool, "decision": "deny", "reason": v.reason,
                                            "target": _clip(summarize_target(tool, tin), 300)})
                return self._respond(run, rid, {"behavior": "deny", "message": v.reason})
            if v.decision == "allow" and tool not in INTERACTIVE_TOOLS:
                self._snap(run, t, tool, tin, cid)
                return self._respond(run, rid, {"behavior": "allow", "updatedInput": tin})
            if v.decision == "ask" or pre_unlisted == "ask" or tool == "ExitPlanMode":
                kind = "plan" if tool == "ExitPlanMode" else "permission"
                appr = Approval(id=secrets.token_hex(4), request_id=rid, kind=kind, tool=tool, input=tin,
                                suggest=suggest_rules(tool, tin) if kind == "permission" else [],
                                reason=v.reason if v.decision == "ask" else "Claude Code demande l'autorisation.")

                def answer_p(a: Approval, rid=rid, tin=tin):
                    if a.decision == "allow":
                        self._snap(run, t, tool, tin, cid)  # the file may have changed while the approval waited
                        self._respond(run, rid, {"behavior": "allow", "updatedInput": tin})
                    else:
                        self._respond(run, rid, {"behavior": "deny", "message":
                                                 f"Refusé par l'utilisateur{' : ' + a.message if a.message else '.'}"})
                return self._park(tid, run, appr, answer_p)
            reason = f"Outil non autorisé par le preset « {t['preset_name']} »."
            self._event(tid, "policy", {"tool": tool, "decision": "deny", "reason": reason,
                                        "target": _clip(summarize_target(tool, tin), 300)})
            return self._respond(run, rid, {"behavior": "deny", "message": reason})

        if sub == "mcp_message":
            message = req.get("message") or {}
            if (req.get("server_name") == CONSOLE_MCP and message.get("method") == "tools/call"
                    and (message.get("params") or {}).get("name") == PROPOSE_SPEC["name"]):
                return self._propose(tid, run, rid, message)
            return self._respond(run, rid, {"mcp_response": self._console_mcp(tid, req.get("server_name"), message)})

        self._respond(run, rid, error=f"Requête non prise en charge par la console : {sub}")

    # -------------------------------------------------------- the console's own MCP server
    def _console_mcp(self, tid: str, server: str | None, msg: dict, silent: bool = False) -> dict:
        """JSON-RPC answer of the in-process "jarvis" server (the CLI dials it at start)."""
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}

        def ok(result):
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        if server != CONSOLE_MCP:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Serveur inconnu : {server}"}}
        if method == "initialize":
            return ok({"protocolVersion": params.get("protocolVersion") or "2024-11-05", "capabilities": {"tools": {}},
                       "serverInfo": {"name": CONSOLE_MCP, "version": "1.0.0"}})
        if method == "tools/list":
            return ok({"tools": [SHOW_SPEC, RESULT_SPEC, PRESENT_SPEC, PROPOSE_SPEC]})
        if method == "tools/call":
            if silent:
                return ok({"content": [{"type": "text", "text": "Indisponible pendant un maintien du cache."}], "isError": True})
            args = params.get("arguments") or {}
            if params.get("name") == SHOW_SPEC["name"]:
                text, failed = self.show_files(tid, args.get("fichiers"))
            elif params.get("name") == RESULT_SPEC["name"]:
                text, failed = self.show_result(tid, args)
            elif params.get("name") == PRESENT_SPEC["name"]:
                text, failed = self.present(tid, args)
            elif params.get("name") == PROPOSE_SPEC["name"]:
                text, failed = "La proposition passe par la fenêtre de la discussion.", True  # (see _propose)
            else:
                text, failed = f"Outil inconnu : {params.get('name')}", True
            return ok({"content": [{"type": "text", "text": text}], "isError": failed})
        if method and method.startswith("notifications/"):
            return {"jsonrpc": "2.0", "result": {}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Méthode non prise en charge : {method}"}}

    def show_files(self, tid: str, items) -> tuple[str, bool]:
        """Claude shows files to the user: each one checked like a preview, then opened in the UI."""
        if isinstance(items, str):
            items = [items]
        items = [str(x).strip().strip('"') for x in (items or []) if str(x).strip()][:12]
        if not items:
            return ("Aucun fichier indiqué : passe « fichiers », une liste de chemins ou d'adresses https:// "
                    "(ex. {\"fichiers\": [\"https://…\"]}). Pour un résultat d'outil déjà reçu (un mail…), "
                    "afficher_resultat ; pour composer une fiche ou un tableau, presenter."), True
        files, urls, ask, errors = [], [], [], []
        trusted = self.cfg.security.trusted_domains
        for item in items:
            if re.match(r"^https?://", item, re.I):
                if not item.lower().startswith("https://"):
                    errors.append(f"{item} : seules les adresses https:// s'affichent")
                elif not web_domain(item):
                    errors.append(f"{item} : adresse invalide")
                else:
                    (urls if trusted_url(item, trusted) else ask).append(item)
                continue
            try:
                files.append(str(self.task_file(tid, item)))
            except TaskError as exc:
                errors.append(f"{item} : {exc}")
        if files or urls or ask:
            self._event(tid, "show", {"files": files, "urls": urls, "ask": ask})
        lines = []
        if files or urls:
            lines.append("Affiché dans la console JARVIS : " + ", ".join([*files, *urls]))
        if ask:
            lines.append("Proposé à l'utilisateur, qui l'ouvrira s'il le souhaite (domaine non approuvé dans la "
                         "configuration de la console) : " + ", ".join(ask))
        if errors:
            lines.append("Non affiché : " + " ; ".join(errors))
        return "\n".join(lines), not (files or urls or ask)

    # -------------------------------------------------------- displays composed by Claude (tool "presenter")
    def _display(self, tid: str, key: str) -> dict | None:
        """{doc, rev, answers} of a display: from memory, or rebuilt from the stored events after a restart."""
        with self._displays_lock:
            d = self._displays.get(tid, {}).get(key)
        if d is not None:
            return d
        found = None
        after = 0
        while True:
            evs = self.store.events(tid, after=after)
            for ev in evs:
                data = ev["data"] or {}
                if data.get("key") != key:
                    continue
                if ev["kind"] == "display":
                    found = {"doc": {k: data[k] for k in ("titre", "ou", "blocs")}, "rev": data.get("rev", 1), "answers": {}}
                elif ev["kind"] == "display_answer" and found and data.get("rev", found["rev"]) == found["rev"]:
                    if data.get("annule"):
                        found["answers"].pop(int(data.get("bloc", -1)), None)
                    else:
                        found["answers"][int(data.get("bloc", -1))] = data.get("labels") or []
            if len(evs) < 5000:
                break
            after = evs[-1]["seq"]
        if found:
            with self._displays_lock:
                self._displays.setdefault(tid, {}).setdefault(key, found)
        return found

    def present(self, tid: str, args: dict) -> tuple[str, bool]:
        """Claude composes a display: blocks checked (files like a preview, web addresses), then drawn by the UI."""
        t = self._get(tid)
        args = args if isinstance(args, dict) else {}
        key = display_mod.key_of(args.get("id")) if str(args.get("id") or "").strip() else display_mod.key_of(None)
        prev = self._display(tid, key) if args.get("id") else None
        doc, problems, waiting = display_mod.check(
            args, lambda p: str(self.task_file(tid, p)), self.cfg.security.trusted_domains,
            previous=prev["doc"] if prev else None)
        if doc is None:
            return "Rien d'affiché. " + " ; ".join(problems), True
        rev = (prev["rev"] + 1) if prev else 1
        with self._displays_lock:
            per_task = self._displays.setdefault(tid, {})
            per_task[key] = {"doc": doc, "rev": rev, "answers": {}}
            while len(per_task) > 100:  # old ones are rebuilt from the events if Claude reuses them
                per_task.pop(next(iter(per_task)))
        self._event(tid, "display", {"key": key, "rev": rev, **doc})
        self._audit("affichage", {"titre": _clip(doc["titre"], 200), "où": doc["ou"], "id": key,
                                  "blocs": [b["type"] for b in doc["blocs"]], **({"mise à jour": rev} if rev > 1 else {})}, t)
        where = {"conversation": "dans la conversation", "fenetre": "dans une fenêtre", "modale": "au premier plan"}[doc["ou"]]
        lines = [f"{'Mis à jour' if prev else 'Affiché'} {where} de la console JARVIS : « {doc['titre']} » "
                 f"({display_mod.summary(doc)}). Identifiant : {key} (à passer en id pour le modifier)."]
        if any(b["type"] in ("choix", "actions", "application") for b in doc["blocs"]):
            lines.append("Les réponses de l'utilisateur t'arriveront comme un nouveau message, préfixé par "
                         f"« [Affichage « {doc['titre']} »] » : termine ton tour sans les attendre.")
        if waiting:
            lines.append(f"{waiting} image(s) du web hors des domaines approuvés : l'utilisateur les charge d'un clic.")
        if problems:
            lines.append("Ignoré : " + " ; ".join(problems))
        return "\n".join(lines), False

    def display_answer(self, tid: str, key: str, index: int, choice: list[int] | None = None,
                       other: str = "", button: int | None = None) -> dict:
        """A click in a display (a choice, a button): the message is built here from the stored display, then
        sent to the session like a follow-up."""
        t = self._get(tid)
        d = self._display(tid, key)
        if not d:
            raise TaskError("Affichage introuvable.", 404)
        if index in d["answers"]:
            raise TaskError("Déjà répondu.", 409)
        try:
            message, labels = display_mod.answer_text(d["doc"], index, choice, other, button)
        except ValueError as exc:
            raise TaskError(str(exc)) from exc
        d["answers"][index] = labels
        self._event(tid, "display_answer", {"key": key, "rev": d["rev"], "bloc": index, "labels": labels})
        try:
            out = self.followup(tid, message)
        except TaskError:
            d["answers"].pop(index, None)
            self._event(tid, "display_answer", {"key": key, "rev": d["rev"], "bloc": index, "labels": [], "annule": True})
            raise
        self._audit("réponse à un affichage", {"titre": _clip(d["doc"]["titre"], 200), "réponse": labels}, t)
        return out

    APP_MESSAGES = 40  # messages one application may send to its session

    def _app_block(self, tid: str, key: str, index: int) -> tuple[dict, dict]:
        d = self._display(tid, key)
        if not d:
            raise TaskError("Affichage introuvable.", 404)
        blocks = d["doc"]["blocs"]
        if not 0 <= index < len(blocks) or blocks[index]["type"] != "application":
            raise TaskError("Ce bloc n'est pas une application.", 404)
        return d, blocks[index]

    def display_app(self, tid: str, key: str, index: int) -> dict:
        """The address of an application of a display: a shell on the preview origin around the sandboxed page."""
        d, b = self._app_block(tid, key, index)
        title = b.get("titre") or d["doc"]["titre"]
        return {"url": self.content_url(self.contents.put_app(b["html"], title, self.content_url)), "hauteur": b["hauteur"]}

    def display_app_message(self, tid: str, key: str, index: int, text: str) -> dict:
        """jarvis.envoyer() in an application, after a click of the user: sent to the session as a message."""
        t = self._get(tid)
        d, b = self._app_block(tid, key, index)
        text = str(text or "").strip()
        if not text:
            raise TaskError("Message vide.")
        sent = d.setdefault("app_sent", {})
        if sent.get(index, 0) >= self.APP_MESSAGES:
            raise TaskError(f"Cette application a déjà envoyé {self.APP_MESSAGES} messages : rouvre-la depuis Claude.", 429)
        if len(text) > 8000:
            text = text[:8000].rstrip() + "…"
        name = b.get("titre") or d["doc"]["titre"]
        out = self.followup(tid, f"[Affichage « {d['doc']['titre']} »] Application « {name} » :\n{text}")
        sent[index] = sent.get(index, 0) + 1
        self._audit("message d'une application", {"titre": _clip(name, 200), "caractères": len(text)}, t)
        return out

    # -------------------------------------------------------- results of tools, shown as they are
    def _remember_call(self, tid: str, cid: str | None, name: str, inp: dict):
        if not cid or name in CONSOLE_TOOLS:
            return
        with self._calls_lock:
            calls = self._calls.setdefault(tid, {})
            calls[cid] = {"id": cid, "name": name, "input": inp, "content": None, "is_error": False, "ts": time.time()}
            while len(calls) > KEEP_CALLS:
                calls.pop(next(iter(calls)))

    def _remember_result(self, tid: str, cid: str | None, result, is_error: bool):
        with self._calls_lock:
            c = (self._calls.get(tid) or {}).get(cid or "")
            if c is not None:
                c["content"], c["is_error"] = result, is_error

    def _tool_calls(self, t: dict, transcript: bool) -> list[dict]:
        """The task's tool calls with a result, newest first: those seen since the console started, then
        (transcript=True) the older ones of its session's transcript."""
        with self._calls_lock:
            seen = [c for c in (self._calls.get(t["id"]) or {}).values() if c["content"] is not None]
        prof = self.cfg.profile(t.get("profile") or "")
        if transcript and prof and t.get("session_id"):
            main = activity_mod.transcript(prof, t["workdir"], t["session_id"])
            if not main.is_file():
                main = library.find_transcript(prof, t["session_id"]) or main
            sub = main.with_suffix("") / "subagents"
            paths = [main, *(sorted(sub.glob("*.jsonl")) if sub.is_dir() else [])]
            known = {c["id"] for c in seen}
            seen = [c for c in results_mod.from_transcript(paths) if c["id"] not in known
                    and c["name"] not in CONSOLE_TOOLS] + seen
        return seen[::-1]

    def _find_call(self, t: dict, args: dict) -> tuple[dict | None, int]:
        """The tool call that Claude designates (afficher_resultat), and how many matched."""
        want_id = str(args.get("id") or "").strip()
        tool = str(args.get("outil") or "").strip().lower()
        needle = str(args.get("contient") or "").strip().lower()
        try:
            rank = max(1, min(50, int(args.get("rang") or 1)))
        except (TypeError, ValueError):
            rank = 1
        matched = []
        for transcript in (False, True):
            matched = []
            for c in self._tool_calls(t, transcript):
                if want_id and c["id"] != want_id:
                    continue
                if tool and tool not in c["name"].lower():
                    continue
                text = results_mod.text_of(c["content"])
                if c["is_error"] and not results_mod.SAVED.search(text):
                    continue
                if needle and needle not in text.lower() and needle not in self._saved_text(t, text).lower():
                    continue
                matched.append(c)
                if len(matched) >= rank:
                    return c, len(matched)
        return None, len(matched)

    def _saved_text(self, t: dict, text: str) -> str:
        prof = self.cfg.profile(t.get("profile") or "")
        p = results_mod.saved_file(text, library.config_dir(prof)) if prof else None
        try:
            return p.read_text(encoding="utf-8", errors="replace") if p else ""
        except OSError:
            return ""

    def show_result(self, tid: str, args: dict) -> tuple[str, bool]:
        """Claude shows a result it already received: the console finds it and opens it in a window."""
        t = self._get(tid)
        args = args if isinstance(args, dict) else {}
        call, n = self._find_call(t, args)
        if not call:
            what = " ; ".join(f"{k} = {args[k]}" for k in ("outil", "contient", "rang", "id") if args.get(k))
            return (f"Aucun résultat d'outil ne correspond{f' ({what})' if what else ''}"
                    f"{f' : seulement {n} trouvé(s)' if n else ''}."), True
        needle = str(args.get("contient") or "")
        try:
            v = self._view(t, call, needle)
        except (OSError, ValueError) as exc:
            return f"Résultat illisible : {exc}", True
        label = {"mail": "mail", "html": "page", "json": "données", "text": "texte", "image": "image"}.get(v["kind"], "résultat")
        self._event(tid, "show", {"files": [], "urls": [], "ask": [], "results": [
            {"id": call["id"], "tool": call["name"], "kind": v["kind"], "title": v["title"], "contient": needle}]})
        self._audit("résultat affiché", {"outil": call["name"], "type": v["kind"], "titre": _clip(v["title"], 200)}, t)
        return f"Affiché dans la console JARVIS : {label} « {v['title']} » (résultat de `{call['name']}`).", False

    def _view(self, t: dict, call: dict, needle: str) -> dict:
        prof = self.cfg.profile(t.get("profile") or "")
        return results_mod.view(call["content"], needle=needle, config_dir=library.config_dir(prof) if prof else None)

    def content_url(self, cid: str) -> str:
        return f"http://{content.HOST}:{self.port}/v/{cid}/"

    def result_view(self, tid: str, call_id: str, needle: str = "", remote: bool = False) -> dict:
        """A result shown by Claude, for its window: the HTML goes to the preview origin."""
        t = self._get(tid)
        call, _ = self._find_call(t, {"id": call_id})
        if not call:
            raise TaskError("Ce résultat n'est plus disponible (session introuvable ou effacée).", 404)
        v = self._view(t, call, needle)
        out = {**{k: x for k, x in v.items() if k != "page"}, "tool": call["name"], "id": call["id"]}
        if "text" in out:
            out["text"] = _clip(out["text"], 2_000_000)
        if v.get("page"):
            try:
                cid = self.contents.put(v["page"], remote=remote, style=content.MAIL_STYLE)
            except ValueError as exc:
                raise TaskError(str(exc), 413) from exc
            out.update(url=self.content_url(cid), remote=content.remote_refs(v["page"]))
        return out

    def _html_frame(self, p: Path, allowed, remote: bool) -> dict:
        """A local HTML file on the preview origin, with the files next to it (images, styles)."""
        if p.suffix.lower() not in (".html", ".htm", ".xhtml"):
            raise TaskError("Ce fichier n'est pas une page HTML.")
        page = content.decode_html(p.read_bytes())
        base = str(p.parent)

        def resolve(rest: str) -> Path | None:
            real = os.path.realpath(os.path.join(base, rest))
            return Path(real) if within(real, [base]) and os.path.isfile(real) and allowed(real) else None

        try:
            cid = self.contents.put(page, remote=remote, resolve=resolve)
        except ValueError as exc:
            raise TaskError(str(exc), 413) from exc
        return {"url": self.content_url(cid), "remote": content.remote_refs(page), "path": str(p)}

    def _task_guard(self, t: dict) -> Policy:
        spec = t.get("spec") or {}
        ctx = policy_context(t["workdir"], t.get("add_dirs") or [], spec.get("forbidden") or self.cfg.security.forbidden_paths,
                             str(self.data_dir), [self.port])
        return Policy(Preset.model_validate(spec["preset"]) if spec.get("preset") else self.cfg.presets[0], [], [], ctx)

    def task_frame(self, tid: str, path: str, remote: bool = False) -> dict:
        p = self.task_file(tid, path)
        guard = self._task_guard(self._get(tid))
        return self._html_frame(p, lambda real: not guard.forbidden_hit({"file_path": real}, "Read"), remote)

    def workspace_frame(self, pid: str | None, folder: str | None, path: str, remote: bool = False) -> dict:
        p = self.workspace_file(pid, folder, path)
        _, wd = self._ws_folder(pid, folder)
        guard = self._guard(wd)
        return self._html_frame(p, lambda real: within(real, [wd]) and not guard.forbidden_hit({"file_path": real}, "Read"), remote)

    # -------------------------------------------------------- misc actions
    def update_task(self, tid: str, patch: dict) -> dict:
        with self._lock:
            t = self._get(tid)
            if "pinned" in patch:
                t["pinned"] = bool(patch["pinned"])
            if "closed" in patch:
                t["closed"] = bool(patch["closed"])
                if t["closed"] and t.get("keep_warm_until"):
                    self._stop_warm(t, "fenêtre fermée")
            if "title" in patch and str(patch["title"]).strip():
                t["title"] = str(patch["title"]).strip()[:120]
            self.tasks[tid] = t
            self._save(t)
            return self.public(t)

    # -------------------------------------------------------- a title found by Claude
    TITLE_PROMPT = (
        "Voici des extraits d'une session de travail entre un utilisateur et Claude Code. Donne-lui un titre "
        "court et précis, dans la langue de l'utilisateur : 3 à 7 mots, sans guillemets ni point final, qui dit "
        "de quoi parle la session (sujet, client, livrable…) plutôt que ce qui a été demandé en premier. "
        "Réponds uniquement par le titre.\n\n<extraits>\n{context}\n</extraits>")

    def _title_context(self, t: dict, budget: int = 6000) -> str:
        """The first request, then the latest exchanges and actions of the session, within a budget."""
        def short(x, n):
            x = re.sub(r"\s+", " ", str(x or "")).strip()
            return x if len(x) <= n else x[:n] + "…"

        first = f"Première demande : {short(t.get('prompt'), 1500)}"
        lines = [f"{'Utilisateur' if i.get('role') == 'user' else 'Assistant'} (avant la reprise) : {short(i.get('text'), 500)}"
                 for i in (t.get("history") or [])[-30:] if i.get("text") and i.get("role") in ("user", "assistant")]
        for e in self.store.events(t["id"], limit=100_000):
            d = e["data"] or {}
            if e["kind"] == "user" and not d.get("first") and d.get("text"):
                lines.append(f"Utilisateur : {short(d['text'], 600)}")
            elif e["kind"] == "text" and not d.get("parent") and d.get("text"):
                lines.append(f"Assistant : {short(d['text'], 600)}")
            elif e["kind"] == "tool" and not d.get("parent") and d.get("target"):
                lines.append(f"Action : {d.get('name')} {short(d['target'], 200)}")
        keep, size = [], len(first)
        for line in reversed(lines):
            if size + len(line) + 1 > budget:
                break
            keep.append(line)
            size += len(line) + 1
        return "\n".join([first, *(["[…]"] if len(keep) < len(lines) else []), *reversed(keep)])

    # -------------------------------------------------------- keeping the prompt cache warm
    def keep_warm(self, tid: str, hours: float) -> dict:
        """Keep the session's cache warm for `hours` (0 stops)."""
        hours = float(hours or 0)
        if hours < 0 or hours > WARM_MAX_HOURS:
            raise TaskError(f"Durée entre 0 et {WARM_MAX_HOURS} heures.")
        with self._lock:
            t = self._get(tid)
            if hours and not t.get("session_started"):
                raise TaskError("La session n'a pas encore démarré : il n'y a pas de cache à garder.")
            if hours:
                t["keep_warm_until"] = time.time() + hours * 3600
                t.setdefault("warm", {"pings": 0, "read": 0, "written": 0, "output": 0, "last": 0, "misses": 0})
                until = time.strftime("%H:%M", time.localtime(t["keep_warm_until"]))
                self._event(tid, "info", {"text": f"Cache gardé au chaud jusqu'à {until} (un maintien toutes les 50 min tant que la discussion est inactive)."})
                self._audit("maintien du cache activé", {"jusqu'à": until}, t)
                self._save(t)
            elif t.get("keep_warm_until"):
                self._stop_warm(t, "arrêté à ta demande")
        self._wake.set()
        return self.public(t)

    def _stop_warm(self, t: dict, why: str):
        with self._lock:
            if not t.get("keep_warm_until"):
                return
            t["keep_warm_until"] = 0
            self._event(t["id"], "info", {"text": f"Maintien du cache arrêté : {why}."})
            self._save(t)

    def _near_limit(self, pid: str) -> bool:
        cur = self.limits.get(pid) or {}
        return cur.get("status") == "rejected" or any((w.get("used") or 0) >= 0.9 for w in (cur.get("windows") or {}).values())

    def _keep_warm_tick(self, now: float):
        for tid, t in list(self.tasks.items()):
            until = t.get("keep_warm_until") or 0
            if not until:
                continue
            if now >= until:
                self._stop_warm(t, "fin de la durée choisie")
            elif t.get("closed"):
                self._stop_warm(t, "fenêtre fermée")
            elif self.emergency:
                self._stop_warm(t, "arrêt d'urgence")
            elif tid in self.runs or tid in self._warming or t["status"] in ACTIVE:
                continue
            elif now - max(t.get("context_at") or 0, (t.get("warm") or {}).get("last") or 0) >= WARM_EVERY:
                if self._near_limit(t["profile"]):
                    self._stop_warm(t, "le quota du compte approche de sa limite")
                    continue
                self._warming.add(tid)
                threading.Thread(target=self._warm, args=(tid,), name=f"warm-{tid}", daemon=True).start()

    def _warm(self, tid: str):
        """One read of the session's cache by a throwaway copy of it: same command as the session (same
        prefix, hence the same cache), forked and not saved, auto-compaction off. Nothing reaches the
        conversation but a line saying what it cost."""
        t = self.tasks[tid]
        run = Run()
        proc = None
        try:
            cmd = self._command(t, run)
            if "--fork-session" not in cmd:
                cmd.append("--fork-session")
            cmd.append("--no-session-persistence")
            env = claude_cli.build_env(Profile.model_validate(t["spec"]["profile"]), self.cfg.general)
            if (t["spec"] or {}).get("team"):
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = t["spec"]["team"].get("subagent_default") or "sonnet"
            env["DISABLE_AUTO_COMPACT"] = "1"  # compacting a throwaway copy would be paid for nothing
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace", cwd=t["workdir"], env=env,
                                    bufsize=1, **claude_cli.spawn_kwargs())
            timer = threading.Timer(180, lambda: claude_cli.kill_tree(proc.pid))
            timer.start()

            def send(obj):
                proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                proc.stdin.flush()

            send({"type": "control_request", "request_id": f"warm_{tid}", "request": {
                "subtype": "initialize", "sdkMcpServers": [CONSOLE_MCP],
                "hooks": {"PreToolUse": [{"matcher": None, "hookCallbackIds": [HOOK_ID], "timeout": 60}]}}})
            send({"type": "user", "session_id": "", "parent_tool_use_id": None, "message": {"role": "user", "content": WARM_PROMPT}})
            usage, model = None, ""
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                typ = msg.get("type")
                if typ == "control_request":
                    req, rid = msg.get("request") or {}, msg.get("request_id", "")
                    sub = req.get("subtype")
                    if sub == "mcp_message":
                        resp = {"subtype": "success", "request_id": rid, "response": {"mcp_response": self._console_mcp(
                            tid, req.get("server_name"), req.get("message") or {}, silent=True)}}
                    elif sub == "hook_callback":
                        resp = {"subtype": "success", "request_id": rid, "response": _hook("deny", "Maintien du cache : aucune action.")}
                    elif sub == "can_use_tool":
                        resp = {"subtype": "success", "request_id": rid, "response": {"behavior": "deny", "message": "Maintien du cache."}}
                    else:
                        resp = {"subtype": "error", "request_id": rid, "error": "non pris en charge"}
                    send({"type": "control_response", "response": resp})
                elif typ == "rate_limit_event":
                    self._on_rate_limit(t["profile"], msg.get("rate_limit_info") or {})
                elif typ == "system" and msg.get("subtype") == "init":
                    model = msg.get("model") or ""
                elif typ == "result":
                    usage = msg.get("usage") or {}
                    break
            timer.cancel()
            self._warm_done(t, usage, model)
        except Exception as exc:  # noqa: BLE001 - a failed ping only stops the upkeep
            self._stop_warm(t, f"échec du maintien ({exc.__class__.__name__})")
        finally:
            if proc:
                try:
                    proc.stdin.close()
                    proc.wait(timeout=20)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    claude_cli.kill_tree(proc.pid)
            for f in run.files:
                mcp.remove_config(f)
            self._warming.discard(tid)

    def _warm_done(self, t: dict, usage: dict | None, model: str):
        if usage is None:
            self._stop_warm(t, "Claude Code n'a pas répondu")
            return
        read = int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("input_tokens") or 0)
        written = int(usage.get("cache_creation_input_tokens") or 0)
        hit = read >= 0.7 * (read + written)
        with self._lock:
            w = t.setdefault("warm", {"pings": 0, "read": 0, "written": 0, "output": 0, "last": 0, "misses": 0})
            w["pings"] += 1
            w["read"] += read
            w["written"] += written
            w["output"] += int(usage.get("output_tokens") or 0)
            w["last"] = time.time()
            w["misses"] = 0 if hit else w.get("misses", 0) + 1
            self._event(t["id"], "info", {"text": f"Maintien du cache : {_ktok(read)} tokens relus en cache"
                                                  + (f", {_ktok(written)} réécrits" if written >= 1000 else "")
                                                  + (" (le cache avait expiré, il est recréé)." if not hit else ".")})
            self._audit("maintien du cache", {"relus": read, "écrits": written, "modèle": model}, t)
            self._save(t)
        if w["misses"] >= 2:
            self._stop_warm(t, "le cache n'a pas pu être retrouvé deux fois de suite")

    # -------------------------------------------------------- what the session did (its transcripts)
    def activity(self, tid: str) -> dict:
        """Tools of the lead and of every sub-agent (background ones too), tokens per model and the
        lead's context, from the session's transcript files. Also fills the context gauge of a session
        that has not answered since the console started."""
        t = self._get(tid)
        prof = self.cfg.profile(t.get("profile") or "")
        if not prof or not t.get("session_started") or not t.get("session_id"):
            return {"available": False}
        a = activity_mod.session_activity(prof, t["workdir"], t["session_id"], hidden_servers=(CONSOLE_MCP,))
        if not a:
            return {"available": False}
        size, at = a["context"]["tokens"], a["context"]["at"]
        if size and at > (t.get("context_at") or 0) + 1:
            with self._lock:
                t["context_tokens"], t["context_at"] = size, at
                if not t.get("context_limit") and self.cfg.general.compact_at_k:
                    t["context_limit"] = self.cfg.general.compact_at_k * 1000
                self._save(t)
        return {"available": True, **a}

    def ai_title(self, tid: str) -> dict:
        """Rename a task after its content: one short Haiku request on the task's account, outside
        the conversation (nothing is written in the session, a running task is not disturbed)."""
        t = self._get(tid)
        prof = self.cfg.profile(t.get("profile") or "")
        if not prof:
            raise TaskError("Profil inconnu.", 404)
        cli = self.cli()
        if not cli:
            raise TaskError("Claude Code introuvable.", 404)
        until = self.limit_block(prof.id)
        if until:
            raise TaskError(f"Limite d'utilisation atteinte pour {prof.name} jusqu'à "
                            f"{time.strftime('%H:%M', time.localtime(until))}.", 429)
        wd = expand_path(prof.workdir) or str(self.runtime)
        Path(wd).mkdir(parents=True, exist_ok=True)
        res = claude_cli.oneshot(cli, claude_cli.build_env(prof, self.cfg.general), wd,
                                 self.TITLE_PROMPT.format(context=self._title_context(t)), timeout=90)
        for info in res.get("events") or []:
            self._on_rate_limit(prof.id, info)
        title = _clean_title(res.get("text"))
        if not title:
            raise TaskError(res.get("error") or "Claude n'a pas proposé de titre.", 502)
        self._audit("tâche renommée par Claude", {"titre": title, "ancien": t["title"]}, t)
        return self.update_task(tid, {"title": title})

    def delete_task(self, tid: str):
        with self._lock:
            t = self._get(tid)
            if t["status"] in ACTIVE:
                raise TaskError("Annule la tâche avant de la supprimer.", 409)
            self.tasks.pop(tid, None)
            self.store.delete_task(tid)
            self._audit("tâche supprimée", {"titre": t["title"]}, t)
        with self._calls_lock:
            self._calls.pop(tid, None)
        self.changes.drop(tid)
        self.bus.publish("deleted", {"id": tid})

    def list_tasks(self, include_closed: bool = True, limit: int = 500) -> list[dict]:
        with self._lock:
            items = sorted(self.tasks.values(), key=lambda t: t["created"], reverse=True)
        if not include_closed:
            items = [t for t in items if not t.get("closed")]
        return [self.public(t) for t in items[:limit]]

    def purge(self, days: int | None) -> int:
        cutoff = time.time() - (days * 86400 if days is not None else -1)
        n = self.store.purge(cutoff, sorted(TERMINAL))
        with self._lock:
            alive = {t["id"] for t in self.store.list_tasks(limit=100000)}
            self.tasks = {k: v for k, v in self.tasks.items() if k in alive}
        self.changes.sweep(alive)
        self._audit("purge de l'historique", {"tâches supprimées": n, "plus_de_jours": days})
        self.bus.publish("reload", {})
        return n

    def export_history(self) -> dict:
        tasks = [self.public(t) for t in sorted(self.tasks.values(), key=lambda t: t["created"])]
        return {"exported": time.time(), "tasks": [{**t, "events": self.store.events(t["id"])} for t in tasks]}

    def probe(self, pid: str) -> dict:
        prof = self.cfg.profile(pid)
        if not prof:
            raise TaskError("Profil inconnu.", 404)
        cli = self.cli()
        if not cli:
            raise TaskError("Claude Code introuvable.", 404)
        wd = expand_path(prof.workdir)
        Path(wd).mkdir(parents=True, exist_ok=True)
        mfile = mcp.write_config(prof, self.runtime, f"probe-{pid}-{secrets.token_hex(3)}")
        try:
            res = claude_cli.probe(cli, claude_cli.build_env(prof, self.cfg.general), wd,
                                   mcp_config=mfile, strict=prof.mcp.strict)
        finally:
            mcp.remove_config(mfile)
        res["cli"] = {"path": cli[-1],
                      "version": "test" if self._cli_override else claude_cli.cli_version(cli[0])}
        res["config_dir"] = expand_path(prof.config_dir) or str(Path.home() / ".claude")
        self.probes[pid] = res
        self.store.kv_set("probes", self.probes)
        self._audit("test de connexion", {"profil": prof.name, "connecté": res.get("logged_in"),
                                          "erreur": res.get("error", "")})
        self.bus.publish("probe", {"profile": pid, "probe": res})
        self.bus.publish("state", self.state())
        return res

    def open_login(self, pid: str):
        prof = self.cfg.profile(pid)
        cli = self.cli()
        if not prof or not cli:
            raise TaskError("Profil ou Claude Code introuvable.", 404)
        cd = expand_path(prof.config_dir)
        if cd:
            Path(cd).mkdir(parents=True, exist_ok=True)
        claude_cli.open_terminal(f"Connexion Claude - {prof.name}", cli[0], ["auth", "login"],
                                 claude_cli.build_env(prof, self.cfg.general), str(Path.home()))
        self._audit("connexion ouverte", {"profil": prof.name})

    def open_session_terminal(self, tid: str):
        t = self._get(tid)
        if not t.get("session_started"):
            raise TaskError("Cette tâche n'a pas encore de session Claude à reprendre.", 409)
        prof = self.cfg.profile(t["profile"]) or Profile.model_validate(t["spec"]["profile"])
        cli = self.cli()
        if not cli:
            raise TaskError("Claude Code introuvable.", 404)
        claude_cli.open_terminal(f"{prof.name} - {t['title']}", cli[0], ["--resume", t["session_id"]],
                                 claude_cli.build_env(prof, self.cfg.general), t["workdir"])
        self._audit("session ouverte dans un terminal", {"session": t["session_id"]}, t)

    # -------------------------------------------------------- previews: files of a task
    RISKY_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
                 ".wsh", ".hta", ".msi", ".msp", ".lnk", ".reg", ".cpl", ".jar", ".docm", ".xlsm", ".pptm", ".dotm",
                 ".app", ".command", ".sh", ".pkg", ".dmg", ".py", ".pyw"}

    def _cited(self, t: dict, asked: str, real: str) -> bool:
        """The task's own conversation (answers, tool inputs, resumed history) names this file."""
        spelled = os.path.expandvars(os.path.expanduser(asked))
        want = {norm(real).lower(), *([asked.replace("\\", "/").lower()] if os.path.isabs(spelled) else [])}
        return any(w in s.replace("\\", "/").lower() for s in self._leaves(t) for w in want)

    def _leaves(self, t: dict):
        """Every string of the task's conversation (events, tool inputs, resumed history)."""
        def leaves(v):
            if isinstance(v, str):
                yield v
            elif isinstance(v, dict):
                for x in v.values():
                    yield from leaves(x)
            elif isinstance(v, list):
                for x in v:
                    yield from leaves(x)

        for src in [e["data"] for e in self.store.events(t["id"], limit=100_000)] + [t.get("history") or []]:
            yield from leaves(src)

    SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build", ".cache"}

    def _relative_file(self, t: dict, asked: str, roots: list[str]) -> str | None:
        """A name given relative to the task ("logo.png", "images/logo.png") that is not directly in
        its folder: the file the task wrote or cited under that name, else the most recent match in
        its folders (bounded search)."""
        tail = "/" + re.sub(r"^(?:\.{1,2}/)+", "", asked.replace("\\", "/")).lower()
        cited = [s for s in self._leaves(t) if len(s) < 1024 and "\n" not in s and os.path.isabs(s)
                 and ("/" + s.replace("\\", "/").lower()).endswith(tail) and os.path.isfile(s)]
        if cited:
            return os.path.realpath(cited[-1])
        found, seen = [], 0
        for root in dict.fromkeys(r for r in roots if r and os.path.isdir(r)):
            for base, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in self.SKIP_DIRS and not d.startswith(".")]
                seen += len(files) + len(dirs)
                for f in files:
                    full = os.path.join(base, f)
                    if ("/" + full.replace("\\", "/").lower()).endswith(tail):
                        found.append(full)
                if seen > 20_000:
                    break
        return os.path.realpath(max(found, key=os.path.getmtime)) if found else None

    def task_file(self, tid: str, path: str) -> Path:
        """A file for preview: in the task's folders or the profile's, or cited by the task itself;
        never a forbidden path."""
        t = self._get(tid)
        asked = str(path or "").strip().strip('"')
        raw = os.path.expandvars(os.path.expanduser(asked))
        if not raw:
            raise TaskError("Chemin manquant.")
        real = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(t["workdir"], raw))
        prof = self.cfg.profile(t.get("profile") or "")
        roots = [t["workdir"], *(t.get("add_dirs") or []), *([t["attachments_dir"]] if t.get("attachments_dir") else []),
                 *([expand_path(prof.workdir)] if prof and prof.workdir else [])]
        if not os.path.isabs(raw) and not os.path.isfile(real):
            real = self._relative_file(t, raw, [t["workdir"], *(t.get("add_dirs") or [])]) or real
        if not within(real, roots) and not self._cited(t, asked, real):
            raise TaskError("Aperçu limité aux dossiers de la tâche et aux fichiers cités dans sa conversation.", 403)
        if self._task_guard(t).forbidden_hit({"file_path": real}, "Read"):
            raise TaskError("Ce fichier est protégé par la configuration de sécurité.", 403)
        p = Path(real)
        if not p.is_file():
            raise TaskError("Fichier introuvable.", 404)
        if p.stat().st_size > 100 * 1024 * 1024:
            raise TaskError("Fichier trop volumineux pour un aperçu (plus de 100 Mo).", 413)
        return p

    def open_task_file(self, tid: str, path: str, reveal: bool = False):
        self._open_path(self.task_file(tid, path), reveal, self._get(tid))

    def _open_path(self, p: Path, reveal: bool, t: dict | None = None):
        if not reveal and p.suffix.lower() in self.RISKY_EXT:
            raise TaskError("Ouverture refusée : ce type de fichier peut exécuter du code. Utilise « Afficher dans le dossier ».", 403)
        if os.name == "nt":
            if reveal:
                subprocess.Popen(["explorer", f"/select,{p}"])
            else:
                os.startfile(str(p))  # noqa: S606 - the user's own document, default application
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)] if reveal else ["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent if reveal else p)])
        self._audit("fichier ouvert" if not reveal else "fichier affiché dans le dossier", {"chemin": str(p)}, t)

    # -------------------------------------------------------- project folder: instructions, memory, files
    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".idea", ".vs"}
    MEM_NAME = re.compile(r"^[\w .()\-]{1,120}\.md$")

    def _guard(self, folder: str) -> Policy:
        return Policy(self.cfg.presets[0], [], [], policy_context(folder, [], self.cfg.security.forbidden_paths,
                                                                   str(self.data_dir), [self.port]))

    def _ws_folder(self, pid: str | None, folder: str | None) -> tuple[Profile, str]:
        prof = self.cfg.profile(pid or self.cfg.general.default_profile)
        if not prof:
            raise TaskError("Profil inconnu.", 404)
        default = expand_path(prof.workdir)
        p = Path(expand_path(folder) if folder else default)
        if default and os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(default)):
            p.mkdir(parents=True, exist_ok=True)
        if not p.is_dir():
            raise TaskError(f"Dossier introuvable : {p}", 404)
        wd = str(p.resolve())
        if self._guard(wd).forbidden_hit({"file_path": wd}, "Read"):
            raise TaskError("Ce dossier est protégé par la configuration de sécurité.", 403)
        return prof, wd

    def workspace(self, pid: str | None, folder: str | None) -> dict:
        prof, wd = self._ws_folder(pid, folder)

        def doc(path: Path) -> dict:
            try:
                text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            except OSError:
                text = ""
            return {"path": str(path), "exists": path.is_file(), "text": text[:400_000]}

        mem = library.memory_dir(prof, wd)
        notes = []
        if mem.is_dir():
            for f in mem.glob("*.md"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                notes.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime})
        notes.sort(key=lambda x: (x["name"] != "MEMORY.md", x["name"].lower()))
        with self._lock:
            used = [t["workdir"] for t in sorted(self.tasks.values(), key=lambda t: t.get("created") or 0, reverse=True)
                    if t.get("profile") == prof.id and t.get("workdir")]
        folders = list(dict.fromkeys([str(Path(expand_path(prof.workdir)).resolve()), *used]))[:20]
        return {"profile": prof.id, "profile_name": prof.name, "folder": wd, "folders": folders,
                "instructions": {"folder": doc(Path(wd) / "CLAUDE.md"), "profile": doc(library.config_dir(prof) / "CLAUDE.md")},
                "jarvis_instructions": prof.instructions, "memory": {"dir": str(mem), "files": notes},
                "rules": [{"pattern": r.pattern, "created": r.created} for r in self.cfg.project_rules if norm(r.folder) == norm(wd)]}

    def save_instructions(self, pid: str | None, folder: str | None, scope: str, text: str) -> dict:
        prof, wd = self._ws_folder(pid, folder)
        if scope not in ("folder", "profile"):
            raise TaskError("Portée inconnue.")
        if len(text or "") > 400_000:
            raise TaskError("Consignes trop longues (400 000 caractères au plus).")
        path = Path(wd) / "CLAUDE.md" if scope == "folder" else library.config_dir(prof) / "CLAUDE.md"
        self._write_doc(path, text or "")
        self._audit("consignes modifiées", {"fichier": str(path), "caractères": len(text or "")})
        return {"path": str(path), "exists": True}

    def _write_doc(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(3)}.tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(tmp, path)

    def _mem_path(self, pid: str | None, folder: str | None, name: str) -> Path:
        prof, wd = self._ws_folder(pid, folder)
        if not self.MEM_NAME.fullmatch(name or ""):
            raise TaskError("Nom de note de mémoire invalide.")
        return library.memory_dir(prof, wd) / name

    def memory_note(self, pid: str | None, folder: str | None, name: str) -> dict:
        p = self._mem_path(pid, folder, name)
        if not p.is_file():
            raise TaskError("Note introuvable.", 404)
        return {"name": name, "text": p.read_text(encoding="utf-8", errors="replace")[:400_000]}

    def save_memory_note(self, pid: str | None, folder: str | None, name: str, text: str) -> dict:
        p = self._mem_path(pid, folder, name)
        if len(text or "") > 400_000:
            raise TaskError("Note trop longue.")
        self._write_doc(p, text or "")
        self._audit("mémoire modifiée", {"note": str(p)})
        return {"name": name}

    def delete_memory_note(self, pid: str | None, folder: str | None, name: str):
        p = self._mem_path(pid, folder, name)
        if p.is_file():
            p.unlink()
            self._audit("note de mémoire supprimée", {"note": str(p)})

    def workspace_files(self, pid: str | None, folder: str | None, sub: str = "") -> dict:
        _, wd = self._ws_folder(pid, folder)
        base = os.path.realpath(os.path.join(wd, sub or ""))
        if not within(base, [wd]):
            raise TaskError("Hors du dossier du projet.", 403)
        if not os.path.isdir(base):
            raise TaskError("Dossier introuvable.", 404)
        guard, entries, more = self._guard(wd), [], False
        with os.scandir(base) as it:
            for e in it:
                if e.name in self.SKIP_DIRS:
                    continue
                full = os.path.join(base, e.name)
                if guard.forbidden_hit({"file_path": full}, "Read"):
                    continue  # protected files are not even listed
                try:
                    is_dir, st = e.is_dir(), e.stat()
                except OSError:
                    continue
                if len(entries) >= 1000:
                    more = True
                    break
                entries.append({"name": e.name, "dir": is_dir, "size": None if is_dir else st.st_size, "mtime": st.st_mtime})
        entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        rel = os.path.relpath(base, wd)
        return {"folder": wd, "sub": "" if rel == "." else rel.replace("\\", "/"), "entries": entries, "truncated": more}

    def workspace_file(self, pid: str | None, folder: str | None, path: str) -> Path:
        _, wd = self._ws_folder(pid, folder)
        raw = os.path.expandvars(os.path.expanduser(str(path or "").strip().strip('"')))
        if not raw:
            raise TaskError("Chemin manquant.")
        real = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(wd, raw))
        if not within(real, [wd]):
            raise TaskError("Hors du dossier du projet.", 403)
        if self._guard(wd).forbidden_hit({"file_path": real}, "Read"):
            raise TaskError("Ce fichier est protégé par la configuration de sécurité.", 403)
        p = Path(real)
        if not p.is_file():
            raise TaskError("Fichier introuvable.", 404)
        if p.stat().st_size > 100 * 1024 * 1024:
            raise TaskError("Fichier trop volumineux pour un aperçu (plus de 100 Mo).", 413)
        return p

    def open_workspace_file(self, pid: str | None, folder: str | None, path: str, reveal: bool = False):
        if path in ("", "."):
            _, wd = self._ws_folder(pid, folder)
            self._open_path(Path(wd), False)  # the folder itself, in the file explorer
            return
        self._open_path(self.workspace_file(pid, folder, path), reveal)

    # -------------------------------------------------------- folder picker (any folder of the disk)
    HIDDEN_DIRS = {"$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws", "config.msi", "recovery"}

    def fs_places(self) -> list[dict]:
        """Starting points of the folder picker: account folders, usual folders, drives."""
        home = Path.home()
        places, seen = [], set()

        def add(label: str, path, kind: str):
            try:
                p = str(Path(path).resolve())
            except (OSError, ValueError):
                return
            if p.lower() in seen or not os.path.isdir(p) or self._guard(p).forbidden_hit({"file_path": p}, "Read"):
                return
            seen.add(p.lower())
            places.append({"label": label, "path": p, "kind": kind})

        for prof in self.cfg.profiles:
            add(f"Dossier du compte {prof.name}", expand_path(prof.workdir), "account")
        known = {"Bureau": "Desktop", "Documents": "Documents", "Téléchargements": "Downloads"}
        if os.name == "nt":
            try:
                import ctypes
                for label, csidl in (("Bureau", 0x10), ("Documents", 0x05)):
                    buf = ctypes.create_unicode_buffer(260)
                    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
                        add(label, buf.value, "folder")
            except (AttributeError, OSError):
                pass
        for label, name in known.items():
            add(label, home / name, "folder")
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            if os.environ.get(var):
                add(Path(os.environ[var]).name, os.environ[var], "cloud")
        add("Dossier personnel", home, "home")
        if os.name == "nt":
            try:
                import ctypes
                mask, k32 = ctypes.windll.kernel32.GetLogicalDrives(), ctypes.windll.kernel32
            except (AttributeError, OSError):
                mask, k32 = 0, None
            for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
                if not mask >> i & 1 or letter in "AB":
                    continue
                root = f"{letter}:\\"
                kind = k32.GetDriveTypeW(root)  # 2 removable, 3 fixed, 4 network, 5 CD
                if kind == 4:  # a network drive: never probed (a disconnected one would freeze the picker)
                    places.append({"label": f"Réseau {letter}:", "path": root, "kind": "network"})
                elif kind in (2, 3, 5) and os.path.isdir(root):
                    places.append({"label": f"Disque {letter}:", "path": root, "kind": "drive"})
        elif sys.platform == "darwin" and os.path.isdir("/Volumes"):
            for v in sorted(os.listdir("/Volumes")):
                add(v, f"/Volumes/{v}", "drive")
        return places

    def fs_dirs(self, path: str) -> dict:
        """Sub-folders of a folder (names only), for the picker. Protected folders are left out."""
        raw = os.path.expandvars(os.path.expanduser(str(path or "").strip().strip('"'))) or str(Path.home())
        if not os.path.isabs(raw):
            raise TaskError("Indique un chemin complet (par exemple C:\\Users\\…\\Documents).")
        base = os.path.realpath(raw)
        if not os.path.isdir(base):
            raise TaskError(f"Dossier introuvable : {raw}", 404)
        guard = self._guard(base)
        if guard.forbidden_hit({"file_path": base}, "Read"):
            raise TaskError("Ce dossier est protégé par la configuration de sécurité.", 403)
        dirs, more = [], False
        try:
            with os.scandir(base) as it:
                for e in it:
                    try:
                        if not e.is_dir() or e.name.startswith(".") or e.name.lower() in self.HIDDEN_DIRS:
                            continue
                        if os.name == "nt" and e.stat().st_file_attributes & 0x6:  # hidden or system
                            continue
                    except OSError:
                        continue
                    if guard.forbidden_hit({"file_path": os.path.join(base, e.name)}, "Read"):
                        continue
                    if len(dirs) >= 2000:
                        more = True
                        break
                    dirs.append(e.name)
        except PermissionError as exc:
            raise TaskError("Accès refusé par Windows à ce dossier.", 403) from exc
        dirs.sort(key=str.lower)
        parent = os.path.dirname(base.rstrip("\\/")) if base.rstrip("\\/") != base[:3].rstrip("\\/") else ""
        if parent and os.path.normcase(parent) == os.path.normcase(base):
            parent = ""
        return {"path": base, "parent": parent, "dirs": dirs, "truncated": more}

    def fs_mkdir(self, path: str, name: str) -> dict:
        base = self.fs_dirs(path)["path"]
        clean = att.safe_name(name)
        if clean in ("fichier", "") and not str(name or "").strip():
            raise TaskError("Nom de dossier manquant.")
        target = Path(base) / clean
        if self._guard(base).forbidden_hit({"file_path": str(target)}, "Read"):
            raise TaskError("Ce dossier serait protégé par la configuration de sécurité.", 403)
        if target.exists():
            raise TaskError(f"« {clean} » existe déjà ici.", 409)
        target.mkdir()
        self._audit("dossier créé", {"chemin": str(target)})
        return {"path": str(target)}

    # -------------------------------------------------------- existing Claude Code sessions
    def sessions(self, pid: str | None = None) -> list[dict]:
        profiles = [p for p in self.cfg.profiles if pid in (None, "", p.id)]
        rows = []
        for p in profiles:
            for r in library.list_sessions(p):
                rows.append({**r, "profile_name": p.name, "color": p.color})
        rows.sort(key=lambda r: r["updated"] or 0, reverse=True)
        return rows

    def _session(self, pid: str, sid: str) -> tuple[Profile, dict]:
        prof = self.cfg.profile(pid)
        if not prof:
            raise TaskError("Profil inconnu.", 404)
        row = next((r for r in library.list_sessions(prof) if r["id"] == sid), None)
        if not row:
            raise TaskError("Session introuvable pour ce profil.", 404)
        return prof, row

    def session_transcript(self, pid: str, sid: str) -> dict:
        prof, row = self._session(pid, sid)
        return {"session": {**row, "profile_name": prof.name, "color": prof.color},
                "items": library.read_transcript(prof, sid)}

    def resume_session(self, pid: str, sid: str, prompt: str, preset: str | None = None, model: str | None = None,
                       effort: str | None = None, fork: bool = True, confirmed: bool = False,
                       workdir: str | None = None) -> dict:
        """Continue an existing session in the console; with another folder, it first moves there."""
        prof, row = self._session(pid, sid)
        origin = "reprise desktop" if row["origin"] == "desktop" else "reprise"
        if workdir and not (row["cwd"] and norm(workdir) == norm(row["cwd"])):
            self.move_session(prof.id, sid, workdir)
            prof, row = self._session(pid, sid)
        elif not row["resumable"]:
            raise TaskError(f"Le dossier de cette session n'existe plus ({row['cwd'] or 'inconnu'}) : "
                            "choisis un projet pour la reprendre.", 409)
        history = [{"role": i["role"], "text": (i.get("text") or f"{i.get('name')} · {i.get('target', '')}")[:1500]}
                   for i in library.read_transcript(prof, sid, limit=60) if not i.get("sidechain")]
        t = self.create_task(prompt, profile=prof.id, model=model, preset=preset, workdir=row["cwd"], effort=effort,
                             confirmed=confirmed, resume=sid, fork=fork, history=history, origin=origin)
        with self._lock:
            live = self.tasks[t["id"]]
            live["title"] = _title(row["title"]) if row["title"] else live["title"]
            self._save(live)
        return self.public(live)

    def open_existing_session_terminal(self, pid: str, sid: str):
        prof, row = self._session(pid, sid)
        cli = self.cli()
        if not cli or not row["resumable"]:
            raise TaskError("Claude Code ou le dossier de la session est introuvable.", 409)
        claude_cli.open_terminal(f"{prof.name} - {row['title'][:40]}", cli[0], ["--resume", sid],
                                 claude_cli.build_env(prof, self.cfg.general), row["cwd"])
        self._audit("session existante ouverte dans un terminal", {"profil": prof.name, "session": sid})

    # -------------------------------------------------------- cloud routines (claude.ai)
    def _relay(self, pid: str, params: dict):
        prof = self.cfg.profile(pid)
        cli = self.cli()
        if not prof or not cli:
            raise TaskError("Profil ou Claude Code introuvable.", 404)
        wd = expand_path(prof.workdir)
        Path(wd).mkdir(parents=True, exist_ok=True)
        try:
            status, body = cloud.relay(cli, claude_cli.build_env(prof, self.cfg.general), wd, params)
        except cloud.CloudError as exc:
            raise TaskError(str(exc), 502)
        if status >= 400:
            detail = body.get("error", {}).get("message") if isinstance(body, dict) and isinstance(body.get("error"), dict) else ""
            raise TaskError(f"claude.ai a refusé la demande (HTTP {status}){' : ' + detail if detail else ''}.", 502)
        return prof, body

    def cloud_routines(self, pid: str, refresh: bool = False) -> dict:
        key = f"cloud:{pid}"
        cached = self.store.kv_get(key)
        if cached and not refresh:
            return cached
        prof, body = self._relay(pid, {"action": "list"})
        items = body.get("data") if isinstance(body, dict) else body
        res = {"profile": pid, "fetched": time.time(), "routines": [cloud.summarize(t) for t in items or [] if isinstance(t, dict)],
               "has_more": bool(isinstance(body, dict) and body.get("has_more"))}
        self.store.kv_set(key, res)
        self._audit("routines cloud lues", {"profil": prof.name, "nombre": len(res["routines"])})
        return res

    def cloud_action(self, pid: str, rid: str, action: str, enabled: bool | None = None):
        if not re.fullmatch(r"[\w-]{1,128}", rid or ""):
            raise TaskError("Identifiant de routine invalide.")
        if action == "run":
            params = {"action": "run", "trigger_id": rid}
        elif action == "toggle":
            params = {"action": "update", "trigger_id": rid, "body": {"enabled": bool(enabled)}}
        elif action == "runs":
            params = {"action": "list_runs", "trigger_id": rid}
        else:
            raise TaskError("Action inconnue.")
        prof, body = self._relay(pid, params)
        self._audit(f"routine cloud : {action}", {"profil": prof.name, "routine": rid,
                                                   **({"active": bool(enabled)} if action == "toggle" else {})})
        if action == "runs":
            return {"runs": cloud.summarize_runs(body)}
        if action == "toggle":
            try:
                self.cloud_routines(pid, refresh=True)
            except TaskError:
                pass
        return {"ok": True}

    # -------------------------------------------------------- routines
    def _persist_routines(self):
        self.store.kv_set("routines", [r.model_dump() for r in self.routines.values()])
        self.bus.publish("routines", {"routines": self.list_routines()})

    def list_routines(self) -> list[dict]:
        return [r.public() for r in sorted(self.routines.values(), key=lambda r: (r.next_run or 9e18, r.name))]

    def _check_routine(self, r: Routine):
        prof = self.cfg.profile(r.profile)
        pre = self.cfg.preset(r.preset)
        if not prof:
            raise TaskError(f"Profil inconnu : {r.profile}")
        if not pre:
            raise TaskError(f"Preset inconnu : {r.preset}")
        if not pre.enabled:
            raise TaskError(f"Le preset « {pre.name} » est désactivé.")
        if pre.require_confirm:
            raise TaskError(f"Le preset « {pre.name} » exige une confirmation à chaque lancement : impossible en routine.")
        if r.workdir:
            self._workdir(prof, pre, r.workdir)

    def save_routine(self, data: dict) -> dict:
        from pydantic import ValidationError
        from .config import format_errors
        with self._lock:
            old = self.routines.get(str(data.get("id", "")))
            base = old.model_dump() if old else {}
            keep = {k: base[k] for k in ("created", "last_run", "runs") if k in base}
            try:
                r = Routine.model_validate({**data, **keep, "next_run": None})
            except ValidationError as exc:
                raise TaskError("Routine invalide : " + " ; ".join(format_errors(exc)), 422)
            self._check_routine(r)
            r.next_run = r.schedule.next_after(time.time(), r.last_run if r.schedule.kind != "once" else None) if r.enabled else None
            self.routines[r.id] = r
            self._audit("routine enregistrée", {"nom": r.name, "planification": r.schedule.label(),
                                                "profil": r.profile, "preset": r.preset, "active": r.enabled})
            self._persist_routines()
            return r.public()

    def delete_routine(self, rid: str):
        with self._lock:
            r = self.routines.pop(rid, None)
            if not r:
                raise TaskError("Routine inconnue.", 404)
            self._audit("routine supprimée", {"nom": r.name})
            self._persist_routines()

    def run_routine(self, rid: str, manual: bool = False) -> dict:
        with self._lock:
            r = self.routines.get(rid)
            if not r:
                raise TaskError("Routine inconnue.", 404)
            now = time.time()
            run = {"ts": now, "manual": manual, "task_id": None, "status": "lancée", "error": ""}
            try:
                if self.emergency:
                    raise TaskError("Arrêt d'urgence actif.", 423)
                self._check_routine(r)
                why = self._routine_action_problem(r)
                if why:
                    raise TaskError(why)
                blocked = self.limit_block(r.profile)
                t = self.create_task(r.prompt, profile=r.profile, model=r.model or None, preset=r.preset,
                                     workdir=r.workdir or None, effort=r.effort or None, confirmed=True,
                                     origin="routine", routine={"id": r.id, "name": r.name}, closed=not r.open_window,
                                     team=r.team, not_before=blocked + 60 if blocked else None)
                run["task_id"] = t["id"]
                if blocked:
                    run["status"] = "reportée"
                    run["error"] = "Limite du compte atteinte : lancée à la réinitialisation, " \
                                   + time.strftime("%d/%m %H:%M", time.localtime(blocked + 60)) + "."
            except TaskError as exc:
                run["status"], run["error"] = "non lancée", exc.message
                t = None
            r.runs = ([run] + r.runs)[:20]
            if not manual:
                r.last_run = now
                r.next_run = r.schedule.next_after(now, now) if r.enabled else None
                if r.schedule.kind == "once":
                    r.enabled, r.next_run = False, None
            self._persist_routines()
            if run["status"] == "non lancée" and manual:
                raise TaskError(run["error"], 409)
            return t or {}

    def _schedule_routines(self, startup: bool = False):
        now = time.time()
        with self._lock:
            for r in self.routines.values():
                if not r.enabled:
                    r.next_run = None
                    continue
                if r.next_run and r.next_run <= now and startup and not r.catch_up:
                    r.runs = ([{"ts": now, "manual": False, "task_id": None, "status": "manquée",
                                "error": "La console était arrêtée à l'heure prévue."}] + r.runs)[:20]
                    r.next_run = None
                if not r.next_run:
                    r.next_run = r.schedule.next_after(now, r.last_run if r.schedule.kind != "once" else None)
                    if r.schedule.kind == "once" and not r.next_run:
                        r.enabled = False
            self.store.kv_set("routines", [r.model_dump() for r in self.routines.values()])

    def _routine_loop(self):
        while not self._stop:
            time.sleep(5)
            now = time.time()
            due = [r.id for r in list(self.routines.values()) if r.enabled and r.next_run and r.next_run <= now]
            for rid in due:
                try:
                    self.run_routine(rid)
                except Exception:  # noqa: BLE001 - one failing routine must not stop the others
                    pass

    def _routine_done(self, t: dict):
        info = t.get("routine") or {}
        r = self.routines.get(info.get("id", ""))
        if not r:
            return
        for run in r.runs:
            if run.get("task_id") == t["id"]:
                run["status"] = t["status"]
                run["error"] = t.get("error", "")
                break
        self._persist_routines()


def _hook(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def dump_model(m) -> dict:
    return json.loads(m.model_dump_json())
