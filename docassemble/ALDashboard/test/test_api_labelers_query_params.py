import unittest
from unittest.mock import patch

from docassemble.ALDashboard.api_dashboard_utils import DashboardAPIValidationError
from docassemble.ALDashboard.api_labelers import (
    _build_docx_labeler_bootstrap,
    _build_pdf_labeler_bootstrap,
    _labeler_initial_playground_source_from_request,
    _parse_initial_playground_source,
    _render_template_content,
    app,
)


class TestLabelerQueryParams(unittest.TestCase):
    def test_parse_initial_playground_source_accepts_filename_spaces(self):
        source = _parse_initial_playground_source(
            "demo-project",
            "Template With Spaces.docx",
            allowed_extensions=(".docx",),
        )
        self.assertEqual(
            source,
            {
                "project": "demo-project",
                "filename": "Template With Spaces.docx",
            },
        )

    def test_parse_initial_playground_source_defaults_project(self):
        source = _parse_initial_playground_source(
            "",
            "intake form.pdf",
            allowed_extensions=(".pdf",),
        )
        self.assertEqual(
            source,
            {
                "project": "default",
                "filename": "intake form.pdf",
            },
        )

    def test_parse_initial_playground_source_rejects_wrong_extension(self):
        with self.assertRaises(DashboardAPIValidationError):
            _parse_initial_playground_source(
                "demo-project",
                "wrong-extension.txt",
                allowed_extensions=(".pdf",),
            )

    def test_parse_initial_playground_source_rejects_invalid_project(self):
        with self.assertRaises(DashboardAPIValidationError):
            _parse_initial_playground_source(
                "../bad-project",
                "sample.pdf",
                allowed_extensions=(".pdf",),
            )

    def test_parse_initial_playground_source_keeps_project_without_filename(self):
        source = _parse_initial_playground_source(
            "demo-project",
            "",
            allowed_extensions=(".docx",),
        )
        self.assertEqual(source, {"project": "demo-project"})

    def test_request_query_params_are_url_decoded_for_pdf(self):
        with app.test_request_context(
            "/al/pdf-labeler?project=demo-project&filename=My%20Form%20v2.pdf"
        ):
            source = _labeler_initial_playground_source_from_request(
                allowed_extensions=(".pdf",)
            )
        self.assertEqual(
            source,
            {
                "project": "demo-project",
                "filename": "My Form v2.pdf",
            },
        )

    def test_docx_bootstrap_includes_initial_playground_source(self):
        with app.test_request_context(
            "/al/docx-labeler?project=demo-project&filename=Family+Intake.docx"
        ):
            bootstrap = _build_docx_labeler_bootstrap()
        self.assertEqual(
            bootstrap["initialPlaygroundSource"],
            {
                "project": "demo-project",
                "filename": "Family Intake.docx",
            },
        )

    def test_pdf_bootstrap_ignores_invalid_filename_extension(self):
        with app.test_request_context(
            "/al/pdf-labeler?project=demo-project&filename=not-a-pdf.docx"
        ):
            bootstrap = _build_pdf_labeler_bootstrap()
        self.assertEqual(bootstrap["initialPlaygroundSource"], {})

    def test_render_template_content_escapes_script_breakout_sequences(self):
        with patch(
            "docassemble.ALDashboard.api_labelers._get_template_content",
            return_value='<script type="application/json">__LABELER_BOOTSTRAP_JSON__</script>',
        ):
            rendered = _render_template_content(
                "ignored.html",
                bootstrap_data={
                    "initialPlaygroundSource": {
                        "project": "demo-project",
                        "filename": "</script><script>alert(1)</script>.pdf",
                    }
                },
            )

        self.assertIn("\\u003c/script\\u003e", rendered)
        self.assertNotIn("</script><script>", rendered)


if __name__ == "__main__":
    unittest.main()
