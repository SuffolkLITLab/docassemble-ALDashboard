# do not pre-load
import os
import tempfile
import unittest

from docassemble.ALDashboard.pdf_accessibility import (
    apply_pdf_accessibility_settings,
    build_default_field_order,
    default_pdf_field_tooltip,
    extract_pdf_field_tooltips,
)


class TestPDFAccessibilityHelpers(unittest.TestCase):
    def test_default_tooltip_replaces_underscores(self):
        self.assertEqual(
            default_pdf_field_tooltip("users1_name_first"),
            "users1 name first",
        )

    def test_default_tooltip_handles_empty(self):
        self.assertEqual(default_pdf_field_tooltip(""), "Field")
        self.assertEqual(default_pdf_field_tooltip(None), "Field")

    def test_build_default_field_order_sorts_page_then_top_then_left(self):
        fields = [
            {"name": "third", "pageIndex": 1, "x": 10, "y": 5},
            {"name": "second", "pageIndex": 0, "x": 20, "y": 10},
            {"name": "first", "pageIndex": 0, "x": 5, "y": 10},
            {"name": "zero", "pageIndex": 0, "x": 2, "y": 1},
        ]
        self.assertEqual(
            build_default_field_order(fields),
            ["zero", "first", "second", "third"],
        )

    def test_extract_pdf_field_tooltips_reads_parent_and_widget_tu(self):
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            parent_tooltip_field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("parent_field"),
                        "/TU": String("Parent tooltip"),
                    }
                )
            )
            parent_tooltip_widget = pdf.make_indirect(
                Dictionary(
                    {
                        "/Parent": parent_tooltip_field,
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 0, 100, 20]),
                    }
                )
            )
            widget_tooltip_field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("widget_field"),
                        "/TU": String("Widget tooltip"),
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 30, 100, 50]),
                    }
                )
            )
            unlabeled_field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("unlabeled_field"),
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 60, 100, 80]),
                    }
                )
            )
            page.obj["/Annots"] = Array(
                [parent_tooltip_widget, widget_tooltip_field, unlabeled_field]
            )
            pdf.Root["/AcroForm"] = Dictionary(
                {
                    "/Fields": Array(
                        [parent_tooltip_field, widget_tooltip_field, unlabeled_field]
                    )
                }
            )
            pdf.save(pdf_path)
            pdf.close()

            self.assertEqual(
                extract_pdf_field_tooltips(pdf_path),
                {
                    "parent_field": "Parent tooltip",
                    "widget_field": "Widget tooltip",
                },
            )
        finally:
            os.remove(pdf_path)

    def test_apply_pdf_accessibility_settings_can_reapply_copied_tooltips(self):
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("copied_field"),
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 0, 100, 20]),
                    }
                )
            )
            page.obj["/Annots"] = Array([field])
            pdf.Root["/AcroForm"] = Dictionary({"/Fields": Array([field])})
            pdf.save(pdf_path)
            pdf.close()

            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                field_tooltips={"copied_field": "Copied source tooltip"},
                auto_fill_missing_tooltips=False,
            )

            with pikepdf.open(pdf_path) as pdf:
                widget = pdf.pages[0]["/Annots"][0]
                self.assertEqual(str(widget["/TU"]), "Copied source tooltip")
                self.assertEqual(
                    str(pdf.Root["/AcroForm"]["/Fields"][0]["/TU"]),
                    "Copied source tooltip",
                )
        finally:
            os.remove(pdf_path)


if __name__ == "__main__":
    unittest.main()
