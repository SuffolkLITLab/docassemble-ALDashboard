# do not pre-load
"""Saved text corrections follow content through redrafts, never positions."""

import pikepdf
import pytest
from reportlab.pdfgen import canvas

from docassemble.ALDashboard.pdf_accessibility import (
    analyze_screen_reader_readback,
    create_draft_structure_tree,
    inspect_pdf_accessibility,
    repair_readback_text,
)


def _source(tmp_path):
    path = str(tmp_path / "source.pdf")
    drawing = canvas.Canvas(path)
    for index, text in enumerate(["First passage", "Same words", "Same words", "Last passage"]):
        drawing.drawString(72, 720 - index * 60, text)
    drawing.save()
    draft = str(tmp_path / "draft.pdf")
    create_draft_structure_tree(path, draft)
    return draft


@pytest.mark.parametrize("change", ["reorder", "remove_earlier", "remove_target"])
def test_saved_correction_follows_drawn_content(tmp_path, change):
    draft = _source(tmp_path)
    original = analyze_screen_reader_readback(draft)["announcements"]
    target = original[1]
    assert target["text"] == original[2]["text"]
    assert target["contentId"] != original[2]["contentId"]
    correction = {"contentId": target["contentId"], "announcedIndex": target["index"],
                  "actualText": "A person's correction", "apply": True}
    corrected = str(tmp_path / "corrected.pdf")
    repair_readback_text(draft, corrected, decisions=[correction])
    assert analyze_screen_reader_readback(corrected)["announcements"][1]["contentId"] == target["contentId"]
    blocks = inspect_pdf_accessibility(corrected)["content_blocks"]
    if change == "reorder":
        blocks = list(reversed(blocks))
    decisions = [dict(block, order=index, orderReviewed=True, roleReviewed=True,
                      role="Artifact" if (change == "remove_earlier" and block["text"] == "First passage")
                      or (change == "remove_target" and block["text"] == "Same words" and block.get("occurrence", 0) == 0)
                      else "P") for index, block in enumerate(blocks)]
    rebuilt = str(tmp_path / "rebuilt.pdf")
    create_draft_structure_tree(corrected, rebuilt, overwrite=True, content_decisions=decisions)
    before = analyze_screen_reader_readback(rebuilt)["announcements"]
    repaired = str(tmp_path / "repaired.pdf")
    result = repair_readback_text(rebuilt, repaired, decisions=[correction])
    after = analyze_screen_reader_readback(repaired)["announcements"]
    if change == "remove_target":
        assert result["unresolved_decisions"]
        assert not any(item["text"] == correction["actualText"] for item in after)
    else:
        matched = next(item for item in after if item["contentId"] == target["contentId"])
        assert matched["index"] != target["index"]
        assert matched["text"] == correction["actualText"]
        assert not result["unresolved_decisions"]
    for old, new in zip(before, after):
        if old["contentId"] != target["contentId"]:
            assert new["text"] == old["text"]


def test_ambiguous_identity_does_not_fall_back_to_index(tmp_path):
    draft = _source(tmp_path)
    duplicated = str(tmp_path / "duplicated.pdf")
    with pikepdf.open(draft) as pdf:
        children = pdf.Root.StructTreeRoot.K.K[0].K
        children.append(children[1])
        pdf.save(duplicated)
    target = analyze_screen_reader_readback(duplicated)["announcements"][1]
    repaired = str(tmp_path / "repaired.pdf")
    result = repair_readback_text(duplicated, repaired, decisions=[{
        "contentId": target["contentId"], "announcedIndex": 0,
        "actualText": "Must not appear",
    }])
    assert result["actual_text_added"] == 0
    assert len(result["unresolved_decisions"]) == 1
