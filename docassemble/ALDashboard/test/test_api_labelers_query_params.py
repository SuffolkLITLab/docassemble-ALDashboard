# do not pre-load
import json
import subprocess
import sys
import textwrap
import unittest

_STUBBED_IMPORT_PREFIX = textwrap.dedent("""
    import importlib
    import json
    import sys
    import types
    from flask import Flask

    fake_app = Flask("api_labelers_test")

    app_object_module = types.ModuleType("docassemble.webapp.app_object")
    app_object_module.app = fake_app
    app_object_module.csrf = types.SimpleNamespace(exempt=lambda func: func)

    class _FakePipeline:
        def set(self, *args, **kwargs):
            return self

        def expire(self, *args, **kwargs):
            return self

        def execute(self):
            return None

    class _FakeRedis:
        def get(self, *args, **kwargs):
            return None

        def pipeline(self):
            return _FakePipeline()

    server_module = types.ModuleType("docassemble.webapp.server")
    server_module.api_verify = lambda: False
    server_module.jsonify_with_status = lambda body, status: (body, status)
    server_module.r = _FakeRedis()

    worker_common_module = types.ModuleType("docassemble.webapp.worker_common")
    worker_common_module.workerapp = types.SimpleNamespace(
        AsyncResult=lambda *args, **kwargs: None
    )

    base_config_module = types.ModuleType("docassemble.base.config")
    base_config_module.daconfig = {}

    base_functions_module = types.ModuleType("docassemble.base.functions")
    base_functions_module.this_thread = types.SimpleNamespace(current_info={})

    base_util_module = types.ModuleType("docassemble.base.util")
    base_util_module.log = lambda *args, **kwargs: None

    sys.modules["docassemble.webapp.app_object"] = app_object_module
    sys.modules["docassemble.webapp.server"] = server_module
    sys.modules["docassemble.webapp.worker_common"] = worker_common_module
    sys.modules["docassemble.base.config"] = base_config_module
    sys.modules["docassemble.base.functions"] = base_functions_module
    sys.modules["docassemble.base.util"] = base_util_module
    sys.modules.pop("docassemble.ALDashboard.api_labelers", None)

    module = importlib.import_module("docassemble.ALDashboard.api_labelers")
    app = app_object_module.app
    """)


class TestLabelerQueryParams(unittest.TestCase):
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
        stdout = result.stdout.strip()
        self.assertTrue(stdout, msg=f"stderr:\n{result.stderr}")
        return json.loads(stdout)

    def test_parse_initial_playground_source_accepts_filename_spaces(self):
        result = self._run_probe("""
            print(json.dumps(module._parse_initial_playground_source(
                \"demo-project\",
                \"Template With Spaces.docx\",
                allowed_extensions=(\".docx\",),
            )))
            """)
        self.assertEqual(
            result,
            {
                "project": "demo-project",
                "filename": "Template With Spaces.docx",
            },
        )

    def test_parse_initial_playground_source_defaults_project(self):
        result = self._run_probe("""
            print(json.dumps(module._parse_initial_playground_source(
                \"\",
                \"intake form.pdf\",
                allowed_extensions=(\".pdf\",),
            )))
            """)
        self.assertEqual(
            result,
            {
                "project": "default",
                "filename": "intake form.pdf",
            },
        )

    def test_parse_initial_playground_source_rejects_wrong_extension(self):
        result = self._run_probe("""
            try:
                module._parse_initial_playground_source(
                    \"demo-project\",
                    \"wrong-extension.txt\",
                    allowed_extensions=(\".pdf\",),
                )
            except Exception as exc:
                print(json.dumps({
                    \"type\": exc.__class__.__name__,
                    \"message\": getattr(exc, \"message\", str(exc)),
                }))
            else:
                raise AssertionError(\"Expected validation error\")
            """)
        self.assertEqual(result["type"], "DashboardAPIValidationError")

    def test_parse_initial_playground_source_rejects_invalid_project(self):
        result = self._run_probe("""
            try:
                module._parse_initial_playground_source(
                    \"../bad-project\",
                    \"sample.pdf\",
                    allowed_extensions=(\".pdf\",),
                )
            except Exception as exc:
                print(json.dumps({
                    \"type\": exc.__class__.__name__,
                    \"message\": getattr(exc, \"message\", str(exc)),
                }))
            else:
                raise AssertionError(\"Expected validation error\")
            """)
        self.assertEqual(result["type"], "DashboardAPIValidationError")

    def test_parse_initial_playground_source_keeps_project_without_filename(self):
        result = self._run_probe("""
            print(json.dumps(module._parse_initial_playground_source(
                \"demo-project\",
                \"\",
                allowed_extensions=(\".docx\",),
            )))
            """)
        self.assertEqual(result, {"project": "demo-project"})

    def test_request_query_params_are_url_decoded_for_pdf(self):
        result = self._run_probe("""
            with app.test_request_context(
                \"/al/pdf-labeler?project=demo-project&filename=My%20Form%20v2.pdf\"
            ):
                payload = module._labeler_initial_playground_source_from_request(
                    allowed_extensions=(\".pdf\",)
                )
            print(json.dumps(payload))
            """)
        self.assertEqual(
            result,
            {
                "project": "demo-project",
                "filename": "My Form v2.pdf",
            },
        )

    def test_docx_bootstrap_includes_initial_playground_source(self):
        result = self._run_probe("""
            with app.test_request_context(
                \"/al/docx-labeler?project=demo-project&filename=Family+Intake.docx\"
            ):
                payload = module._build_docx_labeler_bootstrap()
            print(json.dumps(payload))
            """)
        self.assertEqual(
            result["initialPlaygroundSource"],
            {
                "project": "demo-project",
                "filename": "Family Intake.docx",
            },
        )

    def test_pdf_bootstrap_ignores_invalid_filename_extension(self):
        result = self._run_probe("""
            with app.test_request_context(
                \"/al/pdf-labeler?project=demo-project&filename=not-a-pdf.docx\"
            ):
                payload = module._build_pdf_labeler_bootstrap()
            print(json.dumps(payload))
            """)
        self.assertEqual(result["initialPlaygroundSource"], {})

    def test_render_template_content_escapes_script_breakout_sequences(self):
        result = self._run_probe("""
            module._get_template_content = lambda _filename: '<script type=\"application/json\">__LABELER_BOOTSTRAP_JSON__</script>'
            rendered = module._render_template_content(
                \"ignored.html\",
                bootstrap_data={
                    \"initialPlaygroundSource\": {
                        \"project\": \"demo-project\",
                        \"filename\": \"</script><script>alert(1)</script>.pdf\",
                    }
                },
            )
            print(json.dumps({\"rendered\": rendered}))
            """)
        self.assertIn("\\u003c/script\\u003e", result["rendered"])
        self.assertNotIn("</script><script>", result["rendered"])

    def test_name_address_phone_email_heuristic_matches_expected_names(self):
        result = self._run_probe("""
            print(json.dumps({
                "name": module._looks_like_name_email_address_phone_field("users[0].name.first"),
                "email": module._looks_like_name_email_address_phone_field("users[0].email"),
                "phone": module._looks_like_name_email_address_phone_field("users[0].phone_number"),
                "address": module._looks_like_name_email_address_phone_field("users[0].address.city"),
                "other": module._looks_like_name_email_address_phone_field("users[0].income.monthly"),
            }))
            """)
        self.assertEqual(
            result,
            {
                "name": True,
                "email": True,
                "phone": True,
                "address": True,
                "other": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
