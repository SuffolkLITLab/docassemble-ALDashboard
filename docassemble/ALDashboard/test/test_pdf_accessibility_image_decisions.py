# do not pre-load
"""Reviewed image decisions turn page images into Figures or Artifacts."""

import os
import tempfile
import unittest

from docassemble.ALDashboard.pdf_accessibility import (
    PDFAccessibilityError,
    analyze_screen_reader_readback,
    create_draft_structure_tree,
    inspect_pdf_accessibility,
)


def _page_with_image(path):
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    image = pdf.make_stream(bytes([200, 40, 40] * 64 * 64))
    image["/Type"] = pikepdf.Name("/XObject")
    image["/Subtype"] = pikepdf.Name("/Image")
    image["/Width"] = 64
    image["/Height"] = 64
    image["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    image["/BitsPerComponent"] = 8
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
            }
        )
    )
    page.obj["/Resources"] = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/Im0": image}),
            "/Font": pikepdf.Dictionary({"/F1": font}),
        }
    )
    page.obj["/Contents"] = pdf.make_stream(
        b"BT /F1 12 Tf 72 720 Td (Visiting our office) Tj ET "
        b"q 100 0 0 100 72 560 cm /Im0 Do Q "
        b"BT /F1 12 Tf 72 540 Td (Two blocks north of the station.) Tj ET"
    )
    pdf.save(path)
    pdf.close()


class TestImageDecisions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.directory.name, "source.pdf")
        _page_with_image(self.source)

    def tearDown(self):
        self.directory.cleanup()

    def path(self, name):
        return os.path.join(self.directory.name, name)

    def spoken(self, path):
        readback = analyze_screen_reader_readback(path)
        return [(item["role"], item["text"]) for item in readback["announcements"]]

    def test_a_meaningful_image_becomes_a_described_figure_in_place(self):
        result = create_draft_structure_tree(
            self.source,
            self.path("figure.pdf"),
            image_decisions=[
                {"assetId": "p1:Im0", "decision": "figure", "altText": "Street map"}
            ],
        )
        self.assertEqual(result["figures_tagged"], 1)
        self.assertEqual(result["unmatched_image_decisions"], [])
        self.assertEqual(
            self.spoken(self.path("figure.pdf")),
            [
                ("P", "Visiting our office"),
                ("Figure", "Street map"),
                ("P", "Two blocks north of the station."),
            ],
        )
        figures = inspect_pdf_accessibility(self.path("figure.pdf"))[
            "structure_editor"
        ]["figures"]
        self.assertEqual([figure["altText"] for figure in figures], ["Street map"])

    def test_a_decorative_image_is_an_artifact_and_no_longer_reported(self):
        untouched = analyze_screen_reader_readback(
            self._drafted("plain.pdf", image_decisions=None)
        )
        self.assertTrue(
            any(f["category"] == "image-coverage" for f in untouched["findings"])
        )
        decorative = self._drafted(
            "decorative.pdf",
            image_decisions=[{"assetId": "p1:Im0", "decision": "artifact"}],
        )
        readback = analyze_screen_reader_readback(decorative)
        self.assertFalse(
            any(f["category"] == "image-coverage" for f in readback["findings"])
        )
        self.assertNotIn("Figure", [role for role, _text in self.spoken(decorative)])

    def test_a_rebuild_can_change_a_decorative_image_into_a_figure(self):
        decorative = self._drafted(
            "decorative.pdf",
            image_decisions=[{"assetId": "p1:Im0", "decision": "artifact"}],
        )
        create_draft_structure_tree(
            decorative,
            self.path("figure.pdf"),
            overwrite=True,
            image_decisions=[
                {"assetId": "p1:Im0", "decision": "figure", "altText": "Map"}
            ],
        )
        self.assertIn(("Figure", "Map"), self.spoken(self.path("figure.pdf")))

    def test_a_figure_without_a_description_is_left_for_review(self):
        result = create_draft_structure_tree(
            self.source,
            self.path("blank.pdf"),
            image_decisions=[{"assetId": "p1:Im0", "decision": "figure", "altText": " "}],
        )
        self.assertEqual(result["figures_tagged"], 0)

    def test_an_unknown_decision_is_rejected(self):
        with self.assertRaises(PDFAccessibilityError):
            create_draft_structure_tree(
                self.source,
                self.path("bad.pdf"),
                image_decisions=[{"assetId": "p1:Im0", "decision": "maybe"}],
            )

    def test_announcements_carry_their_page_position(self):
        drafted = self._drafted("plain.pdf", image_decisions=None)
        first = analyze_screen_reader_readback(drafted)["announcements"][0]
        self.assertEqual((first["x"], first["y"]), (72.0, 720.0))

    def _drafted(self, name, *, image_decisions):
        create_draft_structure_tree(
            self.source, self.path(name), image_decisions=image_decisions
        )
        return self.path(name)


if __name__ == "__main__":
    unittest.main()
