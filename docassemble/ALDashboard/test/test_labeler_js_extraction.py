"""Tests to verify JS extraction from labeler HTML templates to static files.

Ensures no regression from moving inline JavaScript out of the DOCX and PDF
labeler HTML templates into dedicated static JS files.
"""

import importlib.resources
import re
import unittest


def _read_package_file(*path_parts: str) -> str:
    """Read a file from the docassemble.ALDashboard package tree."""
    ref = importlib.resources.files("docassemble.ALDashboard")
    for part in path_parts:
        ref = ref / part
    with importlib.resources.as_file(ref) as path:
        return path.read_text(encoding="utf-8")


class TestDocxLabelerJsExtraction(unittest.TestCase):
    """Verify the DOCX labeler JS was correctly extracted to a static file."""

    def setUp(self):
        self.html = _read_package_file("data", "templates", "docx_labeler.html")
        self.js = _read_package_file("data", "static", "docx_labeler.js")

    # -- HTML template checks ------------------------------------------------

    def test_html_references_static_js(self):
        self.assertIn(
            'src="/packagestatic/docassemble.ALDashboard/docx_labeler.js"',
            self.html,
        )

    def test_html_has_no_inline_application_js(self):
        """The only <script> tags should be CDN libs or the static JS src."""
        script_tags = re.findall(r"<script[^>]*>", self.html)
        for tag in script_tags:
            # Every script tag must have a src attribute (no inline JS)
            self.assertIn("src=", tag, f"Found inline <script> tag: {tag}")

    def test_html_still_loads_mammoth_cdn(self):
        self.assertIn("mammoth", self.html)
        self.assertIn("cdnjs.cloudflare.com", self.html)

    def test_html_preserves_body_structure(self):
        self.assertIn('<div id="app"', self.html)
        self.assertIn("</body>", self.html)
        self.assertIn("</html>", self.html)

    # -- Static JS file checks -----------------------------------------------

    def test_js_file_is_nonempty(self):
        self.assertGreater(len(self.js.strip()), 1000)

    def test_js_contains_iife_wrapper(self):
        self.assertIn("(function()", self.js)
        self.assertTrue(self.js.strip().endswith("})();"))

    def test_js_contains_variable_tree(self):
        self.assertIn("AL_VARIABLE_TREE", self.js)
        self.assertIn("PERSON_ATTRIBUTES", self.js)

    def test_js_contains_core_functions(self):
        """Key functions that the DOCX labeler UI depends on."""
        expected = [
            "fetchAuthStatus",
            "fetchModelCatalog",
            "processFile",
            "renderGenerationMethodFields",
        ]
        for fn_name in expected:
            self.assertIn(fn_name, self.js, f"Missing function: {fn_name}")

    def test_js_contains_api_calls(self):
        """Verify API endpoint references are present in the JS."""
        self.assertIn("/docx-labeler/api/", self.js)

    def test_js_references_mammoth(self):
        """The JS should reference the mammoth library loaded from the CDN."""
        self.assertIn("mammoth", self.js)


