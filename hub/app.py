"""API v1 du hub. Chaque réponse porte le numéro d'API."""
from __future__ import annotations

import re
from typing import Literal

import queue

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from . import API
from .db import HubDB
from .live import Live

MAX_ARCHIVE = 50 * 1024 * 1024

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:\-\[\]]{0,80}$")
_FOLDER = re.compile(r"^[A-Za-z0-9._-]{0,80}$")
PRESETS = {"lecture", "web", "brouillons", "edition", "assiste", "complet"}
EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}


def create_app(db: HubDB) -> FastAPI:
    app = FastAPI(title="JARVIS Hub", docs_url=None, redoc_url=None)
    app.state.db = db
    app.state.live = Live()

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"api": API, "error": "requête invalide"})

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        detail.setdefault("error", "refusé")
        return JSONResponse(status_code=exc.status_code, content={"api": API, **detail})

    @app.get("/health")
    def health():
        return {"api": API, "ok": True}

    @app.get("/api/v1/me")
    def me(user: dict = Depends(current_user)):
        return {"api": API, "user": user}

    @app.get("/api/v1/projects")
    def projects(user: dict = Depends(current_user)):
        return {"api": API, "projects": db.projects_of(user["id"])}

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectIn, user: dict = Depends(current_user)):
        if not user["admin"]:
            raise HTTPException(403, {"error": "réservé à l'administrateur"})
        try:
            project = db.create_project(user["id"], body.model_dump())
        except ValueError as exc:
            raise HTTPException(409, {"error": str(exc)}) from exc
        return {"api": API, "project": project}

    @app.put("/api/v1/projects/{project_id}")
    def update_project(project_id: str, body: ProjectPatch, user: dict = Depends(current_user)):
        _need_manage(db, user, project_id)
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            state, project = db.access(user["id"], project_id, manage=True)
            _raise_access(state)
            return {"api": API, "project": project}
        try:
            project = db.update_project(user["id"], project_id, fields)
        except KeyError:
            raise HTTPException(404, {"error": "projet introuvable"}) from None
        return {"api": API, "project": project}

    @app.put("/api/v1/projects/{project_id}/members")
    def put_members(project_id: str, body: MembersIn, user: dict = Depends(current_user)):
        _need_manage(db, user, project_id)
        missing = db.members_known([m.id for m in body.members])
        if missing:
            raise HTTPException(400, {"error": "utilisateur inconnu", "users": missing})
        try:
            members = db.set_members(user["id"], project_id, [m.model_dump() for m in body.members])
        except KeyError:
            raise HTTPException(404, {"error": "projet introuvable"}) from None
        return {"api": API, "members": members}

    @app.get("/api/v1/audit")
    def audit(user: dict = Depends(current_user)):
        if not user["admin"]:
            raise HTTPException(403, {"error": "réservé à l'administrateur"})
        return {"api": API, "entries": db.audit()}

    @app.post("/api/v1/presence")
    def presence(body: PresenceIn, user: dict = Depends(current_user)):
        allowed = []
        for item in body.projects:
            if db.is_member(user["id"], item.id):
                allowed.append(item.model_dump())
        app.state.live.beat(user["id"], allowed)
        return {"api": API, "ok": True}

    @app.get("/api/v1/presence")
    def presence_now(user: dict = Depends(current_user)):
        projects = {p["id"] for p in db.projects_of(user["id"])}
        return {"api": API, "presence": app.state.live.snapshot(projects)}

    @app.get("/api/v1/syncthing")
    def syncthing_info(user: dict = Depends(current_user)):
        from .sync_link import local_id
        try:
            return {"api": API, "device_id": local_id()}
        except (OSError, ValueError) as exc:
            raise HTTPException(503, {"error": "Syncthing indisponible sur le serveur."}) from exc

    @app.post("/api/v1/projects/{project_id}/sync")
    def sync_join(project_id: str, body: SyncIn, user: dict = Depends(current_user)):
        state, project = db.access(user["id"], project_id, manage=False)
        _raise_access(state)
        from .sync_link import register
        try:
            register(body.device_id, project["syncthing_folder"] or project["id"])
        except ValueError as exc:
            raise HTTPException(422, {"error": str(exc)}) from exc
        return {"api": API, "ok": True}

    @app.get("/api/v1/events")
    def events(request: Request, user: dict = Depends(current_user)):
        live = app.state.live
        sub = live.subscribe()

        def gen():
            import json
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        event = sub.get(timeout=15)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    project = event.get("project")
                    if project and not db.is_member(user["id"], project):
                        continue
                    yield f"data: {json.dumps({'api': API, **event}, ensure_ascii=False)}\n\n"
            finally:
                live.unsubscribe(sub)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/projects/{project_id}/conversations")
    def list_conversations(project_id: str, user: dict = Depends(current_user)):
        _need_member(db, user, project_id)
        return {"api": API, "conversations": db.conversations_of(project_id)}

    @app.post("/api/v1/projects/{project_id}/conversations", status_code=201)
    async def publish_conversation(project_id: str, request: Request, user: dict = Depends(current_user)):
        _need_member(db, user, project_id)
        blob = await request.body()
        if len(blob) > MAX_ARCHIVE:
            raise HTTPException(413, {"error": "archive trop lourde"})
        title = (request.query_params.get("title") or "").strip()
        session = (request.query_params.get("session") or "").strip()
        mode = request.query_params.get("mode") or "stripped"
        if not title or not session or mode not in {"complete", "stripped"}:
            raise HTTPException(400, {"error": "titre, session et mode requis"})
        try:
            import json
            stats = json.loads(request.query_params.get("stats") or "{}")
        except ValueError:
            stats = {}
        parent = request.query_params.get("parent") or None
        try:
            conv = db.save_conversation(user["id"], project_id, title=title[:120], session_id=session[:80],
                                        mode=mode, stats=stats if isinstance(stats, dict) else {},
                                        blob=blob, parent=parent)
        except KeyError:
            raise HTTPException(404, {"error": "introuvable"}) from None
        except PermissionError:
            raise HTTPException(403, {"error": "réservé à l'auteur"}) from None
        app.state.live.publish("conversation", {"project": project_id, "conversation": conv["id"]})
        return {"api": API, "conversation": conv}

    @app.get("/api/v1/conversations/{conv_id}")
    def conversation_meta(conv_id: str, user: dict = Depends(current_user)):
        conv = db.conversation(conv_id)
        if conv is None or not db.is_member(user["id"], conv["project"]):
            raise HTTPException(404, {"error": "introuvable"})
        return {"api": API, "conversation": conv}

    @app.get("/api/v1/conversations/{conv_id}/archive")
    def conversation_archive(conv_id: str, user: dict = Depends(current_user), rev: int | None = None):
        conv = db.conversation(conv_id)
        if conv is None or not db.is_member(user["id"], conv["project"]):
            raise HTTPException(404, {"error": "introuvable"})
        path = db.conversation_zip(conv_id, rev)
        if path is None:
            raise HTTPException(404, {"error": "archive introuvable"})
        return Response(path.read_bytes(), media_type="application/zip")

    @app.put("/api/v1/conversations/{conv_id}")
    async def revise_conversation(conv_id: str, request: Request, user: dict = Depends(current_user)):
        conv = db.conversation(conv_id)
        if conv is None or not db.is_member(user["id"], conv["project"]):
            raise HTTPException(404, {"error": "introuvable"})
        blob = await request.body()
        if len(blob) > MAX_ARCHIVE:
            raise HTTPException(413, {"error": "archive trop lourde"})
        mode = request.query_params.get("mode") or conv["mode"]
        if mode not in {"complete", "stripped"}:
            raise HTTPException(400, {"error": "mode inconnu"})
        try:
            updated = db.save_conversation(
                user["id"], conv["project"], title=request.query_params.get("title") or conv["title"],
                session_id=conv["session_id"], mode=mode, stats=conv["stats"], blob=blob, parent=conv_id)
        except PermissionError:
            raise HTTPException(403, {"error": "réservé à l'auteur"}) from None
        return {"api": API, "conversation": updated}

    @app.delete("/api/v1/conversations/{conv_id}")
    def delete_conversation(conv_id: str, user: dict = Depends(current_user)):
        conv = db.conversation(conv_id)
        if conv is None or not db.is_member(user["id"], conv["project"]):
            raise HTTPException(404, {"error": "introuvable"})
        try:
            db.remove_conversation(user["id"], conv_id, admin=user["admin"])
        except PermissionError:
            raise HTTPException(403, {"error": "réservé à l'auteur"}) from None
        return {"api": API, "ok": True}

    @app.get("/api/v1/projects/{project_id}/messages")
    def list_messages(project_id: str, user: dict = Depends(current_user), before: float | None = None):
        _need_member(db, user, project_id)
        return {"api": API, "messages": db.messages_of(project_id, before)}

    @app.post("/api/v1/projects/{project_id}/messages", status_code=201)
    def post_message(project_id: str, body: MessageIn, user: dict = Depends(current_user)):
        _need_member(db, user, project_id)
        try:
            message = db.add_message(user["id"], project_id, body.text, [r.model_dump() for r in body.refs])
        except KeyError:
            raise HTTPException(404, {"error": "projet introuvable"}) from None
        app.state.live.publish("message", {"project": project_id, "message": message["id"]})
        return {"api": API, "message": message}

    @app.patch("/api/v1/messages/{msg_id}")
    def patch_message(msg_id: str, body: MessagePatch, user: dict = Depends(current_user)):
        try:
            message = db.edit_message(user["id"], msg_id, text=body.text, delete=body.deleted)
        except KeyError:
            raise HTTPException(404, {"error": "introuvable"}) from None
        except PermissionError:
            raise HTTPException(403, {"error": "réservé à l'auteur"}) from None
        return {"api": API, "message": message}

    return app


