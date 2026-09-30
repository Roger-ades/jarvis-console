"""Updates from the project's Git repository (GitHub): check, then pull and reinstall.

Only fixed git commands run here, never with user input, and never a password prompt
(a private repository needs its credentials stored once, e.g. by a first `git pull`).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "echo"}
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, stdin=subprocess.DEVNULL, **kw)


def git(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return _run(["git", *args], timeout)


def status(fetch: bool = True) -> dict:
    """Where the installed copy stands against its remote branch."""
    out = {"git": (ROOT / ".git").exists(), "checked": time.time(), "behind": 0, "ahead": 0, "dirty": False,
           "commits": [], "branch": "", "error": ""}
    if not out["git"]:
        out["error"] = "Ce dossier n'est pas un dépôt Git : mise à jour manuelle (voir le README)."
        return out
    try:
        out["branch"] = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if git("rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
            out["error"] = "Pas de dépôt distant suivi (git remote / git push -u)."
            return out
        if fetch:
            f = git("fetch", "--quiet", timeout=45)
            if f.returncode != 0:
                out["error"] = "GitHub injoignable ou accès refusé : " + (f.stderr.strip().splitlines() or ["?"])[-1][:200]
        counts = git("rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.split()
        if len(counts) == 2:
            out["ahead"], out["behind"] = int(counts[0]), int(counts[1])
        if out["behind"]:
            out["commits"] = [line for line in git("log", "--format=%s", "-n", "20", "HEAD..@{u}").stdout.splitlines() if line]
        out["dirty"] = bool(git("status", "--porcelain", "--untracked-files=no").stdout.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["error"] = f"Git indisponible : {exc}"
    return out


def update() -> dict:
    """Fast-forward to the remote branch; reinstall the dependencies when requirements.txt changed."""
    st = status(fetch=True)
    if st["error"] and not st["behind"]:
        raise RuntimeError(st["error"])
    if st["dirty"]:
        raise RuntimeError("Des fichiers du programme ont été modifiés sur ce poste : mise à jour annulée pour ne rien écraser.")
    if not st["behind"]:
        return {"updated": False, "requirements": False, "commits": []}
    before = git("rev-parse", "HEAD").stdout.strip()
    pull = git("pull", "--ff-only", "--quiet", timeout=120)
    if pull.returncode != 0:
        raise RuntimeError("Mise à jour impossible : " + (pull.stderr.strip().splitlines() or ["?"])[-1][:300])
    changed = git("diff", "--name-only", before, "HEAD").stdout.split()
    reqs = "requirements.txt" in changed
    if reqs:
        py = Path(sys.executable)
        if os.name == "nt" and py.name.lower() == "pythonw.exe":
            py = py.with_name("python.exe")  # pip needs a console interpreter
        pip = _run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(ROOT / "requirements.txt")], timeout=600)
        if pip.returncode != 0:
            raise RuntimeError("Code mis à jour, mais l'installation des dépendances a échoué : " + pip.stderr.strip()[-300:])
    return {"updated": True, "requirements": reqs, "commits": st["commits"]}
