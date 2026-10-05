"""HTTP layer: security guard, REST API, server-sent events, static UI."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import MutableHeaders

from . import __version__, attachments, claude_cli, content, mcp, updater, winsys
from .config import (BASE_MODELS, MODE_LABELS, Config, ConfigStore, default_config, dump,
                     expand_path, format_errors, web_domain)
from .engine import Engine, TaskError
from .permissions import LOCKED_RULES
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
PUBLIC_API = {"/api/ping", "/api/auth/exchange"}


def code_stamp() -> float:
    """Newest change to the server's code. The web page is read from disk on every load, the
    Python code only at start: a newer stamp means an update waits for a restart."""
    return max((p.stat().st_mtime for p in Path(__file__).resolve().parent.glob("*.py")), default=0.0)


# Scripts only from the console itself. Web images and pages are allowed as sources, but
# the page never creates them on its own: only a click of the user does (no silent
# exfiltration through an image URL written by a model), and web pages run sandboxed.
# HTML of mails and files comes from the preview origin (content.py), never from the console's.
def console_csp(port: int) -> str:
    return ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https:; "
            f"frame-src 'self' blob: https: http://{content.HOST}:{port}; media-src 'self' blob:; connect-src 'self'; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


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
    attachments: list[str] = []
    context: list[dict] = []
    not_before: float | None = None  # start at that time (e.g. when the account limit resets)
    regard: dict | None = None       # what the user looks at in the console (console/regard.py)


class MessageIn(BaseModel):
    text: str = ""
    attachments: list[str] = []
    compact: bool = False  # compact the session's context first
    regard: dict | None = None


class DisplayAnswerIn(BaseModel):
    bloc: int
    choix: list[int] = []
    autre: str = ""
    bouton: int | None = None
    valeurs: dict = Field(default_factory=dict)


class DisplayActIn(BaseModel):
    bloc: int
    carte: int
    bouton: int


class DecisionIn(BaseModel):
    decision: str
    message: str = ""
    answers: dict | None = None
    remember: list[str] = []  # "toujours pour ce projet"
    via: str = ""             # "boite": decided from the inbox (said in the log)


class InboxMarkIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    section: Literal["", "todo", "read", "reminder"] = ""
    tasks: list[str] = Field(default_factory=list, max_length=500)  # discussions the user looked at


def _err(status: int, detail: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": detail, **extra}, status_code=status)


class Guard:
    """Pure ASGI middleware: host / origin / token checks and security headers.

    (Pure ASGI rather than BaseHTTPMiddleware so long-lived SSE streams and
    client disconnects behave.)
    """

    def __init__(self, app, check, on_reject, csp: str = "", content_host: str = ""):
        self.app, self.check, self.on_reject = app, check, on_reject
        self.csp, self.content_host = csp, content_host

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
        if self.content_host and headers.get("host") == self.content_host:
            async def send_content(message):
                # A preview page: framed by the console only (its CSP says so), loaded from an opaque
                # origin (sandbox), so its own images and styles must be readable cross-origin.
                if message["type"] == "http.response.start":
                    h = MutableHeaders(scope=message)
                    h["X-Content-Type-Options"] = "nosniff"
                    h["Referrer-Policy"] = "no-referrer"
                    h["X-DNS-Prefetch-Control"] = "off"
                    h["Cross-Origin-Resource-Policy"] = "cross-origin"
                    h["Cache-Control"] = "no-store"
                    if "content-security-policy" not in h:
                        h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
                await send(message)

            return await self.app(scope, receive, send_content)

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
                    h["Content-Security-Policy"] = self.csp
            await send(message)

        await self.app(scope, receive, send_headers)


def file_stamp(p: Path) -> str:
    """Changes whenever the file is written: an open preview compares it to reload itself."""
    st = p.stat()
    return f"{st.st_mtime_ns}-{st.st_size}"


def preview_response(p: Path, stat: bool = False):
    """A file for the preview. sandbox: even opened directly, an HTML/SVG file of the folder never
    runs as the console. X-File-Path: where a name such as "logo.png" was found. stat: only the
    file's stamp, which an open preview checks to follow the changes."""
    if stat:
        return JSONResponse({"stamp": file_stamp(p)}, headers={"Cache-Control": "no-store"})
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, headers={"Content-Disposition": "inline", "Cache-Control": "no-store",
                                                      "Content-Security-Policy": "sandbox", "X-File-Path": quote(str(p)),
                                                      "X-File-Stamp": file_stamp(p)})

