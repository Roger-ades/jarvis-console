"""Demo server: the real console UI driven by the fake CLI (no account, no tokens).

    .venv\\Scripts\\python.exe tests\\demo_server.py

Type DEMO, ASK, SLEEP 20, or TOOL <outil> <json> in the command bar to see the
streaming, approval and question flows. Data lives in .demo-data/.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from console.app import create_app  # noqa: E402
from console.config import ConfigStore, default_config  # noqa: E402


def seed_sessions(p):
    """One fake Claude Desktop session per profile, for the sessions library."""
    import json
    import time
    import uuid
    projects = Path(p.config_dir) / "projects" / "demo-project"
    if projects.exists():
        return
    projects.mkdir(parents=True)
    cwd = Path(p.workdir)
    cwd.mkdir(parents=True, exist_ok=True)
    sid = str(uuid.uuid4())
    lines = [
        {"type": "user", "sessionId": sid, "cwd": str(cwd), "timestamp": "2026-09-27T09:12:00Z",
         "message": {"content": "Analyse le fichier des ventes de septembre et prépare une synthèse."}},
        {"type": "assistant", "sessionId": sid, "cwd": str(cwd), "timestamp": "2026-09-27T09:12:08Z",
         "message": {"content": [{"type": "text", "text": "Je lis le fichier puis je calcule les totaux par client."},
                                 {"type": "tool_use", "name": "Read", "input": {"file_path": "ventes-septembre.csv"}}]}},
        {"type": "assistant", "sessionId": sid, "cwd": str(cwd), "timestamp": "2026-09-27T09:13:02Z",
         "message": {"content": [{"type": "text", "text": "**Synthèse** : 48 200 € HT, +12 % sur août. Trois clients font 61 % du total."}]}},
    ]
    (projects / f"{sid}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    meta = Path(p.mcp.desktop_config).parent / "claude-code-sessions" / "acc" / "org"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / f"local_{uuid.uuid4()}.json").write_text(json.dumps({
        "cliSessionId": sid, "title": f"Synthèse des ventes ({p.name})", "cwd": str(cwd),
        "lastActivityAt": int(time.time() * 1000) - 86400000, "model": "opus", "isArchived": False}), encoding="utf-8")


def main():
    port = int(os.environ.get("PORT") or os.environ.get("DEMO_PORT", "8790"))
    data = Path(os.environ.get("DEMO_DATA", ROOT / ".demo-data"))
    data.mkdir(parents=True, exist_ok=True)
    store = ConfigStore(data)
    cfg = store.config if (data / "config.json").exists() else default_config()
    cfg.general.port = port
    cfg.general.attachments_dir = str(ROOT / ".demo-work" / "pieces-jointes")
    for p in cfg.profiles:
        # Work folders must live outside the console's data folder, which agents may not touch.
        p.workdir = str(ROOT / ".demo-work" / p.id)
        p.config_dir = str(ROOT / ".demo-work" / f"cfg-{p.id}")
        p.mcp.import_desktop = False
        p.mcp.desktop_config = str(ROOT / ".demo-work" / f"app-{p.id}" / "claude_desktop_config.json")
        seed_sessions(p)
    store.save(cfg, "démo")
    app = create_app(data, port, cli_command=[sys.executable, str(ROOT / "tests" / "fake_claude.py")])
    code = app.state.auth.new_code(ttl=3600)
    print(f"DEMO http://127.0.0.1:{port}/#code={code}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
