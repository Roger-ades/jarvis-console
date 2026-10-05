"""Local index of the user's documents: full-text search for Claude (tool chercher_documents) and Ctrl+K.

The text of PDF, Word, Excel, PowerPoint, OpenDocument, saved mails (.eml), text and HTML files is extracted
on this computer, cut into passages and kept in a SQLite FTS5 index (data/documents.db, out of the agents'
reach). Nothing leaves the computer and no model reads a document to index it. Which folders are indexed is
the user's choice (Configuration → Documents); who may search what is decided by the engine.
"""
from __future__ import annotations

import email
import email.policy
import html
import os
import re
import sqlite3
import stat
import threading
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from .permissions import norm

KINDS = {
    ".pdf": "pdf", ".docx": "word", ".docm": "word", ".odt": "word", ".xlsx": "excel", ".xlsm": "excel",
    ".ods": "excel", ".pptx": "powerpoint", ".odp": "powerpoint", ".eml": "mail", ".html": "page", ".htm": "page",
    ".txt": "texte", ".md": "texte", ".markdown": "texte", ".csv": "texte", ".tsv": "texte", ".json": "texte",
    ".xml": "texte", ".yaml": "texte", ".yml": "texte", ".log": "texte", ".ini": "texte", ".rst": "texte",
}
KIND_LABELS = {"pdf": "PDF", "word": "Word", "excel": "Excel", "powerpoint": "PowerPoint", "mail": "mail",
               "page": "page HTML", "texte": "texte"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".idea", ".vs",
             ".codegraph", "$recycle.bin", "system volume information", ".claude"}
MAX_CHARS = 2_000_000      # text kept per document
PASSAGE = 1200             # characters per passage (about 300 tokens)
MAX_FILES = 50_000         # per folder and per pass
PDF_PAGES = 400
PAGE_BREAK = "\n\f\n"      # between the pages of a PDF: each passage knows its page
VERSION = 1                # PRAGMA user_version: 1 = PDF passages carry their page
ZIP_PART = 64 * 1024 * 1024  # an Office part bigger than this, uncompressed, is not read (zip bomb)
# OneDrive / SharePoint « Files on demand »: reading such a file downloads it. Only its name is indexed.
_ONLINE_ONLY = 0x00400000 | 0x00040000 | 0x00001000  # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, key TEXT NOT NULL, root TEXT NOT NULL,
    kind TEXT NOT NULL, title TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
    indexed REAL NOT NULL, chars INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS docs_key ON docs(key);
CREATE INDEX IF NOT EXISTS docs_root ON docs(root);
CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
    name, body, doc UNINDEXED, part UNINDEXED, tokenize = 'unicode61 remove_diacritics 2');
