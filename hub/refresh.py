"""Après un git push, le hub tire la copie et se relance. Personne ne tape rien sur le serveur.

Le crochet du dépôt, qui tourne sous le compte deploy, ne peut pas écrire dans la copie
du service. Il dépose seulement un fichier. Ce fil, qui tourne sous jarvis, fait le reste.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BARE = "/var/repo/jarvis-console.git"
REPO = Path(os.environ.get("JARVIS_HUB_REPO", "/opt/jarvis-hub"))
REQUEST = Path(os.environ.get("JARVIS_HUB_UPDATE_REQUEST", f"{BARE}/update-request"))

_rev = ""


def armed() -> bool:
    if os.environ.get("JARVIS_HUB_UPDATE") == "0":
        return False
    return Path(BARE).is_dir() and (REPO / ".git").is_dir()


def current_rev() -> str:
    return _rev


def _env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(*args: str, repo: str | None = None) -> str:
    cmd = ["git", "-c", f"safe.directory={BARE}"]
    if repo:
        cmd += ["-C", repo]
    cmd += list(args)
    return subprocess.check_output(cmd, text=True, env=_env()).strip()


def _stamp(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def published() -> str:
    return _git("--git-dir", BARE, "rev-parse", "refs/heads/main")


def apply(repo: Path = REPO) -> bool:
    """Tire main. Vrai s'il faut relancer (nouveau commit ou dépendances)."""
    req = repo / "requirements.txt"
    before_req = req.read_bytes() if req.is_file() else b""
    before = _git("rev-parse", "HEAD", repo=str(repo))
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={BARE}", "-C", str(repo), "pull", "--ff-only", "origin", "main"],
        capture_output=True, text=True, env=_env(),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git pull").strip()
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, detail)
    after = _git("rev-parse", "HEAD", repo=str(repo))
    after_req = req.read_bytes() if req.is_file() else b""
    if before_req != after_req:
        pip = repo / ".venv" / "bin" / "pip"
        if pip.is_file():
            subprocess.run([str(pip), "install", "-r", str(req)], check=True)
    return before != after or before_req != after_req


def relaunch() -> None:
    os.execv(sys.executable, [sys.executable, *sys.argv])


def _take(repo: Path) -> None:
    try:
        if apply(repo):
            print("hub : mise à jour prise, relance", flush=True)
            relaunch()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"hub : mise à jour échouée ({exc})", flush=True)


def loop(repo: Path, request: Path, pause) -> None:
    seen = _stamp(request)
    while True:
        pause(2)
        current = _stamp(request)
        if not current or current == seen:
            continue
        seen = current
        _take(repo)


def start() -> None:
    global _rev
    if not armed():
        return
    try:
        _rev = _git("rev-parse", "HEAD", repo=str(REPO))
        if _rev != published():
            _take(REPO)
            _rev = _git("rev-parse", "HEAD", repo=str(REPO))
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"hub : copie non comparée ({exc})", flush=True)
    threading.Thread(target=loop, args=(REPO, REQUEST, time.sleep), name="hub-update", daemon=True).start()
