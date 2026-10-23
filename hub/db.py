"""SQLite du hub. Le jeton en clair n'est jamais écrit : seulement son empreinte SHA-256."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    admin INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    revoked REAL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    syncthing_folder TEXT NOT NULL DEFAULT '',
    preset TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    effort TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    project_id TEXT NOT NULL REFERENCES projects(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,
    joined REAL NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id TEXT,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
"""

COLORS = ("#40dcff", "#7dffb3", "#ffb020", "#ff7ad9", "#c9a6ff", "#ff8f6b")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class HubDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self.db.close()

    def _write(self, fn):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                result = fn()
                self.db.execute("COMMIT")
                return result
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def _audit(self, user_id: str | None, kind: str, detail: dict):
        self.db.execute(
            "INSERT INTO audit(ts, user_id, kind, detail) VALUES(?,?,?,?)",
            (time.time(), user_id, kind, json.dumps(detail, ensure_ascii=False)),
        )

    # ---------------------------------------------------------------- users
    def add_user(self, user_id: str, *, admin: bool = False, color: str = "") -> str:
        """Crée le compte et rend le jeton en clair. L'appelant l'affiche une seule fois."""
        token = secrets.token_urlsafe(32)

        def go():
            row = self.db.execute("SELECT revoked FROM users WHERE id=?", (user_id,)).fetchone()
            if row is not None and row["revoked"] is None:
                raise ValueError("déjà présent")
            active_admins = self.db.execute(
                "SELECT COUNT(*) AS n FROM users WHERE admin=1 AND revoked IS NULL"
            ).fetchone()["n"]
            make_admin = admin or active_admins == 0
            chosen = color or COLORS[self.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] % len(COLORS)]
            now = time.time()
            if row is None:
                self.db.execute(
                    "INSERT INTO users(id, name, color, token_hash, admin, created) VALUES(?,?,?,?,?,?)",
                    (user_id, user_id, chosen, hash_token(token), int(make_admin), now),
                )
            else:
                self.db.execute(
                    "UPDATE users SET token_hash=?, admin=?, revoked=NULL, color=? WHERE id=?",
                    (hash_token(token), int(make_admin), chosen, user_id),
                )
            self._audit(user_id, "user.add", {"admin": make_admin, "again": row is not None})
            return token

        return self._write(go)

    def revoke_user(self, user_id: str) -> None:
        def go():
            row = self.db.execute(
                "SELECT admin, revoked FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if row is None:
                raise ValueError("inconnu")
            if row["revoked"] is not None:
                raise ValueError("déjà révoqué")
            if row["admin"]:
                others = self.db.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE admin=1 AND revoked IS NULL AND id!=?",
                    (user_id,),
                ).fetchone()["n"]
                if others == 0:
                    raise ValueError("dernier administrateur")
            self.db.execute("UPDATE users SET revoked=? WHERE id=?", (time.time(), user_id))
            self._audit(user_id, "user.revoke", {})

        self._write(go)

    def user_by_token(self, token: str) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT id, name, color, admin, created FROM users WHERE token_hash=? AND revoked IS NULL",
                (hash_token(token),),
            ).fetchone()
        return _user(row) if row else None

    def user_row(self, user_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT id, name, color, admin, created, revoked FROM users WHERE id=?", (user_id,)
            ).fetchone()
        if row is None or row["revoked"] is not None:
            return None
        return _user(row)

    # ------------------------------------------------------------- projects
    def create_project(self, actor: str, fields: dict) -> dict:
        def go():
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (fields["id"],)).fetchone():
                raise ValueError("déjà présent")
            now = time.time()
            self.db.execute(
                "INSERT INTO projects(id, name, color, syncthing_folder, preset, model, effort, created) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (fields["id"], fields["name"], fields["color"], fields["syncthing_folder"],
                 fields["preset"], fields["model"], fields["effort"], now),
            )
            self.db.execute(
                "INSERT INTO members(project_id, user_id, role, joined) VALUES(?,?,?,?)",
                (fields["id"], actor, "administrateur", now),
            )
            self._audit(actor, "project.create", {"project": fields["id"], "name": fields["name"]})
            return self._project_for(actor, fields["id"])

        return self._write(go)

    def update_project(self, actor: str, project_id: str, fields: dict) -> dict:
        if not fields:
            raise ValueError("vide")

        def go():
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise KeyError(project_id)
            cols = ", ".join(f"{key}=?" for key in fields)
            self.db.execute(
                f"UPDATE projects SET {cols} WHERE id=?",
                (*fields.values(), project_id),
            )
            self._audit(actor, "project.update", {"project": project_id, **fields})
            return self._project_for(actor, project_id)

        return self._write(go)

    def projects_of(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT p.*, m.role FROM projects p "
                "JOIN members m ON m.project_id=p.id AND m.user_id=? "
                "ORDER BY p.name",
                (user_id,),
            ).fetchall()
        return [_project(r) for r in rows]

    def access(self, user_id: str, project_id: str, *, manage: bool) -> tuple[str, dict | None]:
        """(état, projet). état : ok, missing, hidden, forbidden."""
        with self._lock:
            user = self.db.execute(
                "SELECT admin FROM users WHERE id=? AND revoked IS NULL", (user_id,)
            ).fetchone()
            if user is None:
                return "hidden", None
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                return "missing", None
            member = self.db.execute(
                "SELECT role FROM members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
            if manage:
                if user["admin"] or (member and member["role"] == "administrateur"):
                    return "ok", self._project_for(user_id, project_id)
                return ("forbidden" if member else "hidden"), None
            if user["admin"] or member:
                return "ok", self._project_for(user_id, project_id)
            return "hidden", None

    def set_members(self, actor: str, project_id: str, members: list[dict]) -> list[dict]:
        def go():
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise KeyError(project_id)
            now = time.time()
            before = {
                r["user_id"]: r["role"]
                for r in self.db.execute(
                    "SELECT user_id, role FROM members WHERE project_id=?", (project_id,)
                )
            }
            self.db.execute("DELETE FROM members WHERE project_id=?", (project_id,))
            for member in members:
                self.db.execute(
                    "INSERT INTO members(project_id, user_id, role, joined) VALUES(?,?,?,?)",
                    (project_id, member["id"], member["role"], now),
                )
            self._audit(actor, "member.set", {
                "project": project_id,
                "before": before,
                "after": {m["id"]: m["role"] for m in members},
            })
            return self._members(project_id)

        return self._write(go)

    def add_member(self, actor: str, project_id: str, user_id: str, role: str) -> None:
        def go():
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise KeyError(project_id)
            if self.user_active(user_id) is None:
                raise ValueError("inconnu")
            current = self.db.execute(
                "SELECT role FROM members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
            if current and current["role"] == "administrateur" and role != "administrateur":
                others = self.db.execute(
                    "SELECT COUNT(*) AS n FROM members WHERE project_id=? AND role='administrateur' AND user_id!=?",
                    (project_id, user_id),
                ).fetchone()["n"]
                if others == 0:
                    raise ValueError("dernier administrateur")
            self.db.execute(
                "INSERT INTO members(project_id, user_id, role, joined) VALUES(?,?,?,?) "
                "ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role",
                (project_id, user_id, role, time.time()),
            )
            self._audit(actor, "member.set", {"project": project_id, "user": user_id, "role": role})

        self._write(go)

    def remove_member(self, actor: str, project_id: str, user_id: str) -> None:
        def go():
            row = self.db.execute(
                "SELECT role FROM members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError("pas membre")
            if row["role"] == "administrateur":
                others = self.db.execute(
                    "SELECT COUNT(*) AS n FROM members WHERE project_id=? AND role='administrateur' AND user_id!=?",
                    (project_id, user_id),
                ).fetchone()["n"]
                if others == 0:
                    raise ValueError("dernier administrateur")
            self.db.execute(
                "DELETE FROM members WHERE project_id=? AND user_id=?", (project_id, user_id)
            )
            self._audit(actor, "member.set", {"project": project_id, "user": user_id, "role": None})

        self._write(go)

    def user_active(self, user_id: str):
        return self.db.execute(
            "SELECT id FROM users WHERE id=? AND revoked IS NULL", (user_id,)
        ).fetchone()

    def members_known(self, ids: list[str]) -> list[str]:
        missing = []
        with self._lock:
            for user_id in ids:
                if self.user_active(user_id) is None:
                    missing.append(user_id)
        return missing

    def audit(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, ts, user_id, kind, detail FROM audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r["id"], "ts": r["ts"], "user": r["user_id"], "kind": r["kind"],
             "detail": json.loads(r["detail"])}
            for r in rows
        ]

    def plaintext_token(self, token: str) -> bool:
        with self._lock:
            rows = self.db.execute("SELECT id, name, token_hash, color FROM users").fetchall()
        blob = " ".join(" ".join(str(v) for v in r) for r in rows)
        return token in blob

    def _project_for(self, user_id: str, project_id: str) -> dict:
        row = self.db.execute(
            "SELECT p.*, m.role FROM projects p "
            "LEFT JOIN members m ON m.project_id=p.id AND m.user_id=? WHERE p.id=?",
            (user_id, project_id),
        ).fetchone()
        return _project(row)

    def _members(self, project_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT user_id, role, joined FROM members WHERE project_id=? ORDER BY user_id",
            (project_id,),
        ).fetchall()
        return [{"id": r["user_id"], "role": r["role"], "joined": r["joined"]} for r in rows]


def _user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "color": row["color"],
        "admin": bool(row["admin"]), "created": row["created"],
    }


def _project(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"], "name": row["name"], "color": row["color"],
        "syncthing_folder": row["syncthing_folder"], "preset": row["preset"],
        "model": row["model"], "effort": row["effort"], "created": row["created"],
        "role": row["role"] if "role" in keys else None,
    }
