import unittest

import docx

from docassemble.ALDashboard.docx_wrangling import update_docx


class TestDocxWranglingUpdateDocx(unittest.TestCase):
    def test_update_docx_replaces_existing_run(self):
        document = docx.Document()
        paragraph = document.add_paragraph()
        paragraph.add_run("Name: ____")

        updated = update_docx(document, [(0, 0, "Name: {{ users[0] }}", 0)])

        self.assertEqual(updated.paragraphs[0].runs[0].text, "Name: {{ users[0] }}")

    def test_update_docx_inserts_wordprocessingml_safe_paragraphs(self):
        document = docx.Document()
        paragraph = document.add_paragraph("Anchor")

        updated = update_docx(
            document,
            [
                (0, 0, "{%p if has_value %}\t", -1),
                (0, 0, "{%p endif %}\n", 1),
            ],
        )

        self.assertEqual(updated.paragraphs[0].text, "{%p if has_value %}\t")
        self.assertEqual(updated.paragraphs[1].text, "Anchor")
        self.assertEqual(updated.paragraphs[2].text, "{%p endif %}\n")

        before_xml = updated.paragraphs[0]._p.xml
        after_xml = updated.paragraphs[2]._p.xml

        # New paragraphs should contain proper run/text elements, not raw text directly under <w:p>.
        self.assertIn("<w:r>", before_xml)
        self.assertIn("<w:t", before_xml)
        self.assertIn("<w:tab/>", before_xml)
        self.assertIn("<w:br/>", after_xml)

    def test_update_docx_appends_run_when_run_index_is_out_of_bounds(self):
        document = docx.Document()
        document.add_paragraph("Only one run")

        updated = update_docx(document, [(0, 99, "Fallback run", 0)])

        self.assertEqual(updated.paragraphs[0].runs[-1].text, "Fallback run")

    def test_update_docx_ignores_invalid_items_and_accepts_dict_items(self):
        document = docx.Document()
        document.add_paragraph("Original")

        updated = update_docx(
            document,
            [
                {"paragraph": 0, "run": 0, "text": "From dict", "new_paragraph": 0},
                ["bad", "item"],
                None,
            ],
        )

        self.assertEqual(updated.paragraphs[0].runs[0].text, "From dict")


if __name__ == "__main__":
    unittest.main()
