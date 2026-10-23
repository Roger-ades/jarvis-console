"""Lien entre la console et le hub : la page ne voit jamais le jeton."""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from fastapi import Body, FastAPI

from . import share, syncbox
from .config import ConfigStore, expand_path
from .engine import TaskError
from .hub_client import HubClient, HubError, normalize_fingerprint

MARKER = ".jarvis-projet"
STIGNORE = """~$*
.~lock.*
*.tmp
Thumbs.db
.claude/settings.local.json
CLAUDE.local.md
"""
ACTIVE = {"running", "awaiting"}


def mount(app: FastAPI, data_dir: Path, cfg_store: ConfigStore, engine, store):
    client = HubClient(data_dir)
    app.state.hub = client

    def cfg():
        return cfg_store.config

    def fail(exc: HubError):
        from .app import _err
        return _err(exc.status, exc.message)

    @app.get("/api/hub/status")
    def status():
        hub = cfg().hub
        seen = ""
        error = ""
        if hub.url:
            try:
                seen = client.probe(hub.url)
            except HubError as exc:
                error = exc.message
        me = None
        if hub.enabled and hub.fingerprint and client.token() and not error:
            try:
                me = client.call(hub.url, hub.fingerprint, "GET", "/api/v1/me").get("user")
            except HubError as exc:
                error = exc.message
        return {
            "enabled": hub.enabled, "url": hub.url,
            "fingerprint": hub.fingerprint, "seen": seen,
            "confirmed": bool(hub.fingerprint) and seen == hub.fingerprint,
            "has_token": bool(client.token()), "user": me, "error": error,
            "share_files": hub.share_files,
        }

    @app.post("/api/hub/probe")
    def probe(body: dict = Body(...)):
        try:
            return {"fingerprint": client.probe(str(body.get("url") or cfg().hub.url))}
        except HubError as exc:
            return fail(exc)

    @app.post("/api/hub/token")
    def save_token(body: dict = Body(...)):
        try:
            client.set_token(str(body.get("token") or ""))
        except HubError as exc:
            return fail(exc)
        store.audit("jeton d'équipe enregistré", {})
        return {"ok": True}

    @app.get("/api/hub/projects")
    def projects():
        hub = cfg().hub
        try:
            found = client.call(hub.url, hub.fingerprint, "GET", "/api/v1/projects")
        except HubError as exc:
            return fail(exc)
        linked = {p.team_id: p.folder for p in cfg().projects if p.team_id}
        for project in found.get("projects", []):
            project["folder"] = linked.get(project["id"], "")
        return found

    @app.post("/api/hub/join")
    def join(body: dict = Body(...)):
        team_id = str(body.get("team_id") or "").strip()
        folder = expand_path(str(body.get("folder") or ""))
        if not team_id or not folder:
            return fail(HubError("Projet et dossier requis."))
        root = Path(folder)
        if not root.is_dir():
            return fail(HubError("Dossier introuvable.", 404))
        hub = cfg().hub
        try:
            listed = client.call(hub.url, hub.fingerprint, "GET", "/api/v1/projects")
        except HubError as exc:
            return fail(exc)
        remote = next((p for p in listed.get("projects", []) if p.get("id") == team_id), None)
        if remote is None:
            return fail(HubError("Projet d'équipe introuvable sur le hub.", 404))
        other = next((p for p in cfg().projects if p.team_id == team_id and not _same(p.folder, folder)), None)
        if other is not None:
            return fail(HubError(f"Ce projet d'équipe est déjà relié à {other.folder}.", 409))
        marker = root / MARKER
        try:
            current = marker.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
        if current and current != team_id:
            return fail(HubError("Ce dossier est déjà relié à un autre projet d'équipe.", 409))
        if not current:
            marker.write_text(team_id + "\n", encoding="utf-8")
        ignore = root / ".stignore"
        try:
            existing = ignore.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        missing = [line for line in STIGNORE.splitlines() if line and line not in existing.splitlines()]
        if missing:
            ignore.write_text((existing.rstrip() + "\n" + "\n".join(missing) + "\n").lstrip("\n") if existing else STIGNORE,
                              encoding="utf-8")
        updated = cfg()
        target = next((p for p in updated.projects if _same(p.folder, folder)), None)
        if target is None:
            # Pas encore de projet JARVIS sur ce dossier : il prend le nom et la couleur du hub.
            fields = {"folder": str(folder), "name": str(remote.get("name") or team_id)[:60], "team_id": team_id}
            if re.fullmatch(r"#[0-9a-fA-F]{6}", remote.get("color") or ""):
                fields["color"] = remote["color"].lower()
            try:
                engine.save_project(fields)
            except TaskError as exc:
                return fail(HubError(exc.message, exc.status))
        else:
            target.team_id = team_id
            cfg_store.save(updated, "projet d'équipe rejoint")
            engine.bus.publish("projects", {"projects": engine.projects()})
        store.audit("projet d'équipe rejoint", {"projet": team_id})
        engine.bus.publish("config", {"reason": "équipe"})
        return {"ok": True, "folder": folder, "syncthing": _link_sync(client, cfg().hub, remote, str(folder), data_dir)}

    @app.post("/api/hub/create")
    def create(body: dict = Body(...)):
        """Fait du projet JARVIS de ce dossier un projet d'équipe (administrateur du hub)."""
        hub = cfg().hub
        folder = expand_path(str(body.get("folder") or ""))
        local = next((p for p in cfg().projects if _same(p.folder, folder)), None) if folder else None
        if local is None:
            return fail(HubError("Projet JARVIS introuvable pour ce dossier.", 404))
        if local.team_id:
            return fail(HubError("Ce projet est déjà un projet d'équipe.", 409))
        name = str(body.get("name") or local.name).strip()[:80]
        team_id = slug(str(body.get("id") or name))
        if not team_id:
            return fail(HubError("Nom de projet requis."))
        fields = {"id": team_id, "name": name, "syncthing_folder": team_id,
                  "preset": local.preset or "assiste", "model": local.model, "effort": local.effort}
        if re.fullmatch(r"#[0-9a-fA-F]{6}", local.color or ""):
            fields["color"] = local.color
        try:
            client.call(hub.url, hub.fingerprint, "POST", "/api/v1/projects", fields)
        except HubError as exc:
            return fail(exc)
        store.audit("projet d'équipe créé", {"projet": team_id})
        return join({"team_id": team_id, "folder": str(folder)})

    @app.get("/api/hub/users")
    def users():
        hub = cfg().hub
        try:
            return client.call(hub.url, hub.fingerprint, "GET", "/api/v1/users")
        except HubError as exc:
            return fail(exc)

    @app.get("/api/hub/members")
    def members(project: str):
        hub = cfg().hub
        try:
            return client.call(hub.url, hub.fingerprint, "GET", f"/api/v1/projects/{_q(project)}/members")
        except HubError as exc:
            return fail(exc)

    @app.put("/api/hub/members")
    def put_members(body: dict = Body(...)):
        hub = cfg().hub
        project = str(body.get("project") or "")
        try:
            return client.call(hub.url, hub.fingerprint, "PUT", f"/api/v1/projects/{_q(project)}/members",
                               {"members": body.get("members") or []})
        except HubError as exc:
            return fail(exc)

    @app.get("/api/hub/presence")
    def presence():
        hub = cfg().hub
        try:
            return client.call(hub.url, hub.fingerprint, "GET", "/api/v1/presence")
        except HubError as exc:
            return fail(exc)

    @app.get("/api/hub/conversations")
    def conversations(project: str):
        hub = cfg().hub
        try:
            return client.call(hub.url, hub.fingerprint, "GET", f"/api/v1/projects/{_q(project)}/conversations")
        except HubError as exc:
            return fail(exc)

    @app.get("/api/hub/messages")
    def messages(project: str):
        hub = cfg().hub
        try:
            return client.call(hub.url, hub.fingerprint, "GET", f"/api/v1/projects/{_q(project)}/messages")
        except HubError as exc:
            return fail(exc)

    @app.post("/api/hub/messages")
    def post_message(body: dict = Body(...)):
        hub = cfg().hub
        project = str(body.get("project") or "")
        try:
            return client.call(hub.url, hub.fingerprint, "POST", f"/api/v1/projects/{_q(project)}/messages",
                               {"text": str(body.get("text") or ""), "refs": body.get("refs") or []})
        except HubError as exc:
            return fail(exc)

    @app.get("/api/hub/share/preview")
    def share_preview(profile: str, session: str, folder: str):
        prof = cfg().profile(profile)
        if not prof:
            return fail(HubError("Compte inconnu.", 404))
        try:
            return share.preview(prof, session, folder)
        except FileNotFoundError:
            return fail(HubError("Discussion introuvable.", 404))

    @app.post("/api/hub/share")
    def share_publish(body: dict = Body(...)):
        hub = cfg().hub
        prof = cfg().profile(str(body.get("profile") or ""))
        session = str(body.get("session") or "")
        folder = str(body.get("folder") or "")
        mode = str(body.get("mode") or "stripped")
        project = str(body.get("project") or "")
        if prof is None:
            return fail(HubError("Compte inconnu.", 404))
        if not project:
            linked = next((p for p in cfg().projects if _same(p.folder, folder) and p.team_id), None)
            project = linked.team_id if linked else ""
        if not project:
            return fail(HubError("Ce dossier n'est pas un projet d'équipe.", 409))
        try:
            blob, info = share.archive(prof, session, folder, mode)
        except FileNotFoundError:
            return fail(HubError("Discussion introuvable.", 404))
        except ValueError as exc:
            return fail(HubError(str(exc)))
        query = urlencode({
            "title": info["title"][:120], "session": session, "mode": mode,
            "stats": json.dumps({k: info[k] for k in ("odoo", "mail", "sharepoint", "outside")}),
        })
        try:
            return client.call(hub.url, hub.fingerprint, "POST", f"/api/v1/projects/{_q(project)}/conversations",
                               content=blob, query=query)
        except HubError as exc:
            return fail(exc)

    @app.post("/api/hub/resume")
    def share_resume(body: dict = Body(...)):
        hub = cfg().hub
        profile = str(body.get("profile") or "")
        folder = str(body.get("folder") or "")
        conversation = str(body.get("conversation") or "")
        prompt = str(body.get("prompt") or "").strip() or "Reprise de la conversation partagée."
        prof = cfg().profile(profile)
        if prof is None or not folder or not conversation:
            return fail(HubError("Compte, dossier ou conversation manquant."))
        try:
            blob = client.call(hub.url, hub.fingerprint, "GET",
                               f"/api/v1/conversations/{_q(conversation)}/archive", raw=True)
            new_id = share.install_transcript(prof, folder, blob)
            return engine.resume_session(profile, new_id, prompt, fork=False, workdir=folder)
        except HubError as exc:
            return fail(exc)
        except TaskError as exc:
            return fail(HubError(exc.message, exc.status))
        except (OSError, ValueError, KeyError) as exc:
            return fail(HubError(f"Reprise impossible ({exc.__class__.__name__})."))

    @app.post("/api/hub/syncthing")
    def syncthing_start():
        """Démarre Syncthing et relie chaque projet d'équipe de ce poste : sert aussi de réparation."""
        try:
            syncbox.start(data_dir)
            device = syncbox.device_id(data_dir)
        except (OSError, TimeoutError, ValueError) as exc:
            return fail(HubError(str(exc)))
        return {"ok": True, "device_id": device, "projects": relink()}

    def relink() -> dict:
        hub = cfg().hub
        linked = [p for p in cfg().projects if p.team_id]
        if not (linked and hub.enabled and hub.fingerprint and client.token()):
            return {}
        try:
            found = client.call(hub.url, hub.fingerprint, "GET", "/api/v1/projects").get("projects", [])
        except HubError as exc:
            return {p.team_id: {"ok": False, "error": exc.message} for p in linked}
        listed = {p["id"]: p for p in found}
        out = {}
        for proj in linked:
            remote = listed.get(proj.team_id)
            out[proj.team_id] = (_link_sync(client, hub, remote, proj.folder, data_dir) if remote
                                 else {"ok": False, "error": "plus membre de ce projet"})
        return out

    @app.post("/api/hub/memory")
    def memory(body: dict = Body(...)):
        folder = Path(expand_path(str(body.get("folder") or ""))).resolve()
        name = Path(str(body.get("name") or "note")).name
        text = str(body.get("text") or "").strip()
        if not folder.is_dir() or not name or not text:
            return fail(HubError("Note ou dossier manquant."))
        memo = folder / "MEMOIRE-EQUIPE.md"
        try:
            previous = memo.read_text(encoding="utf-8")
        except OSError:
            previous = "# Mémoire d'équipe\n"
        block = f"## {name}\n\n{text}\n"
        if f"## {name}\n" in previous:
            head, _, rest = previous.partition(f"## {name}\n")
            tail = rest.split("\n## ", 1)
            rest_after = ("\n## " + tail[1]) if len(tail) > 1 else ""
            memo.write_text(head.rstrip() + "\n\n" + block + rest_after.lstrip("\n"), encoding="utf-8")
        else:
            memo.write_text(previous.rstrip() + "\n\n" + block, encoding="utf-8")
        claude = folder / "CLAUDE.md"
        try:
            guide = claude.read_text(encoding="utf-8")
        except OSError:
            guide = ""
        if "@MEMOIRE-EQUIPE.md" not in guide:
            claude.write_text((guide.rstrip() + "\n\n@MEMOIRE-EQUIPE.md\n").lstrip("\n"), encoding="utf-8")
        return {"ok": True}

    def loop():
        # Après un redémarrage du poste, la synchro des projets d'équipe reprend d'elle-même.
        try:
            # Seulement si Syncthing est déjà installé : le démarrage ne télécharge rien.
            if cfg().hub.enabled and syncbox.binary_of(data_dir).is_file() and any(p.team_id for p in cfg().projects):
                relink()
        except Exception:
            pass
        while True:
            time.sleep(30)
            try:
                hub = cfg().hub
                if not (hub.enabled and hub.fingerprint and client.token()):
                    continue
                with engine._lock:
                    tasks = list(engine.tasks.values())
                projects = []
                for proj in cfg().projects:
                    if not proj.team_id:
                        continue
                    sessions = sum(1 for t in tasks if t.get("status") in ACTIVE and _same(t.get("workdir"), proj.folder))
                    projects.append({"id": proj.team_id, "sessions": sessions, "files": []})
                if projects:
                    client.call(hub.url, hub.fingerprint, "POST", "/api/v1/presence", {"projects": projects})
            except Exception:
                continue

    threading.Thread(target=loop, name="hub-presence", daemon=True).start()


