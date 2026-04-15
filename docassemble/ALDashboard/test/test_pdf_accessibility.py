import unittest

from docassemble.ALDashboard.pdf_accessibility import (
    build_default_field_order,
    default_pdf_field_tooltip,
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


if __name__ == "__main__":
    unittest.main()
