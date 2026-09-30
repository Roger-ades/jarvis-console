"""Locating the Claude Code CLI, building per-profile environments, probing."""
from __future__ import annotations

import fnmatch
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import General, Profile, expand_path

WIN = os.name == "nt"
MAC = sys.platform == "darwin"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if WIN else 0
NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE if WIN else 0
# Programs started at login on macOS get a bare PATH: add the usual install places.
EXTRA_PATHS = [str(Path.home() / ".local/bin"), str(Path.home() / ".claude/local"), "/opt/homebrew/bin",
               "/usr/local/bin", str(Path.home() / ".npm-global/bin")]

_versions: dict[str, str] = {}


def spawn_kwargs() -> dict:
    """No console window on Windows; own process group elsewhere, so kill_tree gets the children too."""
    return {"creationflags": NO_WINDOW} if WIN else {"start_new_session": True}


def _version_key(p: Path) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", p.parent.name)) or (0,)


def _search_path() -> str:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(parts + [p for p in EXTRA_PATHS if p not in parts]) if not WIN else os.environ.get("PATH", "")


def find_cli(configured: str = "") -> str | None:
    """Explicit path > claude on PATH > ~/.local/bin > newest CLI bundled with the desktop apps > npm shim."""
    if configured:
        p = expand_path(configured)
        return p if Path(p).is_file() else None
    exe = shutil.which("claude.exe") if WIN else shutil.which("claude", path=_search_path())
    if exe:
        return exe
    local = Path.home() / ".local" / "bin" / ("claude.exe" if WIN else "claude")
    if local.is_file():
        return str(local)
    if WIN:
        appdata = os.environ.get("APPDATA")
        bundled = list(Path(appdata).glob("Claude*/claude-code/*/claude.exe")) if appdata else []
    else:
        support = Path.home() / "Library" / "Application Support"
        bundled = [p for p in support.glob("Claude*/claude-code/*/claude") if p.is_file()] if MAC else []
    if bundled:
        return str(max(bundled, key=_version_key))
    return shutil.which("claude")


def cli_version(cli: str) -> str:
    if cli in _versions:
        return _versions[cli]
    try:
        out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    _versions[cli] = out
    return out


def build_env(profile: Profile, general: General) -> dict[str, str]:
    """Clean environment: nothing inherited from a parent Claude session or API key."""
    pats = [p.upper() for p in general.env_strip]
    env = {k: v for k, v in os.environ.items()
           if not any(fnmatch.fnmatchcase(k.upper(), p) for p in pats)}
    cd = expand_path(profile.config_dir)
    if cd:
        env["CLAUDE_CONFIG_DIR"] = cd
    if not WIN:
        env["PATH"] = _search_path()  # an npm-installed claude needs node on the PATH
    if general.ask_user_questions:
        env["CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL"] = "1"
    env.update(profile.env)
    return env


def kill_tree(pid: int):
    if WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=NO_WINDOW)
        return
    import signal
    try:
        if os.getpgid(pid) == pid:  # started with start_new_session: the whole group goes
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _safe_account(acc: dict) -> dict:
    return {k: v for k, v in (acc or {}).items()
            if "token" not in k.lower() or k == "tokenSource"}


def _safe_mcp(entry: dict) -> dict:
    conf = entry.get("config") or {}
    url = str(conf.get("url", "")).split("?", 1)[0]
    return {"name": entry.get("name"), "status": entry.get("status"),
            "scope": entry.get("scope") or entry.get("source"),
            "type": conf.get("type") or ("http" if url else "stdio"),
            "command": Path(str(conf.get("command", ""))).name if conf.get("command") else "",
            "url": url, "error": entry.get("error") or "",
            "tools": len(entry.get("tools") or [])}


