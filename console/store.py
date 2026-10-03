"""Persistence: tasks, their event streams, the audit log, notes and UI state (SQLite)."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
    status TEXT NOT NULL, profile TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tasks_created ON tasks(created);
CREATE TABLE IF NOT EXISTS events (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL,
    kind TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (task_id, seq));
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, task_id TEXT,
    profile TEXT, preset TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS audit_task ON audit(task_id);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
    folder TEXT NOT NULL, data TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self.db.close()

    # -------------------------------------------------------- tasks
    def save_task(self, task: dict):
        with self._lock:
            self.db.execute(
                "INSERT INTO tasks(id, created, updated, status, profile, data) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, status=excluded.status, "
                "profile=excluded.profile, data=excluded.data",
                (task["id"], task["created"], time.time(), task["status"], task["profile"],
                 json.dumps(task, ensure_ascii=False)))

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_tasks(self, limit: int = 500, statuses: list[str] | None = None) -> list[dict]:
        q, args = "SELECT data FROM tasks", []
        if statuses:
            q += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args += statuses
        q += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self.db.execute(q, args).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def delete_task(self, task_id: str):
        with self._lock:
            self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self.db.execute("DELETE FROM events WHERE task_id=?", (task_id,))

    def purge(self, older_than: float, statuses: list[str]) -> int:
        with self._lock:
            ids = [r["id"] for r in self.db.execute(
                f"SELECT id FROM tasks WHERE created < ? AND status IN ({','.join('?' * len(statuses))})",
                [older_than, *statuses]).fetchall()]
            for tid in ids:
                self.db.execute("DELETE FROM tasks WHERE id=?", (tid,))
                self.db.execute("DELETE FROM events WHERE task_id=?", (tid,))
            self.db.execute("DELETE FROM audit WHERE ts < ?", (older_than,))
        return len(ids)

    # -------------------------------------------------------- events
    def add_event(self, task_id: str, seq: int, ts: float, kind: str, data: dict):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO events(task_id, seq, ts, kind, data) VALUES(?,?,?,?,?)",
                            (task_id, seq, ts, kind, json.dumps(data, ensure_ascii=False)))

    def search_events(self, needle: str, limit: int = 200) -> list[dict]:
        """Requests and answers containing the words (newest first), for the search."""
        pat = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._lock:
            rows = self.db.execute(
                "SELECT task_id, kind, data FROM events WHERE kind IN ('user','text','result') "
                "AND data LIKE ? ESCAPE '\\' ORDER BY ts DESC LIMIT ?", (pat, limit)).fetchall()
        return [{"task_id": r["task_id"], "kind": r["kind"], "data": json.loads(r["data"])} for r in rows]

    def events(self, task_id: str, after: int = 0, limit: int = 5000) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT seq, ts, kind, data FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, after, limit)).fetchall()
        return [{"task_id": task_id, "seq": r["seq"], "ts": r["ts"], "kind": r["kind"],
                 "data": json.loads(r["data"])} for r in rows]

    def last_seq(self, task_id: str) -> int:
        with self._lock:
            row = self.db.execute("SELECT MAX(seq) m FROM events WHERE task_id=?", (task_id,)).fetchone()
        return int(row["m"] or 0)

    # -------------------------------------------------------- audit
    def audit(self, kind: str, detail: dict, task_id: str | None = None,
              profile: str | None = None, preset: str | None = None):
        with self._lock:
            self.db.execute("INSERT INTO audit(ts, task_id, profile, preset, kind, detail) VALUES(?,?,?,?,?,?)",
                            (time.time(), task_id, profile, preset, kind,
                             json.dumps(detail, ensure_ascii=False)))

    def audit_rows(self, limit: int = 200, offset: int = 0, task_id: str | None = None,
                   kind: str | None = None) -> tuple[list[dict], int]:
        where, args = [], []
        if task_id:
            where.append("task_id=?"); args.append(task_id)
        if kind:
            where.append("kind=?"); args.append(kind)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            total = self.db.execute(f"SELECT COUNT(*) c FROM audit{w}", args).fetchone()["c"]
            rows = self.db.execute(f"SELECT * FROM audit{w} ORDER BY id DESC LIMIT ? OFFSET ?",
                                   [*args, limit, offset]).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows], total

    def audit_csv(self) -> str:
        rows, _ = self.audit_rows(limit=1_000_000)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["date", "tache", "profil", "preset", "type", "detail"])
        for r in reversed(rows):
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["task_id"] or "",
                        r["profile"] or "", r["preset"] or "", r["kind"],
                        json.dumps(r["detail"], ensure_ascii=False)])
        return buf.getvalue()

    # -------------------------------------------------------- notes
    def save_note(self, note: dict):
        with self._lock:
            self.db.execute(
                "INSERT INTO notes(id, created, updated, folder, data) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, folder=excluded.folder, "
                "data=excluded.data",
                (note["id"], note["created"], note["updated"], note["folder"],
                 json.dumps(note, ensure_ascii=False)))

    def get_note(self, note_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT data FROM notes WHERE id=?", (note_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_notes(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT data FROM notes ORDER BY updated DESC").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def delete_note(self, note_id: str) -> bool:
        with self._lock:
            return self.db.execute("DELETE FROM notes WHERE id=?", (note_id,)).rowcount > 0

    # -------------------------------------------------------- key/value
    def kv_get(self, key: str, default=None):
        with self._lock:
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?,?)",
                            (key, json.dumps(value, ensure_ascii=False)))
