"""Per-profile MCP configuration handed to each Claude Code process.

Claude Code already loads the MCP servers of its own configuration directory
and, once signed in with a claude.ai account, the account's connectors. On top
of that, a profile can import the servers declared in a Claude Desktop app
(claude_desktop_config.json) and add servers of its own. Those are written to a
throw-away file under data/runtime/ passed with --mcp-config.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .config import Profile, expand_path

_URL_VAR = re.compile(r"(^|_)URL$", re.I)


def desktop_servers(path: str) -> tuple[dict, str]:
    """mcpServers of a Claude Desktop config file, and an error message if any."""
    p = expand_path(path)
    if not p:
        return {}, ""
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"fichier introuvable : {p}"
    except (OSError, ValueError) as exc:
        return {}, f"lecture impossible : {exc}"
    servers = data.get("mcpServers") or {}
    return ({k: v for k, v in servers.items() if isinstance(v, dict)} if isinstance(servers, dict) else {}), ""


def profile_servers(profile: Profile) -> tuple[dict, str]:
    servers, err = ({}, "")
    if profile.mcp.import_desktop:
        servers, err = desktop_servers(profile.mcp.desktop_config)
    servers = {**servers, **profile.mcp.extra_servers}
    for name in profile.mcp.disabled_servers:
        servers.pop(name, None)
    return servers, err


_cli_configs: dict[str, tuple[tuple, dict]] = {}


def _cli_servers(profile: Profile, workdir: str = "") -> dict:
    """The servers the user declared in Claude Code's own configuration: <config dir>/.claude.json (~/.claude.json
    without one), for every folder and for this one. Only their declarations are kept (the file also holds the
    CLI's state), read again when it changes. Not a folder's .mcp.json: it comes with the folder's content, and
    its servers wait for the user's approval in Claude Code."""
    cd = expand_path(profile.config_dir)
    path = Path(cd) / ".claude.json" if cd else Path.home() / ".claude.json"
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        hit = _cli_configs.get(str(path))
        if hit and hit[0] == stamp:
            data = hit[1]
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw = raw if isinstance(raw, dict) else {}
            projects = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
            data = {"mcpServers": raw.get("mcpServers") if isinstance(raw.get("mcpServers"), dict) else {},
                    "projects": {k: v["mcpServers"] for k, v in projects.items()
                                 if isinstance(v, dict) and isinstance(v.get("mcpServers"), dict)}}
            _cli_configs[str(path)] = (stamp, data)
    except (OSError, ValueError):
        return {}
    servers = dict(data["mcpServers"])
    if workdir:
        want = os.path.normcase(os.path.normpath(workdir))
        for folder, local in data["projects"].items():
            if os.path.normcase(os.path.normpath(folder)) == want:
                servers.update(local)
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def _https(value) -> tuple | None:
    """(netloc, path) of an https:// address without credentials, else None."""
    try:
        u = urlsplit(str(value).strip())
        ok = u.scheme == "https" and u.hostname and not u.username and not u.password
    except ValueError:
        return None
    return (u.netloc, u.path) if ok else None


def _app_url(conf: dict, odoo: bool) -> str:
    """The address of the web application a server works on: an env variable named …URL (ODOO_URL), else an
    argument named so (--url https://…, --odoo-url=https://…, ODOO_URL=https://…), else, for an Odoo server, an
    https:// argument or the address it is reached at. Only those values are read, never the other env values;
    an argument or a server address keeps only its site (its path or query may carry a key)."""
    env = conf.get("env") if isinstance(conf.get("env"), dict) else {}
    for key, value in env.items():
        u = _https(value) if _URL_VAR.search(str(key)) else None
        if u:
            return f"https://{u[0]}{u[1]}".rstrip("/")
    args = [str(a) for a in conf.get("args")] if isinstance(conf.get("args"), list) else []
    for i, arg in enumerate(args):
        named = re.match(r"^(--?[\w-]*url|\w*URL)=(.*)$", arg, re.I)
        after = i > 0 and re.fullmatch(r"--?[\w-]*url", args[i - 1], re.I)
        u = _https(named.group(2) if named else arg) if named or after or odoo else None
        if u:
            return f"https://{u[0]}"
    u = _https(conf.get("url")) if odoo else None
    return f"https://{u[0]}" if u else ""


def web_apps(profile: Profile, workdir: str = "") -> list[dict]:
    """The web applications behind the MCP servers of a task, so that Claude can open their pages (afficher) and
    the console opens them without asking (the user gave that address to the server): [{name, url, kind}].
    Servers of the profile (Claude Desktop, Intégrations) and of Claude Code's own configuration. Never an
    address that carries credentials."""
    cli = {} if profile.mcp.strict else _cli_servers(profile, workdir)  # --strict-mcp-config: not loaded
    servers = {**cli, **profile_servers(profile)[0]}
    apps = []
    for name, conf in servers.items():
        env = conf.get("env") if isinstance(conf.get("env"), dict) else {}
        odoo = "odoo" in " ".join(map(str, [name, conf.get("command", ""), conf.get("args", ""), conf.get("url", ""), *env])).lower()
        url = _app_url(conf, odoo)
        if url and all(a["url"] != url for a in apps):
            apps.append({"name": name, "url": url, "kind": "odoo" if odoo else ""})
    return apps


def summary(profile: Profile) -> dict:
    """What the UI may show: names and kinds, never env values or headers."""
    imported, err = desktop_servers(profile.mcp.desktop_config) if profile.mcp.import_desktop else ({}, "")
    rows = []
    for name, conf in {**imported, **profile.mcp.extra_servers}.items():
        rows.append({
            "name": name,
            "origin": "manuel" if name in profile.mcp.extra_servers else "desktop",
            "type": conf.get("type") or ("http" if conf.get("url") else "stdio"),
            "command": Path(str(conf.get("command", ""))).name,
            "url": str(conf.get("url", "")).split("?", 1)[0],
            "enabled": name not in profile.mcp.disabled_servers,
        })
    return {"servers": rows, "error": err, "desktop_config": expand_path(profile.mcp.desktop_config)}


def write_config(profile: Profile, runtime_dir: Path, name: str, extra: dict | None = None) -> str | None:
    """extra: servers the console adds itself (its in-process "jarvis" server)."""
    servers, _ = profile_servers(profile)
    servers = {**servers, **(extra or {})}
    if not servers:
        return None
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / f"{name}.mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False), encoding="utf-8")
    return str(path)


def remove_config(path: str | None):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
