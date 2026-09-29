"""Start the console with the session (so routines run): Windows and macOS."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "JARVIS Console.lnk"
LABEL = "local.jarvis.console"


def startup_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs/Startup"


def _agent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def startup_enabled() -> bool:
    if sys.platform == "darwin":
        return _agent_plist().exists()
    return (startup_dir() / NAME).exists()


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def set_startup(on: bool) -> bool:
    if sys.platform == "darwin":
        return _set_mac(on)
    if os.name != "nt":
        raise OSError("démarrage automatique disponible sous Windows et macOS")
    link = startup_dir() / NAME
    if not on:
        link.unlink(missing_ok=True)
        return False
    # pythonw straight away: no console window flashes at login
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    target, arguments = (pyw, "-m console --no-browser") if pyw.exists() else (ROOT / "start.bat", "--no-browser")
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(link)) + ");"
        "$s.TargetPath = " + _ps_quote(str(target)) + ";"
        "$s.Arguments = " + _ps_quote(arguments) + ";"
        "$s.WorkingDirectory = " + _ps_quote(str(ROOT)) + ";"
        "$s.WindowStyle = 7;"
        "$s.Description = 'JARVIS Console (routines)';"
        "$s.Save()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return startup_enabled()


def _set_mac(on: bool) -> bool:
    """A LaunchAgent: started at login, no Terminal window, logs in data/console.log."""
    plist = _agent_plist()
    uid = str(os.getuid())
    if plist.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True)
    if not on:
        plist.unlink(missing_ok=True)
        return False
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        raise OSError("lance d'abord start.command une fois (création de l'environnement Python)")
    plist.parent.mkdir(parents=True, exist_ok=True)
    home = str(Path.home())
    with plist.open("wb") as fh:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": [str(py), "-m", "console", "--no-browser", "--background"],
            "WorkingDirectory": str(ROOT),
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        }, fh)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True)
    return True
