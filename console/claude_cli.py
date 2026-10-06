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
    """The version folder right under claude-code/ (the exe sits in it or in a hashed subfolder)."""
    parts = p.parts
    ver = parts[parts.index("claude-code") + 1] if "claude-code" in parts[:-1] else p.parent.name
    return tuple(int(x) for x in re.findall(r"\d+", ver)) or (0,)


def _bundled(root: Path, name: str) -> list[Path]:
    """CLIs shipped with the desktop apps: claude-code/<ver>/<exe> (before 2.1.286) or claude-code/<ver>/<hash>/<exe>."""
    return [p for pat in (f"Claude*/claude-code/*/{name}", f"Claude*/claude-code/*/*/{name}")
            for p in root.glob(pat) if p.is_file()]


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
        bundled = _bundled(Path(appdata), "claude.exe") if appdata else []
    else:
        bundled = _bundled(Path.home() / "Library" / "Application Support", "claude") if MAC else []
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
    if general.compact_at_k:
        # compact at this size instead of near the model's window (1M tokens for Opus)
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(general.compact_at_k * 1000)
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


# Fields safe to show. Anything else in the credential files (access tokens) stays on disk.
_ACCOUNT_KEYS = ("email", "emailAddress", "organizationName", "orgName",
                 "subscriptionType", "organizationType", "seatTier", "displayName",
                 "tokenSource", "authMethod", "loggedIn")
_PLANS = {"pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Entreprise"}
_ORG_TYPES = {"claude_pro": "pro", "claude_max": "max", "claude_team": "team", "claude_enterprise": "enterprise"}


def _public_fields(acc: dict | None) -> dict:
    """One flat account, from the CLI payload or from oauthAccount. No tokens."""
    if not isinstance(acc, dict):
        return {}
    nested = acc.get("oauthAccount") if isinstance(acc.get("oauthAccount"), dict) else {}
    out: dict = {}
    for src in (nested, acc):
        for k in _ACCOUNT_KEYS:
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
            elif isinstance(v, bool) and k == "loggedIn":
                out[k] = v
    return out


def _plan_code(fields: dict) -> str:
    sub = str(fields.get("subscriptionType") or "").lower()
    if sub in _PLANS:
        return sub
    org = _ORG_TYPES.get(str(fields.get("organizationType") or "").lower(), "")
    if org:
        return org
    seat = str(fields.get("seatTier") or "").lower()
    return "team" if seat.startswith("team") else ""


def account_identity(raw: dict | None) -> dict:
    """Email, active plan and organization. One Claude folder keeps a single active plan."""
    fields = _public_fields(raw)
    email = fields.get("email") or fields.get("emailAddress") or ""
    org = fields.get("organizationName") or fields.get("orgName") or ""
    plan = _plan_code(fields)
    source = fields.get("tokenSource") or ""
    method = (fields.get("authMethod") or "").lower()
    logged = fields.get("loggedIn")
    if logged is False or source in ("none", "missing"):
        logged_in = False
    else:
        logged_in = bool(logged is True or email or plan or org or source
                         or method in ("claude.ai", "console"))
    bits = []
    if email:
        bits.append(email)
    if plan:
        bits.append(f"forfait {_PLANS[plan]}")
    if org:
        bits.append(org)
    return {"logged_in": logged_in, "email": email, "plan": plan,
            "plan_label": _PLANS.get(plan, ""), "organization": org,
            "label": " · ".join(bits)}


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _local_fields(config_dir: str) -> dict:
    """Account saved in this Claude config folder. Credentials only contribute the plan, never the token."""
    base = Path(expand_path(config_dir) or str(Path.home() / ".claude"))
    fields = _public_fields(_read_json(base / ".claude.json").get("oauthAccount"))
    cred = _read_json(base / ".credentials.json")
    oauth = cred.get("claudeAiOauth") if isinstance(cred.get("claudeAiOauth"), dict) else {}
    plan = oauth.get("subscriptionType")
    if isinstance(plan, str) and plan.strip():
        fields["subscriptionType"] = plan.strip()
    return fields


def read_local_account(config_dir: str) -> dict:
    return account_identity(_local_fields(config_dir))


