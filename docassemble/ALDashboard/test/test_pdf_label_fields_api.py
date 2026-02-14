import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from docassemble.ALDashboard.api_dashboard_utils import (
    DashboardAPIValidationError,
    pdf_label_fields_payload_from_options,
)
from docassemble.ALDashboard.pdf_field_labeler import PDFLabelingError


class TestPDFLabelFieldsAPI(unittest.TestCase):
    def test_requires_file_content(self):
        with self.assertRaises(DashboardAPIValidationError):
            pdf_label_fields_payload_from_options({"filename": "test.pdf"})

    def test_rejects_non_pdf_upload(self):
        with self.assertRaises(DashboardAPIValidationError):
            pdf_label_fields_payload_from_options(
                {
                    "filename": "test.txt",
                    "file_content_base64": base64.b64encode(b"abc").decode("ascii"),
                }
            )

    def test_successful_pdf_labeling_payload(self):
        def fake_labeler(**kwargs):
            Path(kwargs["output_pdf_path"]).write_bytes(b"%PDF-labeled")
            return {"total fields": 2, "fields": ["name", "address"], "extra": "ok"}

        with patch(
            "docassemble.ALDashboard.pdf_field_labeler.apply_formfyxer_pdf_labeling",
            side_effect=fake_labeler,
        ):
            payload = pdf_label_fields_payload_from_options(
                {
                    "filename": "test.pdf",
                    "file_content_base64": base64.b64encode(b"%PDF-input").decode(
                        "ascii"
                    ),
                    "include_pdf_base64": "true",
                    "include_parse_stats": "true",
                }
            )

        self.assertEqual(payload["input_filename"], "test.pdf")
        self.assertEqual(payload["output_filename"], "labeled_test.pdf")
        self.assertEqual(payload["field_count"], 2)
        self.assertEqual(payload["fields"], ["name", "address"])
        self.assertIn("parse_stats", payload)
        self.assertEqual(base64.b64decode(payload["pdf_base64"]), b"%PDF-labeled")

    def test_omits_optional_outputs(self):
        def fake_labeler(**kwargs):
            Path(kwargs["output_pdf_path"]).write_bytes(b"%PDF-labeled")
            return {"total fields": 1, "fields": ["name"]}

        with patch(
            "docassemble.ALDashboard.pdf_field_labeler.apply_formfyxer_pdf_labeling",
            side_effect=fake_labeler,
        ):
            payload = pdf_label_fields_payload_from_options(
                {
                    "filename": "test.pdf",
                    "file_content_base64": base64.b64encode(b"%PDF-input").decode(
                        "ascii"
                    ),
                    "include_pdf_base64": "false",
                    "include_parse_stats": "false",
                }
            )

        self.assertNotIn("pdf_base64", payload)
        self.assertNotIn("parse_stats", payload)

    def test_translates_pdf_labeling_error(self):
        with patch(
            "docassemble.ALDashboard.pdf_field_labeler.apply_formfyxer_pdf_labeling",
            side_effect=PDFLabelingError("broken"),
        ):
            with self.assertRaises(DashboardAPIValidationError) as cm:
                pdf_label_fields_payload_from_options(
                    {
                        "filename": "test.pdf",
                        "file_content_base64": base64.b64encode(b"%PDF-input").decode(
                            "ascii"
                        ),
                    }
                )
        self.assertIn("broken", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
