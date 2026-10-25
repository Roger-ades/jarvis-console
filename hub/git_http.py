"""Le dépôt de JARVIS en lecture seule, pour les postes inscrits : la mise à jour en un clic tire d'ici.

nginx publie déjà tout le port 8443 vers le hub : rien à installer sur la Debian. Le hub vérifie le
jeton du poste, puis passe la requête à `git http-backend` (protocole « smart » de git). Seul
git-upload-pack (fetch, clone) passe : personne ne pousse par ici, les pushes restent en SSH.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import refresh

NAME = "jarvis-console.git"
MAX_BODY = 20 * 1024 * 1024     # liste des commits voulus et déjà présents : quelques kilo-octets d'habitude
_ROUTES = {("GET", "info/refs"), ("POST", "git-upload-pack")}


def bare() -> Path:
    return Path(os.environ.get("JARVIS_HUB_GIT_REPO", refresh.BARE))


def allowed(method: str, path: str, query: str) -> bool:
    if (method, path) not in _ROUTES:
        return False
    return path != "info/refs" or query == "service=git-upload-pack"


def backend(method: str, path: str, query: str, headers: dict, body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    """Lance git http-backend (CGI) et renvoie statut, en-têtes et corps."""
    repo = bare()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_PROJECT_ROOT": str(repo.parent),
        "GIT_HTTP_EXPORT_ALL": "1",
        "PATH_INFO": f"/{repo.name}/{path}",
        "REQUEST_METHOD": method,
        "QUERY_STRING": query,
        "CONTENT_TYPE": headers.get("content-type", ""),
        "CONTENT_LENGTH": str(len(body)),
        "REMOTE_ADDR": headers.get("x-forwarded-for", "127.0.0.1"),
        # Le dépôt appartient à deploy, le hub tourne sous jarvis.
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(repo),
    }
    for name, var in (("content-encoding", "HTTP_CONTENT_ENCODING"), ("git-protocol", "GIT_PROTOCOL")):
        if headers.get(name):
            env[var] = headers[name]
    proc = subprocess.run(["git", "http-backend"], input=body, capture_output=True, env=env, timeout=300)
    sep = b"\r\n\r\n" if b"\r\n\r\n" in proc.stdout else b"\n\n"
    head, _, payload = proc.stdout.partition(sep)
    status, out = 200, []
    for line in head.decode("latin-1").splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key.lower() == "status":
            status = int(value.split()[0] or 500)
        elif key:
            out.append((key, value))
    if proc.returncode != 0 and not proc.stdout:
        return 500, [], b""
    return status, out, payload