def probe(cli: list[str], env: dict, cwd: str, mcp_config: str | None = None, strict: bool = False,
          timeout: float = 40, mcp_wait: float = 20) -> dict:
    """Connection test: initialize + mcp_status over the control protocol.

    No user message is sent, so no token is spent.
    """
    cmd = [*cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--permission-prompt-tool", "stdio"]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config]
    if strict:
        cmd.append("--strict-mcp-config")
    t0 = time.time()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", cwd=cwd, env=env, **spawn_kwargs())
    except OSError as exc:
        return {"ok": False, "error": f"Impossible de lancer la CLI : {exc}"}
    lines: queue.Queue = queue.Queue()
    err_tail: list[str] = []
    threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout] + [lines.put(None)],
                     daemon=True).start()
    threading.Thread(target=lambda: [err_tail.append(l.rstrip()) for l in proc.stderr],
                     daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def wait_for(rid: str, until: float):
        while time.time() < until:
            try:
                line = lines.get(timeout=max(0.05, until - time.time()))
            except queue.Empty:
                break
            if line is None:
                return None
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
                return msg["response"]
        return None

    result: dict = {"ok": False}
    try:
        send({"type": "control_request", "request_id": "probe_init",
              "request": {"subtype": "initialize", "hooks": None}})
        init = wait_for("probe_init", t0 + timeout)
        if not init or init.get("subtype") != "success":
            result["error"] = (init or {}).get("error") or "La CLI n'a pas répondu à l'initialisation."
        else:
            r = init.get("response") or {}
            acc = _safe_account(r.get("account") or {})
            src = acc.get("tokenSource")
            result.update({
                "ok": True,
                "logged_in": bool(src and src != "none") or bool(acc.get("email") or acc.get("emailAddress")),
                "account": acc,
                "models": [{"value": m.get("value"), "label": m.get("displayName") or m.get("value"),
                            "resolved": m.get("resolvedModel"), "description": m.get("description", ""),
                            "efforts": m.get("supportedEffortLevels") or []} for m in r.get("models") or []],
                "commands": [{"name": c.get("name"), "description": c.get("description", ""),
                              "hint": c.get("argumentHint", "")} for c in r.get("commands") or []],
                "agents": [{"name": a.get("name"), "description": a.get("description", "")}
                           for a in r.get("agents") or []],
                "output_styles": r.get("available_output_styles") or [],
            })
            deadline = time.time() + mcp_wait
            servers: list = []
            n = 0
            while True:
                n += 1
                rid = f"probe_mcp_{n}"
                send({"type": "control_request", "request_id": rid, "request": {"subtype": "mcp_status"}})
                resp = wait_for(rid, time.time() + 10)
                servers = ((resp or {}).get("response") or {}).get("mcpServers") or []
                if not any(s.get("status") == "pending" for s in servers) or time.time() > deadline:
                    break
                time.sleep(1)
            result["mcp"] = [_safe_mcp(s) for s in servers]
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
    if not result.get("ok") and err_tail:
        result["error"] = (result.get("error", "") + " " + " | ".join(err_tail[-3:])).strip()
    result["duration"] = round(time.time() - t0, 1)
    result["checked"] = time.time()
    return result


def _clean_title(t: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._-]", "", t)[:60] or "Claude"


def posix_command_line(cli: str, args: list[str], env: dict, cwd: str) -> str:
    """Shell line run in the new terminal: profile env only, never an API key."""
    import shlex
    keep = {k: env[k] for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL", "PATH") if k in env}
    return " ".join([f"cd {shlex.quote(cwd)} &&", "env -u ANTHROPIC_API_KEY",
                     *(f"{k}={shlex.quote(v)}" for k, v in keep.items()), shlex.quote(cli), *map(shlex.quote, args)])


def applescript_do(line: str) -> str:
    return 'tell application "Terminal" to do script "' + line.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_terminal(title: str, cli: str, args: list[str], env: dict, cwd: str):
    """Open a visible console running the CLI (login, resumed session...)."""
    for a in args:
        if not re.fullmatch(r"[A-Za-z0-9._:=/-]+", a):
            raise ValueError(f"argument refusé : {a!r}")
    if WIN:
        inner = f'title {_clean_title(title)} && "{cli}" {" ".join(args)}'
        subprocess.Popen(f'cmd.exe /k "{inner}"', cwd=cwd, env=env, creationflags=NEW_CONSOLE)
        return
    line = posix_command_line(cli, args, env, cwd)
    if MAC:
        subprocess.Popen(["osascript", "-e", applescript_do(line), "-e", 'tell application "Terminal" to activate'])
    else:
        term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("xterm")
        if not term:
            raise ValueError("aucun terminal graphique trouvé")
        subprocess.Popen([term, "-e", "bash", "-lc", line + "; exec bash"])


def limits_probe(cli: list[str], env: dict, cwd: str, timeout: float = 120) -> dict:
    """Plan usage limits of an account: one tiny request (Haiku, no tool, no MCP, not saved as a
    session) whose response carries the rate-limit windows. Returns {"events": [...]} or {"error": ...}."""
    cmd = [*cli, "-p", "Réponds seulement : OK", "--model", "haiku", "--max-turns", "1", "--tools", "",
           "--strict-mcp-config", "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=cwd, env=env, timeout=timeout, stdin=subprocess.DEVNULL, **spawn_kwargs())
    except subprocess.TimeoutExpired:
        return {"error": "Claude n'a pas répondu à temps."}
    except OSError as exc:
        return {"error": f"Impossible de lancer la CLI : {exc}"}
    events, error = [], ""
    for line in r.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "rate_limit_event" and isinstance(msg.get("rate_limit_info"), dict):
            events.append(msg["rate_limit_info"])
        elif msg.get("type") == "result" and msg.get("is_error"):
            error = str(msg.get("result") or "")[:300]
    if events:
        return {"events": events}
    tail = " ".join(r.stderr.strip().splitlines()[-2:])[:300]
    return {"error": error or tail or "Aucune information de limite reçue (compte non connecté ?)."}
