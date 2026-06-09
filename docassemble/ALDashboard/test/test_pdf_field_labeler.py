# do not pre-load
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

    def test_relabel_existing_with_ai_applies_mapping_via_rename_helper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            state = {"renamed": False}

            def fake_get_existing(in_file):
                if str(in_file) == output_path and state["renamed"]:
                    return [
                        [
                            SimpleNamespace(name="users[0].name.first"),
                            SimpleNamespace(name="users[0].name.last"),
                        ],
                    ]
                return [
                    [
                        SimpleNamespace(name="field_a"),
                        SimpleNamespace(name="field_b"),
                    ],
                ]

            def fake_rename_with_context(
                *, pdf_path, original_field_names, api_key, model, openai_base_url
            ):
                self.assertEqual(pdf_path, input_path)
                self.assertEqual(original_field_names, ["field_a", "field_b"])
                self.assertIsNone(api_key)
                self.assertIsNone(openai_base_url)
                self.assertEqual(model, "gpt-5-nano")
                return {
                    "field_a": "users[0].name.first",
                    "field_b": "users[0].name.last",
                }

            def fake_rename(_in_file, out_file, mapping):
                self.assertEqual(
                    mapping,
                    {
                        "field_a": "users[0].name.first",
                        "field_b": "users[0].name.last",
                    },
                )
                state["renamed"] = True
                Path(out_file).write_bytes(b"%PDF-relabeled")

            fake_module = SimpleNamespace(
                get_existing_pdf_fields=fake_get_existing,
                rename_pdf_fields_with_context=fake_rename_with_context,
                rename_pdf_fields=fake_rename,
            )
            with (
                patch.dict("sys.modules", {"formfyxer": fake_module}),
                patch(
                    "docassemble.ALDashboard.pdf_field_labeler._generate_ai_relabel_target_field_names",
                    side_effect=AssertionError("ordered AI path should not be used"),
                ),
                patch(
                    "docassemble.ALDashboard.pdf_field_labeler._rewrite_pdf_fields_in_order",
                    side_effect=AssertionError(
                        "ordered rewrite path should not be used"
                    ),
                ),
            ):
                result = relabel_existing_pdf_fields(
                    input_pdf_path=input_path,
                    output_pdf_path=output_path,
                    relabel_with_ai=True,
                )

            self.assertEqual(
                result["fields"],
                ["users[0].name.first", "users[0].name.last"],
            )
            self.assertEqual(result["renamed fields"], 2)

    def test_relabel_existing_with_ai_uses_prompt_marker_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            state = {"renamed": False}

            def fake_get_existing(in_file):
                if str(in_file) == output_path and state["renamed"]:
                    return [
                        [
                            SimpleNamespace(name="defendant_name"),
                            SimpleNamespace(name="father_name"),
                        ],
                    ]
                return [
                    [
                        SimpleNamespace(name="father_name"),
                        SimpleNamespace(name="defendant_name"),
                    ],
                ]

            def fake_generate(
                _formfyxer_module,
                *,
                input_pdf_path,
                current_names,
                pdf_text_with_fields,
                openai_api,
                openai_base_url,
                model,
            ):
                self.assertEqual(input_pdf_path, input_path)
                self.assertEqual(
                    current_names,
                    ["defendant_name", "father_name"],
                )
                self.assertIn("{{defendant_name}}", pdf_text_with_fields)
                self.assertIn("{{father_name}}", pdf_text_with_fields)
                self.assertIsNone(openai_api)
                self.assertIsNone(openai_base_url)
                self.assertIsNone(model)
                return ["defendant_name", "father_name"]

            def fake_rewrite(
                _formfyxer_module,
                *,
                input_pdf_path,
                output_pdf_path,
                current_names,
                target_field_names,
            ):
                self.assertEqual(input_pdf_path, input_path)
                self.assertEqual(output_pdf_path, output_path)
                self.assertEqual(
                    current_names,
                    ["defendant_name", "father_name"],
                )
                self.assertEqual(
                    target_field_names,
                    ["defendant_name", "father_name"],
                )
                state["renamed"] = True
                Path(output_pdf_path).write_bytes(b"%PDF-relabeled")

            fake_module = SimpleNamespace(
                get_existing_pdf_fields=fake_get_existing,
                get_original_text_with_fields=lambda _pdf, out_path: Path(
                    out_path
                ).write_text(
                    "Top {{defendant_name}} then {{father_name}}",
                    encoding="utf-8",
                ),
            )
            with (
                patch.dict("sys.modules", {"formfyxer": fake_module}),
                patch(
                    "docassemble.ALDashboard.pdf_field_labeler._generate_ai_relabel_target_field_names",
                    side_effect=fake_generate,
                ),
                patch(
                    "docassemble.ALDashboard.pdf_field_labeler._rewrite_pdf_fields_in_order",
                    side_effect=fake_rewrite,
                ),
            ):
                result = relabel_existing_pdf_fields(
                    input_pdf_path=input_path,
                    output_pdf_path=output_path,
                    relabel_with_ai=True,
                )

            self.assertEqual(
                result["fields"],
                ["defendant_name", "father_name"],
            )

    def test_relabel_existing_with_ai_handles_duplicate_source_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = str(Path(tmpdir) / "input.pdf")
            output_path = str(Path(tmpdir) / "output.pdf")
            Path(input_path).write_bytes(b"%PDF-input")

            state = {"renamed": False}

            def fake_get_existing(in_file):
                if str(in_file) == output_path and state["renamed"]:
                    return [
                        [
                            SimpleNamespace(name="docket_number"),
                            SimpleNamespace(name="docket_number__2"),
                        ],
                    ]
                return [
                    [
                        SimpleNamespace(name="field_dup"),
                        SimpleNamespace(name="field_dup"),
                    ],
                ]

            def fake_rewrite(
                _formfyxer_module,
                *,
                input_pdf_path,
                output_pdf_path,
                current_names,
                target_field_names,
            ):
                self.assertEqual(input_pdf_path, input_path)
                self.assertEqual(output_pdf_path, output_path)
                self.assertEqual(current_names, ["field_dup", "field_dup"])
                self.assertEqual(
                    target_field_names,
                    ["docket_number", "docket_number__2"],
                )
                state["renamed"] = True
                Path(output_pdf_path).write_bytes(b"%PDF-relabeled")

            fake_module = SimpleNamespace(
                get_existing_pdf_fields=fake_get_existing,
                get_original_text_with_fields=lambda _pdf, out_path: Path(
                    out_path
                ).write_text(
                    "{{field_dup}} {{field_dup}}",
                    encoding="utf-8",
                ),
            )
            with (
                patch.dict("sys.modules", {"formfyxer": fake_module}),
                patch(
                    "docassemble.ALDashboard.pdf_field_labeler._generate_ai_relabel_target_field_names",
                    return_value=["docket_number", "docket_number__2"],
                ),
                patch(
                    "docassemble.ALDashboard.pdf_field_labeler._rewrite_pdf_fields_in_order",
                    side_effect=fake_rewrite,
                ),
            ):
                result = relabel_existing_pdf_fields(
                    input_pdf_path=input_path,
                    output_pdf_path=output_path,
                    relabel_with_ai=True,
                )

            self.assertEqual(
                result["fields"],
                ["docket_number", "docket_number__2"],
            )
            self.assertEqual(result["renamed fields"], 2)


if __name__ == "__main__":
    unittest.main()