def describe_login(account: dict | None, config_dir: str = "") -> tuple[dict, dict]:
    """CLI account completed with the folder's saved plan and organization.

    A second plan on the same address lives in another folder: this one only describes the plan
    that was chosen at login. Tokens are dropped.
    """
    cli = _public_fields(account)
    if cli.get("tokenSource") in ("none", "missing") or cli.get("loggedIn") is False:
        return cli, account_identity({"loggedIn": False})
    local = _local_fields(config_dir) if config_dir else {}
    cli_email = (cli.get("email") or cli.get("emailAddress") or "").lower()
    local_email = (local.get("email") or local.get("emailAddress") or "").lower()
    # the folder still holds a previous address: don't attach its plan to the new login
    merged = {} if cli_email and local_email and cli_email != local_email else dict(local)
    for k, v in cli.items():
        if v not in ("", None):
            merged[k] = v
    return _public_fields(merged), account_identity(merged)


def profile_accounts(profiles) -> dict[str, dict]:
    """Who is logged in in each profile's folder, and which profiles share a folder or an address."""
    groups: dict[str, list] = {}
    rows = []
    for p in profiles:
        folder = expand_path(getattr(p, "config_dir", "") or "") or str(Path.home() / ".claude")
        key = os.path.normcase(os.path.normpath(folder))
        acc = read_local_account(folder)
        groups.setdefault(key, []).append(p)
        rows.append((p, folder, key, acc))
    by_email: dict[str, list] = {}
    for p, folder, key, acc in rows:
        email = (acc.get("email") or "").lower()
        if email:
            by_email.setdefault(email, []).append((p, acc))
    out = {}
    for p, folder, key, acc in rows:
        email = (acc.get("email") or "").lower()
        also = [{"id": o.id, "name": o.name, "plan": oacc.get("plan") or "",
                 "plan_label": oacc.get("plan_label") or ""}
                for o, oacc in by_email.get(email, []) if o.id != p.id] if email else []
        out[p.id] = {
            "config_dir": folder,
            "account": acc,
            "shared_with": [o.name for o in groups[key] if o.id != p.id],
            "also": also,
        }
    return out


def _safe_account(acc: dict) -> dict:
    return _public_fields(acc)


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
          timeout: float = 40, mcp_wait: float = 20, config_dir: str = "") -> dict:
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
            acc, ident = describe_login(r.get("account") or {}, config_dir)
            result.update({
                "ok": True,
                "logged_in": ident["logged_in"],
                "account": acc,
                "identity": ident,
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


def oneshot(cli: list[str], env: dict, cwd: str, prompt: str, model: str = "haiku", timeout: float = 120) -> dict:
    """One short request (no tool, no MCP, no thinking, not saved as a session): the cheapest call,
    whatever model or effort the account's sessions use. Haiku has no effort levels, so its lowest
    effort is thinking off. Returns {"text": answer, "events": rate-limit infos, "error": message or ""}."""
    cmd = [*cli, "-p", prompt, "--model", model, "--max-turns", "1", "--tools", "",
           "--strict-mcp-config", "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
    env = {**env, "MAX_THINKING_TOKENS": "0"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=cwd, env=env, timeout=timeout, stdin=subprocess.DEVNULL, **spawn_kwargs())
    except subprocess.TimeoutExpired:
        return {"text": "", "events": [], "error": "Claude n'a pas répondu à temps."}
    except OSError as exc:
        return {"text": "", "events": [], "error": f"Impossible de lancer la CLI : {exc}"}
    events, text, error = [], "", ""
    for line in r.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "rate_limit_event" and isinstance(msg.get("rate_limit_info"), dict):
            events.append(msg["rate_limit_info"])
        elif msg.get("type") == "result":
            if msg.get("is_error"):
                error = str(msg.get("result") or "")[:300]
            else:
                text = str(msg.get("result") or "")
    if not text and not error:
        error = " ".join(r.stderr.strip().splitlines()[-2:])[:300]
    return {"text": text, "events": events, "error": error}


def limits_probe(cli: list[str], env: dict, cwd: str, timeout: float = 120) -> dict:
    """Plan usage limits of an account: one tiny Haiku request whose response carries the rate-limit
    windows. Returns {"events": [...]} or {"error": ...}."""
    res = oneshot(cli, env, cwd, "Réponds seulement : OK", timeout=timeout)
    if res["events"]:
        return {"events": res["events"]}
    return {"error": res["error"] or "Aucune information de limite reçue (compte non connecté ?)."}
