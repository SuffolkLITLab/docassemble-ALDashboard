# do not pre-load
"""The accessibility workshop endpoints round-trip a PDF through one repair."""

import json
import os
import subprocess
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_api_labelers_query_params import _STUBBED_IMPORT_PREFIX  # noqa: E402

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "civil_docketing_statement_polished_repaired.pdf"
)


class TestAccessibilityWorkshopEndpoints(unittest.TestCase):
    def _run_probe(self, probe_code: str):
        script = _STUBBED_IMPORT_PREFIX + "\n" + textwrap.dedent(probe_code)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def _post(self, route: str, form: dict):
        return self._run_probe(f"""
            import base64, io
            client = app.test_client()
            form = {form!r}
            with open({_FIXTURE!r}, "rb") as handle:
                form["file"] = (io.BytesIO(handle.read()), "form.pdf")
            response = client.post({route!r}, data=form, content_type="multipart/form-data")
            body = response.get_json()
            data = body.get("data") or {{}}
            if "pdf_base64" in data:
                data["pdf_starts_with"] = base64.b64decode(data.pop("pdf_base64"))[:5].decode()
            print(json.dumps({{"status": response.status_code, "body": body}}))
            """)

    def test_draft_structure_returns_a_repaired_pdf_and_its_result(self):
        result = self._post(
            "/al/pdf-labeler/api/accessibility-remediate",
            {"action": "draft_structure", "image_decisions": "[]"},
        )
        self.assertEqual(result["status"], 200)
        data = result["body"]["data"]
        self.assertEqual(data["pdf_starts_with"], "%PDF-")
        self.assertEqual(data["filename"], "form-accessible.pdf")
        self.assertGreater(data["remediation_result"]["widgets_tagged"], 0)

    def test_metadata_writes_title_and_language(self):
        result = self._post(
            "/al/pdf-labeler/api/accessibility-remediate",
            {
                "action": "metadata",
                "metadata": json.dumps({"title": "Docketing statement", "language": "en-US"}),
            },
        )
        self.assertEqual(result["status"], 200)
        self.assertGreater(result["body"]["data"]["remediation_result"]["metadata_updates"], 0)

    def test_an_unknown_action_is_a_validation_error(self):
        result = self._post(
            "/al/pdf-labeler/api/accessibility-remediate", {"action": "certify"}
        )
        self.assertEqual(result["status"], 400)
        self.assertEqual(result["body"]["error"]["type"], "validation_error")

    def test_malformed_decisions_are_a_validation_error(self):
        result = self._post(
            "/al/pdf-labeler/api/accessibility-remediate",
            {"action": "draft_structure", "image_decisions": "{\"not\": \"a list\"}"},
        )
        self.assertEqual(result["status"], 400)

    def test_ai_endpoints_require_a_signed_in_user(self):
        result = self._run_probe("""
            client = app.test_client()
            response = client.post(
                "/al/pdf-labeler/api/accessibility-ai-review", json={"context": {}}
            )
            print(json.dumps({"status": response.status_code}))
            """)
        self.assertEqual(result["status"], 401)


if __name__ == "__main__":
    unittest.main()