"""


class Unreadable(Exception):
    """A document whose text cannot be read (protected PDF, damaged file): its name stays searchable."""


# ---------------------------------------------------------------- extraction

def _decode(raw: bytes) -> str:
    """Text files of a Windows computer: UTF-8, UTF-16 with its mark, else Windows-1252."""
    encodings = ("utf-16", "utf-8-sig") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",)
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", errors="replace")


class _Text(HTMLParser):
    """The visible text of a page: no script, no style, a line per block."""
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "noscript", "template"):
            self._skip += 1
        elif tag in self.BLOCKS:
            self.out.append("\t" if tag in ("td", "th") else "\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "noscript", "template"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_text(page: str) -> str:
    p = _Text()
    try:
        p.feed(page)
        p.close()
    except Exception:  # noqa: BLE001 - a broken page keeps what was read
        pass
    return re.sub(r"[ \t\r\f\v]*\n\s*", "\n", re.sub(r"[ \xa0]+", " ", "".join(p.out))).strip()


def _part(z: zipfile.ZipFile, name: str) -> str:
    info = z.getinfo(name)
    if info.file_size > ZIP_PART:
        raise Unreadable("partie trop volumineuse")
    return z.read(info).decode("utf-8", errors="replace")


_XML_TAG = re.compile(r"<[^>]+>")


def _xml_text(xml: str, para: str, tab: str = "") -> str:
    """Text of an Office part: a line per paragraph (para: its closing tag), tabs kept."""
    xml = re.sub(rf"</{para}>", "\n", xml)
    if tab:
        xml = re.sub(rf"<{tab}\s*/>", "\t", xml)
    xml = re.sub(r"<w:br\s*/>|<a:br\s*/>|<text:line-break\s*/>", "\n", xml)
    return html.unescape(_XML_TAG.sub("", xml))


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        parts = ["word/document.xml", *sorted(n for n in names if re.fullmatch(r"word/(header|footer|footnotes)\d*\.xml", n))]
        return "\n".join(_xml_text(_part(z, n), "w:p", "w:tab") for n in parts if n in names)


def _odf(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = _part(z, "content.xml")
    xml = re.sub(r"<text:tab\s*/>|</table:table-cell>", "\t", xml)
    xml = re.sub(r"<text:s(\s[^>]*)?/>", " ", xml)
    return html.unescape(_XML_TAG.sub("", re.sub(r"</text:(p|h)>|</table:table-row>", "\n", xml)))


def _pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        out = []
        for i, n in enumerate(slides, 1):
            out.append(f"Diapositive {i}\n" + _xml_text(_part(z, n), "a:p"))
            notes = n.replace("slides/slide", "notesSlides/notesSlide")
            if notes in z.namelist():
                out.append("Notes : " + _xml_text(_part(z, notes), "a:p"))
        return "\n\n".join(out)


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _xlsx(path: Path) -> str:
    """Each sheet with its name, a line per row, cells separated by tabs (values, not formulas)."""
    m = "{%s}" % _NS["m"]
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(_part(z, "xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{m}t")) for si in root.iter(f"{m}si")]
        sheets = []
        try:
            wb = ElementTree.fromstring(_part(z, "xl/workbook.xml"))
            rels = ElementTree.fromstring(_part(z, "xl/_rels/workbook.xml.rels"))
            target = {r.get("Id"): r.get("Target", "") for r in rels.iter("{%s}Relationship" % _NS["rel"])}
            for s in wb.iter(f"{m}sheet"):
                t = target.get(s.get("{%s}id" % _NS["r"]), "").lstrip("/")
                t = t if t.startswith("xl/") else f"xl/{t}"
                if t in names:
                    sheets.append((s.get("name") or "", t))
        except (KeyError, ElementTree.ParseError):
            sheets = [(n.rsplit("/", 1)[-1][:-4], n) for n in sorted(names) if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        out, size = [], 0
        for name, part in sheets:
            rows = [f"Feuille « {name} »"]
            for row in ElementTree.fromstring(_part(z, part)).iter(f"{m}row"):
                cells = []
                for c in row.iter(f"{m}c"):
                    kind, v = c.get("t"), c.find(f"{m}v")
                    if kind == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                        cells.append(shared[int(v.text)])
                    elif kind == "inlineStr":
                        cells.append("".join(t.text or "" for t in c.iter(f"{m}t")))
                    elif v is not None and v.text:
                        cells.append(v.text)
                if any(x.strip() for x in cells):
                    line = "\t".join(cells)
                    rows.append(line)
                    size += len(line)
                if size > MAX_CHARS:
                    break
            out.append("\n".join(rows))
            if size > MAX_CHARS:
                break
        return "\n\n".join(out)


def _pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import DependencyError, PdfReadError
    except ImportError as exc:
        raise Unreadable("lecture des PDF indisponible (module pypdf absent : relance start.bat)") from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise Unreadable("PDF protégé par un mot de passe")
            except NotImplementedError as exc:
                raise Unreadable("PDF chiffré") from exc
        out, size = [], 0
        for page in reader.pages[:PDF_PAGES]:
            text = page.extract_text() or ""
            out.append(text)
            size += len(text)
            if size > MAX_CHARS:
                break
    except Unreadable:
        raise
    except DependencyError as exc:   # (AES: the cryptography module)
        raise Unreadable("PDF chiffré illisible (module cryptography absent : relance start.bat)") from exc
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
        raise Unreadable(f"PDF illisible ({exc.__class__.__name__})") from exc
    if not "".join(out).strip():
        raise Unreadable("PDF sans texte (document scanné ?)")
    return PAGE_BREAK.join(out)


def _eml(path: Path) -> tuple[str, str]:
    msg = email.message_from_bytes(path.read_bytes()[:20 * 1024 * 1024], policy=email.policy.default)
    head = [f"{k} : {msg[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if msg[k]]
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        try:
            body = part.get_content()
        except (LookupError, ValueError):
            body = _decode(part.get_payload(decode=True) or b"")
        if part.get_content_subtype() == "html":
            body = html_text(body)
    names = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    if names:
        head.append("Pièces jointes : " + ", ".join(names))
    return str(msg["Subject"] or ""), "\n".join(head) + "\n\n" + body


def extract(path: str | Path) -> tuple[str, str]:
    """(title, text) of a document; Unreadable when its text cannot be read."""
    p = Path(path)
    ext = p.suffix.lower()
    kind = KINDS.get(ext)
    title = p.stem
    try:
        if kind == "pdf":
            text = _pdf(p)
        elif ext in (".docx", ".docm"):
            text = _docx(p)
        elif ext in (".odt", ".ods", ".odp"):
            text = _odf(p)
        elif ext in (".xlsx", ".xlsm"):
            text = _xlsx(p)
        elif ext == ".pptx":
            text = _pptx(p)
        elif kind == "mail":
            subject, text = _eml(p)
            title = subject.strip() or title
        elif kind == "page":
            text = html_text(_decode(p.read_bytes()[:MAX_CHARS * 2]))
        elif kind == "texte":
            text = _decode(p.read_bytes()[:MAX_CHARS * 2])
        else:
            raise Unreadable("type de fichier non pris en charge")
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise Unreadable(f"fichier endommagé ou d'un autre format ({exc.__class__.__name__})") from exc
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("\x00", "")).strip()
    return title, text[:MAX_CHARS]


def passages(text: str, size: int = PASSAGE) -> list[str]:
    """Pieces of about `size` characters, cut between paragraphs, then lines, then words."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:
            cut = max(para.rfind("\n", 0, size), para.rfind(". ", 0, size - 1), para.rfind(" ", 0, size))
            end = cut + 1 if cut > size // 2 else size
            piece, para = para[:end].strip(), para[end:].strip()
            if cur:
                out.append(cur)
                cur = ""
            out.append(piece)
        if cur and len(cur) + len(para) + 2 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return [x for x in out if x.strip()]


