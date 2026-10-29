"""API v1 du hub. Chaque réponse porte le numéro d'API."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from typing import Literal

import queue

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from . import API, git_http, phone
from .db import HubDB
from .live import Live

MAX_ARCHIVE = 50 * 1024 * 1024
MAX_MODULE = 25 * 1024 * 1024    # un module fait 20 Mo au plus une fois dézippé (console/addons.py)

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:\-\[\]]{0,80}$")
_FOLDER = re.compile(r"^[A-Za-z0-9._-]{0,80}$")
PRESETS = {"lecture", "web", "brouillons", "edition", "assiste", "complet"}
EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}
# Empreinte de la clé d'inscription écrite dans JARVIS (console/hub_client.py) : seule une copie de JARVIS
# crée un compte. JARVIS_HUB_REGISTER_KEY_HASH dans hub.env la remplace, pour changer de clé sans attendre.
REGISTER_KEY_HASH = "a3213ee4d3ec31c3acfeae059f9390a4cb4f4830b745e5a3876579b09662a53d"
REGISTER_TRIES = 10     # essais refusés tolérés par minute, tous postes confondus


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
        from . import refresh
        body = {"api": API, "ok": True}
        if refresh.armed():
            body["refresh"] = True
            body["rev"] = refresh.current_rev()
        return body

    @app.get("/m", response_class=HTMLResponse)
    def mobile_page():
        """Validations Approuver / Refuser depuis le téléphone (VPN / LAN)."""
        phone.ensure(db)
        return HTMLResponse(phone.mobile_html())

    @app.put("/api/v1/phone/pending")
    def phone_push(body: dict, user: dict = Depends(current_user)):
        items = body.get("pending") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise HTTPException(422, {"error": "pending (liste) requis"})
        n = phone.put_many(db, user["id"], items)
        return {"api": API, "ok": True, "n": n}

    @app.get("/api/v1/phone/pending")
    def phone_list(user: dict = Depends(current_user)):
        return {"api": API, "pending": phone.list_pending(db, user["id"])}

    @app.post("/api/v1/phone/decide")
    def phone_decide(body: dict, user: dict = Depends(current_user)):
        try:
            return {"api": API, **phone.decide(db, user["id"], body or {})}
        except ValueError as exc:
            raise HTTPException(400, {"error": str(exc)}) from exc

    @app.get("/api/v1/phone/decisions")
    def phone_decisions(user: dict = Depends(current_user)):
        return {"api": API, "decisions": phone.take_decisions(db, user["id"])}

    @app.get("/api/v1/me")
    def me(user: dict = Depends(current_user)):
        return {"api": API, "user": user}

    @app.get("/api/v1/projects")
    def projects(user: dict = Depends(current_user)):
        return {"api": API, "projects": db.projects_of(user["id"])}

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectIn, user: dict = Depends(current_user)):
        # Tout compte actif partage un projet : il en devient l'administrateur.
        try:
            project = db.create_project(user["id"], body.model_dump(exclude={"members"}),
                                        [m.model_dump() for m in body.members])
        except LookupError as exc:
            raise HTTPException(400, {"error": "utilisateur inconnu", "users": [str(exc.args[0])]}) from exc
        except ValueError as exc:
            raise HTTPException(409, {"error": str(exc)}) from exc
        app.state.live.publish("members", {"project": project["id"]})
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

    @app.get("/api/v1/users")
    def users(user: dict = Depends(current_user)):
        # Les comptes actifs, pour choisir les membres d'un projet : ni jeton ni empreinte.
        return {"api": API, "users": [{"id": u["id"], "name": u["name"], "color": u["color"]} for u in db.users()]}

    refused: list[float] = []
    refused_lock = threading.Lock()

    @app.post("/api/v1/register", status_code=201)
    def register(body: RegisterIn):
        """Un poste JARVIS sans jeton crée son compte. Le jeton n'est rendu qu'ici, une seule fois."""
        now = time.time()
        with refused_lock:
            refused[:] = [t for t in refused if now - t < 60]
            if len(refused) >= REGISTER_TRIES:
                raise HTTPException(429, {"error": "trop d'essais, réessaie dans une minute"})
            expected = os.environ.get("JARVIS_HUB_REGISTER_KEY_HASH") or REGISTER_KEY_HASH
            if not hmac.compare_digest(hashlib.sha256(body.key.encode("utf-8")).hexdigest(), expected):
                refused.append(now)
                raise HTTPException(403, {"error": "clé d'inscription refusée : mets JARVIS à jour"})
        try:
            token = db.register_user(body.id, body.name, body.color.lower())
        except ValueError as exc:
            raise HTTPException(409, {"error": str(exc)}) from exc
        app.state.live.publish("users", {"user": body.id})
        return {"api": API, "user": db.user_row(body.id), "token": token}

    @app.delete("/api/v1/users/{user_id}")
    def revoke_user(user_id: str, user: dict = Depends(current_user)):
        """L'administrateur retire un compte : son jeton ne vaut plus rien et ses postes quittent chaque dossier."""
        if not user["admin"]:
            raise HTTPException(403, {"error": "réservé à l'administrateur"})
        if user_id == user["id"]:
            raise HTTPException(400, {"error": "un administrateur ne se révoque pas lui-même"})
        projects = [p["id"] for p in db.projects_of(user_id)] if db.user_row(user_id) else []
        try:
            db.revoke_user(user_id)
        except ValueError as exc:
            raise HTTPException(404 if "inconnu" in str(exc) else 409, {"error": str(exc)}) from exc
        from .sync_link import revoke
        errors = []
        for project_id in projects:
            errors += revoke(db, user_id, project_id)
        app.state.live.publish("users", {"user": user_id})
        return {"api": API, "ok": True, **({"sync_errors": errors} if errors else {})}

    @app.get("/api/v1/projects/{project_id}/members")
    def get_members(project_id: str, user: dict = Depends(current_user)):
        state, _project = db.access(user["id"], project_id, manage=False)
        _raise_access(state)
        return {"api": API, "members": db.members(project_id)}

    @app.put("/api/v1/projects/{project_id}/members")
    def put_members(project_id: str, body: MembersIn, user: dict = Depends(current_user)):
        _need_manage(db, user, project_id)
        missing = db.members_known([m.id for m in body.members])
        if missing:
            raise HTTPException(400, {"error": "utilisateur inconnu", "users": missing})
        before = {m["id"] for m in db.members(project_id)}
        try:
            members = db.set_members(user["id"], project_id, [m.model_dump() for m in body.members])
        except KeyError:
            raise HTTPException(404, {"error": "projet introuvable"}) from None
        errors = _revoke_left(db, project_id, before - {m["id"] for m in members})
        app.state.live.publish("members", {"project": project_id})
        return {"api": API, "members": members, **({"sync_errors": errors} if errors else {})}

    @app.delete("/api/v1/projects/{project_id}/members/me")
    def leave(project_id: str, user: dict = Depends(current_user)):
        """Un membre quitte le projet de lui-même ; le dernier administrateur passe d'abord la main."""
        _need_member(db, user, project_id)
        try:
            db.remove_member(user["id"], project_id, user["id"])
        except ValueError as exc:
            raise HTTPException(409, {"error": "dernier administrateur du projet : nomme d'abord un autre administrateur"
                                      if "dernier" in str(exc) else str(exc)}) from exc
        _revoke_left(db, project_id, {user["id"]})
        app.state.live.publish("members", {"project": project_id})
        return {"api": API, "ok": True}

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
            register(body.device_id, project["syncthing_folder"] or project["id"],
                     label=project["name"], name=f"{user['id']}-{body.device_id[:7]}")
        except ValueError as exc:
            raise HTTPException(422, {"error": str(exc)}) from exc
        db.add_device(user["id"], project_id, body.device_id, project["syncthing_folder"] or project["id"])
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

    @app.api_route("/git/" + git_http.NAME + "/{path:path}", methods=["GET", "POST"])
    async def git_repo(path: str, request: Request, user: dict = Depends(current_user)):
        """Le dépôt de JARVIS en lecture seule : la mise à jour en un clic des postes inscrits."""
        query = request.url.query
        if not git_http.allowed(request.method, path, query):
            raise HTTPException(403, {"error": "lecture seule"})
        body = await request.body()
        if len(body) > git_http.MAX_BODY:
            raise HTTPException(413, {"error": "requête trop lourde"})
        headers = {k.lower(): v for k, v in request.headers.items()}
        status, out, payload = await run_in_threadpool(git_http.backend, request.method, path, query, headers, body)
        return Response(payload, status_code=status, headers=dict(out))

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
        except ValueError as exc:
            raise HTTPException(422, {"error": str(exc)}) from exc
        return {"api": API, "message": message}

    # Un module envoyé d'un JARVIS à l'autre : le zip attend dans la boîte du destinataire, qui l'installe d'un clic.
    @app.post("/api/v1/modules", status_code=201)
    async def send_module(request: Request, user: dict = Depends(current_user)):
        blob = await request.body()
        if len(blob) > MAX_MODULE:
            raise HTTPException(413, {"error": "module trop lourd"})
        to = sorted({t.strip() for t in (request.query_params.get("to") or "").split(",") if t.strip()} - {user["id"]})
        if not to or len(to) > 50:
            raise HTTPException(400, {"error": "destinataires requis"})
        info = _module_info(blob)
        if info is None:
            raise HTTPException(422, {"error": "ce n'est pas un module JARVIS"})
        try:
            sent = db.send_module(user["id"], to, info, blob)
        except KeyError as exc:
            raise HTTPException(404, {"error": f"compte inconnu : {exc.args[0]}"}) from None
        return {"api": API, "modules": sent}

    @app.get("/api/v1/modules")
    def received_modules(user: dict = Depends(current_user)):
        return {"api": API, "modules": db.modules_for(user["id"])}

    @app.get("/api/v1/modules/{mod_id}/archive")
    def module_archive(mod_id: str, user: dict = Depends(current_user)):
        path = db.module_zip(user["id"], mod_id)
        if path is None:
            raise HTTPException(404, {"error": "introuvable"})
        return Response(path.read_bytes(), media_type="application/zip")

    @app.delete("/api/v1/modules/{mod_id}")
    def close_module(mod_id: str, user: dict = Depends(current_user), outcome: str = "ignoré"):
        if outcome not in ("installé", "ignoré"):
            raise HTTPException(400, {"error": "installé ou ignoré"})
        try:
            db.close_module(user["id"], mod_id, outcome)
        except KeyError:
            raise HTTPException(404, {"error": "introuvable"}) from None
        return {"api": API, "ok": True}

    # Des compétences envoyées d'un JARVIS à l'autre, avec leurs groupes : le zip attend dans la boîte du destinataire.
    @app.post("/api/v1/competences", status_code=201)
    async def send_skills(request: Request, user: dict = Depends(current_user)):
        blob = await request.body()
        if len(blob) > MAX_MODULE:
            raise HTTPException(413, {"error": "compétences trop lourdes"})
        to = sorted({t.strip() for t in (request.query_params.get("to") or "").split(",") if t.strip()} - {user["id"]})
        if not to or len(to) > 50:
            raise HTTPException(400, {"error": "destinataires requis"})
        skills = _skills_info(blob)
        if not skills:
            raise HTTPException(422, {"error": "ce ne sont pas des compétences JARVIS"})
        try:
            sent = db.send_skills(user["id"], to, skills, blob)
        except KeyError as exc:
            raise HTTPException(404, {"error": f"compte inconnu : {exc.args[0]}"}) from None
        return {"api": API, "packs": sent}

    @app.get("/api/v1/competences")
    def received_skills(user: dict = Depends(current_user)):
        return {"api": API, "packs": db.skills_for(user["id"])}

    @app.get("/api/v1/competences/{pack_id}/archive")
    def skills_archive(pack_id: str, user: dict = Depends(current_user)):
        path = db.skills_zip(user["id"], pack_id)
        if path is None:
            raise HTTPException(404, {"error": "introuvable"})
        return Response(path.read_bytes(), media_type="application/zip")

    @app.delete("/api/v1/competences/{pack_id}")
    def close_skills(pack_id: str, user: dict = Depends(current_user), outcome: str = "ignoré"):
        if outcome not in ("installé", "ignoré"):
            raise HTTPException(400, {"error": "installé ou ignoré"})
        try:
            db.close_skills(user["id"], pack_id, outcome)
        except KeyError:
            raise HTTPException(404, {"error": "introuvable"}) from None
        return {"api": API, "ok": True}

    # Les compétences partagées : une bibliothèque commune à tous les comptes du hub, que chaque JARVIS recopie dans
    # ses comptes et met à jour (console/skill_library.py). Pas de liste de membres : tout compte inscrit y a accès.
    @app.get("/api/v1/bibliotheque")
    def library(user: dict = Depends(current_user)):
        return {"api": API, **db.library()}

    @app.get("/api/v1/bibliotheque/{name}/archive")
    def library_archive(name: str, user: dict = Depends(current_user)):
        path = db.library_zip(name)
        if path is None:
            raise HTTPException(404, {"error": "introuvable"})
        return Response(path.read_bytes(), media_type="application/zip")

    @app.put("/api/v1/bibliotheque/{name}")
    async def library_put(name: str, request: Request, user: dict = Depends(current_user), base: int = 0):
        blob = await request.body()
        if len(blob) > MAX_MODULE:
            raise HTTPException(413, {"error": "compétence trop lourde"})
        skills = _skills_info(blob)
        if len(skills) != 1 or skills[0]["nom"] != name:
            raise HTTPException(422, {"error": "une seule compétence JARVIS, de ce nom, attendue"})
        try:
            row = db.library_put(user["id"], name, _group(skills[0]["groupe"]), skills[0]["description"], blob, base)
        except ValueError as exc:
            raise HTTPException(409, {"error": "modifiée entre-temps par un collègue", "rev": exc.args[0]}) from None
        return {"api": API, "competence": row}

    @app.post("/api/v1/bibliotheque/{name}/groupe")
    def library_classify(name: str, body: dict = Body(...), user: dict = Depends(current_user)):
        try:
            return {"api": API, **db.library_classify(user["id"], name, _group(body.get("groupe")))}
        except KeyError:
            raise HTTPException(404, {"error": "introuvable"}) from None

    @app.delete("/api/v1/bibliotheque/{name}")
    def library_remove(name: str, user: dict = Depends(current_user)):
        try:
            return {"api": API, **db.library_remove(user["id"], name)}
        except KeyError:
            raise HTTPException(404, {"error": "introuvable"}) from None

    @app.post("/api/v1/bibliotheque-groupes")
    def library_groups(body: dict = Body(...), user: dict = Depends(current_user)):
        try:
            return {"api": API, **db.library_groups(user["id"], str(body.get("action") or ""), _group(body.get("groupe")),
                                                    _group(body.get("nouveau")))}
        except KeyError:
            raise HTTPException(404, {"error": "groupe introuvable"}) from None
        except ValueError as exc:
            raise HTTPException(400, {"error": str(exc)}) from None

    return app


