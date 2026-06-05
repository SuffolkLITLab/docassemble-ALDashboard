# do not pre-load
import unittest

from docassemble.ALDashboard.pdf_export_utils import (
    build_normalized_pdf_field_definitions,
    build_pdf_export_fields_per_page,
)


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

    def test_export_preserves_duplicate_field_names_by_default(self):
        fields_per_page = build_pdf_export_fields_per_page(
            [
                {
                    "name": "users1_name",
                    "type": "text",
                    "pageIndex": 0,
                    "x": 10,
                    "y": 20,
                },
                {
                    "name": "users1_name",
                    "type": "text",
                    "pageIndex": 0,
                    "x": 30,
                    "y": 40,
                },
                {
                    "name": "users1_name",
                    "type": "text",
                    "pageIndex": 0,
                    "x": 50,
                    "y": 60,
                },
            ],
            page_count=1,
            form_field_cls=FakeFormField,
            field_type_enum=FakeFieldType,
        )

        self.assertEqual(
            [field.name for field in fields_per_page[0]],
            ["users1_name", "users1_name", "users1_name"],
        )

    def test_export_deduplicates_repeated_field_names_when_requested(self):
        fields_per_page = build_pdf_export_fields_per_page(
            [
                {
                    "name": "users1_name",
                    "type": "text",
                    "pageIndex": 0,
                    "x": 10,
                    "y": 20,
                },
                {
                    "name": "users1_name",
                    "type": "text",
                    "pageIndex": 0,
                    "x": 30,
                    "y": 40,
                },
                {
                    "name": "users1_name",
                    "type": "text",
                    "pageIndex": 0,
                    "x": 50,
                    "y": 60,
                },
            ],
            page_count=1,
            form_field_cls=FakeFormField,
            field_type_enum=FakeFieldType,
            deduplicate_field_names=True,
        )

        self.assertEqual(
            [field.name for field in fields_per_page[0]],
            ["users1_name", "users1_name__1", "users1_name__2"],
        )

    def test_bulk_normalize_preserves_per_page_detected_coordinates(self):
        detected = [
            [
                FakeFormField(
                    "first_page_field",
                    FakeFieldType.TEXT,
                    72,
                    144,
                    font_size=0,
                    configs={"width": 200, "height": 18},
                )
            ],
            [
                FakeFormField(
                    "second_page_name",
                    FakeFieldType.CHECK_BOX,
                    55,
                    66,
                    font_size=12,
                    configs={"width": 13, "height": 13},
                )
            ],
        ]

        normalized = build_normalized_pdf_field_definitions(
            detected,
            page_count=2,
            normalize_font_size=True,
            font_size_pt=10,
            checkbox_size_pt=12,
        )

        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["pageIndex"], 0)
        self.assertEqual(normalized[0]["x"], 72)
        self.assertEqual(normalized[0]["y"], 144)
        self.assertEqual(normalized[0]["width"], 200)
        self.assertEqual(normalized[0]["height"], 18)
        self.assertEqual(normalized[0]["fontSize"], 10)
        self.assertEqual(normalized[1]["pageIndex"], 1)
        self.assertEqual(normalized[1]["x"], 55)
        self.assertEqual(normalized[1]["y"], 66)
        self.assertEqual(normalized[1]["width"], 12)
        self.assertEqual(normalized[1]["height"], 12)
        self.assertEqual(normalized[1]["checkboxStyle"], "cross")

    def test_bulk_normalize_preserves_duplicate_names_by_default(self):
        detected = [
            [
                FakeFormField("same_name", FakeFieldType.TEXT, 10, 20),
                FakeFormField("same_name", FakeFieldType.TEXT, 30, 40),
            ]
        ]

        normalized = build_normalized_pdf_field_definitions(
            detected,
            page_count=1,
        )

        self.assertEqual(
            [field["name"] for field in normalized],
            ["same_name", "same_name"],
        )

    def test_bulk_normalize_deduplicates_repeated_names_when_requested(self):
        detected = [
            [
                FakeFormField("same_name", FakeFieldType.TEXT, 10, 20),
                FakeFormField("same_name", FakeFieldType.TEXT, 30, 40),
                FakeFormField("other", FakeFieldType.TEXT, 50, 60),
                FakeFormField("same_name", FakeFieldType.TEXT, 70, 80),
            ]
        ]

        normalized = build_normalized_pdf_field_definitions(
            detected,
            page_count=1,
            deduplicate_field_names=True,
        )

        self.assertEqual(
            [field["name"] for field in normalized],
            ["same_name", "same_name__1", "other", "same_name__2"],
        )

    def test_bulk_normalize_keeps_auto_size_only_when_font_size_is_not_normalized(self):
        detected = [
            [
                FakeFormField(
                    "client_name",
                    FakeFieldType.TEXT,
                    10,
                    20,
                    font_size=0,
                    configs={"width": 150, "height": 18},
                )
            ]
        ]

        normalized_fixed = build_normalized_pdf_field_definitions(
            detected,
            page_count=1,
            normalize_font_size=True,
            font_size_pt=10,
            auto_size_name_address=True,
        )
        normalized_auto = build_normalized_pdf_field_definitions(
            detected,
            page_count=1,
            normalize_font_size=False,
            auto_size_name_address=True,
        )

        self.assertEqual(normalized_fixed[0]["fontSize"], 10)
        self.assertFalse(normalized_fixed[0]["autoSize"])
        self.assertEqual(normalized_auto[0]["fontSize"], 12)
        self.assertTrue(normalized_auto[0]["autoSize"])
        self.assertEqual(normalized_auto[0]["height"], 14)


if __name__ == "__main__":
    unittest.main()
