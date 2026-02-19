import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from docassemble.ALDashboard.api_dashboard_utils import (
    DashboardAPIValidationError,
    pdf_fields_detect_payload_from_options,
    pdf_fields_relabel_payload_from_options,
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

    def test_detect_accepts_target_field_names(self):
        def fake_detect(**kwargs):
            self.assertEqual(
                kwargs["target_field_names"],
                ["users[0].name.first", "users[0].name.last"],
            )
            Path(kwargs["output_pdf_path"]).write_bytes(b"%PDF-labeled")
            return {"total fields": 2, "fields": kwargs["target_field_names"]}

        with patch(
            "docassemble.ALDashboard.pdf_field_labeler.detect_pdf_fields_and_optionally_relabel",
            side_effect=fake_detect,
        ):
            payload = pdf_fields_detect_payload_from_options(
                {
                    "filename": "test.pdf",
                    "file_content_base64": base64.b64encode(b"%PDF-input").decode(
                        "ascii"
                    ),
                    "target_field_names": ["users[0].name.first", "users[0].name.last"],
                }
            )
        self.assertEqual(payload["field_count"], 2)

    def test_relabel_accepts_mapping(self):
        def fake_relabel(**kwargs):
            self.assertEqual(kwargs["field_name_mapping"], {"old1": "new1"})
            Path(kwargs["output_pdf_path"]).write_bytes(b"%PDF-relabeled")
            return {"total fields": 1, "fields_old": ["old1"], "fields": ["new1"]}

        with patch(
            "docassemble.ALDashboard.pdf_field_labeler.relabel_existing_pdf_fields",
            side_effect=fake_relabel,
        ):
            payload = pdf_fields_relabel_payload_from_options(
                {
                    "filename": "test.pdf",
                    "file_content_base64": base64.b64encode(b"%PDF-input").decode(
                        "ascii"
                    ),
                    "field_name_mapping": {"old1": "new1"},
                }
            )
        self.assertEqual(payload["fields"], ["new1"])


if __name__ == "__main__":
    unittest.main()
