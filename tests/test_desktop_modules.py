"""A module (console/addons.py) in the real desktop app (shell/): its window.jarvis gets its answers.

In "Intégré au bureau" a module's frame sits in a native window whose page script is the hidden JARVIS
window's: an answer posted from there to the module's window came from the JARVIS window, not from the
module's window.top, and the bridge dropped it (every module waited forever for its storage). The browser
and the demo never showed it; only Electron does. The app runs hidden (--demarrage), with its own userData
(tests/electron_harness.js), against a console started here. Skipped when shell/node_modules is not there."""
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from console.app import create_app
from console.config import ConfigStore

from .conftest import FAKE, make_config

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "shell" / "node_modules" / "electron"


def electron_exe():
    try:
        exe = DIST / "dist" / (DIST / "path.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return exe if exe.is_file() else None


pytestmark = pytest.mark.skipif(electron_exe() is None, reason="application de bureau non installée (shell/node_modules)")

MODULE = """<p id="etat">…</p>
<script>
(async () => {
  try {
    await jarvis.donnees.ecrire("aller", {n: 1});
    const v = await jarvis.donnees.lire("aller");
    await jarvis.donnees.ecrire("resultat", "relu:" + JSON.stringify(v) + ":" + (await jarvis.donnees.cles()).join(","));
  } catch (e) { document.getElementById("etat").textContent = e.message; }
})();
</script>"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("bureau", ["integre", "fenetre"])
def test_a_module_reads_and_writes_its_storage_in_the_desktop_app(tmp_path, bureau):
    data, port = tmp_path / "data", free_port()
    data.mkdir()
    cfg = make_config(tmp_path)
    cfg.general.port = port
    cfg.ui.bureau = bureau
    ConfigStore(data).save(cfg, "test")
    mod = tmp_path / "addons" / "essai-pont"      # the console's modules: next to its data folder
    mod.mkdir(parents=True)
    (mod / "addon.json").write_text('{"nom": "Essai du pont", "entree": "index.html"}', encoding="utf-8")
    (mod / "index.html").write_text(MODULE, encoding="utf-8")

    app = create_app(data, port, cli_command=FAKE)
    engine = app.state.engine
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    threading.Thread(target=server.run, daemon=True).start()
    env = {**os.environ, "CONSOLE_DATA_DIR": str(data), "CONSOLE_PORT": str(port),
           "JARVIS_TEST_USERDATA": str(tmp_path / "userdata")}
    env.pop("ELECTRON_RUN_AS_NODE", None)
    proc = subprocess.Popen([str(electron_exe()), str(ROOT / "tests" / "electron_harness.js"), "--demarrage"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        got, end, opened = None, time.time() + 90, 0.0
        while time.time() < end and proc.poll() is None:
            got = engine.addon_data("essai-pont").get("resultat")
            if got:
                break
            # the console's page is connected to the live stream: it opens the module (again, every few
            # seconds, in case the first one came before the page listened)
            if engine.bus._subs and time.time() - opened > 8:
                engine.bus.publish("addon_open", {"id": "essai-pont", "profile": "", "task": ""})
                opened = time.time()
            time.sleep(0.3)
        assert proc.poll() is None, "l'application de bureau s'est arrêtée"
        assert engine.addon_data("essai-pont").get("aller") == {"n": 1}, "le module n'a pas pu écrire"
        assert got == 'relu:{"n":1}:aller', "le module n'a pas eu la réponse de la console"
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.kill()
        proc.wait(10)
        server.should_exit = True