def _link_sync(client: HubClient, hub, project: dict, folder: str, data_dir: Path) -> dict:
    """Le serveur crée ou ouvre le dossier du projet, puis ce poste le partage. L'échec ne défait pas le projet."""
    team_id = project["id"]
    try:
        syncbox.start(data_dir)
        local = syncbox.device_id(data_dir)
        remote = client.call(hub.url, hub.fingerprint, "GET", "/api/v1/syncthing")["device_id"]
        client.call(hub.url, hub.fingerprint, "POST", f"/api/v1/projects/{_q(team_id)}/sync", {"device_id": local})
        syncbox.share(data_dir, folder, project.get("syncthing_folder") or team_id, remote,
                      label=project.get("name") or team_id, host=urlsplit(hub.url).hostname or "")
        return {"ok": True, "device_id": local}
    except HubError as exc:
        return {"ok": False, "error": exc.message}
    except Exception as exc:
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}


def slug(text: str) -> str:
    """Identifiant d'un projet d'équipe : minuscules, chiffres et tirets, sans accents."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:40].rstrip("-")


def _q(value: str) -> str:
    return quote(value, safe="")


def _same(a, b) -> bool:
    return str(expand_path(a or "")).replace("/", "\\").rstrip("\\").lower() == str(expand_path(b or "")).replace("/", "\\").rstrip("\\").lower()


def confirmed(value: str) -> str:
    found = normalize_fingerprint(value)
    if found and len(found) != 64:
        raise HubError("Empreinte SHA-256 attendue.")
    return found
