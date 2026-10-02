# do not pre-load
"""Drafting tags again over an earlier draft keeps every text object tagged."""

import os
import tempfile
import unittest

import pikepdf

from docassemble.ALDashboard.pdf_accessibility import (
    _strip_straddling_artifacts,
    create_draft_structure_tree,
    inspect_pdf_accessibility,
)

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "civil_docketing_statement_polished_repaired.pdf"
)


def _ops(text):
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page.obj["/Contents"] = pdf.make_stream(text)
    return list(pikepdf.parse_content_stream(page))


class TestRedraft(unittest.TestCase):
    def test_a_second_draft_tags_as_many_text_blocks_as_the_first(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = os.path.join(directory, "first.pdf")
            second_path = os.path.join(directory, "second.pdf")
            first = create_draft_structure_tree(_FIXTURE, first_path)
            second = create_draft_structure_tree(first_path, second_path, overwrite=True)
            self.assertEqual(second["text_blocks_tagged"], first["text_blocks_tagged"])
            issues = {
                issue["id"]: issue["status"]
                for issue in inspect_pdf_accessibility(second_path)["report"]["issues"]
            }
            self.assertEqual(issues["content-tags"], "pass")

    def test_reviewed_order_and_roles_apply_when_spaces_are_drawn_as_kerning(self):
        # This form draws word gaps with TJ offsets, so its content stream
        # reads "AppealsCourtDocketNumber" while the layout reads it spaced.
        blocks = sorted(
            (
                block
                for block in inspect_pdf_accessibility(_FIXTURE)["content_blocks"]
                if block["pageIndex"] == 0
            ),
            key=lambda block: (block["box"]["y"], block["box"]["x"]),
        )
        texts = [block["text"] for block in blocks]
        self.assertEqual(texts[2:5], ["Appeals Court Docket Number", "-P-", "Caption used in the lower court"])
        blocks[3], blocks[4] = blocks[4], blocks[3]
        decisions = [
            {
                "pageIndex": 0,
                "text": block["text"],
                "occurrence": block.get("occurrence", 0),
                "role": "Artifact" if block["text"] == "v." else "P",
                "order": order,
                "roleReviewed": block["text"] == "v.",
                "orderReviewed": True,
            }
            for order, block in enumerate(blocks)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ordered.pdf")
            result = create_draft_structure_tree(_FIXTURE, path, content_decisions=decisions)
            self.assertEqual(result["manual_artifact_blocks"], 1)
            spoken = [
                item["text"]
                for item in inspect_pdf_accessibility(path)["readback"]["announcements"]
                if item["page"] == 0 and item["kind"] == "text"
            ]
        self.assertLess(
            spoken.index("Captionusedinthelowercourt"), spoken.index("-P-")
        )
        self.assertNotIn("v.", spoken)

    def test_decoration_never_wraps_across_a_text_object_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "draft.pdf")
            create_draft_structure_tree(_FIXTURE, path)
            with pikepdf.open(path) as pdf:
                for page in pdf.pages:
                    instructions = list(pikepdf.parse_content_stream(page))
                    self.assertEqual(
                        len(_strip_straddling_artifacts(instructions)),
                        len(instructions),
                    )

    def test_a_straddling_wrapper_is_dropped_and_an_enclosing_one_kept(self):
        straddling = _ops(
            b"BT (a) Tj /Artifact BMC ET 0 0 m 1 1 l S BT EMC (b) Tj ET"
        )
        operators = [str(item.operator) for item in _strip_straddling_artifacts(straddling)]
        self.assertNotIn("BMC", operators)
        self.assertNotIn("EMC", operators)
        enclosing = _ops(b"/Artifact BMC BT (Page 1) Tj ET EMC")
        self.assertEqual(len(_strip_straddling_artifacts(enclosing)), len(enclosing))


if __name__ == "__main__":
    unittest.main()
