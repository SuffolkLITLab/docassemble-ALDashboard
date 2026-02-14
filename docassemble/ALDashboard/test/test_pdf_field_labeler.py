import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docassemble.ALDashboard.pdf_field_labeler import (
    PDFLabelingError,
    apply_formfyxer_pdf_labeling,
)


class TestPDFFieldLabeler(unittest.TestCase):
    def test_adds_and_normalizes_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            def fake_auto_add_fields(in_file, out_file):
                Path(out_file).write_bytes(b"%PDF-added")

            fake_module = SimpleNamespace(
                auto_add_fields=fake_auto_add_fields,
                parse_form=lambda *_args, **_kwargs: {"total fields": 3},
            )

            with patch.dict("sys.modules", {"formfyxer": fake_module}):
                result = apply_formfyxer_pdf_labeling(
                    input_pdf_path=input_path,
                    output_pdf_path=output_path,
                    add_fields=True,
                    normalize_fields=True,
                )

            self.assertEqual(result["total fields"], 3)
            self.assertEqual(Path(output_path).read_bytes(), b"%PDF-added")

    def test_no_normalization_skips_parse_form(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            called = {"parse_form": False}

            def fake_auto_add_fields(in_file, out_file):
                Path(out_file).write_bytes(b"%PDF-added")

            def fake_parse_form(*_args, **_kwargs):
                called["parse_form"] = True
                return {}

            fake_module = SimpleNamespace(
                auto_add_fields=fake_auto_add_fields,
                parse_form=fake_parse_form,
            )

            with patch.dict("sys.modules", {"formfyxer": fake_module}):
                result = apply_formfyxer_pdf_labeling(
                    input_pdf_path=input_path,
                    output_pdf_path=output_path,
                    normalize_fields=False,
                )

            self.assertEqual(result, {})
            self.assertFalse(called["parse_form"])

    def test_raises_if_output_not_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            fake_module = SimpleNamespace(
                auto_add_fields=lambda *_args, **_kwargs: None,
                parse_form=lambda *_args, **_kwargs: {},
            )

            with patch.dict("sys.modules", {"formfyxer": fake_module}):
                with self.assertRaises(PDFLabelingError):
                    apply_formfyxer_pdf_labeling(
                        input_pdf_path=input_path,
                        output_pdf_path=output_path,
                    )


if __name__ == "__main__":
    unittest.main()
