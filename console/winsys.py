"""Windows and macOS integration: start with the session (so routines run), the pinnable
launcher with the JARVIS icon, the installed app, and restarting the console.

With "Ouverture : application de bureau" (general.open_as = "bureau"), the desktop app (shell/,
Electron) takes the browser's place: it registers itself in data/app.json at each start, and the
session start and the launcher open it (it starts the server itself when needed)."""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "JARVIS Console.lnk"
LABEL = "local.jarvis.console"


APP_FILE = "app.json"
APP_LAUNCHER = "JARVIS"     # the desktop app's own shortcut (it writes it again with its taskbar identity)


def app_info(data_dir: Path) -> dict | None:
    """The desktop app as it registered itself at its last start ({exe, args}), or None when it never
    ran here or its program is gone (node_modules deleted…)."""
    try:
        info = json.loads((Path(data_dir) / APP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not isinstance(info.get("exe"), str) or not Path(info["exe"]).is_file():
        return None
    args = [a for a in info.get("args") or [] if isinstance(a, str)] if isinstance(info.get("args"), list) else []
    return {"exe": info["exe"], "args": args}


def app_command(app: dict, *extra: str) -> list[str]:
    return [app["exe"], *app["args"], *extra]


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


def set_startup(on: bool, app: dict | None = None) -> bool:
    """app: the desktop app, started discreetly (notification area) instead of the bare server."""
    if sys.platform == "darwin":
        return _set_mac(on, app)
    if os.name != "nt":
        raise OSError("démarrage automatique disponible sous Windows et macOS")
    link = startup_dir() / NAME
    if not on:
        link.unlink(missing_ok=True)
        return False
    if app:
        target, arguments, workdir = app["exe"], subprocess.list2cmdline([*app["args"], "--demarrage"]), os.path.dirname(app["exe"])
        what = "JARVIS (application de bureau)"
    else:
        # pythonw straight away: no console window flashes at login
        pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        target, arguments = (pyw, "-m console --no-browser") if pyw.exists() else (ROOT / "start.bat", "--no-browser")
        workdir, what = str(ROOT), "JARVIS Console (routines)"
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(link)) + ");"
        "$s.TargetPath = " + _ps_quote(str(target)) + ";"
        "$s.Arguments = " + _ps_quote(arguments) + ";"
        "$s.WorkingDirectory = " + _ps_quote(workdir) + ";"
        "$s.WindowStyle = 7;"
        "$s.IconLocation = " + _ps_quote(f"{ICON},0") + ";"
        "$s.Description = " + _ps_quote(what) + ";"
        "$s.Save()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return startup_enabled()


# ---------------------------------------------------------------- launcher (pinnable, JARVIS icon)
ICON = ROOT / "static" / "img" / "jarvis.ico"
LAUNCHER = "JARVIS Console"


def _programs_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs"


def _mac_app() -> Path:
    return Path.home() / "Applications" / f"{LAUNCHER}.app"


def launcher_exists() -> bool:
    if sys.platform == "darwin":
        return _mac_app().is_dir()
    return any((_programs_dir() / f"{n}.lnk").exists() for n in (LAUNCHER, APP_LAUNCHER))


def create_launcher(app: dict | None = None) -> list[str]:
    """A "JARVIS Console" launcher with its own icon: it starts the console if needed and opens it.
    Windows: Start Menu and Desktop shortcuts (to pin to the taskbar). macOS: ~/Applications app (to keep in the Dock).
    app: the desktop app, under its own name ("JARVIS"); the browser launcher then goes, so that one entry
    remains (the app writes its shortcut again with its taskbar identity, for pinning and notifications)."""
    if sys.platform == "darwin":
        return [_create_mac_app()]   # (it runs `python -m console`, which opens the desktop app when chosen)
    if os.name != "nt":
        raise OSError("lanceur disponible sous Windows et macOS")
    if app:
        target, arguments, workdir, name, gone = (app["exe"], subprocess.list2cmdline(app["args"]), os.path.dirname(app["exe"]),
                                                  APP_LAUNCHER, LAUNCHER)
    else:
        pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        target, arguments = (pyw, "-m console") if pyw.exists() else (ROOT / "start.bat", "")
        workdir, name, gone = str(ROOT), LAUNCHER, ""
    script = (
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new();"
        "$w = New-Object -ComObject WScript.Shell;"
        "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {"
        + ("  Remove-Item -LiteralPath (Join-Path $dir " + _ps_quote(f"{gone}.lnk") + ") -ErrorAction SilentlyContinue;" if gone else "")
        + "  $p = Join-Path $dir " + _ps_quote(f"{name}.lnk") + ";"
        "  $s = $w.CreateShortcut($p);"
        "  $s.TargetPath = " + _ps_quote(str(target)) + ";"
        "  $s.Arguments = " + _ps_quote(arguments) + ";"
        "  $s.WorkingDirectory = " + _ps_quote(workdir) + ";"
        "  $s.IconLocation = " + _ps_quote(f"{ICON},0") + ";"
        "  $s.Description = " + _ps_quote("JARVIS · Console d'agents Claude") + ";"
        "  $s.Save(); $p"
        "}"
    )
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return [line.strip() for line in r.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def _create_mac_app() -> str:
    import shlex
    import shutil
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        raise OSError("lance d'abord start.command une fois (création de l'environnement Python)")
    app = _mac_app()
    (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
    exe = app / "Contents" / "MacOS" / "jarvis"
    exe.write_text("#!/bin/sh\n"
                   f"cd {shlex.quote(str(ROOT))}\n"
                   'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
                   f"exec {shlex.quote(str(py))} -m console --background\n", encoding="utf-8")
    exe.chmod(0o755)
    shutil.copy(ROOT / "static" / "img" / "jarvis.icns", app / "Contents" / "Resources" / "jarvis.icns")
    with (app / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleName": LAUNCHER, "CFBundleDisplayName": LAUNCHER, "CFBundleIdentifier": "local.jarvis.console.launcher",
                       "CFBundleExecutable": "jarvis", "CFBundleIconFile": "jarvis", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": "1.0", "LSUIElement": True}, fh)  # no Dock icon for the server itself
    return str(app)


def installed_app() -> list[str] | None:
    """The console installed as an app (Chrome/Edge "Installer"): its window then carries the JARVIS
    icon instead of the browser's. Returns the command that opens it, or None."""
    if sys.platform == "darwin":
        for base in (Path.home() / "Applications" / "Chrome Apps.localized", Path.home() / "Applications" / "Edge Apps.localized"):
            for app in sorted(base.glob("JARVIS*.app")) if base.is_dir() else []:
                return ["open", str(app)]
        return None
    if os.name != "nt":
        return None
    progs = _programs_dir()
    links = [p for p in [*sorted((progs / "Chrome Apps").glob("JARVIS*.lnk")), *sorted(progs.glob("JARVIS*.lnk"))]
             if p.name != f"{LAUNCHER}.lnk"]
    if not links:
        return None
    script = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); $w = New-Object -ComObject WScript.Shell;"
              + "".join(f"$s = $w.CreateShortcut({_ps_quote(str(p))}); $s.TargetPath; $s.Arguments;" for p in links))
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    for target, args in zip(lines[0::2], lines[1::2]):
        app_id = re.search(r"--app-id=(\w+)", args)
        profile = re.search(r'--profile-directory=(?:"([^"]+)"|(\S+))', args)
        if app_id and target.strip():
            return [target.strip(), *([f"--profile-directory={profile.group(1) or profile.group(2)}"] if profile else []),
                    f"--app-id={app_id.group(1)}"]
    return None


def relaunch(port: int, data_dir: Path):
    """Start a fresh, windowless console that takes over once this one has freed the port."""
    exe = Path(sys.executable)
    if os.name == "nt" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    args = [str(exe), "-m", "console", "--no-browser", "--background", "--after-restart",
            "--port", str(port), "--data-dir", str(data_dir)]
    kw = {"cwd": str(ROOT), "close_fds": True, "stdin": subprocess.DEVNULL,
          "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


def _set_mac(on: bool, app: dict | None = None) -> bool:
    """A LaunchAgent: started at login, no Terminal window, logs in data/console.log."""
    plist = _agent_plist()
    uid = str(os.getuid())
    if plist.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True)
    if not on:
        plist.unlink(missing_ok=True)
        return False
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists() and not app:
        raise OSError("lance d'abord start.command une fois (création de l'environnement Python)")
    plist.parent.mkdir(parents=True, exist_ok=True)
    home = str(Path.home())
    with plist.open("wb") as fh:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": app_command(app, "--demarrage") if app else [str(py), "-m", "console", "--no-browser", "--background"],
            "WorkingDirectory": str(ROOT),
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        }, fh)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True)
    return True