def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(401, {"error": "jeton manquant"})
    user = request.app.state.db.user_by_token(token)
    if user is None:
        raise HTTPException(401, {"error": "jeton refusé"})
    return user


def _need_member(db: HubDB, user: dict, project_id: str):
    if not db.is_member(user["id"], project_id):
        raise HTTPException(404, {"error": "projet introuvable"})


def _need_manage(db: HubDB, user: dict, project_id: str):
    state, _project = db.access(user["id"], project_id, manage=True)
    _raise_access(state)


def _raise_access(state: str):
    if state == "ok":
        return
    if state == "forbidden":
        raise HTTPException(403, {"error": "réservé à l'administrateur du projet"})
    raise HTTPException(404, {"error": "projet introuvable"})


class PresenceItem(BaseModel):
    id: str
    sessions: int = 0
    files: list[str] = Field(default_factory=list)


class PresenceIn(BaseModel):
    projects: list[PresenceItem] = Field(default_factory=list)


class SyncIn(BaseModel):
    device_id: str


class RefIn(BaseModel):
    kind: Literal["file", "conversation"]
    path: str = ""
    id: str = ""


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    refs: list[RefIn] = Field(default_factory=list)


class MessagePatch(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    deleted: bool = False


class ProjectIn(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    color: str = "#40dcff"
    syncthing_folder: str = ""
    preset: str = "assiste"
    model: str = ""
    effort: str = ""

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.fullmatch(value or ""):
            raise ValueError("identifiant invalide")
        return value

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if not _COLOR.fullmatch(value or ""):
            raise ValueError("couleur invalide")
        return value.lower()

    @field_validator("syncthing_folder")
    @classmethod
    def _folder(cls, value: str) -> str:
        if not _FOLDER.fullmatch(value or ""):
            raise ValueError("dossier Syncthing invalide")
        return value

    @field_validator("preset")
    @classmethod
    def _preset(cls, value: str) -> str:
        if value not in PRESETS:
            raise ValueError("préréglage inconnu")
        return value

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        if not _MODEL.fullmatch(value or ""):
            raise ValueError("modèle invalide")
        return value

    @field_validator("effort")
    @classmethod
    def _effort(cls, value: str) -> str:
        if value not in EFFORTS:
            raise ValueError("effort inconnu")
        return value


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = None
    syncthing_folder: str | None = None
    preset: str | None = None
    model: str | None = None
    effort: str | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        if value is not None and not _COLOR.fullmatch(value):
            raise ValueError("couleur invalide")
        return value.lower() if value else value

    @field_validator("syncthing_folder")
    @classmethod
    def _folder(cls, value: str | None) -> str | None:
        if value is not None and not _FOLDER.fullmatch(value):
            raise ValueError("dossier Syncthing invalide")
        return value

    @field_validator("preset")
    @classmethod
    def _preset(cls, value: str | None) -> str | None:
        if value is not None and value not in PRESETS:
            raise ValueError("préréglage inconnu")
        return value

    @field_validator("model")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        if value is not None and not _MODEL.fullmatch(value):
            raise ValueError("modèle invalide")
        return value

    @field_validator("effort")
    @classmethod
    def _effort(cls, value: str | None) -> str | None:
        if value is not None and value not in EFFORTS:
            raise ValueError("effort inconnu")
        return value


class MemberIn(BaseModel):
    id: str
    role: Literal["membre", "administrateur"]

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.fullmatch(value or ""):
            raise ValueError("identifiant invalide")
        return value


class MembersIn(BaseModel):
    members: list[MemberIn] = Field(min_length=1)

    @field_validator("members")
    @classmethod
    def _one_admin(cls, value: list[MemberIn]) -> list[MemberIn]:
        ids = [m.id for m in value]
        if len(ids) != len(set(ids)):
            raise ValueError("membre en double")
        if not any(m.role == "administrateur" for m in value):
            raise ValueError("il faut un administrateur du projet")
        return value
