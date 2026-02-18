import unittest
import base64
from unittest.mock import patch

from docassemble.ALDashboard.api_dashboard_utils import (
    DEFAULT_MAX_UPLOAD_BYTES,
    DashboardAPIValidationError,
    _validate_upload_size,
    autolabel_payload_from_options,
    coerce_async_flag,
    decode_base64_content,
    docx_runs_payload_from_options,
    interview_lint_payload_from_options,
    parse_bool,
    relabel_payload_from_options,
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
        self.assertTrue(mock_lint.called)

    def test_interview_lint_payload_requires_any_source(self):
        with self.assertRaises(DashboardAPIValidationError):
            interview_lint_payload_from_options({})


if __name__ == "__main__":
    unittest.main()
