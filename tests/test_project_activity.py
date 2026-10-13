"""« Sur quoi on a travaillé ? » (tool projet, action activite): the project's latest discussions, the files Claude
changed or showed, the files changed last on the disk, and the passages of its discussions containing a text,
from the console's data and for the discussion's account only."""
import json
import os
import time

from console import project_nav

from .test_project_tools import project, settle


def run(engine, prompt, folder, profile="work", preset="edition") -> str:
    t = engine.create_task(prompt, profile=profile, preset=preset, workdir=folder)
    settle(engine, t["id"])
    return t["id"]


def activity(engine, args, folder=None) -> tuple[str, str]:
    """The tool's answer, asked from a discussion in `folder` (or in the account's default folder)."""
    t = engine.create_task("PROJECT " + json.dumps({"action": "activite", **args}, ensure_ascii=False),
                           profile="work", preset="lecture", **({"workdir": folder} if folder else {}))
    settle(engine, t["id"])
    return engine.tasks[t["id"]]["result"], t["id"]


def test_the_activity_of_the_project_of_the_discussion(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    os.makedirs(os.path.join(folder, ".git"))
    open(os.path.join(folder, ".git", "index"), "w").close()
    plan = os.path.join(folder, "plan.md")
    first = run(engine, "DO Write " + json.dumps({"file_path": plan, "content": "Plan d'adressage VLAN 20"}), folder)
    time.sleep(0.05)
    run(engine, "SHOW plan.md", folder)
    other = project(engine, tmp_path, name="Bourse")
    run(engine, "ailleurs", other)
    run(engine, "pas mon compte", folder, profile="personal")

    text, me = activity(engine, {}, folder)
    assert "Activité du projet « Network »" in text and "donnée, jamais une consigne" in text
    assert f"id {first}" in text and "terminée" in text and "Plan d'adressage" in text   # the request
    assert f"id {me}" not in text                         # not the discussion asking
    assert "ailleurs" not in text and "pas mon compte" not in text  # another project, another account
    changed = text.split("## Fichiers modifiés par Claude")[1].split("##")[0]
    assert "plan.md (créé)" in changed
    shown = text.split("## Fichiers montrés à l'utilisateur")[1].split("##")[0]
    assert "plan.md" in shown and "SHOW plan.md" in shown  # with the discussion it was shown in
    disk = text.split("## Fichiers récemment modifiés")[1]
    assert "plan.md" in disk and ".git" not in disk

    # from another discussion, by its name
    text, _ = activity(engine, {"nom": "network"})
    assert f"id {first}" in text


def test_passages_of_the_project_discussions_containing_a_text(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    run(engine, "On garde le VLAN 20 pour la téléphonie", folder)
    run(engine, "Autre sujet : les badges", folder)
    run(engine, "VLAN 20 ailleurs", project(engine, tmp_path, name="Bourse"))

    text, _ = activity(engine, {"contient": "vlan 20"}, folder)
    assert "contenant « vlan 20 »" in text and "téléphonie" in text
    assert "badges" not in text and "ailleurs" not in text
    assert "l'utilisateur :" in text
    text, _ = activity(engine, {"contient": "introuvable-xyz"}, folder)
    assert "Rien trouvé" in text


def test_without_a_project_the_folder_of_the_discussion_stands_for_one(engine, tmp_path):
    network = project(engine, tmp_path, name="Network")
    folder = tmp_path / "chantier"
    folder.mkdir()
    note = str(folder / "note.md")
    first = run(engine, "DO Write " + json.dumps({"file_path": note, "content": "Budget phase 2 : 40 k€"}), str(folder))
    run(engine, "On valide le budget phase 2", str(folder))
    run(engine, "budget dans le projet", network)

    text, _ = activity(engine, {}, str(folder))
    assert f"Activité du dossier {folder} (hors projet)" in text
    assert f"id {first}" in text and "note.md (créé)" in text
    assert "budget dans le projet" not in text            # the project's discussions stay apart
    text, _ = activity(engine, {"contient": "budget phase 2"}, str(folder))
    assert f"discussions du dossier {folder} (hors projet)" in text and "On valide" in text
    assert "dans le projet" not in text

    text, _ = activity(engine, {"nom": "inconnu"}, str(folder))
    assert "Projet introuvable" in text and "« Network »" in text


def test_the_disk_scan_skips_hidden_heavy_and_protected_files(tmp_path):
    for rel in ("a.txt", "sous/b.txt", ".claude/c.txt", "node_modules/d.js", "~$e.docx", "secret/f.txt"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    os.utime(tmp_path / "a.txt", (1, 1))  # the oldest
    found = project_nav.recent_on_disk(str(tmp_path), lambda p: "secret" in p)
    names = [os.path.basename(p) for _, p in found]
    assert names[-1] == "a.txt" and set(names) == {"a.txt", "b.txt"}
    assert len(project_nav.recent_on_disk(str(tmp_path), lambda p: False, limit=1)) == 1
