"""What Claude changed in files: the differences, and undoing a change.

Before each write of Claude's file tools (Write, Edit, MultiEdit, NotebookEdit; subagents included),
the console's PreToolUse hook keeps a copy of the file as it is (`snapshot`). When the tool's result
comes back, the change is recorded if the file really changed: the copies before and after go to the
console's data folder (no agent may read or write there), with a few counts for the interface.

Undoing puts the copy from before back (or removes a file Claude created), only if the file is still
as Claude left it, unless the user forces it; what the undo overwrote is kept, so that the undo itself
can be undone ("Rétablir"). A command (Bash, PowerShell) that writes a file is not followed, nor is a
file larger than MAX_FILE.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
MAX_FILE = 2 * 1024 * 1024
MAX_PER_TASK = 300           # the oldest changes of a task are forgotten beyond that
MAX_DIFF_LINES = 4000
MAX_LINE = 2000
ABSENT = ""                  # the hash of a file that does not exist
STATES = ("fait", "annule")


class ChangeError(Exception):
    def __init__(self, message: str, status: int = 400, conflict: bool = False):
        super().__init__(message)
        self.status = status
        self.conflict = conflict


def digest(data: bytes | None) -> str:
    return ABSENT if data is None else hashlib.sha256(data).hexdigest()


def target(tool: str, inp: dict, workdir: str) -> str | None:
    """The real path of the file a file tool writes, or None (another tool, no path)."""
    key = TOOLS.get(tool)
    raw = inp.get(key) if key and isinstance(inp, dict) else None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        return None
    p = os.path.expandvars(os.path.expanduser(raw.strip()))
    if not os.path.isabs(p):
        p = os.path.join(workdir or "", p)
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return None


def read(path: str) -> bytes | None:
    """The file's bytes, None if it does not exist. Raises ChangeError when it cannot be followed."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ChangeError(f"Fichier illisible : {e}") from e
    if not stat.S_ISREG(st.st_mode):
        raise ChangeError("Ce n'est pas un fichier.")
    if st.st_size > MAX_FILE:
        raise ChangeError(f"Fichier de plus de {MAX_FILE // (1024 * 1024)} Mo : non suivi.", 413)
    try:
        with open(path, "rb") as f:
            return f.read(MAX_FILE + 1)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ChangeError(f"Fichier illisible : {e}") from e


@dataclass
class Snapshot:
    """A file as it was just before a tool may write it."""
    tool: str
    path: str
    before: bytes | None
    ts: float = field(default_factory=time.time)


def snapshot(tool: str, inp: dict, workdir: str) -> Snapshot | None:
    path = target(tool, inp, workdir)
    if not path:
        return None
    try:
        return Snapshot(tool, path, read(path))
    except ChangeError:
        return None


def _text(data: bytes | None) -> list[str] | None:
    """Lines of a text file; None for a binary one."""
    if data is None:
        return []
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace").splitlines()


def counts(before: bytes | None, after: bytes | None) -> tuple[int, int, bool]:
    """(lines added, lines removed, binary)."""
    a, b = _text(before), _text(after)
    if a is None or b is None:
        return 0, 0, True
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed, False


def diff_lines(before: bytes | None, after: bytes | None, context: int = 3) -> tuple[list[dict], bool]:
    """The differences as rows {k: "@" | " " | "-" | "+", a: line before, b: line after, t: text},
    and whether they were cut. Empty for a binary file."""
    a, b = _text(before), _text(after)
    if a is None or b is None:
        return [], False
    rows: list[dict] = []
    clip = lambda s: s if len(s) <= MAX_LINE else s[:MAX_LINE] + " …"  # noqa: E731
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(context):
        i1, j1 = group[0][1], group[0][3]
        rows.append({"k": "@", "a": i1 + 1, "b": j1 + 1, "t": f"lignes {i1 + 1} → {j1 + 1}"})
        for tag, x1, x2, y1, y2 in group:
            if tag == "equal":
                rows += [{"k": " ", "a": x1 + n + 1, "b": y1 + n + 1, "t": clip(a[x1 + n])} for n in range(x2 - x1)]
                continue
            if tag in ("replace", "delete"):
                rows += [{"k": "-", "a": x1 + n + 1, "b": None, "t": clip(a[x1 + n])} for n in range(x2 - x1)]
            if tag in ("replace", "insert"):
                rows += [{"k": "+", "a": None, "b": y1 + n + 1, "t": clip(b[y1 + n])} for n in range(y2 - y1)]
        if len(rows) > MAX_DIFF_LINES:
            return rows[:MAX_DIFF_LINES], True
    return rows, False


