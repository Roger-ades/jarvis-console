"""Launcher: `python -m console` starts the server and opens the browser.

When a console is already running on the port, it only mints a fresh one-time
access code and opens a new tab on it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _call(port: int, path: str, token: str | None = None, method: str = "GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=b"{}" if method == "POST" else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Console-Token", token)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def app_browser() -> str | None:
    """Chrome first (then Edge): it can show the console in its own app window."""
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser"):
            for base in (Path("/Applications"), Path.home() / "Applications"):
                exe = base / f"{app}.app" / "Contents" / "MacOS" / app
                if exe.is_file():
                    return str(exe)
        return None
    env = os.environ
    for base, rel in (("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
                      ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
                      ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
                      ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
                      ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe")):
        root = env.get(base)
        if root and (Path(root) / rel).is_file():
            return str(Path(root) / rel)
    return None


def open_ui(url: str, mode: str):
    """A separate app window (no tabs, no address bar) when possible, else a browser tab."""
    exe = app_browser() if mode == "app" else None
    if exe:
        import subprocess
        try:
            subprocess.Popen([exe, f"--app={url}"], close_fds=True)
            return
        except OSError:
            pass
    webbrowser.open(url)


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser(prog="python -m console", description="JARVIS Console d'agents Claude")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--data-dir", default=os.environ.get("CONSOLE_DATA_DIR"))
    ap.add_argument("--no-browser", action="store_true", default=os.environ.get("CONSOLE_NO_BROWSER") == "1")
    ap.add_argument("--background", action="store_true", help="sans fenêtre (journal dans data/console.log)")
    args = ap.parse_args(argv)
    # pythonw.exe has no console at all: behave as --background
    background = args.background or sys.stdout is None

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else ROOT / "data"
    if background:
        _redirect_output(data_dir)
    from .config import ConfigStore
    cfg = ConfigStore(data_dir).config
    port = args.port or int(os.environ.get("CONSOLE_PORT") or cfg.general.port)

    try:
        running = _call(port, "/api/ping").get("app") == "jarvis-console"
    except OSError:
        running = False
    if running:
        token = (data_dir / "token").read_text(encoding="utf-8").strip()
        code = _call(port, "/api/auth/code", token, "POST")["code"]
        url = f"http://127.0.0.1:{port}/#code={code}"
        print(f"\n  La console tourne déjà. Nouvel accès : {url}\n")
        if not args.no_browser:
            open_ui(url, cfg.general.open_as)
        return 0

    import uvicorn
    from .app import create_app
    try:
        app = create_app(data_dir, port)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
        app.state.server = server  # lets the UI stop the console (no terminal to close)
    except Exception as exc:  # noqa: BLE001
        _fatal(f"La console n'a pas pu démarrer : {exc}", background)
        return 1
    code = app.state.auth.new_code(ttl=900)
    url = f"http://127.0.0.1:{port}/#code={code}"
    print("\n  JARVIS · Console d'agents Claude")
    print(f"  Accès (lien à usage unique, valable 15 min) : {url}")
    print("  Pour rouvrir plus tard : relance start.bat. Arrêt : Configuration → Général, ou Ctrl+C.\n", flush=True)
    if not args.no_browser:
        threading.Timer(1.2, open_ui, [url, cfg.general.open_as]).start()
    server.run()
    if not server.started:
        _fatal(f"Le port {port} est déjà utilisé par un autre programme : change-le dans Configuration → Général "
               "ou avec CONSOLE_PORT dans .env.", background)
        return 1
    return 0


def _redirect_output(data_dir: Path):
    """Windowless run (pythonw): logs go to data/console.log, kept under 2 Mo."""
    data_dir.mkdir(parents=True, exist_ok=True)
    log = data_dir / "console.log"
    if log.exists() and log.stat().st_size > 2_000_000:
        log.replace(data_dir / "console.log.1")
    fh = open(log, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = fh


def _fatal(message: str, background: bool):
    print(f"\n  ! {message}\n", flush=True)
    if not background:
        return
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "JARVIS Console", 0x10)
    elif sys.platform == "darwin":
        import subprocess
        text = message.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display alert "JARVIS Console" message "{text}" as critical'], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
