import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docassemble.ALDashboard.pdf_field_labeler import (
    PDFLabelingError,
    apply_formfyxer_pdf_labeling,
    detect_pdf_fields_and_optionally_relabel,
    relabel_existing_pdf_fields,
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

    def test_relabel_existing_with_target_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            def fake_get_existing(_in_file):
                return [
                    [SimpleNamespace(name="old_1"), SimpleNamespace(name="old_2")],
                ]

            def fake_rename(_in_file, out_file, mapping):
                self.assertEqual(mapping, {"old_1": "new_1", "old_2": "new_2"})
                Path(out_file).write_bytes(b"%PDF-relabeled")

            fake_module = SimpleNamespace(
                get_existing_pdf_fields=fake_get_existing,
                rename_pdf_fields=fake_rename,
                parse_form=lambda *_args, **_kwargs: {},
            )
            with patch.dict("sys.modules", {"formfyxer": fake_module}):
                result = relabel_existing_pdf_fields(
                    input_pdf_path=input_path,
                    output_pdf_path=output_path,
                    target_field_names=["new_1", "new_2"],
                )
            self.assertEqual(result["fields"], ["old_1", "old_2"])

    def test_detect_then_relabel_with_target_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            def fake_auto_add(_in_file, out_file):
                Path(out_file).write_bytes(b"%PDF-detected")

            def fake_get_existing(_in_file):
                return [[SimpleNamespace(name="det_1")]]

            def fake_rename(_in_file, out_file, mapping):
                self.assertEqual(mapping, {"det_1": "users[0].name.first"})
                Path(out_file).write_bytes(b"%PDF-relabeled")

            fake_module = SimpleNamespace(
                auto_add_fields=fake_auto_add,
                get_existing_pdf_fields=fake_get_existing,
                rename_pdf_fields=fake_rename,
                parse_form=lambda *_args, **_kwargs: {
                    "total fields": 1,
                    "fields": ["det_1"],
                },
            )
            with patch.dict("sys.modules", {"formfyxer": fake_module}):
                result = detect_pdf_fields_and_optionally_relabel(
                    input_pdf_path=input_path,
                    output_pdf_path=output_path,
                    target_field_names=["users[0].name.first"],
                )
            self.assertIn("post_relabel", result)


if __name__ == "__main__":
    unittest.main()
