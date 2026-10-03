"""The desktop app (shell/) as what the console opens: start.bat, the launcher and the session start.
Nothing here touches the real Start Menu, Startup folder or LaunchAgents."""
import json
import plistlib
import subprocess

from console import __main__ as launcher
from console import winsys

from .test_launcher import fake_ps
from .test_security import client  # noqa: F401 - fixture


def register(data_dir, tmp_path, args=("C:\\JARVIS\\shell",)):
    exe = tmp_path / "electron.exe"
    exe.write_bytes(b"")
    (data_dir / "app.json").write_text(json.dumps({"exe": str(exe), "args": list(args)}), encoding="utf-8")
    return str(exe)


def test_app_info_is_read_from_what_the_app_registered(data_dir, tmp_path):
    assert winsys.app_info(data_dir) is None
    exe = register(data_dir, tmp_path)
    assert winsys.app_info(data_dir) == {"exe": exe, "args": ["C:\\JARVIS\\shell"]}
    assert winsys.app_command(winsys.app_info(data_dir), "--demarrage") == [exe, "C:\\JARVIS\\shell", "--demarrage"]
    (tmp_path / "electron.exe").unlink()          # node_modules deleted: back to the browser
    assert winsys.app_info(data_dir) is None
    (data_dir / "app.json").write_text("{pas du json", encoding="utf-8")
    assert winsys.app_info(data_dir) is None


def test_session_start_opens_the_app_discreetly(monkeypatch, data_dir, tmp_path):
    exe = register(data_dir, tmp_path, args=("C:\\Mes documents\\jarvis\\shell",))
    app, startup = winsys.app_info(data_dir), tmp_path / "Startup"
    monkeypatch.setattr(winsys, "startup_dir", lambda: startup)
    calls = fake_ps(monkeypatch, "")   # (from here on, os.name says Windows: no Path() in the test)
    winsys.set_startup(True, app)
    script = calls[-1]
    assert f"'{exe}'" in script and "--demarrage" in script and '"C:\\Mes documents\\jarvis\\shell"' in script
    assert "application de bureau" in script
    winsys.set_startup(True)                      # without the app: the bare server, as before
    assert "pythonw.exe" in calls[-1] or "start.bat" in calls[-1]


def test_mac_session_start_opens_the_app(monkeypatch, data_dir, tmp_path):
    exe = register(data_dir, tmp_path, args=("/Users/x/jarvis/shell",))
    monkeypatch.setattr(winsys.sys, "platform", "darwin")
    monkeypatch.setattr(winsys, "_agent_plist", lambda: tmp_path / "LaunchAgents" / "local.jarvis.console.plist")
    monkeypatch.setattr(winsys.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(winsys.subprocess, "run", lambda *a, **k: None)
    winsys.set_startup(True, winsys.app_info(data_dir))
    plist = plistlib.loads((tmp_path / "LaunchAgents" / "local.jarvis.console.plist").read_bytes())
    assert plist["ProgramArguments"] == [exe, "/Users/x/jarvis/shell", "--demarrage"]


def test_launcher_for_the_app_replaces_the_browser_one(monkeypatch, data_dir, tmp_path):
    exe = register(data_dir, tmp_path)
    app = winsys.app_info(data_dir)
    calls = fake_ps(monkeypatch, "C:\\P\\JARVIS.lnk\nC:\\D\\JARVIS.lnk\n")
    assert winsys.create_launcher(app) == ["C:\\P\\JARVIS.lnk", "C:\\D\\JARVIS.lnk"]
    script = calls[-1]
    assert "'JARVIS.lnk'" in script and f"'{exe}'" in script
    assert "Remove-Item -LiteralPath (Join-Path $dir 'JARVIS Console.lnk')" in script


def test_start_bat_opens_the_app_once_it_registered(monkeypatch, data_dir, tmp_path):
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: opened.append(args))
    monkeypatch.setattr(launcher, "app_browser", lambda: "chrome.exe")
    launcher.open_ui("http://127.0.0.1:8788/#code=X", "bureau", data_dir)
    assert opened[-1] == ["chrome.exe", "--app=http://127.0.0.1:8788/#code=X"]   # never started here yet
    exe = register(data_dir, tmp_path)
    launcher.open_ui("http://127.0.0.1:8788/#code=X", "bureau", data_dir)
    assert opened[-1] == [exe, "C:\\JARVIS\\shell"]


def test_changing_how_the_console_opens_retargets_the_session_start(client, data_dir, tmp_path, monkeypatch):  # noqa: F811
    h = {"X-Console-Token": client.token}
    exe = register(data_dir, tmp_path)
    targets = []
    monkeypatch.setattr(winsys, "startup_enabled", lambda: True)
    monkeypatch.setattr(winsys, "set_startup", lambda on, app=None: targets.append((on, app)) or on)
    cfg = client.get("/api/config", headers=h).json()["config"]
    cfg["general"]["open_as"] = "bureau"
    assert client.put("/api/config", headers=h, json=cfg).status_code == 200
    assert targets[-1] == (True, {"exe": exe, "args": ["C:\\JARVIS\\shell"]})
    info = client.get("/api/system", headers=h).json()
    assert info["desktop_app"] is True and info["open_as"] == "bureau"
    assert client.post("/api/system/startup", headers=h, json={"on": True}).json() == {"startup": True}
    assert targets[-1][1]["exe"] == exe
    cfg["general"]["open_as"] = "app"
    client.put("/api/config", headers=h, json=cfg)
    assert targets[-1] == (True, None)            # back to the bare server
    n = len(targets)
    cfg["general"]["theme"] = "clair"             # another setting: nothing to retarget
    client.put("/api/config", headers=h, json=cfg)
    assert len(targets) == n