def create_app(data_dir: Path, port: int, cli_command: list[str] | None = None,
               extra_hosts: tuple[str, ...] = (), start_threads: bool = True) -> FastAPI:
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_store = ConfigStore(data_dir)
    store = Store(data_dir / "console.db")
    engine = Engine(cfg_store, store, data_dir, port, cli_command=cli_command, start_threads=start_threads)
    auth = Auth(data_dir)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", *extra_hosts}
    content_host = f"{content.HOST}:{port}"
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
        if headers.get("host", "") == content_host:
            # the preview origin serves pages behind their random address, and nothing else
            return None if path.startswith("/v/") else (404, "Introuvable.")
        if headers.get("host", "") not in hosts:
            return 403, "Hôte refusé (la console n'écoute que 127.0.0.1)."
        if path.startswith("/v/"):
            return 404, "Introuvable."
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

    app.add_middleware(Guard, check=check, on_reject=rejected_call, csp=console_csp(port), content_host=content_host)

    @app.exception_handler(TaskError)
    async def task_error(_req, exc: TaskError):
        return _err(exc.status, exc.message, **exc.extra)

    # -------------------------------------------------------- auth
    boot = secrets.token_hex(6)
    loaded = code_stamp()

    @app.get("/api/ping")
    def ping():
        return {"ok": True, "app": "jarvis-console", "version": __version__, "boot": boot}

    @app.post("/api/auth/exchange")
    def exchange(body: dict = Body(...)):
        if not auth.redeem(str(body.get("code", ""))):
            return _err(401, "Code d'accès expiré ou déjà utilisé : relance start.bat.")
        try:
            (data_dir / "ui-seen").touch()  # the browser now holds the token: the launcher may open the installed app
        except OSError:
            pass
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

    def desktop_app(cfg: Config | None = None) -> dict | None:
        """The desktop app, when it is how the console opens (Configuration → Général) and it ran here."""
        return winsys.app_info(data_dir) if (cfg or cfg_store.config).general.open_as == "bureau" else None

    def save(cfg: Config, reason: str):
        old_port = cfg_store.config.general.port
        old_open = cfg_store.config.general.open_as
        cfg_store.save(cfg, reason)
        if cfg.general.open_as != old_open:
            # the session start opens what the console now opens with (desktop app or bare server)
            try:
                if winsys.startup_enabled():
                    winsys.set_startup(True, desktop_app(cfg))
            except (OSError, subprocess.CalledProcessError):
                pass
        store.audit("configuration modifiée", {"raison": reason})
        engine.bus.publish("config", {"reason": reason})
        engine.sync_briefs()   # the morning briefs' routines follow the accounts' settings
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
        engine.sync_briefs()
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

    @app.post("/api/profiles/{pid}/odoo-users")
    def odoo_users_start(pid: str):
        return engine.odoo_users_start(pid)

    @app.get("/api/profiles/{pid}/odoo-users/{tid}")
    def odoo_users_result(pid: str, tid: str):
        return engine.odoo_users_result(pid, tid)

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
                                  workdir=body.workdir, effort=body.effort, confirmed=body.confirmed, team=body.team,
                                  attachments=body.attachments, context=body.context, not_before=body.not_before,
                                  regard=body.regard)

    # -------------------------------------------------------- attachments (raw body: no multipart dependency)
    @app.post("/api/uploads")
    async def upload(request: Request, name: str = ""):
        try:
            return await attachments.receive(engine.attachments_root(), name, request.stream())
        except attachments.AttachmentError as e:
            raise TaskError(str(e), e.status) from e

    @app.delete("/api/uploads/{uid}")
    def upload_discard(uid: str):
        engine.discard_upload(uid)
        return {"ok": True}

    @app.get("/api/tasks/{tid}")
    def get_task(tid: str):
        return engine.public(engine._get(tid))

    @app.patch("/api/tasks/{tid}")
    def patch_task(tid: str, body: dict = Body(...)):
        return engine.update_task(tid, body)

    @app.post("/api/tasks/archive")
    def archive_tasks(body: dict = Body(...)):
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 1000:
            return _err(400, "« ids » : la liste des discussions (1 000 au plus).")
        return engine.archive_tasks([str(x) for x in ids], body.get("archived", True) is not False)

    @app.post("/api/tasks/{tid}/keep-warm")
    def task_keep_warm(tid: str, body: dict = Body(default={})):
        return engine.keep_warm(tid, body.get("hours") or 0)

    @app.get("/api/tasks/{tid}/activity")
    def task_activity(tid: str):
        return engine.activity(tid)

    @app.post("/api/tasks/{tid}/ai-title")
    def task_ai_title(tid: str):
        return engine.ai_title(tid)

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
        return engine.followup(tid, body.text, body.attachments, compact=body.compact, regard=body.regard)

    # -------------------------------------------------------- what Claude changed in files (console/changes.py)
    @app.get("/api/tasks/{tid}/changes")
    def task_changes(tid: str):
        return engine.file_changes(tid)

    @app.get("/api/tasks/{tid}/changes/{cid}")
    def task_change(tid: str, cid: str):
        return engine.file_change(tid, cid)

    @app.post("/api/tasks/{tid}/changes/{cid}/undo")
    def task_change_undo(tid: str, cid: str, body: dict = Body(default={})):
        return engine.undo_change(tid, cid, force=bool(body.get("force")))

    @app.post("/api/tasks/{tid}/changes/{cid}/redo")
    def task_change_redo(tid: str, cid: str, body: dict = Body(default={})):
        return engine.undo_change(tid, cid, force=bool(body.get("force")), redo=True)

    @app.post("/api/tasks/{tid}/changes-file/undo")
    def task_change_file_undo(tid: str, body: dict = Body(...)):
        return engine.undo_file(tid, str(body.get("path") or ""))

    @app.post("/api/tasks/{tid}/displays/{key}/answer")
    def task_display_answer(tid: str, key: str, body: DisplayAnswerIn):
        return engine.display_answer(tid, key, body.bloc, body.choix, body.autre, body.bouton, body.valeurs)

    @app.post("/api/tasks/{tid}/displays/{key}/act")
    def task_display_act(tid: str, key: str, body: DisplayActIn):
        return engine.display_act(tid, key, body.bloc, body.carte, body.bouton)

    @app.post("/api/tasks/{tid}/displays/{key}/app")
    def task_display_app(tid: str, key: str, body: dict = Body(...)):
        return engine.display_app(tid, key, int(body.get("bloc") or 0))

    @app.post("/api/tasks/{tid}/displays/{key}/app-message")
    def task_display_app_message(tid: str, key: str, body: dict = Body(...)):
        return engine.display_app_message(tid, key, int(body.get("bloc") or 0), str(body.get("contenu") or ""))

    @app.post("/api/tasks/{tid}/cancel")
    def task_cancel(tid: str):
        return engine.cancel(tid)

    @app.post("/api/tasks/{tid}/retry")
    def task_retry(tid: str, body: dict = Body(default={})):
        return engine.retry(tid, confirmed=bool(body.get("confirmed")))

    @app.post("/api/tasks/{tid}/fork")
    def task_fork(tid: str, body: dict = Body(default={})):
        return engine.fork(tid, str(body.get("prompt") or ""), confirmed=bool(body.get("confirmed")))

    @app.post("/api/tasks/{tid}/move")
    def task_move(tid: str, body: dict = Body(...)):
        return engine.move_task(tid, str(body.get("workdir") or ""))

    # -------------------------------------------------------- project folder (instructions, memory, files)
    @app.get("/api/workspace")
    def workspace(profile: str | None = None, folder: str | None = None):
        return engine.workspace(profile, folder)

    @app.put("/api/workspace/instructions")
    def workspace_instructions(body: dict = Body(...)):
        return engine.save_instructions(body.get("profile"), body.get("folder"), str(body.get("scope") or ""),
                                        str(body.get("text") or ""))

    @app.get("/api/search")
    def search(q: str = ""):
        return engine.search(q)

    @app.get("/api/projects")
    def projects():
        return {"projects": engine.projects()}

    @app.put("/api/projects")
    def project_save(body: dict = Body(...)):
        return engine.save_project(body)

    @app.post("/api/projects/brief/run")
    def project_brief_run(body: dict = Body(...)):
        return engine.run_project_brief(str(body.get("folder") or ""))

    @app.get("/api/projects/odoo")
    def project_odoo(folder: str):
        return engine.odoo_tasks(folder)

    @app.post("/api/projects/odoo/link")
    def project_odoo_link(body: dict = Body(...)):
        return engine.link_odoo(str(body.get("folder") or ""), body.get("links") or [])

    @app.post("/api/projects/odoo/unlink")
    def project_odoo_unlink(body: dict = Body(...)):
        try:
            oid = int(body.get("id"))
        except (TypeError, ValueError):
            return _err(400, "Projet Odoo invalide.")
        return engine.unlink_odoo(str(body.get("folder") or ""), oid, str(body.get("app") or ""))

    @app.post("/api/projects/odoo/refresh")
    def project_odoo_refresh(body: dict = Body(...)):
        return engine.odoo_tasks_refresh(str(body.get("folder") or ""))

    @app.post("/api/projects/odoo/search")
    def project_odoo_search(body: dict = Body(...)):
        return engine.odoo_projects_search(str(body.get("folder") or ""), str(body.get("q") or ""))

    @app.get("/api/projects/odoo/search/{tid}")
    def project_odoo_search_result(tid: str, folder: str):
        return engine.odoo_projects_result(folder, tid)

    @app.post("/api/projects/odoo/task/read")
    def project_odoo_task_read(body: dict = Body(...)):
        try:
            tid = int(body.get("id"))
        except (TypeError, ValueError):
            return _err(400, "Tâche Odoo invalide.")
        return engine.odoo_task_read(str(body.get("folder") or ""), tid)

    @app.get("/api/projects/odoo/task/read/{tid}")
    def project_odoo_task_detail(tid: str, folder: str):
        return engine.odoo_task_detail(folder, tid)

    @app.post("/api/projects/odoo/task")
    def project_odoo_task_write(body: dict = Body(...)):
        try:
            tid = int(body.get("id"))
        except (TypeError, ValueError):
            return _err(400, "Tâche Odoo invalide.")
        description = body.get("description")
        return engine.odoo_task_write(str(body.get("folder") or ""), tid, str(body.get("name") or ""),
                                      str(body.get("stage") or ""), body.get("done") is True,
                                      None if description is None else str(description))

    @app.get("/api/projects/office")
    def project_office(folder: str):
        return engine.office_state(folder)

    @app.post("/api/projects/office/refresh")
    def project_office_refresh(body: dict = Body(...)):
        return engine.office_refresh(str(body.get("folder") or ""))

    @app.post("/api/projects/follow")
    def project_follow(body: dict = Body(...)):
        return engine.follow_sender(str(body.get("folder") or ""), str(body.get("sender") or ""))

    @app.delete("/api/projects")
    def project_delete(folder: str):
        engine.delete_project(folder)
        return {"ok": True}

    @app.get("/api/notes")
    def notes(folder: str | None = None):
        return {"notes": engine.notes(folder)}

    @app.post("/api/notes")
    def note_create(body: dict = Body(...)):
        return engine.save_note(body)

    @app.patch("/api/notes/{note_id}")
    def note_update(note_id: str, body: dict = Body(...)):
        return engine.save_note(body, note_id)

    @app.delete("/api/notes/{note_id}")
    def note_delete(note_id: str):
        engine.delete_note(note_id)
        return {"ok": True}

    @app.get("/api/projects/actions")
    def project_actions(folder: str, profile: str | None = None):
        proj = engine._project(folder)
        if not proj:
            return _err(404, "Projet introuvable.")
        return {"actions": engine.project_actions(proj.folder, content=True, profile=profile),
                "routines": engine.project_routines(proj.folder)}

    @app.post("/api/projects/actions/settings")
    def project_action_settings(body: dict = Body(...)):
        return engine.set_action_prefs(str(body.get("folder") or ""), str(body.get("name") or ""),
                                       str(body.get("model") or ""), str(body.get("effort") or ""))

    @app.post("/api/projects/actions/approve")
    def project_action_approve(body: dict = Body(...)):
        return engine.approve_action(str(body.get("folder") or ""), str(body.get("name") or ""),
                                     str(body.get("hash") or ""))

    @app.post("/api/projects/actions/run")
    def project_action_run(body: dict = Body(...)):
        return engine.run_action(str(body.get("folder") or ""), str(body.get("name") or ""),
                                 str(body.get("arguments") or ""), confirmed=bool(body.get("confirmed")),
                                 approve=str(body.get("approve") or ""), profile=body.get("profile") or None)

    # -------------------------------------------------------- the account's actions (its commands and skills)
    @app.get("/api/accounts/{pid}/actions")
    def account_actions(pid: str, content: bool = False):
        return {"actions": engine.account_actions(pid, content=content)}

    @app.post("/api/accounts/{pid}/actions/approve")
    def account_action_approve(pid: str, body: dict = Body(...)):
        return engine.approve_account_action(pid, str(body.get("name") or ""), str(body.get("hash") or ""))

    @app.post("/api/accounts/{pid}/actions/settings")
    def account_action_settings(pid: str, body: dict = Body(...)):
        return engine.set_account_action_prefs(pid, str(body.get("name") or ""), str(body.get("model") or ""),
                                               str(body.get("effort") or ""), bool(body.get("menu")))

    @app.post("/api/accounts/{pid}/actions/run")
    def account_action_run(pid: str, body: dict = Body(...)):
        return engine.run_account_action(pid, str(body.get("name") or ""), str(body.get("arguments") or ""),
                                         str(body.get("workdir") or ""), confirmed=bool(body.get("confirmed")),
                                         approve=str(body.get("approve") or ""))

    @app.delete("/api/workspace/rules")
    def workspace_rule_delete(pattern: str, profile: str | None = None, folder: str | None = None):
        _, wd = engine._ws_folder(profile, folder)
        engine.delete_project_rule(wd, pattern)
        return {"ok": True}

    @app.get("/api/workspace/memory")
    def workspace_memory(name: str, profile: str | None = None, folder: str | None = None):
        return engine.memory_note(profile, folder, name)

    @app.put("/api/workspace/memory")
    def workspace_memory_save(body: dict = Body(...)):
        return engine.save_memory_note(body.get("profile"), body.get("folder"), str(body.get("name") or ""),
                                       str(body.get("text") or ""))

    @app.delete("/api/workspace/memory")
    def workspace_memory_delete(name: str, profile: str | None = None, folder: str | None = None):
        engine.delete_memory_note(profile, folder, name)
        return {"ok": True}

    @app.get("/api/workspace/files")
    def workspace_files(profile: str | None = None, folder: str | None = None, sub: str = ""):
        return engine.workspace_files(profile, folder, sub)

    @app.get("/api/workspace/file")
    def workspace_file(path: str, profile: str | None = None, folder: str | None = None, stat: bool = False):
        return preview_response(engine.workspace_file(profile, folder, path), stat)

    @app.get("/api/workspace/frame")
    def workspace_frame(path: str, profile: str | None = None, folder: str | None = None, remote: bool = False):
        return engine.workspace_frame(profile, folder, path, remote)

    @app.get("/api/workspace/text")
    def workspace_text(path: str, profile: str | None = None, folder: str | None = None):
        return engine.file_text(engine.workspace_file(profile, folder, path))

    # -------------------------------------------------------- the documents' index (console/documents.py)
    @app.get("/api/documents")
    def documents():
        return engine.documents_status()

    @app.post("/api/documents/sync")
    def documents_sync():
        return engine.documents_sync()

    @app.post("/api/documents/clear")
    def documents_clear():
        return engine.documents_clear()

    @app.get("/api/documents/file")
    def document_file(path: str, stat: bool = False):
        return preview_response(engine.document_file(path), stat)

    @app.get("/api/documents/frame")
    def document_frame(path: str, remote: bool = False):
        return engine.document_frame(path, remote)

    @app.get("/api/documents/text")
    def document_text(path: str):
        return engine.file_text(engine.document_file(path))

    @app.post("/api/documents/file/open")
    def document_open(body: dict = Body(...)):
        engine.open_document_file(str(body.get("path") or ""), reveal=bool(body.get("reveal")))
        return {"ok": True}

    @app.get("/api/limits")
    def limits():
        return {"limits": engine.limits, "busy": sorted(engine._limits_busy)}

    @app.post("/api/limits/{pid}/refresh")
    def limits_refresh(pid: str):
        return engine.refresh_limits(pid)

    @app.get("/api/fs/places")
    def fs_places():
        return {"places": engine.fs_places()}

    @app.get("/api/fs/dirs")
    def fs_dirs(path: str = ""):
        return engine.fs_dirs(path)

    @app.post("/api/fs/mkdir")
    def fs_mkdir(body: dict = Body(...)):
        return engine.fs_mkdir(str(body.get("path") or ""), str(body.get("name") or ""))

    @app.post("/api/workspace/file/open")
    def workspace_file_open(body: dict = Body(...)):
        engine.open_workspace_file(body.get("profile"), body.get("folder"), str(body.get("path") or ""),
                                   reveal=bool(body.get("reveal")))
        return {"ok": True}

    @app.post("/api/tasks/{tid}/duplicate")
    def task_duplicate(tid: str, body: dict = Body(...)):
        return engine.duplicate(tid, str(body.get("profile", "")), confirmed=bool(body.get("confirmed")))

    @app.post("/api/tasks/{tid}/approvals/{aid}")
    def task_decide(tid: str, aid: str, body: DecisionIn):
        return engine.decide(tid, aid, body.decision, body.message, body.answers, body.remember, via=body.via)

    # -------------------------------------------------------- inbox (console/inbox.py)
    @app.get("/api/inbox")
    def inbox():
        return engine.inbox(start=True)

    @app.post("/api/inbox/read")
    def inbox_read(body: InboxMarkIn):
        return engine.inbox_mark(body.ids, body.section or None, body.tasks)

    @app.post("/api/inbox/dismiss")
    def inbox_dismiss(body: InboxMarkIn):
        return engine.inbox_mark(body.ids, body.section or None, body.tasks, dismiss=True)

    @app.post("/api/tasks/{tid}/resume-expired")
    def task_resume_expired(tid: str, body: dict = Body(...)):
        return engine.resume_expired(tid, str(body.get("aid", "")))

    @app.get("/api/tasks/{tid}/file")
    def task_file(tid: str, path: str, stat: bool = False):
        return preview_response(engine.task_file(tid, path), stat)

    @app.get("/api/tasks/{tid}/frame")
    def task_frame(tid: str, path: str, remote: bool = False):
        return engine.task_frame(tid, path, remote)

    @app.get("/api/tasks/{tid}/text")
    def task_file_text(tid: str, path: str):
        return engine.file_text(engine.task_file(tid, path))

    @app.get("/api/tasks/{tid}/result")
    def task_result(tid: str, id: str, contient: str = "", remote: bool = False):
        return engine.result_view(tid, id, contient, remote)

    @app.post("/api/security/trust")
    def trust_domain(body: dict = Body(...)):
        d = web_domain(str(body.get("domain") or ""))
        if not d:
            return _err(422, "Domaine invalide.")
        cfg = cfg_store.config.model_copy(deep=True)
        if d not in cfg.security.trusted_domains:
            cfg.security.trusted_domains.append(d)
            out = save(cfg, f"domaine approuvé : {d}")
        else:
            out = {"config": dump(cfg), "meta": meta(), "restart_needed": False}
        return {**out, "domain": d}

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

    @app.get("/api/sessions/duplicates")
    def session_duplicates(profile: str | None = None):
        return {"duplicates": engine.moved_copies(profile)}

    @app.post("/api/sessions/duplicates/merge")
    def session_duplicates_merge(body: dict = Body(default={})):
        return engine.merge_moved_copies(body.get("profile") or None)

    @app.post("/api/sessions/{pid}/{sid}/move")
    def session_move(pid: str, sid: str, body: dict = Body(...)):
        return engine.move_session(pid, sid, str(body.get("workdir") or ""))

    @app.get("/api/sessions/{pid}/{sid}")
    def session_detail(pid: str, sid: str):
        return engine.session_transcript(pid, sid)

    @app.post("/api/sessions/{pid}/{sid}/resume")
    def session_resume(pid: str, sid: str, body: dict = Body(...)):
        return engine.resume_session(pid, sid, str(body.get("prompt", "")), preset=body.get("preset") or None,
                                     model=body.get("model") or None, effort=body.get("effort") or None,
                                     fork=bool(body.get("fork", True)), confirmed=bool(body.get("confirmed")),
                                     workdir=body.get("workdir") or None)

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

    # -------------------------------------------------------- widgets (displays pinned to the desktop)
    @app.get("/api/widgets")
    def widgets():
        return {"widgets": engine.list_widgets()}

    @app.post("/api/widgets")
    def widget_pin(body: dict = Body(...)):
        return engine.pin_widget(str(body.get("task") or ""), str(body.get("key") or ""))

    @app.delete("/api/widgets/{wid}")
    def widget_unpin(wid: str):
        engine.unpin_widget(wid)
        return {"ok": True}

    @app.post("/api/widgets/{wid}/refresh")
    def widget_refresh(wid: str):
        return engine.refresh_widget(wid)

    @app.post("/api/widgets/{wid}/auto")
    def widget_auto(wid: str, body: dict = Body(...)):
        return engine.auto_widget(wid, str(body.get("every") or ""))

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
                "stoppable": getattr(app.state, "server", None) is not None,
                "launcher": winsys.launcher_exists(), "platform": "mac" if sys.platform == "darwin" else os.name,
                "desktop_app": winsys.app_info(data_dir) is not None, "open_as": cfg_store.config.general.open_as}

    @app.post("/api/system/launcher")
    def system_launcher():
        try:
            paths = winsys.create_launcher(desktop_app())
        except (OSError, subprocess.CalledProcessError) as exc:
            return _err(500, f"Lanceur non créé : {exc}")
        store.audit("lanceur créé", {"chemins": paths})
        return {"paths": paths}

    @app.post("/api/system/shutdown")
    def system_shutdown():
        server = getattr(app.state, "server", None)
        if server is None:
            return _err(409, "Cette console n'a pas été lancée par start.bat : arrête-la depuis son terminal.")
        store.audit("arrêt de la console", {"tâches en cours": engine.state()["running"]})
        threading.Timer(0.6, lambda: setattr(server, "should_exit", True)).start()
        return {"ok": True}

    # -------------------------------------------------------- updates from GitHub
    upd: dict = {"status": None}

    def check_updates():
        upd["status"] = updater.status(fetch=True)
        return upd["status"]

    def update_loop():
        time.sleep(20)
        while True:
            if cfg_store.config.general.update_check:
                try:
                    check_updates()
                except Exception:  # noqa: BLE001 - a failed check never stops the console
                    pass
            time.sleep(6 * 3600)

    if start_threads:
        threading.Thread(target=update_loop, name="updates", daemon=True).start()

    @app.get("/api/system/update")
    def update_status():
        if not upd["status"]:  # until the first check on GitHub: what the local copy knows
            upd["status"] = {**updater.status(fetch=False), "stale": True}
        return upd["status"]

    @app.post("/api/system/update/check")
    def update_check():
        return check_updates()

    @app.post("/api/system/update")
    def update_install():
        server = getattr(app.state, "server", None)
        if server is None:
            return _err(409, "Cette console n'a pas été lancée par start.bat : mets-la à jour depuis son terminal (git pull).")
        try:
            res = updater.update()
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            return _err(409, str(exc))
        upd["status"] = None
        store.audit("mise à jour de la console", {"changements": res["commits"], "dépendances": res["requirements"]})
        if res["updated"]:
            winsys.relaunch(port, data_dir)
            threading.Timer(0.6, lambda: setattr(server, "should_exit", True)).start()
        return {**res, "boot": boot}

    @app.get("/api/system/version")
    def system_version():
        return {"version": __version__, "boot": boot, "stale": code_stamp() > loaded + 1,
                "restartable": getattr(app.state, "server", None) is not None}

    @app.post("/api/system/restart")
    def system_restart():
        """Stop, and let a fresh process (waiting for the port) take over with the new code."""
        server = getattr(app.state, "server", None)
        if server is None:
            return _err(409, "Cette console n'a pas été lancée par start.bat : redémarre-la depuis son terminal.")
        store.audit("redémarrage de la console", {"tâches en cours": engine.state()["running"]})
        winsys.relaunch(port, data_dir)
        threading.Timer(0.6, lambda: setattr(server, "should_exit", True)).start()
        return {"ok": True, "boot": boot}

    @app.post("/api/system/startup")
    def system_startup(body: dict = Body(...)):
        try:
            on = winsys.set_startup(bool(body.get("on")), desktop_app())
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

    # -------------------------------------------------------- preview origin (http://apercu.localhost:<port>)
    def frame_parents() -> str:
        return " ".join(sorted(o for o in origins() if re.fullmatch(r"https?://[\w.\-\[\]:]+", o)))

    @app.get("/v/{cid}/{rest:path}")
    def preview_page(cid: str, rest: str = ""):
        e = engine.contents.get(cid)
        if not e:
            return PlainTextResponse("Cet aperçu a expiré : rouvre-le depuis la console.", status_code=404)
        if e.kind != "page":  # an application written by Claude, or its shell: nothing next to them
            csp = (content.shell_policy(e.target, frame_parents()) if e.kind == "shell"
                   else content.app_policy(f"http://{content_host} {frame_parents()}"))
            if rest:
                return PlainTextResponse("Introuvable.", status_code=404, headers={"Content-Security-Policy": csp})
            return Response(e.page, media_type="text/html; charset=utf-8",
                            headers={"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"})
        csp = content.policy(f"http://{content_host}", frame_parents(), e.remote)
        if not rest:
            return Response(e.page, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": csp})
        p = e.resolve(rest) if e.resolve else None
        if not p:
            return PlainTextResponse("Introuvable.", status_code=404, headers={"Content-Security-Policy": csp})
        if p.suffix.lower() in (".html", ".htm", ".xhtml"):
            page = content.prepare(content.decode_html(p.read_bytes())).encode("utf-8")
            return Response(page, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": csp})
        media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return FileResponse(p, media_type=media, headers={"Content-Security-Policy": csp})

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
