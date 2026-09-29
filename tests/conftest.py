import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from console.config import ConfigStore, default_config  # noqa: E402
from console.engine import Engine  # noqa: E402
from console.store import Store  # noqa: E402

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
PORT = 8799


def wait_for(pred, timeout=15.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition non atteinte dans le délai")


def make_config(tmp: Path):
    cfg = default_config()
    for p in cfg.profiles:
        p.workdir = str(tmp / "work" / p.id)
        p.config_dir = str(tmp / "cfg" / p.id)
        p.mcp.import_desktop = False
        # never read the real Claude Desktop data from the tests
        p.mcp.desktop_config = str(tmp / "app" / p.id / "claude_desktop_config.json")
    return cfg


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    store = ConfigStore(d)
    store.save(make_config(tmp_path), "tests")
    return d


@pytest.fixture
def engine(data_dir):
    eng = Engine(ConfigStore(data_dir), Store(data_dir / "console.db"), data_dir, PORT, cli_command=FAKE)
    yield eng
    eng.shutdown()
    time.sleep(0.2)
    eng.store.close()


def task_status(engine, tid):
    return engine.tasks[tid]["status"]
