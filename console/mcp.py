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


def web_apps(profile: Profile) -> list[dict]:
    """The web applications behind the profile's MCP servers, so that Claude can open their pages (afficher):
    [{name, url, kind}] from an https:// address in an env variable named …URL (ODOO_URL). Only that
    address is read: never the other env values, never an address that carries credentials."""
    servers, _ = profile_servers(profile)
    apps = []
    for name, conf in servers.items():
        env = conf.get("env") if isinstance(conf.get("env"), dict) else {}
        for key, value in env.items():
            try:
                u = urlsplit(str(value).strip())
                ok = u.scheme == "https" and u.hostname and not u.username and not u.password
            except ValueError:
                ok = False
            if _URL_VAR.search(str(key)) and ok:
                odoo = "odoo" in f"{name} {key} {conf.get('command', '')} {conf.get('args', '')}".lower()
                apps.append({"name": name, "url": f"https://{u.netloc}{u.path}".rstrip("/"), "kind": "odoo" if odoo else ""})
                break
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
