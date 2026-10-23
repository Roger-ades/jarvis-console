"""API v1 du hub. Chaque réponse porte le numéro d'API."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import API
from .db import HubDB

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:\-\[\]]{0,80}$")
_FOLDER = re.compile(r"^[A-Za-z0-9._-]{0,80}$")
PRESETS = {"lecture", "web", "brouillons", "edition", "assiste", "complet"}
EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}


def create_app(db: HubDB) -> FastAPI:
    app = FastAPI(title="JARVIS Hub", docs_url=None, redoc_url=None)
    app.state.db = db

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


def _need_manage(db: HubDB, user: dict, project_id: str):
    state, _project = db.access(user["id"], project_id, manage=True)
    _raise_access(state)


def _raise_access(state: str):
    if state == "ok":
        return
    if state == "forbidden":
        raise HTTPException(403, {"error": "réservé à l'administrateur du projet"})
    raise HTTPException(404, {"error": "projet introuvable"})


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
