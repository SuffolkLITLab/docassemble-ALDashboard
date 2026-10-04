# do not pre-load
import tempfile
import unittest
from pathlib import Path
import pikepdf
from reportlab.pdfgen import canvas
from docassemble.ALDashboard.pdf_accessibility import (
    create_draft_structure_tree,
    inspect_pdf_accessibility,
    apply_manual_structure_repairs,
    PDFAccessibilityError,
    _readback_sequence,
    _structure_node_at_path,
)
from docassemble.ALDashboard.pdf_accessibility_review import preserve_page_tags


class TestAuthorChoices(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = str(Path(self.folder.name) / "source.pdf")
        self.tagged = str(Path(self.folder.name) / "tagged.pdf")
        self.output = str(Path(self.folder.name) / "out.pdf")
        c = canvas.Canvas(self.source)
        for text, x, y in [
            ("Column one", 40, 720),
            ("Column two", 240, 720),
            ("First value", 40, 650),
            ("Second value", 240, 650),
        ]:
            c.drawString(x, y, text)
        c.acroForm.textfield(
            name="opaque__19", x=40, y=600, width=100, height=20, value="keep me"
        )
        c.showPage()
        c.drawString(40, 720, "Second page")
        c.save()
        create_draft_structure_tree(self.source, self.tagged, mark_as_tagged=False)

    def table_operation(self):
        report = inspect_pdf_accessibility(self.tagged)
        items = report["structure_editor"]["tableItems"]
        chosen = [
            next(item for item in items if item["text"] == text)["contentIds"]
            for text in ["Column one", "Column two", "First value", "Second value"]
        ]
        return {
            "action": "create_table",
            "tableId": "chosen-table",
            "rows": [
                [
                    {"contentIds": chosen[0], "role": "TH", "scope": "Column"},
                    {"contentIds": chosen[1], "role": "TH", "scope": "Column"},
                ],
                [
                    {"contentIds": chosen[2], "role": "TD"},
                    {"contentIds": chosen[3], "role": "TD"},
                ],
            ],
        }

    def test_table_creation_reuses_content_and_survives_rebuild(self):
        operation = self.table_operation()
        before = inspect_pdf_accessibility(self.tagged)["readback"]["announcements"]
        apply_manual_structure_repairs(self.tagged, self.output, [operation])
        report = inspect_pdf_accessibility(self.output)
        table = report["structure_editor"]["tables"][0]
        self.assertEqual(table["tableId"], "chosen-table")
        self.assertEqual(
            [[c["role"] for c in r["cells"]] for r in table["rows"]],
            [["TH", "TH"], ["TD", "TD"]],
        )
        self.assertFalse(table["issueIds"])
        self.assertEqual(
            sorted(i["text"] for i in before),
            sorted(i["text"] for i in report["readback"]["announcements"]),
        )
        with pikepdf.open(self.output) as pdf:
            self.assertEqual(str(pdf.Root.AcroForm.Fields[0].T), "opaque__19")
            self.assertEqual(str(pdf.Root.AcroForm.Fields[0].V), "keep me")
            content = [
                (
                    p.Contents.read_bytes()
                    if isinstance(p.Contents, pikepdf.Stream)
                    else b"".join(s.read_bytes() for s in p.Contents)
                )
                for p in pdf.pages
            ]
        create_draft_structure_tree(
            self.output, self.tagged, overwrite=True, mark_as_tagged=False
        )
        apply_manual_structure_repairs(self.tagged, self.output, [operation])
        self.assertEqual(
            len(inspect_pdf_accessibility(self.output)["structure_editor"]["tables"]), 1
        )
        apply_manual_structure_repairs(self.output, self.tagged, [operation])
        self.assertEqual(
            len(inspect_pdf_accessibility(self.tagged)["structure_editor"]["tables"]), 1
        )

    def test_table_rejects_reused_passages_and_ambiguous_content(self):
        operation = self.table_operation()
        operation["rows"][1][1]["contentIds"] = operation["rows"][0][0]["contentIds"]
        with self.assertRaisesRegex(PDFAccessibilityError, "two cells"):
            apply_manual_structure_repairs(self.tagged, self.output, [operation])
        operation = self.table_operation()
        operation["rows"][0][0]["contentIds"] = ["missing"]
        with self.assertRaisesRegex(PDFAccessibilityError, "missing or ambiguous"):
            apply_manual_structure_repairs(self.tagged, self.output, [operation])

    def test_page_reorder_and_split_keep_tags_metadata_values_and_parent_tree(self):
        with pikepdf.open(self.tagged) as pdf:
            pdf.Root.Lang = pikepdf.String("en-US")
            pdf.docinfo.Title = "Chosen title"
            pdf.save(self.source)
        preserve_page_tags(self.source, self.output, [1, 0])
        with pikepdf.open(self.output) as pdf:
            sequence = _readback_sequence(pdf)
            self.assertEqual(sequence[0]["text"], "Second page")
            self.assertEqual(sequence[0]["page"], 0)
            self.assertEqual(str(pdf.Root.Lang), "en-US")
            self.assertEqual(str(pdf.docinfo.Title), "Chosen title")
            self.assertEqual(str(pdf.Root.AcroForm.Fields[0].V), "keep me")
        preserve_page_tags(self.source, self.output, [1])
        with pikepdf.open(self.output) as pdf:
            self.assertEqual(len(pdf.pages), 1)
            self.assertEqual(len(pdf.Root.AcroForm.Fields), 0)
            self.assertEqual(
                [a["text"] for a in _readback_sequence(pdf)], ["Second page"]
            )
            self.assertEqual(len(pdf.Root.StructTreeRoot.ParentTree.Nums), 2)
        with self.assertRaisesRegex(PDFAccessibilityError, "distinct pages"):
            preserve_page_tags(self.source, self.output, [0, 0])

    def test_table_repair_keeps_spans_and_rejects_unsafe_regrouping_or_padding(self):
        operation = self.table_operation()
        apply_manual_structure_repairs(self.tagged, self.output, [operation])
        operation["tableId"] = "unrelated-table"
        with self.assertRaisesRegex(PDFAccessibilityError, "already belongs"):
            apply_manual_structure_repairs(self.output, self.source, [operation])
        with pikepdf.open(self.output) as pdf:
            path = inspect_pdf_accessibility(self.output)["structure_editor"]["tables"][
                0
            ]["path"]
            table = _structure_node_at_path(pdf.Root.StructTreeRoot, path)
            table.K[0].K[0].A.ColSpan = 2
            pdf.save(self.source)
        table = inspect_pdf_accessibility(self.source)["structure_editor"]["tables"][0]
        self.assertTrue(table["hasSpans"])
        apply_manual_structure_repairs(
            self.source,
            self.output,
            [
                {
                    "action": "set_role",
                    "path": table["rows"][0]["cells"][0]["path"],
                    "role": "TD",
                }
            ],
        )
        with pikepdf.open(self.output) as pdf:
            cell = _structure_node_at_path(
                pdf.Root.StructTreeRoot, table["rows"][0]["cells"][0]["path"]
            )
            self.assertEqual(int(cell.A.ColSpan), 2)
            self.assertNotIn("/Scope", cell.A)
        with self.assertRaisesRegex(PDFAccessibilityError, "merged cells"):
            apply_manual_structure_repairs(
                self.output,
                self.tagged,
                [{"action": "pad_table", "path": table["path"]}],
            )

    def test_disabling_button_keeps_appearance_and_removes_actions_and_form_tag(self):
        with pikepdf.open(self.tagged) as pdf:
            appearance = pdf.make_stream(b"1 0 0 rg 0 0 30 10 re f")
            appearance.Type = pikepdf.Name.XObject
            appearance.Subtype = pikepdf.Name.Form
            appearance.BBox = pikepdf.Array([0, 0, 30, 10])
            appearance.Matrix = pikepdf.Array([0, 1, -1, 0, 10, 0])
            stamp = pdf.make_stream(b"\x00\x00\x00")
            stamp.Type = pikepdf.Name.XObject
            stamp.Subtype = pikepdf.Name.Image
            stamp.Width = stamp.Height = 1
            stamp.ColorSpace = pikepdf.Name.DeviceRGB
            stamp.BitsPerComponent = 8
            appearance.Resources = pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Stamp=stamp)
            )
            widget = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Annot,
                    Subtype=pikepdf.Name.Widget,
                    FT=pikepdf.Name.Btn,
                    Ff=65536,
                    T=pikepdf.String("seal__4"),
                    Rect=pikepdf.Array([300, 100, 340, 220]),
                    AP=pikepdf.Dictionary(N=appearance),
                    A=pikepdf.Dictionary(
                        S=pikepdf.Name.JavaScript, JS=pikepdf.String("this.print();")
                    ),
                )
            )
            pdf.pages[0].Annots.append(widget)
            pdf.Root.AcroForm.Fields.append(widget)
            pdf.save(self.source)
        create_draft_structure_tree(
            self.source, self.tagged, overwrite=True, mark_as_tagged=False
        )
        result = apply_manual_structure_repairs(
            self.tagged,
            self.output,
            [{"action": "disable_decorative_button", "pageIndex": 0, "index": 1}],
        )
        self.assertEqual(result["buttons_disabled"], 1)
        self.assertEqual(inspect_pdf_accessibility(self.output)["images"], [])
        with pikepdf.open(self.output) as pdf:
            self.assertEqual(len(pdf.pages[0].Annots), 1)
            self.assertEqual(str(pdf.Root.AcroForm.Fields[0].T), "opaque__19")
            self.assertEqual(str(pdf.Root.AcroForm.Fields[0].V), "keep me")
            self.assertTrue(
                any(
                    b"/Artifact BMC" in stream.read_bytes()
                    and b"/DADecoration Do" in stream.read_bytes()
                    for stream in pdf.pages[0].Contents
                )
            )
            self.assertNotIn(
                "seal__4", [e.get("name") for e in _readback_sequence(pdf)]
            )
        create_draft_structure_tree(
            self.output, self.source, overwrite=True, mark_as_tagged=False
        )
        self.assertEqual(
            len(inspect_pdf_accessibility(self.source)["structure_editor"]["widgets"]),
            1,
        )
        with self.assertRaisesRegex(PDFAccessibilityError, "Only push buttons"):
            apply_manual_structure_repairs(
                self.source,
                self.output,
                [{"action": "disable_decorative_button", "pageIndex": 0, "index": 0}],
            )