def _write(path: str, data: bytes | None):
    """Put the file in that state (None: remove it), replacing it in one step when possible."""
    p = Path(path)
    if data is None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.jarvis-{secrets.token_hex(3)}")
    try:
        tmp.write_bytes(data)
        try:
            shutil.copymode(p, tmp)
        except OSError:
            pass
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


class Changes:
    """The recorded changes, one folder per task: index.json and the copies <id>.avant, <id>.apres,
    <id>.retabli (what an undo overwrote)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, tid: str) -> Path:
        if not tid or not tid.replace("-", "").isalnum():
            raise ChangeError("Tâche inconnue.", 404)
        return self.root / tid

    def rows(self, tid: str) -> list[dict]:
        try:
            data = json.loads((self._dir(tid) / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []

    def _save(self, tid: str, rows: list[dict]):
        d = self._dir(tid)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"index.{secrets.token_hex(3)}.tmp"
        tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "index.json")

    def _blob(self, tid: str, cid: str, kind: str) -> Path:
        return self._dir(tid) / f"{cid}.{kind}"

    def _load(self, tid: str, cid: str, kind: str, h: str) -> bytes | None:
        if h == ABSENT:
            return None
        try:
            return self._blob(tid, cid, kind).read_bytes()
        except OSError as e:
            raise ChangeError("Copie de la modification introuvable dans les données de la console.", 410) from e

    def record(self, tid: str, snap: Snapshot, after: bytes | None, tool_use_id: str = "", parent: str = "") -> dict | None:
        """A tool's write, kept if the file really changed; returns the row."""
        if after is not None and len(after) > MAX_FILE:
            return None
        hb, ha = digest(snap.before), digest(after)
        if hb == ha:
            return None
        rows = self.rows(tid)
        cid = secrets.token_hex(4)
        d = self._dir(tid)
        d.mkdir(parents=True, exist_ok=True)
        if snap.before is not None:
            self._blob(tid, cid, "avant").write_bytes(snap.before)
        if after is not None:
            self._blob(tid, cid, "apres").write_bytes(after)
        added, removed, binary = counts(snap.before, after)
        row = {"id": cid, "ts": time.time(), "tool": snap.tool, "path": snap.path, "tool_use_id": tool_use_id or "",
               "parent": parent or "", "created": snap.before is None, "deleted": after is None,
               "added": added, "removed": removed, "binary": binary, "hash_before": hb, "hash_after": ha,
               "state": "fait"}
        rows.append(row)
        while len(rows) > MAX_PER_TASK:
            old = rows.pop(0)
            for kind in ("avant", "apres", "retabli"):
                self._blob(tid, old["id"], kind).unlink(missing_ok=True)
        self._save(tid, rows)
        return row

    def get(self, tid: str, cid: str) -> dict:
        for r in self.rows(tid):
            if r["id"] == cid:
                return r
        raise ChangeError("Modification inconnue.", 404)

    @staticmethod
    def status(row: dict) -> str:
        """Where the file is now: "annule", "actuel" (as Claude left it), "avant" (as it was before),
        "modifie" (changed since), "illisible". listing() tells "suivie" (a later change follows) apart."""
        if row.get("state") == "annule":
            return "annule"
        try:
            now = digest(read(row["path"]))
        except ChangeError:
            return "illisible"
        if now == row["hash_after"]:
            return "actuel"
        if now == row["hash_before"]:
            return "avant"
        return "modifie"

    @classmethod
    def public(cls, row: dict, with_status: bool = True) -> dict:
        out = {k: row[k] for k in ("id", "ts", "tool", "path", "tool_use_id", "parent", "created", "deleted",
                                   "added", "removed", "binary", "state") if k in row}
        for k in ("undone_at", "redone_at", "forced"):
            if row.get(k):
                out[k] = row[k]
        if with_status:
            out["status"] = cls.status(row)
        return out

    def listing(self, tid: str) -> list[dict]:
        """The task's changes for the interface; a file changed since by a later change of the same task
        (still in place) is "suivie": undo that one first."""
        rows = self.rows(tid)
        out = []
        for i, r in enumerate(rows):
            p = self.public(r)
            if p["status"] == "modifie" and any(x["path"] == r["path"] and x.get("state") != "annule" for x in rows[i + 1:]):
                p["status"] = "suivie"
            out.append(p)
        return out

    def diff(self, tid: str, cid: str) -> dict:
        row = self.get(tid, cid)
        before = self._load(tid, cid, "avant", row["hash_before"])
        after = self._load(tid, cid, "apres", row["hash_after"])
        lines, cut = diff_lines(before, after)
        public = next((p for p in self.listing(tid) if p["id"] == cid), None) or self.public(row)
        return {**public, "lines": lines, "truncated": cut, "only_endings": not lines and not row.get("binary")}

    def _check(self, row: dict, want: str, force: bool, what: str) -> tuple[bytes | None, str]:
        """The file now (bytes, hash), if it is in the state the operation expects (or forced)."""
        try:
            current = read(row["path"])
        except ChangeError as e:
            raise ChangeError(f"{what} impossible : {e}", e.status) from e
        now = digest(current)
        if now != want and not force:
            raise ChangeError(f"Le fichier a changé depuis ({row['path']}) : une autre modification, de Claude ou de toi. "
                              "Annule d'abord les plus récentes, ou force l'opération : la version actuelle sera gardée "
                              "pour pouvoir revenir en arrière.", 409, conflict=True)
        return current, now

    def undo(self, tid: str, cid: str, force: bool = False) -> dict:
        """The file as it was before the change (removed if Claude created it)."""
        rows = self.rows(tid)
        row = next((r for r in rows if r["id"] == cid), None)
        if row is None:
            raise ChangeError("Modification inconnue.", 404)
        if row.get("state") == "annule":
            raise ChangeError("Cette modification est déjà annulée.", 409)
        before = self._load(tid, cid, "avant", row["hash_before"])
        current, now = self._check(row, row["hash_after"], force, "Annulation")
        if current is not None:
            self._blob(tid, cid, "retabli").write_bytes(current)
        else:
            self._blob(tid, cid, "retabli").unlink(missing_ok=True)
        try:
            _write(row["path"], before)
        except OSError as e:
            raise ChangeError(f"Annulation impossible : {e}") from e
        row.update(state="annule", undone_at=time.time(), hash_redo=now, forced=bool(force and now != row["hash_after"]))
        self._save(tid, rows)
        return row

    def redo(self, tid: str, cid: str, force: bool = False) -> dict:
        """Undo the undo: the file as it was when the user undid the change."""
        rows = self.rows(tid)
        row = next((r for r in rows if r["id"] == cid), None)
        if row is None:
            raise ChangeError("Modification inconnue.", 404)
        if row.get("state") != "annule":
            raise ChangeError("Cette modification n'est pas annulée.", 409)
        again = self._load(tid, cid, "retabli", row.get("hash_redo", row["hash_after"]))
        self._check(row, row["hash_before"], force, "Rétablissement")
        try:
            _write(row["path"], again)
        except OSError as e:
            raise ChangeError(f"Rétablissement impossible : {e}") from e
        self._blob(tid, cid, "retabli").unlink(missing_ok=True)
        row.update(state="fait", redone_at=time.time())
        row.pop("hash_redo", None)
        self._save(tid, rows)
        return row

    def drop(self, tid: str):
        try:
            shutil.rmtree(self._dir(tid), ignore_errors=True)
        except ChangeError:
            pass

    def sweep(self, alive: set[str]):
        """Copies of tasks that no longer exist (deleted, purged) go."""
        if not self.root.is_dir():
            return
        for d in self.root.iterdir():
            if d.is_dir() and d.name not in alive:
                shutil.rmtree(d, ignore_errors=True)
