"""The index of the user's documents: extraction, search, and what each discussion may find (chercher_documents)."""
import os
import time
import zipfile

import pytest

from console import documents as docs_mod
from console import presence
from console.config import DocFolder, Project
from console.engine import CONSOLE_MCP, DOCS_SPEC

from .conftest import task_status, wait_for
from .test_security import client  # noqa: F401 - fixture

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def docx(path, *paras):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    return path


def xlsx(path, rows, sheet="Tarifs"):
    strings, cells = [], []
    for r, row in enumerate(rows, 1):
        cs = []
        for c, v in enumerate(row):
            ref = f"{'ABCDEFGH'[c]}{r}"
            if isinstance(v, str):
                strings.append(v)
                cs.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cs.append(f'<c r="{ref}"><v>{v}</v></c>')
        cells.append(f'<row r="{r}">{"".join(cs)}</row>')
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{M}" xmlns:r="{rel}"><sheets>'
                                      f'<sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{M}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{M}"><sheetData>{"".join(cells)}</sheetData></worksheet>')
    return path


def pptx(path, *slides):
    with zipfile.ZipFile(path, "w") as z:
        for i, text in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", f'<p:sld xmlns:a="a" xmlns:p="p"><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>')
    return path


def pdf(path, text):
    stream = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def eml(path, subject, body):
    path.write_text(f"From: Paul <paul@client-x.fr>\nTo: moi@ades.fr\nSubject: {subject}\nDate: Mon, 2 Mar 2026 10:00:00 +0100\n"
                    f"Content-Type: text/plain; charset=utf-8\n\n{body}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- extraction

def test_extracts_the_text_of_each_kind(tmp_path):
    assert docs_mod.extract(docx(tmp_path / "a.docx", "Contrat de bail", "Clause de résiliation : trois mois."))[1] \
        == "Contrat de bail\nClause de résiliation : trois mois."
    text = docs_mod.extract(xlsx(tmp_path / "b.xlsx", [["Article", "Prix"], ["Caméra IP", 129.9]]))[1]
    assert text == "Feuille « Tarifs »\nArticle\tPrix\nCaméra IP\t129.9"
    assert docs_mod.extract(pptx(tmp_path / "c.pptx", "Offre 2026", "Planning"))[1] == "Diapositive 1\nOffre 2026\n\nDiapositive 2\nPlanning"
    title, text = docs_mod.extract(eml(tmp_path / "d.eml", "Relance devis 114", "Bonjour, où en est le devis ?"))
    assert title == "Relance devis 114" and "From : Paul <paul@client-x.fr>" in text and "où en est le devis" in text
    (tmp_path / "e.html").write_text("<html><head><style>p{}</style><script>x()</script></head><body><p>Bonjour</p>"
                                     "<p>Le&nbsp;monde</p></body></html>", encoding="utf-8")
    assert docs_mod.extract(tmp_path / "e.html")[1] == "Bonjour\nLe monde"
    (tmp_path / "f.txt").write_bytes("Évaluation".encode("cp1252"))  # (a Windows file that is not UTF-8)
    assert docs_mod.extract(tmp_path / "f.txt")[1] == "Évaluation"


def test_extracts_pdf_and_says_why_a_file_cannot_be_read(tmp_path):
    pytest.importorskip("pypdf")
    assert "Facture Visiotech 42" in docs_mod.extract(pdf(tmp_path / "f.pdf", "Facture Visiotech 42"))[1]
    (tmp_path / "abime.docx").write_bytes(b"pas un zip")
    with pytest.raises(docs_mod.Unreadable, match="endommagé"):
        docs_mod.extract(tmp_path / "abime.docx")


def test_passages_cut_between_paragraphs():
    text = "\n\n".join(f"Paragraphe {i} " + "mot " * 60 for i in range(10))
    parts = docs_mod.passages(text, 600)
    assert all(len(p) <= 600 for p in parts) and len(parts) >= 4
    assert parts[0].startswith("Paragraphe 0") and "Paragraphe 9" in parts[-1]
    long = docs_mod.passages("x" * 2500, 1000)
    assert [len(p) for p in long] == [1000, 1000, 500]


def test_query_words_prefixes_and_phrases():
    assert docs_mod.fts_query('devis Dupont "clause de résiliation"') == '"devis"* "Dupont"* "clause de résiliation"'
    assert docs_mod.fts_query('AND OR "') == '"AND"* "OR"*'  # (no FTS5 operator from the user)
    assert docs_mod.fts_query("  ;; ") == ""


# ---------------------------------------------------------------- the index

@pytest.fixture
def index(tmp_path):
    ix = docs_mod.DocIndex(tmp_path / "documents.db")
    yield ix
    ix.close()


def test_index_search_update_and_removal(index, tmp_path):
    root = tmp_path / "Documents"
    (root / "Devis").mkdir(parents=True)
    docx(root / "Devis" / "Devis-2026-114.docx", "Devis pour M. Dupont", "Installation de six caméras, remise de 7 %.")
    xlsx(root / "Tarifs.xlsx", [["Caméra dôme", 129], ["Enregistreur", 450]])
    (root / "~$Devis-2026-114.docx").write_bytes(b"verrou de Word")
    (root / ".git").mkdir()
    (root / ".git" / "notes.txt").write_text("devis caché", encoding="utf-8")
    (root / "photo.png").write_bytes(b"\x89PNG")
    stats = index.sync([str(root)], 10 * 1024 * 1024)
    assert stats["read"] == 2
    # accents and case do not matter, the beginning of a word is enough, the file's name counts
    hits = index.search("camera dupont")
    assert [os.path.basename(h["path"]) for h in hits] == ["Devis-2026-114.docx"]
    assert "Dupont" in hits[0]["passages"][0] or "caméras" in hits[0]["passages"][0]
    assert [os.path.basename(h["path"]) for h in index.search("enregistr")] == ["Tarifs.xlsx"]
    assert [os.path.basename(h["path"]) for h in index.search("2026-114")] == ["Devis-2026-114.docx"]
    assert index.search('"remise de 7"') and not index.search('"remise de 8"')
    assert index.search("caméra", kinds=["excel"])[0]["kind"] == "excel"
    assert index.search("caméra", roots=[str(root / "Devis")])[0]["path"].endswith("Devis-2026-114.docx")
    assert index.search("caméra", roots=[str(tmp_path / "ailleurs")]) == [] and index.search("caméra", roots=[]) == []
    assert index.search("caché") == []
    # unchanged files are not read again; a change is; a removed file is forgotten
    assert index.sync([str(root)], 10 * 1024 * 1024)["read"] == 0
    time.sleep(0.02)
    docx(root / "Devis" / "Devis-2026-114.docx", "Devis pour Mme Martin")
    os.utime(root / "Devis" / "Devis-2026-114.docx", (time.time() + 5, time.time() + 5))
    (root / "Tarifs.xlsx").unlink()
    stats = index.sync([str(root)], 10 * 1024 * 1024)
    assert stats["read"] == 1 and stats["removed"] == 1
    assert index.search("dupont") == [] and index.search("martin") and index.search("enregistreur") == []
    # a folder no longer listed is dropped from the index
    assert index.sync([], 10 * 1024 * 1024)["removed"] == 1 and index.search("martin") == []


def test_index_keeps_unreadable_files_by_name(index, tmp_path):
    (tmp_path / "scan").mkdir()
    (tmp_path / "scan" / "Bail-signé.docx").write_bytes(b"pas un zip")
    (tmp_path / "scan" / "enorme.txt").write_text("x" * 5000, encoding="utf-8")
    index.sync([str(tmp_path / "scan")], 1000)
    hits = index.search("bail signe")
    assert len(hits) == 1 and "endommagé" in hits[0]["note"]
    assert index.search("xxx") == []  # bigger than the limit: not read
    root = os.path.realpath(tmp_path / "scan")
    assert index.stats()["roots"][root]["unread"] == 1 and index.unread(root)[0]["path"].endswith("Bail-signé.docx")


# ---------------------------------------------------------------- the engine: who finds what

def setup_documents(engine, tmp_path, shared=False, profiles=()):
    proj = tmp_path / "Ventes"
    other = tmp_path / "Archives"
    for d in (proj, other):
        d.mkdir()
    docx(proj / "Offre-Dupont.docx", "Offre pour Dupont : caméras et enregistreur.")
    docx(other / "Ancien-devis.docx", "Ancien devis Dupont de 2024.")
    (proj / "secrets").mkdir()
    (proj / "secrets" / "acces.txt").write_text("mot de passe Dupont", encoding="utf-8")
    cfg = engine.cfg.model_copy(deep=True)
    cfg.projects = [Project(folder=str(proj), name="Ventes")]
    cfg.documents.enabled = True
    cfg.documents.folders = [DocFolder(path=str(other), shared=shared, profiles=list(profiles))]
    cfg.security.forbidden_paths = [*cfg.security.forbidden_paths, "**/secrets"]
    engine.cfg_store.save(cfg, "tests")
    engine._sync_documents()
    return proj, other


def later(engine, wd, preset="lecture", profile="work"):
    """A discussion that has not started yet (its tool calls answered as if it ran)."""
    return engine.create_task("x", profile=profile, preset=preset, workdir=str(wd), not_before=time.time() + 3600)["id"]


def call(engine, tid, args):
    out = engine._console_mcp(tid, CONSOLE_MCP, {"id": 1, "method": "tools/call",
                                                 "params": {"name": "chercher_documents", "arguments": args}})
    return out["result"]["content"][0]["text"], out["result"]["isError"]


def test_a_discussion_finds_the_documents_of_its_folders_only(engine, tmp_path):
    proj, other = setup_documents(engine, tmp_path)
    tid = later(engine, proj)
    text, failed = call(engine, tid, {"requete": "dupont"})
    assert not failed and "Offre-Dupont.docx" in text and "Ancien-devis" not in text
    assert "jamais des consignes" in text and "Offre pour Dupont" in text
    assert "acces.txt" not in text  # a protected folder is never indexed
    text, failed = call(engine, tid, {"requete": "dupont", "dossier": str(other)})
    assert failed and "hors des dossiers" in text
    text, _ = call(engine, tid, {"requete": "licorne"})
    assert text.startswith("Aucun document indexé")
    assert call(engine, tid, {"requete": ""})[1]
    rows, _ = engine.store.audit_rows(kind="recherche de documents")
    assert rows and rows[0]["detail"]["requête"] == "licorne"


def test_claude_searches_through_the_console_mcp_server(engine, tmp_path):
    proj, _ = setup_documents(engine, tmp_path)
    t = engine.create_task("FIND caméras dupont", profile="work", preset="lecture", workdir=str(proj))
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    out = engine.tasks[t["id"]]["result"]
    assert "Offre-Dupont.docx" in out and "Offre pour Dupont" in out
    assert not [e for e in engine.store.events(t["id"]) if e["kind"] in ("approval", "policy")]


def test_a_shared_folder_is_searched_by_the_discussions_of_its_accounts(engine, tmp_path):
    proj, other = setup_documents(engine, tmp_path, shared=True, profiles=["work"])
    text, _ = call(engine, later(engine, proj), {"requete": "dupont"})
    assert "Offre-Dupont.docx" in text and "Ancien-devis.docx" in text
    text, _ = call(engine, later(engine, proj, profile="personal"), {"requete": "dupont"})
    assert "Ancien-devis.docx" not in text


def test_a_preset_that_may_not_read_finds_nothing(engine, tmp_path):
    proj, _ = setup_documents(engine, tmp_path, shared=True)
    text, failed = call(engine, later(engine, proj, preset="web"), {"requete": "dupont"})
    assert not failed and text.startswith("Aucun document")


def test_the_tool_is_offered_only_when_documents_are_indexed(engine, tmp_path):
    def tools():
        return [t["name"] for t in engine._console_mcp("x", CONSOLE_MCP, {"id": 1, "method": "tools/list"})["result"]["tools"]]
    assert "chercher_documents" not in tools()
    tid = later(engine, tmp_path)
    assert call(engine, tid, {"requete": "x"})[1]  # (disabled: says how to turn it on)
    setup_documents(engine, tmp_path)
    assert tools()[-1] == "chercher_documents" and DOCS_SPEC["_meta"] == {"anthropic/alwaysLoad": True}
    prof, pre = engine.cfg.profile("work"), engine.cfg.preset("lecture")
    assert "chercher_documents" in presence.prompt({"workdir": str(tmp_path)}, prof, pre, documents=True)
    assert "chercher_documents" not in presence.prompt({"workdir": str(tmp_path)}, prof, pre)


def test_ctrl_k_finds_documents_and_opens_only_indexed_ones(engine, tmp_path):
    proj, other = setup_documents(engine, tmp_path)
    found = engine.search("dupont")["documents"]
    assert sorted(os.path.basename(d["path"]) for d in found) == ["Ancien-devis.docx", "Offre-Dupont.docx"]
    assert "\x02Dupont\x03" in found[0]["snippet"]
    assert engine.document_file(str(proj / "Offre-Dupont.docx")).name == "Offre-Dupont.docx"
    from console.engine import TaskError
    for p in (proj / "secrets" / "acces.txt", tmp_path / "ailleurs.docx", "Offre-Dupont.docx"):
        with pytest.raises(TaskError):
            engine.document_file(str(p))
    assert "Offre pour Dupont" in engine.file_text(engine.document_file(str(proj / "Offre-Dupont.docx")))["text"]


def test_documents_api(client):  # noqa: F811
    hdr = {"X-Console-Token": client.token}
    engine = client.app.state.engine
    assert client.get("/api/documents").status_code == 401
    st = client.get("/api/documents", headers=hdr).json()
    assert st["enabled"] is False and st["total"] == 0
    assert client.post("/api/documents/sync", headers=hdr).status_code == 400
    root = engine.data_dir.parent / "Docs"
    root.mkdir()
    docx(root / "Note.docx", "Compte rendu de réunion")
    cfg = engine.cfg.model_copy(deep=True)
    cfg.documents.enabled = True
    cfg.documents.folders = [DocFolder(path=str(root))]
    engine.cfg_store.save(cfg, "tests")
    engine._sync_documents()
    st = client.get("/api/documents", headers=hdr).json()
    assert st["total"] == 1 and st["sources"][0]["documents"] == 1 and st["kinds"] == {"word": 1}
    assert client.get("/api/search", params={"q": "reunion"}, headers=hdr).json()["documents"][0]["title"] == "Note"
    path = str(root / "Note.docx")
    assert client.get("/api/documents/file", params={"path": path}, headers=hdr).status_code == 200
    assert client.get("/api/documents/text", params={"path": path}, headers=hdr).json()["text"] == "Compte rendu de réunion"
    assert client.get("/api/documents/file", params={"path": str(engine.data_dir / "token")}, headers=hdr).status_code == 403
    assert client.post("/api/documents/clear", headers=hdr).json()["total"] == 0