def _module_info(blob: bytes) -> dict | None:
    """Ce que la boîte du destinataire affiche, lu dans partage.json du zip (console/addon_share.py). La console
    qui l'installe revérifie tout le zip : le hub ne fait que le garder."""
    import io
    import json
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            info = json.loads(z.read("partage.json").decode("utf-8-sig"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        return None
    if not isinstance(info, dict) or not _ID.match(str(info.get("id") or "")):
        return None
    perms = info.get("permissions") if isinstance(info.get("permissions"), list) else []

    def text(key, limit):
        return " ".join(str(info.get(key) or "").split())[:limit]
    return {"id": info["id"], "nom": text("nom", 80) or info["id"], "version": text("version", 20),
            "description": text("description", 400), "permissions": sorted({str(x)[:20] for x in perms})[:10]}


_SKILL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")    # console/project_tools.py NAME


def _skills_info(blob: bytes) -> list[dict]:
    """Ce que la boîte du destinataire affiche, lu dans partage.json du zip (console/skill_share.py) : chaque
    compétence, son groupe et sa description. La console qui les installe revérifie tout le zip."""
    import io
    import json
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            info = json.loads(z.read("partage.json").decode("utf-8-sig"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        return []
    if not isinstance(info, dict) or info.get("type") != "competences" or not isinstance(info.get("competences"), list):
        return []
    out = []
    for c in info["competences"][:200]:
        if not isinstance(c, dict) or not _SKILL.match(str(c.get("nom") or "")):
            return []
        out.append({"nom": c["nom"], "groupe": " ".join(str(c.get("groupe") or "").split())[:200],
                    "description": " ".join(str(c.get("description") or "").split())[:300]})
    return out


def _group(text) -> str:
    """« Métier / Incendie » → "Métier/Incendie", comme console/knowledge.py group_path : 4 niveaux de 40 caractères."""
    parts = [x for x in (re.sub(r"\s+", " ", p).strip() for p in re.split(r"[/›>\\]", str(text or ""))) if x]
    if len(parts) > 4 or any(len(x) > 40 for x in parts):
        raise HTTPException(400, {"error": "groupe : quatre niveaux de 40 caractères au plus"})
    return "/".join(parts)


def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(401, {"error": "jeton manquant"})
    user = request.app.state.db.user_by_token(token)
    if user is None:
        raise HTTPException(401, {"error": "jeton refusé"})
    return user


def _revoke_left(db: HubDB, project_id: str, left: set[str]) -> list[str]:
    """Ceux qui ne sont plus membres : leurs postes ne reçoivent plus les fichiers du projet."""
    from .sync_link import revoke
    errors = []
    for user_id in sorted(left):
        errors += revoke(db, user_id, project_id)
    return errors


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


class RegisterIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    id: str
    name: str = Field(min_length=1, max_length=40)
    color: str = ""

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.fullmatch(value or "") or value == "me":   # « me » désigne soi-même dans /members/me
            raise ValueError("pseudo invalide")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("nom invalide")
        return value

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if value and not _COLOR.fullmatch(value):
            raise ValueError("couleur invalide")
        return value


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
    members: list["MemberIn"] = Field(default_factory=list)   # les collègues invités dès la création

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


ProjectIn.model_rebuild()
