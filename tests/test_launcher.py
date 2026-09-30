"""The pinnable launcher and the installed app (JARVIS icon rather than the browser's).
Nothing here touches the real Start Menu, Desktop or Applications folder."""
import plistlib
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from console import __main__ as launcher
from console import winsys

from .test_security import client  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parent.parent


def test_icons_are_valid():
    ico = (ROOT / "static" / "img" / "jarvis.ico").read_bytes()
    _, kind, count = struct.unpack("<HHH", ico[:6])
    sizes = [struct.unpack("<BBBBHHII", ico[6 + 16 * i:22 + 16 * i])[0] or 256 for i in range(count)]
    assert kind == 1 and {16, 32, 48, 256} <= set(sizes)
    icns = (ROOT / "static" / "img" / "jarvis.icns").read_bytes()
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)


def fake_ps(monkeypatch, stdout: str):
    calls = []

    def run(args, **kw):
        calls.append(args[-1])
        return SimpleNamespace(stdout=stdout.encode("utf-8"), returncode=0)
    monkeypatch.setattr(winsys.subprocess, "run", run)
    monkeypatch.setattr(winsys.os, "name", "nt")
    monkeypatch.setattr(winsys.sys, "platform", "win32")
    monkeypatch.setattr(winsys.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    return calls


def test_windows_launcher_shortcuts(monkeypatch):
    calls = fake_ps(monkeypatch, "C:\\Users\\x\\Programs\\JARVIS Console.lnk\nC:\\Users\\x\\Desktop\\JARVIS Console.lnk\n")
    paths = winsys.create_launcher()
    assert paths == ["C:\\Users\\x\\Programs\\JARVIS Console.lnk", "C:\\Users\\x\\Desktop\\JARVIS Console.lnk"]
    script = calls[0]
    assert "GetFolderPath('Programs')" in script and "GetFolderPath('Desktop')" in script
    assert f"{winsys.ICON},0" in script and "'JARVIS Console.lnk'" in script
    assert "pythonw.exe" in script or "start.bat" in script
    assert "'JARVIS · Console d''agents Claude'" in script  # quote doubled for PowerShell


def test_installed_app_is_found(monkeypatch, tmp_path):
    progs = tmp_path / "Programs"
    (progs / "Chrome Apps").mkdir(parents=True)
    (progs / "Chrome Apps" / "JARVIS · Console d'agents.lnk").write_bytes(b"")
    (progs / "JARVIS Console.lnk").write_bytes(b"")  # our own launcher: never taken for the app
    monkeypatch.setattr(winsys, "_programs_dir", lambda: progs)
    calls = fake_ps(monkeypatch, 'C:\\Chrome\\chrome_proxy.exe\n--profile-directory="Profile 1" --app-id=abcdefghijklmnopabcdefghijklmnop\n')
    assert winsys.installed_app() == ["C:\\Chrome\\chrome_proxy.exe", "--profile-directory=Profile 1",
                                      "--app-id=abcdefghijklmnopabcdefghijklmnop"]
    assert "JARVIS Console.lnk" not in calls[0]
    (progs / "Chrome Apps" / "JARVIS · Console d'agents.lnk").unlink()
    assert winsys.installed_app() is None


def test_launch_prefers_the_installed_app_once_the_browser_has_access(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(launcher, "app_browser", lambda: "chrome.exe")
    monkeypatch.setattr(winsys, "installed_app", lambda: ["chrome_proxy.exe", "--app-id=abc"])
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: opened.append(args))
    launcher.open_ui("http://127.0.0.1:8788/#code=X", "app", tmp_path)
    assert opened[-1] == ["chrome.exe", "--app=http://127.0.0.1:8788/#code=X"]  # first time: the code must reach the page
    (tmp_path / "ui-seen").touch()
    launcher.open_ui("http://127.0.0.1:8788/#code=X", "app", tmp_path)
    assert opened[-1][:2] == ["chrome_proxy.exe", "--app-id=abc"]


def test_mac_launcher_app(monkeypatch, tmp_path):
    root = tmp_path / "projet"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (root / "static" / "img").mkdir(parents=True)
    (root / "static" / "img" / "jarvis.icns").write_bytes(b"icns")
    monkeypatch.setattr(winsys, "ROOT", root)
    monkeypatch.setattr(winsys, "_mac_app", lambda: tmp_path / "Applications" / "JARVIS Console.app")
    monkeypatch.setattr(winsys.sys, "platform", "darwin")
    app = Path(winsys.create_launcher()[0])
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "jarvis" and info["CFBundleIconFile"] == "jarvis" and info["LSUIElement"] is True
    script = (app / "Contents" / "MacOS" / "jarvis").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh") and "-m console --background" in script
    assert (app / "Contents" / "Resources" / "jarvis.icns").read_bytes() == b"icns"


def test_exchange_marks_the_browser_and_launcher_route(client, data_dir, monkeypatch):  # noqa: F811
    code = client.console_auth.new_code()
    assert not (data_dir / "ui-seen").exists()
    assert client.post("/api/auth/exchange", json={"code": code}).status_code == 200
    assert (data_dir / "ui-seen").exists()
    h = {"X-Console-Token": client.token}
    monkeypatch.setattr(winsys, "create_launcher", lambda: ["C:\\x\\JARVIS Console.lnk"])
    assert client.post("/api/system/launcher", headers=h).json() == {"paths": ["C:\\x\\JARVIS Console.lnk"]}
    assert client.post("/api/system/launcher").status_code == 401
    assert "launcher" in client.get("/api/system", headers=h).json()


@pytest.mark.skipif(not hasattr(__import__("os"), "startfile"), reason="Windows")
def test_launcher_targets_exist():
    assert winsys.ICON.is_file()