class TestPdfLabelerJsExtraction(unittest.TestCase):
    """Verify the PDF labeler JS was correctly extracted to a static file."""

    def setUp(self):
        self.html = _read_package_file("data", "templates", "pdf_labeler.html")
        self.js = _read_package_file("data", "static", "pdf_labeler.js")

    # -- HTML template checks ------------------------------------------------

    def test_html_references_static_js(self):
        self.assertIn(
            'src="/packagestatic/docassemble.ALDashboard/pdf_labeler.js"',
            self.html,
        )

    def test_html_loads_js_as_module(self):
        self.assertIn('type="module"', self.html)

    def test_html_has_no_inline_application_js(self):
        """Only CDN scripts, bootstrap JSON data, and static JS src tags."""
        script_tags = re.findall(
            r"<script([^>]*)>(.*?)</script>", self.html, re.DOTALL
        )
        for attrs, body in script_tags:
            body_stripped = body.strip()
            if not body_stripped:
                # Empty body = external src, fine
                continue
            if 'type="application/json"' in attrs:
                # Bootstrap JSON data block, not executable JS
                continue
            self.fail(
                f"Found inline <script> with executable JS:\n"
                f"  attrs: {attrs}\n"
                f"  body (first 120 chars): {body_stripped[:120]}"
            )

    def test_html_preserves_bootstrap_json_placeholder(self):
        self.assertIn("__LABELER_BOOTSTRAP_JSON__", self.html)
        self.assertIn('id="labeler-bootstrap"', self.html)

    def test_html_still_loads_pdf_lib_cdn(self):
        self.assertIn("pdf-lib", self.html)
        self.assertIn("cdnjs.cloudflare.com", self.html)

    def test_html_still_loads_jszip_cdn(self):
        self.assertIn("jszip", self.html)

    def test_html_preserves_body_structure(self):
        self.assertIn("pdf-labeler-body", self.html)
        self.assertIn("</body>", self.html)
        self.assertIn("</html>", self.html)

    # -- Static JS file checks -----------------------------------------------

    def test_js_file_is_nonempty(self):
        self.assertGreater(len(self.js.strip()), 5000)

    def test_js_is_es_module(self):
        """The JS file should use ES module import syntax."""
        self.assertIn("import ", self.js)

    def test_js_imports_pdfjs(self):
        self.assertIn("pdfjsLib", self.js)
        self.assertIn("pdf.min.mjs", self.js)

    def test_js_reads_bootstrap_json(self):
        """JS must read bootstrap config injected by the server."""
        self.assertIn("parseBootstrapJson", self.js)
        self.assertIn("labeler-bootstrap", self.js)
        self.assertIn("LABELER_BOOTSTRAP", self.js)

    def test_js_contains_field_types(self):
        self.assertIn("FIELD_TYPES", self.js)
        for ft in ["text", "multiline", "checkbox", "signature", "radio"]:
            self.assertIn(f"'{ft}'", self.js)

    def test_js_contains_core_functions(self):
        expected = [
            "fetchAuthStatus",
            "fetchModelCatalog",
            "renderFieldsOnPages",
            "updateFieldCount",
            "updateZoomControls",
        ]
        for fn_name in expected:
            self.assertIn(fn_name, self.js, f"Missing function: {fn_name}")

    def test_js_contains_api_calls(self):
        self.assertIn("/pdf-labeler/api/", self.js)

    def test_js_references_PDFLib_global(self):
        """pdf-lib is loaded as a global in the HTML head."""
        self.assertIn("PDFLib", self.js)

    def test_js_references_JSZip_global(self):
        self.assertIn("JSZip", self.js)


class TestLabelerTemplateRendering(unittest.TestCase):
    """Verify that the template-reading helpers still work after refactoring."""

    def test_docx_template_is_valid_html(self):
        html = _read_package_file("data", "templates", "docx_labeler.html")
        self.assertTrue(html.strip().startswith("<!DOCTYPE html>"))
        self.assertIn("<head>", html)
        self.assertIn("</head>", html)
        self.assertIn("<body", html)
        self.assertIn("</body>", html)

    def test_pdf_template_is_valid_html(self):
        html = _read_package_file("data", "templates", "pdf_labeler.html")
        self.assertTrue(html.strip().startswith("<!DOCTYPE html>"))
        self.assertIn("<head>", html)
        self.assertIn("</head>", html)
        self.assertIn("<body", html)
        self.assertIn("</body>", html)

    def test_pdf_bootstrap_json_injection(self):
        """Simulate the server-side bootstrap JSON injection."""
        html = _read_package_file("data", "templates", "pdf_labeler.html")
        rendered = html.replace(
            "__LABELER_BOOTSTRAP_JSON__",
            '{"apiBasePath":"/al","branding":{}}',
        )
        self.assertNotIn("__LABELER_BOOTSTRAP_JSON__", rendered)
        self.assertIn('{"apiBasePath":"/al","branding":{}}', rendered)

    def test_docx_html_does_not_duplicate_js_content(self):
        """The HTML should not contain content from the static JS file."""
        html = _read_package_file("data", "templates", "docx_labeler.html")
        # These are distinctive markers from the JS that should NOT be in HTML
        self.assertNotIn("AL_VARIABLE_TREE", html)
        self.assertNotIn("PERSON_ATTRIBUTES", html)
        self.assertNotIn("fetchAuthStatus", html)

    def test_pdf_html_does_not_duplicate_js_content(self):
        """The HTML should not contain content from the static JS file."""
        html = _read_package_file("data", "templates", "pdf_labeler.html")
        self.assertNotIn("FIELD_TYPES", html)
        self.assertNotIn("renderFieldsOnPages", html)
        self.assertNotIn("fetchAuthStatus", html)


class TestStaticJsFilesExist(unittest.TestCase):
    """Verify that the static JS files are properly packaged."""

    def test_docx_labeler_js_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "docx_labeler.js"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)

    def test_pdf_labeler_js_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "pdf_labeler.js"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)

    def test_docx_labeler_css_still_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "docx_labeler.css"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())

    def test_pdf_labeler_css_still_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "pdf_labeler.css"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
