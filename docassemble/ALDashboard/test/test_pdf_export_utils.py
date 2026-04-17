import unittest

from docassemble.ALDashboard.pdf_export_utils import build_pdf_export_fields_per_page


class FakeFieldType:
    TEXT = "text"
    AREA = "area"
    CHECK_BOX = "checkbox"
    SIGNATURE = "signature"
    RADIO = "radio"
    CHOICE = "choice"
    LIST_BOX = "listbox"


class FakeFormField:
    def __init__(self, field_name, type_name, x, y, font_size=None, configs=None):
        self.name = field_name
        self.type = type_name
        self.x = x
        self.y = y
        self.font_size = font_size
        self.configs = configs or {}


class TestPDFExportUtils(unittest.TestCase):
    def test_renamed_text_field_export_keeps_name_and_auto_size(self):
        fields_per_page = build_pdf_export_fields_per_page(
            [
                {
                    "name": "users1_name_full",
                    "type": "text",
                    "pageIndex": 0,
                    "x": 72,
                    "y": 144,
                    "width": 220,
                    "height": 18,
                    "font": "Helvetica",
                    "autoSize": True,
                    "allowScroll": False,
                }
            ],
            page_count=1,
            form_field_cls=FakeFormField,
            field_type_enum=FakeFieldType,
            color_parser=lambda raw: f"parsed:{raw}",
        )

        self.assertEqual(len(fields_per_page), 1)
        self.assertEqual(len(fields_per_page[0]), 1)
        field = fields_per_page[0][0]
        self.assertEqual(field.name, "users1_name_full")
        self.assertEqual(field.type, FakeFieldType.TEXT)
        self.assertEqual(field.x, 72)
        self.assertEqual(field.y, 144)
        self.assertEqual(field.font_size, 0)
        self.assertEqual(
            field.configs,
            {
                "width": 220.0,
                "height": 18.0,
                "fontName": "Helvetica",
                "fieldFlags": "doNotScroll",
                "borderWidth": 0,
            },
        )

    def test_checkbox_export_uses_size_not_width_height(self):
        fields_per_page = build_pdf_export_fields_per_page(
            [
                {
                    "name": "users1_opt_in",
                    "type": "checkbox",
                    "pageIndex": 0,
                    "x": 10,
                    "y": 20,
                    "width": 30,
                    "height": 18,
                    "checkboxStyle": "cross",
                }
            ],
            page_count=1,
            form_field_cls=FakeFormField,
            field_type_enum=FakeFieldType,
            color_parser=None,
        )

        field = fields_per_page[0][0]
        self.assertEqual(field.name, "users1_opt_in")
        self.assertEqual(field.type, FakeFieldType.CHECK_BOX)
        self.assertEqual(field.x, 16)
        self.assertEqual(field.y, 20)
        self.assertEqual(field.font_size, 12)
        self.assertEqual(field.configs["size"], 18.0)
        self.assertEqual(field.configs["buttonStyle"], "cross")
        self.assertNotIn("width", field.configs)
        self.assertNotIn("height", field.configs)
        self.assertNotIn("fontName", field.configs)
        self.assertEqual(field.configs["borderWidth"], 0)

    def test_multiline_and_dropdown_export_configs_are_widget_specific(self):
        fields_per_page = build_pdf_export_fields_per_page(
            [
                {
                    "name": "users1_address",
                    "type": "multiline",
                    "pageIndex": 0,
                    "x": 40,
                    "y": 50,
                    "width": 240,
                    "height": 54,
                    "font": "Helvetica",
                    "fontSize": 10,
                    "allowScroll": False,
                    "backgroundColor": "#ffffff",
                },
                {
                    "name": "users1_state",
                    "type": "dropdown",
                    "pageIndex": 0,
                    "x": 300,
                    "y": 50,
                    "width": 80,
                    "height": 18,
                    "font": "Helvetica",
                    "fontSize": 10,
                    "options": ["MA", "RI"],
                },
            ],
            page_count=1,
            form_field_cls=FakeFormField,
            field_type_enum=FakeFieldType,
            color_parser=lambda raw: f"parsed:{raw}",
        )

        multiline = fields_per_page[0][0]
        dropdown = fields_per_page[0][1]

        self.assertEqual(multiline.type, FakeFieldType.AREA)
        self.assertEqual(multiline.configs["fieldFlags"], "multiline doNotScroll")
        self.assertEqual(multiline.configs["fillColor"], "parsed:#ffffff")
        self.assertEqual(multiline.configs["fontName"], "Helvetica")
        self.assertEqual(multiline.configs["width"], 240.0)
        self.assertEqual(multiline.configs["height"], 54.0)
        self.assertEqual(multiline.configs["borderWidth"], 0)

        self.assertEqual(dropdown.type, FakeFieldType.CHOICE)
        self.assertEqual(dropdown.configs["fieldFlags"], "combo")
        self.assertEqual(dropdown.configs["options"], ["MA", "RI"])
        self.assertEqual(dropdown.configs["fontName"], "Helvetica")
        self.assertEqual(dropdown.configs["fontSize"], 10)
        self.assertEqual(dropdown.configs["width"], 80.0)
        self.assertEqual(dropdown.configs["height"], 18.0)
        self.assertEqual(dropdown.configs["borderWidth"], 0)


if __name__ == "__main__":
    unittest.main()