# ---------------------------------------------------------------- query

_TOKEN = re.compile(r'"([^"]+)"|(\S+)')


def fts_query(q: str) -> str:
    """The user's words as an FTS5 query: every word must be there (prefixes count), "an exact phrase"."""
    terms = []
    for phrase, word in _TOKEN.findall(q or ""):
        words = re.findall(r"\w+", phrase or word)
        if not words:
            continue
        if phrase:
            terms.append('"' + " ".join(words) + '"')
        else:
            terms += [f'"{w}"*' for w in words]
    return " ".join(terms[:16])


# ---------------------------------------------------------------- the index

class DocIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._pass = threading.Lock()  # one pass over the folders at a time
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            if self.db.execute("PRAGMA user_version").fetchone()[0] < VERSION:
                self.db.execute("UPDATE docs SET mtime = 0 WHERE kind = 'pdf'")  # read again at the next pass
                self.db.execute(f"PRAGMA user_version = {VERSION}")
        self.progress: dict = {"running": False, "current": "", "done": 0, "started": None, "ended": None}

    def close(self):
        with self._lock:
            self.db.close()

    # -------------------------------------------------------- writing
    def _store(self, path: str, root: str, st: os.stat_result, name: str):
        """(Re)index one file: its passages replace the previous ones."""
        kind = KINDS[Path(path).suffix.lower()]
        title, text, note = Path(path).stem, "", ""
        attrs = getattr(st, "st_file_attributes", 0)
        if attrs & _ONLINE_ONLY:
            note = "en ligne seulement (non téléchargé) : seul son nom est indexé"
        else:
            try:
                title, text = extract(path)
            except Unreadable as exc:
                note = str(exc)
            except Exception as exc:  # noqa: BLE001 - one odd file never stops the indexing
                note = f"lecture impossible ({exc.__class__.__name__})"
        # (page, passage): the page of a PDF from 1, 0 for the other documents
        pages = text.split(PAGE_BREAK) if kind == "pdf" else [text]
        pieces = [(n if kind == "pdf" else 0, p) for n, page in enumerate(pages, 1) for p in passages(page)] or [(0, "")]
        text = "\n\n".join(pages)
        label = f"{title} {name}" if title != Path(path).stem else name
        with self._lock:
            self.db.execute("BEGIN")
            try:
                row = self.db.execute("SELECT id FROM docs WHERE path=?", (path,)).fetchone()
                if row:
                    self.db.execute("DELETE FROM passages WHERE doc=?", (row["id"],))
                    self.db.execute("UPDATE docs SET key=?, root=?, kind=?, title=?, size=?, mtime=?, indexed=?, chars=?, note=? "
                                    "WHERE id=?", (norm(path), root, kind, title, st.st_size, st.st_mtime, time.time(),
                                                   len(text), note, row["id"]))
                    did = row["id"]
                else:
                    did = self.db.execute("INSERT INTO docs(path, key, root, kind, title, size, mtime, indexed, chars, note) "
                                          "VALUES(?,?,?,?,?,?,?,?,?,?)", (path, norm(path), root, kind, title, st.st_size,
                                                                         st.st_mtime, time.time(), len(text), note)).lastrowid
                self.db.executemany("INSERT INTO passages(name, body, doc, part) VALUES(?,?,?,?)",
                                    [(label, body, did, page) for page, body in pieces])
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def _drop(self, ids: list[int]):
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                self.db.execute(f"DELETE FROM passages WHERE doc IN ({marks})", chunk)
                self.db.execute(f"DELETE FROM docs WHERE id IN ({marks})", chunk)

    def clear(self):
        with self._lock:
            self.db.execute("DELETE FROM passages")
            self.db.execute("DELETE FROM docs")
            self.db.execute("INSERT INTO passages(passages) VALUES('optimize')")

    def sync(self, roots: list[str], max_bytes: int, skip=lambda path: False, stop=lambda: False) -> dict:
        """Bring the index in line with the folders: new and changed files read, removed ones forgotten,
        folders no longer listed dropped. skip(path): a folder or file that must not be read (protected)."""
        with self._pass:
            return self._sync(roots, max_bytes, skip, stop)

    def _sync(self, roots: list[str], max_bytes: int, skip, stop) -> dict:
        real = []
        for r in roots:
            try:
                rp = os.path.realpath(os.path.expandvars(os.path.expanduser(r)))
            except (OSError, ValueError):
                continue
            if os.path.isdir(rp) and rp not in real:
                real.append(rp)
        # a folder inside another one is walked with it
        real = [r for r in real if not any(o != r and norm(r).startswith(norm(o) + "/") for o in real)]
        keys = {norm(r): r for r in real}
        with self._lock:
            gone = [row["id"] for row in self.db.execute("SELECT id, root FROM docs") if norm(row["root"]) not in keys]
        self._drop(gone)
        self.progress.update(running=True, done=0, started=time.time(), ended=None, current="")
        stats = {"read": 0, "kept": 0, "removed": len(gone), "skipped": 0}
        try:
            for root in real:
                if stop():
                    break
                with self._lock:
                    known = {row["path"]: (row["id"], row["mtime"], row["size"])
                             for row in self.db.execute("SELECT id, path, mtime, size FROM docs WHERE root=?", (root,))}
                seen: set[str] = set()
                count = 0
                for base, dirs, files in os.walk(root):
                    if stop():
                        break
                    dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith((".", "~$"))
                                     and not skip(os.path.join(base, d)))
                    for f in sorted(files):
                        if f.startswith(("~$", ".~lock")) or Path(f).suffix.lower() not in KINDS:
                            continue
                        full = os.path.join(base, f)
                        count += 1
                        if count > MAX_FILES:
                            break
                        try:
                            st = os.stat(full)
                        except OSError:
                            continue
                        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
                            stats["skipped"] += 1
                            continue
                        seen.add(full)
                        old = known.get(full)
                        if old and old[1] == st.st_mtime and old[2] == st.st_size:
                            stats["kept"] += 1
                            continue
                        if skip(full):
                            seen.discard(full)
                            stats["skipped"] += 1
                            continue
                        self.progress["current"] = full
                        self._store(full, root, st, os.path.relpath(full, root).replace("\\", "/"))
                        stats["read"] += 1
                        self.progress["done"] += 1
                if not stop():
                    lost = [v[0] for k, v in known.items() if k not in seen]
                    self._drop(lost)
                    stats["removed"] += len(lost)
        finally:
            self.progress.update(running=False, current="", ended=time.time())
        return stats

    # -------------------------------------------------------- reading
    def doc(self, path: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM docs WHERE key=?", (norm(path),)).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict:
        """Per folder: documents, those whose text could not be read, last read."""
        with self._lock:
            rows = self.db.execute("SELECT root, COUNT(*) n, SUM(note != '') notes, MAX(indexed) last, SUM(chars) chars "
                                   "FROM docs GROUP BY root").fetchall()
            kinds = self.db.execute("SELECT kind, COUNT(*) n FROM docs GROUP BY kind").fetchall()
        return {"roots": {r["root"]: {"documents": r["n"], "unread": r["notes"] or 0, "last": r["last"], "chars": r["chars"] or 0}
                          for r in rows},
                "kinds": {r["kind"]: r["n"] for r in kinds}}

    def unread(self, root: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT path, note FROM docs WHERE root=? AND note != '' ORDER BY path LIMIT ?",
                                   (root, limit)).fetchall()
        return [dict(r) for r in rows]

    def search(self, q: str, roots: list[str] | None = None, kinds: list[str] | None = None, limit: int = 8,
               per_doc: int = 2, marks: tuple[str, str] = ("", ""), words: int = 48) -> list[dict]:
        """The documents that best match, each with its best passages. roots: only under these folders
        (None: everywhere). marks: around the matched words in the passages."""
        query = fts_query(q)
        if not query or roots == []:
            return []
        where, args = ["passages MATCH ?"], [query]
        if roots:
            prefixes = list(dict.fromkeys(norm(r) for r in roots))
            where.append("(" + " OR ".join("(d.key = ? OR substr(d.key, 1, ?) = ?)" for _ in prefixes) + ")")
            for p in prefixes:
                args += [p, len(p) + 1, p + "/"]
        if kinds:
            where.append(f"d.kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        sql = (f"SELECT d.id, d.path, d.kind, d.title, d.mtime, d.size, d.note, passages.part AS part, "
               f"bm25(passages, 6.0, 1.0) AS score, snippet(passages, 1, ?, ?, '…', ?) AS snip "
               f"FROM passages JOIN docs d ON d.id = passages.doc WHERE {' AND '.join(where)} ORDER BY score LIMIT ?")
        with self._lock:
            try:
                rows = self.db.execute(sql, [marks[0], marks[1], max(8, min(64, words)), *args, limit * 8]).fetchall()
            except sqlite3.OperationalError:
                return []  # (a query FTS5 cannot read)
        found: dict[int, dict] = {}
        for r in rows:
            d = found.get(r["id"])
            if d is None:
                if len(found) >= limit:
                    continue
                d = found[r["id"]] = {"path": r["path"], "kind": r["kind"], "title": r["title"], "mtime": r["mtime"],
                                      "size": r["size"], "note": r["note"], "score": r["score"], "passages": [], "pages": []}
            snip = (r["snip"] or "").strip()
            if snip and len(d["passages"]) < per_doc and snip not in d["passages"]:
                d["passages"].append(snip)
                d["pages"].append(int(r["part"] or 0))
        return list(found.values())
