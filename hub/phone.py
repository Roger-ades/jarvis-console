"""Remote approvals for the phone: consoles push pending decisions, the phone decides on /m.

Nothing irreversible runs on the hub: it only stores the request and the user's decision. The
console that owns the task applies Approuver / Refuser locally (same routes as the inbox).
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path

TTL = 6 * 3600  # a pending approval older than this is dropped
_MOBILE = Path(__file__).with_name("static") / "mobile.html"

SCHEMA = """
CREATE TABLE IF NOT EXISTS phone_pending (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    tool TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    expires REAL NOT NULL,
    UNIQUE(user_id, task_id, approval_id)
);
CREATE INDEX IF NOT EXISTS phone_pending_user ON phone_pending(user_id, expires);
CREATE TABLE IF NOT EXISTS phone_decisions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS phone_decisions_user ON phone_decisions(user_id, created);
"""


def mobile_html() -> str:
    return _MOBILE.read_text(encoding="utf-8")


def ensure(db):
    with db._lock:
        db.db.executescript(SCHEMA)


def put_many(db, user_id: str, items: list[dict]) -> int:
    """Replace this user's pending set with ``items`` (from their console)."""
    now = time.time()
    ensure(db)

    def write():
        db.db.execute("DELETE FROM phone_pending WHERE user_id=? OR expires < ?", (user_id, now))
        n = 0
        for it in items[:50]:
            tid = str(it.get("task_id") or "")
            aid = str(it.get("approval_id") or "")
            if not tid or not aid:
                continue
            db.db.execute(
                "INSERT OR REPLACE INTO phone_pending(id, user_id, task_id, approval_id, kind, tool, target, reason, title, created, expires) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (secrets.token_hex(8), user_id, tid, aid, str(it.get("kind") or "hook")[:40],
                 str(it.get("tool") or "")[:120], str(it.get("target") or "")[:400],
                 str(it.get("reason") or "")[:800], str(it.get("title") or "")[:200],
                 now, now + TTL))
            n += 1
        return n

    return db._write(write)


def list_pending(db, user_id: str) -> list[dict]:
    ensure(db)
    now = time.time()
    with db._lock:
        db.db.execute("DELETE FROM phone_pending WHERE expires < ?", (now,))
        rows = db.db.execute(
            "SELECT id, task_id, approval_id, kind, tool, target, reason, title, created, expires "
            "FROM phone_pending WHERE user_id=? ORDER BY created", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def decide(db, user_id: str, body: dict) -> dict:
    ensure(db)
    decision = str(body.get("decision") or "").strip().lower()
    if decision not in ("allow", "deny"):
        raise ValueError("decision = allow ou deny")
    tid = str(body.get("task_id") or "")
    aid = str(body.get("approval_id") or "")
    if not tid or not aid:
        raise ValueError("task_id et approval_id requis")
    now = time.time()

    def write():
        db.db.execute("DELETE FROM phone_pending WHERE user_id=? AND task_id=? AND approval_id=?",
                      (user_id, tid, aid))
        db.db.execute(
            "INSERT INTO phone_decisions(id, user_id, task_id, approval_id, decision, message, created) "
            "VALUES(?,?,?,?,?,?,?)",
            (secrets.token_hex(8), user_id, tid, aid, decision, str(body.get("message") or "")[:500], now))
        return {"ok": True}

    return db._write(write)


def take_decisions(db, user_id: str) -> list[dict]:
    """Console poll: return and delete decisions waiting for this user."""
    ensure(db)

    def write():
        rows = db.db.execute(
            "SELECT id, task_id, approval_id, decision, message, created FROM phone_decisions "
            "WHERE user_id=? ORDER BY created LIMIT 50", (user_id,)).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            db.db.execute("DELETE FROM phone_decisions WHERE id=?", (r["id"],))
        return out

    return db._write(write)
