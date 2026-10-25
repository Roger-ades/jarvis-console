"""Updates from the project's Git repository: check, then pull and reinstall.

The repository lives on the team's Debian server, served read-only by the hub to postes that have
a hub token (hub/git_http.py). A copy cloned from GitHub moves there by itself once it has a token:
its old remote stays as `github`. Only fixed git commands run here, never with user input, and never
a password prompt. The token is passed per command, never written into .git/config.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .hub_client import HUB_URL, HubClient

ROOT = Path(__file__).resolve().parent.parent
SERVER_REPO = HUB_URL + "/git/jarvis-console.git"
CERT = Path(__file__).resolve().parent / "hub.crt"   # the hub's self-signed certificate, the only one trusted


def _run(args: list[str], timeout: float = 60, extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "echo", **(extra or {})}
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, stdin=subprocess.DEVNULL, **kw)


def git(*args: str, timeout: float = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    return _run(["git", *args], timeout, env)


def _origin() -> str:
    return git("remote", "get-url", "origin").stdout.strip()


def _on_server() -> bool:
    return _origin().startswith(SERVER_REPO.rsplit("/", 1)[0] + "/")


def server_env(token: str) -> dict:
    """Git settings for the server's repository, for one command: the hub token and its certificate."""
    scope = "http." + SERVER_REPO.rsplit("/", 1)[0] + "/"
    pairs = [(scope + ".extraHeader", f"Authorization: Bearer {token}"), (scope + ".sslCAInfo", str(CERT))]
    if os.name == "nt":
        pairs.append(("http.sslBackend", "openssl"))  # Schannel ignores sslCAInfo
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    return env


def _token(data_dir: Path | None) -> str:
    return HubClient(data_dir).token() if data_dir else ""


def migrate(token: str, branch: str) -> bool:
    """GitHub → the server's repository, once the poste has a hub token. Anything wrong: back to GitHub."""
    if not token or "github.com" not in _origin() or git("remote", "get-url", "github").returncode == 0:
        return False
    if git("remote", "rename", "origin", "github").returncode != 0:
        return False
    env = server_env(token)
    ok = (git("remote", "add", "origin", SERVER_REPO).returncode == 0
          and git("fetch", "--quiet", "origin", timeout=45, env=env).returncode == 0
          and git("branch", f"--set-upstream-to=origin/{branch}", env=env).returncode == 0)
    if not ok:
        git("remote", "remove", "origin")
        git("remote", "rename", "github", "origin")
    return ok


def status(fetch: bool = True, data_dir: Path | None = None) -> dict:
    """Where the installed copy stands against its remote branch."""
    out = {"git": (ROOT / ".git").exists(), "checked": time.time(), "behind": 0, "ahead": 0, "dirty": False,
           "commits": [], "branch": "", "error": "", "migrated": False}
    if not out["git"]:
        out["error"] = "Ce dossier n'est pas un dépôt Git : mise à jour manuelle (voir le README)."
        return out
    try:
        out["branch"] = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if git("rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
            out["error"] = "Pas de dépôt distant suivi (git remote / git push -u)."
            return out
        if fetch:
            token = _token(data_dir)
            out["migrated"] = migrate(token, out["branch"])
            server = _on_server()
            f = git("fetch", "--quiet", timeout=45, env=server_env(token) if server else None)
            if f.returncode != 0:
                where = ("Serveur de l'équipe injoignable (hors du bureau et du VPN ?) ou jeton refusé : " if server
                         else "GitHub injoignable ou accès refusé : ")
                out["error"] = where + (f.stderr.strip().splitlines() or ["?"])[-1][:200]
        counts = git("rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.split()
        if len(counts) == 2:
            out["ahead"], out["behind"] = int(counts[0]), int(counts[1])
        if out["behind"]:
            out["commits"] = [line for line in git("log", "--format=%s", "-n", "20", "HEAD..@{u}").stdout.splitlines() if line]
        out["dirty"] = bool(git("status", "--porcelain", "--untracked-files=no").stdout.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["error"] = f"Git indisponible : {exc}"
    return out


def update(data_dir: Path | None = None) -> dict:
    """Fast-forward to the remote branch; reinstall the dependencies when requirements.txt changed."""
    st = status(fetch=True, data_dir=data_dir)
    if st["error"] and not st["behind"]:
        raise RuntimeError(st["error"])
    if st["dirty"]:
        raise RuntimeError("Des fichiers du programme ont été modifiés sur ce poste : mise à jour annulée pour ne rien écraser.")
    if not st["behind"]:
        return {"updated": False, "requirements": False, "commits": []}
    before = git("rev-parse", "HEAD").stdout.strip()
    pull = git("pull", "--ff-only", "--quiet", timeout=120, env=server_env(_token(data_dir)) if _on_server() else None)
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
