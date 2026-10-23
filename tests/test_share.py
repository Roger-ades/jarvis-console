"""Partage d'une discussion : chemins masqués, résultats retirables, reprise locale."""
import json

from console.share import inspect_text, pack, unpack


def test_a_shared_transcript_hides_the_folder_and_can_land_elsewhere():
    folder = r"C:\Travail\essai"
    line = json.dumps({
        "type": "assistant", "sessionId": "ancienne", "cwd": folder,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "odoo_search", "input": {"path": folder + r"\devis.docx"}},
            {"type": "tool_result", "content": "secret " + folder},
        ]},
    }, ensure_ascii=False)
    stats = inspect_text(line, folder)
    assert stats["sensitive"] is True and stats["odoo"] == 1
    blob = pack(line, folder, "stripped")
    assert folder.encode("utf-8") not in blob
    new_id, text = unpack(blob, r"D:\Autre")
    parsed = json.loads(text)
    assert parsed["cwd"] == r"D:\Autre"
    assert parsed["sessionId"] == new_id
    assert "secret" not in text
    assert "[retiré au partage]" in text
