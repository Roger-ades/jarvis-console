"""HTTP layer: security guard, REST API, server-sent events, static UI."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from starlette.datastructures import MutableHeaders

from . import __version__, claude_cli, mcp, winsys
from .config import (BASE_MODELS, MODE_LABELS, Config, ConfigStore, default_config, dump,
                     expand_path, format_errors)
from .engine import Engine, TaskError
from .permissions import LOCKED_RULES
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
PUBLIC_API = {"/api/ping", "/api/auth/exchange"}
# Scripts only from the console itself. Web images and pages are allowed as sources, but
# the page never creates them on its own: only a click of the user does (no silent
# exfiltration through an image URL written by a model), and web pages run sandboxed.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https:; "
       "frame-src 'self' blob: https:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


class Auth:
    """Access token (data/token) and one-time codes that open the UI."""

    def __init__(self, data_dir: Path):
        self.file = Path(data_dir) / "token"
        self.token = self.file.read_text(encoding="utf-8").strip() if self.file.exists() else ""
        if len(self.token) < 32:
            self.rotate()
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()

    def rotate(self) -> str:
        self.token = secrets.token_urlsafe(32)
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(self.token, encoding="utf-8")
        return self.token

    def new_code(self, ttl: float = 300) -> str:
        code = secrets.token_urlsafe(18)
        with self._lock:
            now = time.time()
            self._codes = {c: e for c, e in self._codes.items() if e > now}
            self._codes[code] = now + ttl
        return code

    def redeem(self, code: str) -> bool:
        with self._lock:
            exp = self._codes.pop(code or "", None)
        return exp is not None and exp > time.time()

    def check(self, value: str | None) -> bool:
        return bool(value) and secrets.compare_digest(value.encode(), self.token.encode())


class TaskIn(BaseModel):
    prompt: str
    profile: str | None = None
    model: str | None = None
    preset: str | None = None
    workdir: str | None = None
    effort: str | None = None
    confirmed: bool = False
    team: bool = False


class MessageIn(BaseModel):
    text: str


class DecisionIn(BaseModel):
    decision: str
    message: str = ""
    answers: dict | None = None


def _err(status: int, detail: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": detail, **extra}, status_code=status)


class Guard:
    """Pure ASGI middleware: host / origin / token checks and security headers.

    (Pure ASGI rather than BaseHTTPMiddleware so long-lived SSE streams and
    client disconnects behave.)
    """

    def __init__(self, app, check, on_reject):
        self.app, self.check, self.on_reject = app, check, on_reject

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        err = self.check(headers, path)
        if err:
            self.on_reject(headers, path, err[1])
            return await _err(err[0], err[1])(scope, receive, send)
        api = path.startswith("/api/")

        async def send_headers(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "no-referrer"
                h["X-Frame-Options"] = "DENY"
                h["Cross-Origin-Opener-Policy"] = "same-origin"
                h["Cross-Origin-Resource-Policy"] = "same-origin"
                h["Cache-Control"] = "no-store" if api else "no-cache"
                if not api:
                    h["Content-Security-Policy"] = CSP
            await send(message)

        await self.app(scope, receive, send_headers)


def create_app(data_dir: Path, port: int, cli_command: list[str] | None = None,
               extra_hosts: tuple[str, ...] = (), start_threads: bool = True) -> FastAPI:
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_store = ConfigStore(data_dir)
    store = Store(data_dir / "console.db")
    engine = Engine(cfg_store, store, data_dir, port, cli_command=cli_command, start_threads=start_threads)
    auth = Auth(data_dir)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", *extra_hosts}
    rejected: dict[str, float] = {}

    @asynccontextmanager
    async def lifespan(_app):
        threading.Thread(target=engine.state, daemon=True).start()  # warm the CLI version cache
        yield
        engine.shutdown()

    app = FastAPI(title="JARVIS Console", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.engine, app.state.auth, app.state.cfg_store, app.state.store = engine, auth, cfg_store, store

    def origins() -> set[str]:
        return {f"http://127.0.0.1:{port}", f"http://localhost:{port}",
                *(f"http://{h}" for h in extra_hosts), *cfg_store.config.security.extra_origins}

    def check(headers: dict, path: str) -> tuple[int, str] | None:
        if headers.get("host", "") not in hosts:
            return 403, "Hôte refusé (la console n'écoute que 127.0.0.1)."
        origin = headers.get("origin")
        if origin and origin not in origins():
            return 403, "Origine refusée."
        if path.startswith("/api/") and path not in PUBLIC_API and not auth.check(headers.get("x-console-token")):
            return 401, "Jeton d'accès manquant ou invalide."
        return None

    def rejected_call(headers: dict, path: str, reason: str):
        key = f"{reason}:{path}"
        now = time.time()
        if now - rejected.get(key, 0) > 5:
            rejected[key] = now
            if cfg_store.config.security.audit:
                store.audit("appel refusé", {"raison": reason, "chemin": path,
                                             "origine": headers.get("origin", ""), "hôte": headers.get("host", "")})

    app.add_middleware(Guard, check=check, on_reject=rejected_call)

    @app.exception_handler(TaskError)
    async def task_error(_req, exc: TaskError):
        return _err(exc.status, exc.message, **exc.extra)

    # -------------------------------------------------------- auth
    @app.get("/api/ping")
    def ping():
        return {"ok": True, "app": "jarvis-console", "version": __version__}

    @app.post("/api/auth/exchange")
    def exchange(body: dict = Body(...)):
        if not auth.redeem(str(body.get("code", ""))):
            return _err(401, "Code d'accès expiré ou déjà utilisé : relance start.bat.")
        return {"token": auth.token}

    @app.post("/api/auth/code")
    def mint_code():
        return {"code": auth.new_code()}

    @app.post("/api/security/rotate-token")
    def rotate_token():
        tok = auth.rotate()
        store.audit("jeton régénéré", {})
        return {"token": tok}

    # -------------------------------------------------------- state & stream
    @app.get("/api/state")
    def state():
        return {**engine.state(), "version": __version__, "port": port, "data_dir": str(data_dir)}

    @app.get("/api/stream")
    async def stream(request: Request):
        loop = asyncio.get_running_loop()
        q = engine.bus.subscribe(loop)

        async def gen():
            try:
                yield "retry: 2000\n\n"
                yield f"event: hello\ndata: {json.dumps({'ts': time.time()})}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        kind, data = await asyncio.wait_for(q.get(), 15)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            finally:
                engine.bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # -------------------------------------------------------- config
    def meta() -> dict:
        cfg = cfg_store.config
        return {
            "mode_labels": MODE_LABELS, "base_models": BASE_MODELS, "data_dir": str(data_dir),
            "locked_rules": [r.model_dump() for r in LOCKED_RULES],
            "profiles": {p.id: {"config_dir": expand_path(p.config_dir) or str(Path.home() / ".claude"),
                                "workdir": expand_path(p.workdir),
                                "desktop_config": expand_path(p.mcp.desktop_config),
                                "desktop_config_exists": bool(p.mcp.desktop_config) and Path(expand_path(p.mcp.desktop_config)).is_file()}
                         for p in cfg.profiles},
            "cli_detected": claude_cli.find_cli(""),
        }

    def save(cfg: Config, reason: str):
        old_port = cfg_store.config.general.port
        cfg_store.save(cfg, reason)
        store.audit("configuration modifiée", {"raison": reason})
        engine.bus.publish("config", {"reason": reason})
        return {"config": dump(cfg), "meta": meta(),
                "restart_needed": cfg.general.port != old_port}

    def validated(data) -> Config | JSONResponse:
        try:
            return Config.model_validate(data)
        except ValidationError as exc:
            return _err(422, "Configuration invalide.", errors=format_errors(exc))

    @app.get("/api/config")
    def get_config():
        return {"config": dump(cfg_store.config), "meta": meta()}

    @app.put("/api/config")
    def put_config(body: dict = Body(...)):
        cfg = validated(body)
        return cfg if isinstance(cfg, JSONResponse) else save(cfg, "modification")

    @app.post("/api/config/import")
    def import_config(body: dict = Body(...)):
        cfg = validated(body.get("config", body))
        return cfg if isinstance(cfg, JSONResponse) else save(cfg, "import")

    @app.post("/api/config/reset")
    def reset_config():
        return save(default_config(), "valeurs par défaut")

    @app.get("/api/config/schema")
    def schema():
        return ConfigStore.schema()

    @app.get("/api/config/history")
    def history():
        return {"versions": cfg_store.history()}

    @app.post("/api/config/rollback")
    def rollback(body: dict = Body(...)):
        try:
            cfg = cfg_store.rollback(str(body.get("id", "")))
        except FileNotFoundError:
            return _err(404, "Version introuvable.")
        except ValidationError as exc:
            return _err(422, "Cette version n'est plus valide.", errors=format_errors(exc))
        store.audit("configuration restaurée", {"version": body.get("id")})
        engine.bus.publish("config", {"reason": "retour"})
        return {"config": dump(cfg), "meta": meta()}

    @app.get("/api/config/export")
    def export_config():
        data = json.dumps(dump(cfg_store.config), ensure_ascii=False, indent=2)
        return Response(data, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="jarvis-config-{time.strftime("%Y%m%d-%H%M")}.json"'})

    # -------------------------------------------------------- profiles
    @app.get("/api/probes")
    def probes():
        return {"probes": engine.probes}

    @app.post("/api/profiles/{pid}/test")
    def test_profile(pid: str):
        return engine.probe(pid)

    @app.post("/api/profiles/{pid}/login")
    def login_profile(pid: str):
        engine.open_login(pid)
        return {"ok": True}

    @app.get("/api/profiles/{pid}/mcp")
    def profile_mcp(pid: str):
        prof = cfg_store.config.profile(pid)
        if not prof:
            return _err(404, "Profil inconnu.")
        return {**mcp.summary(prof), "status": (engine.probes.get(pid) or {}).get("mcp") or []}

    # -------------------------------------------------------- tasks
    @app.get("/api/tasks")
    def list_tasks(include_closed: bool = True, limit: int = 500):
        return {"tasks": engine.list_tasks(include_closed, min(limit, 5000))}

    @app.post("/api/tasks")
    def create_task(body: TaskIn):
        return engine.create_task(body.prompt, profile=body.profile, model=body.model, preset=body.preset,
                                  workdir=body.workdir, effort=body.effort, confirmed=body.confirmed, team=body.team)

    @app.get("/api/tasks/{tid}")
    def get_task(tid: str):
        return engine.public(engine._get(tid))

    @app.patch("/api/tasks/{tid}")
    def patch_task(tid: str, body: dict = Body(...)):
        return engine.update_task(tid, body)

    @app.delete("/api/tasks/{tid}")
    def delete_task(tid: str):
        engine.delete_task(tid)
        return {"ok": True}

    @app.get("/api/tasks/{tid}/events")
    def task_events(tid: str, after: int = 0):
        engine._get(tid)
        return {"events": store.events(tid, after)}

    @app.post("/api/tasks/{tid}/message")
    def task_message(tid: str, body: MessageIn):
        return engine.followup(tid, body.text)

    @app.post("/api/tasks/{tid}/cancel")
    def task_cancel(tid: str):
        return engine.cancel(tid)

    @app.post("/api/tasks/{tid}/retry")
    def task_retry(tid: str, body: dict = Body(default={})):
        return engine.retry(tid, confirmed=bool(body.get("confirmed")))

    @app.post("/api/tasks/{tid}/duplicate")
    def task_duplicate(tid: str, body: dict = Body(...)):
        return engine.duplicate(tid, str(body.get("profile", "")), confirmed=bool(body.get("confirmed")))

    @app.post("/api/tasks/{tid}/approvals/{aid}")
    def task_decide(tid: str, aid: str, body: DecisionIn):
        return engine.decide(tid, aid, body.decision, body.message, body.answers)

    @app.get("/api/tasks/{tid}/file")
    def task_file(tid: str, path: str):
        p = engine.task_file(tid, path)
        media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        # sandbox: even opened directly, an HTML/SVG file of the folder never runs as the console
        return FileResponse(p, media_type=media, headers={"Content-Disposition": "inline", "Cache-Control": "no-store",
                                                          "Content-Security-Policy": "sandbox"})

    @app.post("/api/tasks/{tid}/file/open")
    def task_file_open(tid: str, body: dict = Body(...)):
        engine.open_task_file(tid, str(body.get("path", "")), reveal=bool(body.get("reveal")))
        return {"ok": True}

    @app.post("/api/tasks/{tid}/terminal")
    def task_terminal(tid: str):
        engine.open_session_terminal(tid)
        return {"ok": True}

    # -------------------------------------------------------- existing sessions (Desktop Code tab, CLI)
    @app.get("/api/sessions")
    def sessions(profile: str | None = None):
        return {"sessions": engine.sessions(profile)}

    @app.get("/api/sessions/{pid}/{sid}")
    def session_detail(pid: str, sid: str):
        return engine.session_transcript(pid, sid)

    @app.post("/api/sessions/{pid}/{sid}/resume")
    def session_resume(pid: str, sid: str, body: dict = Body(...)):
        return engine.resume_session(pid, sid, str(body.get("prompt", "")), preset=body.get("preset") or None,
                                     model=body.get("model") or None, effort=body.get("effort") or None,
                                     fork=bool(body.get("fork", True)), confirmed=bool(body.get("confirmed")))

    @app.post("/api/sessions/{pid}/{sid}/terminal")
    def session_terminal(pid: str, sid: str):
        engine.open_existing_session_terminal(pid, sid)
        return {"ok": True}

    # -------------------------------------------------------- routines
    @app.get("/api/routines")
    def routines():
        return {"routines": engine.list_routines(), "startup": winsys.startup_enabled()}

    @app.post("/api/routines")
    def routine_save(body: dict = Body(...)):
        return engine.save_routine(body)

    @app.delete("/api/routines/{rid}")
    def routine_delete(rid: str):
        engine.delete_routine(rid)
        return {"ok": True}

    @app.post("/api/routines/{rid}/run")
    def routine_run(rid: str):
        return engine.run_routine(rid, manual=True)

    @app.get("/api/cloud-routines")
    def cloud_routines(profile: str, refresh: bool = False):
        return engine.cloud_routines(profile, refresh)

    @app.post("/api/cloud-routines/{pid}/{rid}/run")
    def cloud_run(pid: str, rid: str):
        return engine.cloud_action(pid, rid, "run")

    @app.post("/api/cloud-routines/{pid}/{rid}/toggle")
    def cloud_toggle(pid: str, rid: str, body: dict = Body(...)):
        return engine.cloud_action(pid, rid, "toggle", enabled=bool(body.get("enabled")))

    @app.get("/api/cloud-routines/{pid}/{rid}/runs")
    def cloud_runs(pid: str, rid: str):
        return engine.cloud_action(pid, rid, "runs")

    @app.get("/api/system")
    def system_info():
        return {"startup": winsys.startup_enabled(), "log": str(data_dir / "console.log"),
                "stoppable": getattr(app.state, "server", None) is not None}

    @app.post("/api/system/shutdown")
    def system_shutdown():
        server = getattr(app.state, "server", None)
        if server is None:
            return _err(409, "Cette console n'a pas été lancée par start.bat : arrête-la depuis son terminal.")
        store.audit("arrêt de la console", {"tâches en cours": engine.state()["running"]})
        threading.Timer(0.6, lambda: setattr(server, "should_exit", True)).start()
        return {"ok": True}

    @app.post("/api/system/startup")
    def system_startup(body: dict = Body(...)):
        try:
            on = winsys.set_startup(bool(body.get("on")))
        except (OSError, subprocess.CalledProcessError) as exc:
            return _err(500, f"Impossible de modifier le démarrage automatique : {exc}")
        store.audit("démarrage avec Windows", {"actif": on})
        return {"startup": on}

    # -------------------------------------------------------- safety, audit, history
    @app.post("/api/emergency")
    def emergency(body: dict = Body(...)):
        return engine.set_emergency(bool(body.get("on")))

    @app.get("/api/audit")
    def audit(limit: int = 100, offset: int = 0, task_id: str | None = None, kind: str | None = None):
        rows, total = store.audit_rows(min(limit, 1000), offset, task_id, kind)
        return {"rows": rows, "total": total}

    @app.get("/api/audit/export")
    def audit_export(format: str = "csv"):
        stamp = time.strftime("%Y%m%d-%H%M")
        if format == "json":
            rows, _ = store.audit_rows(limit=1_000_000)
            return Response(json.dumps(rows, ensure_ascii=False, indent=1), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="jarvis-audit-{stamp}.json"'})
        return PlainTextResponse("﻿" + store.audit_csv(), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="jarvis-audit-{stamp}.csv"'})

    @app.get("/api/history/export")
    def history_export():
        data = json.dumps(engine.export_history(), ensure_ascii=False, default=str)
        return Response(data, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="jarvis-historique-{time.strftime("%Y%m%d-%H%M")}.json"'})

    @app.post("/api/history/purge")
    def history_purge(body: dict = Body(default={})):
        days = body.get("days")
        return {"deleted": engine.purge(int(days) if days is not None else None)}

    @app.get("/api/ui-state")
    def get_ui_state():
        return store.kv_get("ui", {}) or {}

    @app.put("/api/ui-state")
    def put_ui_state(body: dict = Body(...)):
        if len(json.dumps(body)) > 400_000:
            return _err(413, "État d'interface trop volumineux.")
        store.kv_set("ui", body)
        return {"ok": True}

    # -------------------------------------------------------- static UI
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    # installable web app: manifest and service worker served from the root scope
    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(STATIC / "js" / "sw.js", media_type="text/javascript; charset=utf-8",
                            headers={"Service-Worker-Allowed": "/"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
