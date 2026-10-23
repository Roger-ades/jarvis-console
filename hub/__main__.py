"""Ligne de commande du hub.

    python -m hub serve
    python -m hub user add paul
    python -m hub user revoke paul
    python -m hub project add offre --name Offres --folder essai
    python -m hub member add offre paul
    python -m hub member remove offre paul
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .app import EFFORTS, PRESETS, create_app
from .db import HubDB

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def load_env():
    path = Path(os.environ.get("JARVIS_HUB_ENV", "/etc/jarvis-hub/hub.env"))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database() -> HubDB:
    load_env()
    return HubDB(Path(os.environ.get("JARVIS_HUB_DB", "data/hub.db")))


def _check_id(value: str) -> str:
    if not _ID.fullmatch(value or ""):
        raise SystemExit(f"identifiant invalide : {value}")
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m hub", description="Hub JARVIS")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="écouter en local")

    user = sub.add_parser("user", help="comptes")
    user_sub = user.add_subparsers(dest="action", required=True)
    add = user_sub.add_parser("add", help="créer un compte et afficher son jeton")
    add.add_argument("name")
    add.add_argument("--admin", action="store_true")
    add.add_argument("--color", default="")
    revoke = user_sub.add_parser("revoke", help="révoquer un compte")
    revoke.add_argument("name")

    project = sub.add_parser("project", help="projets")
    project_sub = project.add_subparsers(dest="action", required=True)
    create = project_sub.add_parser("add", help="créer un projet")
    create.add_argument("id")
    create.add_argument("--name", required=True)
    create.add_argument("--color", default="#40dcff")
    create.add_argument("--folder", default="")
    create.add_argument("--preset", default="assiste")
    create.add_argument("--model", default="")
    create.add_argument("--effort", default="")
    create.add_argument("--owner", default="", help="administrateur du projet ; défaut : le premier admin du hub")

    member = sub.add_parser("member", help="membres d'un projet")
    member_sub = member.add_subparsers(dest="action", required=True)
    madd = member_sub.add_parser("add")
    madd.add_argument("project")
    madd.add_argument("user")
    madd.add_argument("--role", default="membre", choices=("membre", "administrateur"))
    mdel = member_sub.add_parser("remove")
    mdel.add_argument("project")
    mdel.add_argument("user")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        serve()
        return
    db = database()
    try:
        if args.cmd == "user" and args.action == "add":
            user_add(db, args.name, admin=args.admin, color=args.color)
        elif args.cmd == "user" and args.action == "revoke":
            user_revoke(db, args.name)
        elif args.cmd == "project":
            project_add(db, args)
        elif args.cmd == "member" and args.action == "add":
            member_add(db, args.project, args.user, args.role)
        elif args.cmd == "member":
            member_remove(db, args.project, args.user)
    finally:
        db.close()


def serve():
    import uvicorn

    load_env()
    host = os.environ.get("JARVIS_HUB_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_HUB_PORT", "8770"))
    db = database()
    uvicorn.run(create_app(db), host=host, port=port, log_level="info")


def user_add(db: HubDB, name: str, *, admin: bool, color: str):
    _check_id(name)
    if color and not _COLOR.fullmatch(color):
        raise SystemExit("couleur invalide")
    try:
        token = db.add_user(name, admin=admin, color=color.lower() if color else "")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(token)
    print(f"Jeton de {name}, affiché une seule fois.", file=sys.stderr)


def user_revoke(db: HubDB, name: str):
    _check_id(name)
    try:
        db.revoke_user(name)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{name} révoqué")


def project_add(db: HubDB, args):
    _check_id(args.id)
    if args.owner:
        _check_id(args.owner)
    if not args.name.strip():
        raise SystemExit("nom vide")
    if not _COLOR.fullmatch(args.color):
        raise SystemExit("couleur invalide")
    if args.folder and not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.folder):
        raise SystemExit("dossier Syncthing invalide")
    if args.preset not in PRESETS:
        raise SystemExit("préréglage inconnu")
    if args.effort not in EFFORTS:
        raise SystemExit("effort inconnu")
    owner = args.owner or _first_admin(db)
    if owner is None:
        raise SystemExit("aucun administrateur : python -m hub user add <nom>")
    if db.user_row(owner) is None:
        raise SystemExit(f"utilisateur inconnu : {owner}")
    try:
        db.create_project(owner, {
            "id": args.id, "name": args.name.strip(), "color": args.color.lower(),
            "syncthing_folder": args.folder, "preset": args.preset,
            "model": args.model, "effort": args.effort,
        })
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(args.id)


def member_add(db: HubDB, project: str, user: str, role: str):
    _check_id(project)
    _check_id(user)
    actor = _first_admin(db)
    if actor is None:
        raise SystemExit("aucun administrateur")
    try:
        db.add_member(actor, project, user, role)
    except KeyError:
        raise SystemExit("projet introuvable") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{user} → {project} ({role})")


def member_remove(db: HubDB, project: str, user: str):
    _check_id(project)
    _check_id(user)
    actor = _first_admin(db)
    if actor is None:
        raise SystemExit("aucun administrateur")
    try:
        db.remove_member(actor, project, user)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{user} retiré de {project}")


def _first_admin(db: HubDB) -> str | None:
    with db._lock:
        row = db.db.execute(
            "SELECT id FROM users WHERE admin=1 AND revoked IS NULL ORDER BY created LIMIT 1"
        ).fetchone()
    return row["id"] if row else None


if __name__ == "__main__":
    main()
