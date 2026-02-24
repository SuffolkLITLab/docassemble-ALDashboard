import unittest
import base64
from unittest.mock import patch

from docassemble.ALDashboard.api_dashboard_utils import (
    DEFAULT_MAX_UPLOAD_BYTES,
    DashboardAPIValidationError,
    _validate_upload_size,
    autolabel_payload_from_options,
    build_openapi_spec,
    coerce_async_flag,
    decode_base64_content,
    docx_runs_payload_from_options,
    interview_lint_payload_from_options,
    parse_bool,
    relabel_payload_from_options,
    validate_docx_payload_from_options,
    yaml_check_payload_from_options,
    yaml_reformat_payload_from_options,
)


class TestDashboardAPIUtils(unittest.TestCase):
    def test_parse_bool_accepts_common_values(self):
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("YES"))
        self.assertFalse(parse_bool("0"))
        self.assertFalse(parse_bool("off"))

    def test_parse_bool_rejects_invalid(self):
        with self.assertRaises(DashboardAPIValidationError):
            parse_bool("not-a-bool")

    def test_decode_base64_content_validation(self):
        self.assertEqual(decode_base64_content("YQ=="), b"a")
        with self.assertRaises(DashboardAPIValidationError):
            decode_base64_content("")
        with self.assertRaises(DashboardAPIValidationError):
            decode_base64_content("%%%")

    def test_coerce_async_flag(self):
        self.assertTrue(coerce_async_flag({"mode": "async"}))
        self.assertFalse(coerce_async_flag({"mode": "sync"}))
        self.assertTrue(coerce_async_flag({"async": "true"}))
        self.assertFalse(coerce_async_flag({}))
        with self.assertRaises(DashboardAPIValidationError):
            coerce_async_flag({"mode": "later"})

    def test_validate_upload_size(self):
        _validate_upload_size(b"x")
        with self.assertRaises(DashboardAPIValidationError):
            _validate_upload_size(b"")
        with self.assertRaises(DashboardAPIValidationError):
            _validate_upload_size(b"x" * (DEFAULT_MAX_UPLOAD_BYTES + 1))

    @patch("docassemble.ALDashboard.docx_wrangling.get_labeled_docx_runs")
    def test_autolabel_supports_prompt_customization_fields(self, mock_get_labeled):
        mock_get_labeled.return_value = [[0, 0, "{{ users[0] }}", 0]]
        payload = autolabel_payload_from_options(
            {
                "filename": "sample.docx",
                "file_content_base64": base64.b64encode(b"docx-bytes").decode("ascii"),
                "custom_prompt": "Use concise labels.",
                "additional_instructions": "Prefer landlord/tenant naming.",
                "max_output_tokens": "9000",
            }
        )
        self.assertEqual(payload["results"], [[0, 0, "{{ users[0] }}", 0]])
        self.assertTrue(mock_get_labeled.called)
        kwargs = mock_get_labeled.call_args.kwargs
        self.assertEqual(kwargs["custom_prompt"], "Use concise labels.")
        self.assertEqual(
            kwargs["additional_instructions"], "Prefer landlord/tenant naming."
        )
        self.assertEqual(kwargs["max_output_tokens"], 9000)

    def test_autolabel_rejects_invalid_max_output_tokens(self):
        with self.assertRaises(DashboardAPIValidationError):
            autolabel_payload_from_options(
                {
                    "filename": "sample.docx",
                    "file_content_base64": base64.b64encode(b"docx-bytes").decode(
                        "ascii"
                    ),
                    "max_output_tokens": "not-a-number",
                }
            )
        with self.assertRaises(DashboardAPIValidationError):
            autolabel_payload_from_options(
                {
                    "filename": "sample.docx",
                    "file_content_base64": base64.b64encode(b"docx-bytes").decode(
                        "ascii"
                    ),
                    "max_output_tokens": "0",
                }
            )

    def test_relabel_replace_skip_and_add_by_index(self):
        payload = relabel_payload_from_options(
            {
                "results": [
                    [0, 0, "{{ old_0 }}", 0],
                    [1, 0, "{{ old_1 }}", 0],
                    [2, 0, "{{ old_2 }}", 0],
                ],
                "replace_labels_by_index": {"1": "{{ new_1 }}"},
                "skip_label_indexes": [0],
                "add_labels": [[5, 0, "{{ added_label }}", 0]],
            }
        )
        self.assertEqual(
            payload["results"],
            [
                [1, 0, "{{ new_1 }}", 0],
                [2, 0, "{{ old_2 }}", 0],
                [5, 0, "{{ added_label }}", 0],
            ],
        )

    def test_relabel_add_rules_requires_file(self):
        with self.assertRaises(DashboardAPIValidationError):
            relabel_payload_from_options(
                {
                    "results": [[0, 0, "{{ a }}", 0]],
                    "add_label_rules": [
                        {
                            "paragraph_start": 0,
                            "paragraph_end": 5,
                            "replacement": "{{ b }}",
                        }
                    ],
                }
            )

    @patch("docassemble.ALDashboard.docx_wrangling.get_docx_run_items")
    def test_relabel_add_rules_by_paragraph_range(self, mock_get_docx_runs):
        mock_get_docx_runs.return_value = [
            [0, 0, "Intro"],
            [1, 0, "Tenant Name: ____"],
            [2, 0, "Unrelated"],
        ]
        payload = relabel_payload_from_options(
            {
                "filename": "sample.docx",
                "file_content_base64": base64.b64encode(b"fake-docx").decode("ascii"),
                "results": [],
                "add_label_rules": [
                    {
                        "paragraph_start": 1,
                        "paragraph_end": 1,
                        "contains": "Tenant Name",
                        "replacement": "{{ users[0].name.full() }}",
                    }
                ],
            }
        )
        self.assertEqual(payload["results"], [[1, 0, "{{ users[0].name.full() }}", 0]])

    @patch("docassemble.ALDashboard.docx_wrangling.get_docx_run_items")
    def test_docx_runs_payload_returns_indexed_runs(self, mock_get_docx_runs):
        mock_get_docx_runs.return_value = [
            [0, 0, "Title"],
            [1, 0, "Dear ____"],
            [1, 1, "Address ____"],
        ]
        payload = docx_runs_payload_from_options(
            {
                "filename": "sample.docx",
                "file_content_base64": base64.b64encode(b"fake-docx").decode("ascii"),
            }
        )
        self.assertEqual(payload["input_filename"], "sample.docx")
        self.assertEqual(payload["paragraph_count"], 2)
        self.assertEqual(payload["run_count"], 3)
        self.assertEqual(payload["results"][1], [1, 0, "Dear ____"])

    @patch("docassemble.ALDashboard.interview_linter.lint_multiple_sources")
    def test_interview_lint_payload_accepts_sources(self, mock_lint):
        mock_lint.return_value = [{"name": "x.yml", "error": None, "result": {}}]
        payload = interview_lint_payload_from_options(
            {
                "include_llm": "true",
                "language": "en",
                "lint_mode": "wcag",
                "sources": [
                    {
                        "name": "x.yml",
                        "token": "ref:docassemble.Example:data/questions/test.yml",
                    }
                ],
            }
        )
        self.assertEqual(payload["count"], 1)
        self.assertTrue(payload["include_llm"])
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["lint_mode"], "wcag-basic")
        self.assertIn("wcag-basic", payload["available_lint_modes"])
        mock_lint.assert_called_once()
        call_kwargs = mock_lint.call_args.kwargs
        self.assertEqual(call_kwargs.get("lint_mode"), "wcag-basic")

    def test_interview_lint_payload_rejects_unknown_lint_mode(self):
        with self.assertRaises(DashboardAPIValidationError):
            interview_lint_payload_from_options(
                {
                    "lint_mode": "unknown-mode",
                    "sources": [
                        {
                            "name": "x.yml",
                            "token": "ref:docassemble.Example:data/questions/test.yml",
                        }
                    ],
                }
            )

    def test_interview_lint_payload_requires_any_source(self):
        with self.assertRaises(DashboardAPIValidationError):
            interview_lint_payload_from_options({})

    @patch("docassemble.ALDashboard.api_dashboard_utils._run_dayaml_checker")
    def test_yaml_check_payload_classifies_warning_and_error(self, mock_dayaml):
        class _Issue:
            def __init__(self, err_str, line_number, file_name, experimental=True):
                self.err_str = err_str
                self.line_number = line_number
                self.file_name = file_name
                self.experimental = experimental

        mock_dayaml.return_value = [
            _Issue(
                "validation code does not call validation_error(); consider calling validation_error(...) to provide user-facing error messages",
                4,
                "sample.yml",
            ),
            _Issue("Keys that shouldn't exist! ['bad key']", 2, "sample.yml", False),
        ]
        payload = yaml_check_payload_from_options(
            {"yaml_text": "question: hi", "filename": "sample.yml"}
        )
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["warning_count"], 1)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertEqual(len(payload["errors"]), 1)

    @patch(
        "docassemble.ALDashboard.validate_docx.detect_docx_automation_features",
        return_value={
            "warnings": ["Structured Document Tags (content controls, w:sdt) detected."],
            "warning_details": [
                {
                    "code": "structured_document_tags",
                    "severity": "medium",
                    "message": "Structured Document Tags (content controls, w:sdt) detected.",
                    "count": 1,
                    "evidence": ["word/document.xml"],
                }
            ],
        },
    )
    @patch("docassemble.ALDashboard.validate_docx.get_jinja_errors", return_value=None)
    def test_validate_docx_payload_returns_warnings(
        self, _mock_jinja_errors, _mock_findings
    ):
        payload = validate_docx_payload_from_options(
            {
                "files": [
                    {
                        "filename": "sample.docx",
                        "file_content_base64": base64.b64encode(b"fake-docx").decode(
                            "ascii"
                        ),
                    }
                ]
            }
        )
        self.assertEqual(payload["files"][0]["file"], "sample.docx")
        self.assertEqual(payload["files"][0]["errors"], None)
        self.assertEqual(len(payload["files"][0]["warnings"]), 1)
        self.assertEqual(
            payload["files"][0]["warning_details"][0]["code"],
            "structured_document_tags",
        )

    @patch(
        "docassemble.ALDashboard.validate_docx.detect_docx_automation_features",
        return_value={
            "warnings": ["Heavily fragmented runs detected in visible text paragraphs."],
            "warning_details": [
                {
                    "code": "fragmented_runs",
                    "severity": "low",
                    "message": "Heavily fragmented runs detected in visible text paragraphs.",
                    "count": 1,
                    "evidence": ["word/document.xml"],
                }
            ],
        },
    )
    @patch("docassemble.ALDashboard.validate_docx.get_jinja_errors", return_value=None)
    @patch("docassemble.ALDashboard.validate_docx.strip_docx_problem_controls")
    def test_validate_docx_payload_can_include_stripped_docx(
        self, mock_strip, _mock_jinja_errors, _mock_findings
    ):
        def _fake_strip(_input_path, output_path):
            with open(output_path, "wb") as handle:
                handle.write(b"cleaned-docx-bytes")
            return {"modified": True, "parts_modified": 1, "removed_sdt": 2, "removed_fldSimple": 1}

        mock_strip.side_effect = _fake_strip

        payload = validate_docx_payload_from_options(
            {
                "include_stripped_docx_base64": True,
                "files": [
                    {
                        "filename": "sample.docx",
                        "file_content_base64": base64.b64encode(b"fake-docx").decode(
                            "ascii"
                        ),
                    }
                ],
            }
        )
        item = payload["files"][0]
        self.assertEqual(item["stripped_output_filename"], "stripped_sample.docx")
        self.assertEqual(
            item["stripped_docx_base64"],
            base64.b64encode(b"cleaned-docx-bytes").decode("ascii"),
        )
        self.assertEqual(item["strip_stats"]["removed_sdt"], 2)
        self.assertEqual(item["strip_stats"]["removed_fldSimple"], 1)

    @patch("docassemble.ALDashboard.api_dashboard_utils._run_dayaml_reformat")
    def test_yaml_reformat_payload_returns_formatted_yaml(self, mock_reformat):
        mock_reformat.return_value = ("question: |\n  Hello\n", True)
        payload = yaml_reformat_payload_from_options(
            {
                "yaml_text": "question: |\n    Hello\n",
                "line_length": "99",
                "convert_indent_4_to_2": "true",
            }
        )
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["line_length"], 99)
        self.assertTrue(payload["convert_indent_4_to_2"])
        self.assertEqual(payload["formatted_yaml"], "question: |\n  Hello\n")

    def test_yaml_reformat_rejects_invalid_line_length(self):
        with self.assertRaises(DashboardAPIValidationError):
            yaml_reformat_payload_from_options(
                {"yaml_text": "question: hi", "line_length": "abc"}
            )
        with self.assertRaises(DashboardAPIValidationError):
            yaml_reformat_payload_from_options(
                {"yaml_text": "question: hi", "line_length": "0"}
            )

    def test_openapi_includes_yaml_paths(self):
        spec = build_openapi_spec()
        self.assertIn("/al/api/v1/dashboard/yaml/check", spec["paths"])
        self.assertIn("/al/api/v1/dashboard/yaml/reformat", spec["paths"])


if __name__ == "__main__":
    unittest.main()
