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
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    title TEXT NOT NULL,
    session_id TEXT NOT NULL,
    rev INTEGER NOT NULL,
    parent_id TEXT,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    size INTEGER NOT NULL,
    stats TEXT NOT NULL,
    mode TEXT NOT NULL,
    removed REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    refs TEXT NOT NULL,
    edited REAL,
    deleted REAL
);
CREATE INDEX IF NOT EXISTS messages_project ON messages(project_id, ts);
CREATE TABLE IF NOT EXISTS devices (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    created REAL NOT NULL,
    PRIMARY KEY (project_id, user_id, device_id)
);
"""

COLORS = ("#40dcff", "#7dffb3", "#ffb020", "#ff7ad9", "#c9a6ff", "#ff8f6b")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class HubDB:
    def __init__(self, path: Path, files: Path | None = None):
        self.path = Path(path)
        self.files = Path(files) if files else self.path.parent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(members)")}
            if "added_by" not in cols:  # (un hub d'avant les invitations)
                self.db.execute("ALTER TABLE members ADD COLUMN added_by TEXT")

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

    def register_user(self, user_id: str, name: str, color: str) -> str:
        """Inscription depuis JARVIS : jamais administrateur, et un pseudo déjà vu (même révoqué) reste pris,
        pour qu'une personne retirée ne retrouve pas ses projets en se réinscrivant."""
        token = secrets.token_urlsafe(32)

        def go():
            if self.db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is not None:
                raise ValueError("pseudo déjà pris")
            chosen = color or COLORS[self.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] % len(COLORS)]
            self.db.execute(
                "INSERT INTO users(id, name, color, token_hash, admin, created) VALUES(?,?,?,?,0,?)",
                (user_id, name, chosen, hash_token(token), time.time()),
            )
            self._audit(user_id, "user.register", {"name": name})
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

    def users(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, name, color, admin, created FROM users WHERE revoked IS NULL ORDER BY id"
            ).fetchall()
        return [_user(r) for r in rows]

    # ------------------------------------------------------------- projects
    def create_project(self, actor: str, fields: dict, members: list[dict] | None = None) -> dict:
        """Le créateur en est l'administrateur ; `members` : les collègues choisis, invités par lui."""
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
                "INSERT INTO members(project_id, user_id, role, joined, added_by) VALUES(?,?,?,?,?)",
                (fields["id"], actor, "administrateur", now, actor),
            )
            invited = {}
            for member in members or []:
                if member["id"] == actor or member["id"] in invited:
                    continue
                if self.user_active(member["id"]) is None:
                    raise LookupError(member["id"])
                invited[member["id"]] = member["role"]
                self.db.execute(
                    "INSERT INTO members(project_id, user_id, role, joined, added_by) VALUES(?,?,?,?,?)",
                    (fields["id"], member["id"], member["role"], now, actor),
                )
            self._audit(actor, "project.create", {"project": fields["id"], "name": fields["name"], "members": invited})
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
                "SELECT p.*, m.role, m.joined, m.added_by, "
                "(SELECT COUNT(*) FROM members x WHERE x.project_id=p.id) AS member_count "
                "FROM projects p "
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
        """La liste entière. Un membre qui reste garde sa date d'arrivée et qui l'a invité."""
        def go():
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise KeyError(project_id)
            now = time.time()
            rows = self.db.execute(
                "SELECT user_id, role, joined, added_by FROM members WHERE project_id=?", (project_id,)
            ).fetchall()
            before = {r["user_id"]: r["role"] for r in rows}
            kept = {r["user_id"]: (r["joined"], r["added_by"]) for r in rows}
            self.db.execute("DELETE FROM members WHERE project_id=?", (project_id,))
            for member in members:
                joined, added_by = kept.get(member["id"], (now, actor))
                self.db.execute(
                    "INSERT INTO members(project_id, user_id, role, joined, added_by) VALUES(?,?,?,?,?)",
                    (project_id, member["id"], member["role"], joined, added_by),
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
                "INSERT INTO members(project_id, user_id, role, joined, added_by) VALUES(?,?,?,?,?) "
                "ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role",
                (project_id, user_id, role, time.time(), actor),
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

    def members(self, project_id: str) -> list[dict]:
        with self._lock:
            return self._members(project_id)

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

    def is_member(self, user_id: str, project_id: str) -> bool:
        with self._lock:
            row = self.db.execute(
                "SELECT 1 FROM members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
        return row is not None

    def save_conversation(self, author: str, project_id: str, *, title: str, session_id: str,
                          mode: str, stats: dict, blob: bytes, parent: str | None) -> dict:
        conv_id = parent or secrets.token_hex(8)
        dest_dir = self.files / "conversations" / conv_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        def go():
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise KeyError(project_id)
            now = time.time()
            if parent:
                row = self.db.execute(
                    "SELECT project_id, author_id, rev, removed FROM conversations WHERE id=?", (parent,)
                ).fetchone()
                if row is None or row["removed"] is not None or row["project_id"] != project_id:
                    raise KeyError(parent)
                if row["author_id"] != author:
                    raise PermissionError("auteur")
                rev = row["rev"] + 1
                self.db.execute(
                    "UPDATE conversations SET rev=?, updated=?, size=?, stats=?, mode=?, title=? WHERE id=?",
                    (rev, now, len(blob), json.dumps(stats, ensure_ascii=False), mode, title, parent),
                )
            else:
                rev = 1
                self.db.execute(
                    "INSERT INTO conversations(id, project_id, author_id, title, session_id, rev, parent_id, "
                    "created, updated, size, stats, mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (conv_id, project_id, author, title, session_id, rev, None, now, now, len(blob),
                     json.dumps(stats, ensure_ascii=False), mode),
                )
            path = dest_dir / f"{rev}.zip"
            path.write_bytes(blob)
            self._audit(author, "conversation.publish", {"project": project_id, "id": conv_id, "rev": rev, "mode": mode})
            return self._conversation(conv_id)

        return self._write(go)

    def conversations_of(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM conversations WHERE project_id=? AND removed IS NULL ORDER BY updated DESC",
                (project_id,),
            ).fetchall()
        return [_conversation_row(r) for r in rows]

    def conversation(self, conv_id: str) -> dict | None:
        with self._lock:
            return self._conversation(conv_id)

    def conversation_zip(self, conv_id: str, rev: int | None) -> Path | None:
        with self._lock:
            row = self.db.execute(
                "SELECT rev, removed FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
        if row is None or row["removed"] is not None:
            return None
        wanted = row["rev"] if rev is None else rev
        path = self.files / "conversations" / conv_id / f"{wanted}.zip"
        return path if path.is_file() else None

    def remove_conversation(self, actor: str, conv_id: str, *, admin: bool) -> None:
        def go():
            row = self.db.execute(
                "SELECT author_id, project_id, removed FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
            if row is None or row["removed"] is not None:
                raise KeyError(conv_id)
            if row["author_id"] != actor and not admin:
                raise PermissionError("auteur")
            self.db.execute("UPDATE conversations SET removed=? WHERE id=?", (time.time(), conv_id))
            self._audit(actor, "conversation.remove", {"id": conv_id, "project": row["project_id"]})

        self._write(go)

    def add_message(self, author: str, project_id: str, text: str, refs: list) -> dict:
        msg_id = secrets.token_hex(8)

        def go():
            if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise KeyError(project_id)
            now = time.time()
            self.db.execute(
                "INSERT INTO messages(id, project_id, author_id, ts, text, refs) VALUES(?,?,?,?,?,?)",
                (msg_id, project_id, author, now, text, json.dumps(refs, ensure_ascii=False)),
            )
            self._audit(author, "message.add", {"project": project_id, "id": msg_id})
            return self._message(msg_id)

        return self._write(go)

    def messages_of(self, project_id: str, before: float | None, limit: int = 50) -> list[dict]:
        with self._lock:
            if before is None:
                rows = self.db.execute(
                    "SELECT * FROM messages WHERE project_id=? ORDER BY ts DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM messages WHERE project_id=? AND ts<? ORDER BY ts DESC LIMIT ?",
                    (project_id, before, limit),
                ).fetchall()
        return [_message_row(r) for r in reversed(rows)]

    def edit_message(self, actor: str, msg_id: str, *, text: str | None, delete: bool) -> dict:
        def go():
            row = self.db.execute("SELECT author_id, deleted FROM messages WHERE id=?", (msg_id,)).fetchone()
            if row is None or row["deleted"] is not None:
                raise KeyError(msg_id)
            if row["author_id"] != actor:
                raise PermissionError("auteur")
            now = time.time()
            if delete:
                self.db.execute("UPDATE messages SET deleted=?, text='' WHERE id=?", (now, msg_id))
            elif text:
                self.db.execute("UPDATE messages SET text=?, edited=? WHERE id=?", (text, now, msg_id))
            else:
                raise ValueError("texte vide")
            self._audit(actor, "message.edit", {"id": msg_id, "deleted": delete})
            return self._message(msg_id)

        return self._write(go)

    def _conversation(self, conv_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM conversations WHERE id=? AND removed IS NULL", (conv_id,)).fetchone()
        return _conversation_row(row) if row else None

    def _message(self, msg_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return _message_row(row) if row else None

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
            "SELECT m.user_id, m.role, m.joined, m.added_by, u.name, u.color FROM members m "
            "LEFT JOIN users u ON u.id=m.user_id WHERE m.project_id=? ORDER BY m.user_id",
            (project_id,),
        ).fetchall()
        return [{"id": r["user_id"], "role": r["role"], "joined": r["joined"], "added_by": r["added_by"],
                 "name": r["name"] or r["user_id"], "color": r["color"] or ""} for r in rows]

    # -------------------------------------------------------------- devices
    def add_device(self, user_id: str, project_id: str, device_id: str, folder: str) -> None:
        """Le poste inscrit sur le dossier du projet : il en sort quand son compte quitte le projet."""
        def go():
            self.db.execute(
                "INSERT OR IGNORE INTO devices(project_id, user_id, device_id, folder, created) VALUES(?,?,?,?,?)",
                (project_id, user_id, device_id, folder, time.time()),
            )

        self._write(go)

    def drop_devices(self, user_id: str, project_id: str) -> list[dict]:
        """Oublie les postes de ce compte sur ce projet et rend ceux qu'aucun autre membre n'utilise."""
        def go():
            rows = self.db.execute(
                "SELECT device_id, folder FROM devices WHERE project_id=? AND user_id=?", (project_id, user_id)
            ).fetchall()
            self.db.execute("DELETE FROM devices WHERE project_id=? AND user_id=?", (project_id, user_id))
            out = []
            for r in rows:
                shared = self.db.execute(
                    "SELECT 1 FROM devices WHERE project_id=? AND device_id=?", (project_id, r["device_id"])
                ).fetchone()
                if not shared:
                    out.append({"device_id": r["device_id"], "folder": r["folder"]})
            return out

        return self._write(go)


def _user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "color": row["color"],
        "admin": bool(row["admin"]), "created": row["created"],
    }


def _conversation_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "project": row["project_id"], "author": row["author_id"],
        "title": row["title"], "session_id": row["session_id"], "rev": row["rev"],
        "parent": row["parent_id"], "created": row["created"], "updated": row["updated"],
        "size": row["size"], "stats": json.loads(row["stats"] or "{}"), "mode": row["mode"],
    }


def _message_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "project": row["project_id"], "author": row["author_id"],
        "ts": row["ts"], "text": "" if row["deleted"] else row["text"],
        "refs": json.loads(row["refs"] or "[]"), "edited": row["edited"], "deleted": row["deleted"],
    }


def _project(row: sqlite3.Row) -> dict:
    keys = row.keys()
    out = {
        "id": row["id"], "name": row["name"], "color": row["color"],
        "syncthing_folder": row["syncthing_folder"], "preset": row["preset"],
        "model": row["model"], "effort": row["effort"], "created": row["created"],
        "role": row["role"] if "role" in keys else None,
    }
    for key in ("joined", "added_by", "member_count"):
        if key in keys:
            out[key] = row[key]
    return out
