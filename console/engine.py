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

from . import claude_cli, cloud, library, mcp
from .config import (Config, ConfigStore, InputConstraint, Preset, Profile, ToolRule, dump,
                     expand_path)
from .permissions import (INTERACTIVE_TOOLS, Policy, cli_permission_args, is_mcp, norm,
                          policy_context, summarize_target, within)
from . import team as team_mod
from .routines import Routine
from .store import Store

ACTIVE = {"queued", "running", "awaiting"}
TERMINAL = {"done", "error", "cancelled", "interrupted"}
HOOK_ID = "console_pretool"


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
    kind: str                 # hook | permission | question | plan
    tool: str
    input: dict
    reason: str
    created: float = field(default_factory=time.time)
    event: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None
    message: str = ""
    answers: dict | None = None
    by: str = "utilisateur"
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
                "input": _clip_json(self.input, 20000), "created": self.created}


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


def _clip_json(value, limit: int):
    s = json.dumps(value, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return value
    return {"_tronqué": True, "aperçu": s[:limit]}


def _clip(s: str, limit: int) -> str:
    s = s or ""
    return s if len(s) <= limit else s[:limit] + f"\n… ({len(s) - limit} caractères de plus)"


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
        self._wake = threading.Event()
        self._stop = False
        self.tasks: dict[str, dict] = {}
        self.runs: dict[str, Run] = {}
        self.queue: list[str] = []
        self._seq: dict[str, int] = {}
        self.emergency = bool(store.kv_get("emergency_stop", False))
        self.probes: dict = store.kv_get("probes", {}) or {}
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

    # -------------------------------------------------------- helpers
    @property
    def cfg(self) -> Config:
        return self.cfg_store.config

    def cli(self) -> list[str] | None:
        if self._cli_override:
            return list(self._cli_override)
        p = claude_cli.find_cli(self.cfg.general.cli_path)
        return [p] if p else None

    def public(self, t: dict) -> dict:
        out = {k: v for k, v in t.items() if k not in ("spec", "queued_messages")}
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

    def _purge_old(self):
        days = self.cfg.history.retention_days
        n = self.store.purge(time.time() - days * 86400, sorted(TERMINAL))
        if n:
            with self._lock:
                alive = {t["id"] for t in self.store.list_tasks(limit=100000)}
                self.tasks = {k: v for k, v in self.tasks.items() if k in alive}

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
                    routine: dict | None = None, closed: bool = False, team: bool = False) -> dict:
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
        if not text:
            raise TaskError("Demande vide.")
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
        if resume and not re.fullmatch(r"[0-9a-fA-F-]{36}", resume):
            raise TaskError("Identifiant de session invalide.")
        team_agents, team_models, team_prompt = ({}, {}, "")
        if team:
            resolved = {m.get("value"): m.get("resolved") for m in (self.probes.get(prof.id) or {}).get("models") or []
                        if m.get("value") and m.get("resolved")}
            team_agents, team_models, team_prompt = team_mod.build(model, cfg.team, resolved)
        tid = secrets.token_hex(4)
        now = time.time()
        t = {
            "id": tid, "title": _title(text), "prompt": text, "profile": prof.id,
            "profile_name": prof.name, "color": prof.color, "model": model, "effort": effort,
            "preset": pre.id, "preset_name": pre.name, "workdir": wd, "add_dirs": add_dirs,
            "status": "queued", "created": now, "started": None, "ended": None,
            "session_id": str(uuid.uuid4()), "session_started": False, "turns": 0,
            "cost_usd": 0.0, "duration_ms": 0, "result": "", "error": "", "is_error": False,
            "pinned": False, "closed": bool(closed), "retry_of": retry_of, "pending": [], "mcp": [],
            "todos": [], "usage": {}, "output_bytes": 0, "truncated": False, "model_resolved": "",
            "queued_messages": [text], "origin": origin, "routine": routine,
            "resumed_from": resume, "fork_next": bool(resume and fork),
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
            self._event(tid, "user", {"text": text, "first": True})
            self._audit("tâche créée", {"demande": _clip(text, 2000), "modèle": model, "preset": pre.name,
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
                                team=bool(t.get("team")))

    def duplicate(self, tid: str, profile: str, confirmed: bool = False) -> dict:
        t = self._get(tid)
        prof = self.cfg.profile(profile)
        if not prof:
            raise TaskError(f"Profil inconnu : {profile}")
        preset = t["preset"] if self.cfg.preset(t["preset"]) else None
        return self.create_task(t["prompt"], profile=prof.id, model=t["model"], preset=preset,
                                effort=t["effort"], confirmed=confirmed, retry_of=tid, team=bool(t.get("team")))

    def _get(self, tid: str) -> dict:
        t = self.tasks.get(tid) or self.store.get_task(tid)
        if not t:
            raise TaskError("Tâche inconnue.", 404)
        return t

    # -------------------------------------------------------- follow-ups
    def followup(self, tid: str, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise TaskError("Message vide.")
        if self.emergency:
            raise TaskError("Arrêt d'urgence actif.", 423)
        with self._lock:
            t = self._get(tid)
            run = self.runs.get(tid)
            self._event(tid, "user", {"text": text})
            self._audit("message de suite", {"texte": _clip(text, 2000)}, t)
            if run and run.proc and not run.stdin_closed and t["status"] in ("running", "awaiting"):
                self._send_user(run, text)
            else:
                t.setdefault("queued_messages", []).append(text)
                if t["status"] in TERMINAL:
                    t["status"] = "queued"
                    t["error"] = ""
                    self.queue.append(tid)
                    self._event(tid, "status", {"status": "queued"})
            self._save(t)
        self._wake.set()
        return self.public(t)

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
    def decide(self, tid: str, aid: str, decision: str, message: str = "", answers: dict | None = None) -> dict:
        if decision not in ("allow", "deny"):
            raise TaskError("Décision invalide.")
        run = self.runs.get(tid)
        appr = run.approvals.get(aid) if run else None
        if not appr or not appr.claim(decision, (message or "").strip()[:2000], "utilisateur",
                                      answers if isinstance(answers, dict) else None):
            raise TaskError("Cette validation n'est plus en attente.", 409)
        return {"ok": True}

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
                    if len(busy) >= limit:
                        break
                    per = (t["spec"]["profile"] or {}).get("max_concurrent", 2)
                    if sum(1 for b in busy if b["profile"] == t["profile"]) >= per:
                        continue
                    self.queue.remove(tid)
                    run = Run()
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
        for d in t.get("add_dirs") or []:
            args += ["--add-dir", d]
        team = spec.get("team") or {}
        system = "\n\n".join(x for x in (spec.get("security_instructions", ""), prof.instructions, team.get("prompt", ""))
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
        mcp_file = mcp.write_config(prof, self.runtime, f"{t['id']}-{secrets.token_hex(3)}")
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
            run.policy = Policy(pre, [ToolRule.model_validate(r) for r in spec["rules"]],
                                [InputConstraint.model_validate(c) for c in spec["constraints"]],
                                policy_context(t["workdir"], t.get("add_dirs") or [], spec["forbidden"],
                                               str(self.data_dir), [self.port]))
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
                          "request": {"subtype": "initialize", "hooks": {
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

    def _on_system(self, tid: str, t: dict, msg: dict):
        st = msg.get("subtype")
        if st == "init":
            with self._lock:
                t["session_id"] = msg.get("session_id") or t["session_id"]
                t["session_started"] = True
                t["fork_next"] = False
                t["model_resolved"] = msg.get("model") or ""
                t["mcp"] = [{"name": s.get("name"), "status": s.get("status")} for s in msg.get("mcp_servers") or []]
                self._save(t)
            self._event(tid, "init", {"model": msg.get("model"), "mcp": t["mcp"],
                                      "tools": len(msg.get("tools") or []),
                                      "permission_mode": msg.get("permissionMode"),
                                      "skills": len(msg.get("slash_commands") or msg.get("skills") or [])})
        elif st == "compact_boundary":
            self._event(tid, "info", {"text": "Contexte compacté par Claude Code."})
        elif st and "retry" in st:
            self._event(tid, "info", {"text": f"Nouvelle tentative de l'API ({msg.get('attempt', '?')})…"})

    def _on_assistant(self, tid: str, t: dict, msg: dict):
        parent = msg.get("parent_tool_use_id")
        for block in (msg.get("message") or {}).get("content") or []:
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                self._event(tid, "text", {"text": block["text"], "parent": parent})
            elif kind == "thinking" and block.get("thinking"):
                self._event(tid, "thinking", {"text": _clip(block["thinking"], 6000), "parent": parent})
            elif kind in ("tool_use", "server_tool_use"):
                name, inp = block.get("name", ""), block.get("input") or {}
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
                self._event(tid, "tool_result", {"id": block.get("tool_use_id"),
                                                 "is_error": bool(block.get("is_error")),
                                                 "preview": _clip(_tool_result_text(block.get("content")), 4000),
                                                 "parent": msg.get("parent_tool_use_id")})

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
            t["cost_usd"] = round(t.get("cost_usd", 0) + float(msg.get("total_cost_usd") or 0), 6)
            t["duration_ms"] += int(msg.get("duration_ms") or 0)
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
            if not more:
                self._close_stdin(run)

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
            if v.decision == "ask":
                appr = Approval(id=secrets.token_hex(4), request_id=rid, kind="hook", tool=tool,
                                input=tin, reason=v.reason)

                def answer(a: Approval, rid=rid):
                    reason = (f"Refusé par l'utilisateur{' : ' + a.message if a.message else '.'}"
                              if a.decision == "deny" else "Approuvé par l'utilisateur.")
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
            if v.decision == "deny":
                self._event(tid, "policy", {"tool": tool, "decision": "deny", "reason": v.reason,
                                            "target": _clip(summarize_target(tool, tin), 300)})
                return self._respond(run, rid, {"behavior": "deny", "message": v.reason})
            if v.decision == "allow" and tool not in INTERACTIVE_TOOLS:
                return self._respond(run, rid, {"behavior": "allow", "updatedInput": tin})
            if v.decision == "ask" or pre_unlisted == "ask" or tool == "ExitPlanMode":
                kind = "plan" if tool == "ExitPlanMode" else "permission"
                appr = Approval(id=secrets.token_hex(4), request_id=rid, kind=kind, tool=tool, input=tin,
                                reason=v.reason if v.decision == "ask" else "Claude Code demande l'autorisation.")

                def answer_p(a: Approval, rid=rid, tin=tin):
                    if a.decision == "allow":
                        self._respond(run, rid, {"behavior": "allow", "updatedInput": tin})
                    else:
                        self._respond(run, rid, {"behavior": "deny", "message":
                                                 f"Refusé par l'utilisateur{' : ' + a.message if a.message else '.'}"})
                return self._park(tid, run, appr, answer_p)
            reason = f"Outil non autorisé par le preset « {t['preset_name']} »."
            self._event(tid, "policy", {"tool": tool, "decision": "deny", "reason": reason,
                                        "target": _clip(summarize_target(tool, tin), 300)})
            return self._respond(run, rid, {"behavior": "deny", "message": reason})

        self._respond(run, rid, error=f"Requête non prise en charge par la console : {sub}")

    # -------------------------------------------------------- misc actions
    def update_task(self, tid: str, patch: dict) -> dict:
        with self._lock:
            t = self._get(tid)
            if "pinned" in patch:
                t["pinned"] = bool(patch["pinned"])
            if "closed" in patch:
                t["closed"] = bool(patch["closed"])
            if "title" in patch and str(patch["title"]).strip():
                t["title"] = str(patch["title"]).strip()[:120]
            self.tasks[tid] = t
            self._save(t)
            return self.public(t)

    def delete_task(self, tid: str):
        with self._lock:
            t = self._get(tid)
            if t["status"] in ACTIVE:
                raise TaskError("Annule la tâche avant de la supprimer.", 409)
            self.tasks.pop(tid, None)
            self.store.delete_task(tid)
            self._audit("tâche supprimée", {"titre": t["title"]}, t)
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

    def task_file(self, tid: str, path: str) -> Path:
        """A file of the task's folders, for preview: never outside them, never a forbidden path."""
        t = self._get(tid)
        raw = os.path.expandvars(os.path.expanduser(str(path or "").strip().strip('"')))
        if not raw:
            raise TaskError("Chemin manquant.")
        real = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(t["workdir"], raw))
        roots = [t["workdir"], *(t.get("add_dirs") or [])]
        if not within(real, roots):
            raise TaskError("Aperçu limité aux dossiers de travail de la tâche.", 403)
        spec = t.get("spec") or {}
        ctx = policy_context(t["workdir"], t.get("add_dirs") or [], spec.get("forbidden") or self.cfg.security.forbidden_paths,
                             str(self.data_dir), [self.port])
        if Policy(Preset.model_validate(spec["preset"]) if spec.get("preset") else self.cfg.presets[0], [], [], ctx) \
                .forbidden_hit({"file_path": real}, "Read"):
            raise TaskError("Ce fichier est protégé par la configuration de sécurité.", 403)
        p = Path(real)
        if not p.is_file():
            raise TaskError("Fichier introuvable.", 404)
        if p.stat().st_size > 100 * 1024 * 1024:
            raise TaskError("Fichier trop volumineux pour un aperçu (plus de 100 Mo).", 413)
        return p

    def open_task_file(self, tid: str, path: str, reveal: bool = False):
        p = self.task_file(tid, path)
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
        self._audit("fichier ouvert" if not reveal else "fichier affiché dans le dossier", {"chemin": str(p)}, self._get(tid))

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
                       effort: str | None = None, fork: bool = True, confirmed: bool = False) -> dict:
        prof, row = self._session(pid, sid)
        if not row["resumable"]:
            raise TaskError(f"Le dossier de cette session n'existe plus : {row['cwd'] or '(inconnu)'}.", 409)
        history = [{"role": i["role"], "text": (i.get("text") or f"{i.get('name')} · {i.get('target', '')}")[:1500]}
                   for i in library.read_transcript(prof, sid, limit=60) if not i.get("sidechain")]
        t = self.create_task(prompt, profile=prof.id, model=model, preset=preset, workdir=row["cwd"], effort=effort,
                             confirmed=confirmed, resume=sid, fork=fork, history=history,
                             origin="reprise desktop" if row["origin"] == "desktop" else "reprise")
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
                t = self.create_task(r.prompt, profile=r.profile, model=r.model or None, preset=r.preset,
                                     workdir=r.workdir or None, effort=r.effort or None, confirmed=True,
                                     origin="routine", routine={"id": r.id, "name": r.name}, closed=not r.open_window,
                                     team=r.team)
                run["task_id"] = t["id"]
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
            if run["error"] and manual:
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
