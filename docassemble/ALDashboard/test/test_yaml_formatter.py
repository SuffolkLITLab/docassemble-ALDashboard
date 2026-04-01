import importlib
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch


def _import_yaml_formatter_with_mocks():
    fake_base_pkg = types.ModuleType("docassemble.base")
    fake_base_pkg.__path__ = []

    fake_util = types.ModuleType("docassemble.base.util")
    fake_util.user_info = lambda: None
    fake_base_pkg.util = fake_util

    fake_webapp_pkg = types.ModuleType("docassemble.webapp")
    fake_webapp_pkg.__path__ = []

    fake_backend = types.ModuleType("docassemble.webapp.backend")
    fake_backend.directory_for = lambda area, project: None

    fake_files = types.ModuleType("docassemble.webapp.files")

    class _ImportTimeSavedFile:
        def __init__(self, *args, **kwargs):
            pass

    fake_files.SavedFile = _ImportTimeSavedFile
    fake_webapp_pkg.backend = fake_backend
    fake_webapp_pkg.files = fake_files

    fake_api_utils = types.ModuleType("docassemble.ALDashboard.api_dashboard_utils")
    fake_api_utils.yaml_reformat_payload_from_options = lambda payload: {
        "formatted_yaml": str(payload.get("yaml_text") or ""),
        "changed": False,
    }

    fake_interview_linter = types.ModuleType("docassemble.ALDashboard.interview_linter")
    fake_interview_linter._resolve_current_user_id = lambda: None

    sys.modules.pop("docassemble.ALDashboard.yaml_formatter", None)
    with patch.dict(
        sys.modules,
        {
            "docassemble.base": fake_base_pkg,
            "docassemble.base.util": fake_util,
            "docassemble.webapp": fake_webapp_pkg,
            "docassemble.webapp.backend": fake_backend,
            "docassemble.webapp.files": fake_files,
            "docassemble.ALDashboard.api_dashboard_utils": fake_api_utils,
            "docassemble.ALDashboard.interview_linter": fake_interview_linter,
        },
    ):
        module = importlib.import_module("docassemble.ALDashboard.yaml_formatter")
    sys.modules["docassemble.ALDashboard.yaml_formatter"] = module
    return module


yaml_formatter = _import_yaml_formatter_with_mocks()


class TestBlackReleaseStatus(unittest.TestCase):
    def test_reports_outdated_black_only_when_newer_release_exists(self):
        with patch.object(
            yaml_formatter.metadata,
            "version",
            return_value="24.1.0",
        ), patch.object(
            yaml_formatter,
            "_fetch_latest_black_version",
            return_value="24.3.0",
        ):
            status = yaml_formatter.get_black_release_status()

        self.assertEqual(status["installed_version"], "24.1.0")
        self.assertEqual(status["latest_version"], "24.3.0")
        self.assertTrue(status["update_available"])


class _FakeSavedFile:
    def __init__(self, section: str):
        self.section = section
        self.writes: list[dict[str, Any]] = []
        self.finalized = False

    def write_content(self, content, filename=None, project=None, save=False):
        self.writes.append(
            {
                "content": content,
                "filename": filename,
                "project": project,
                "save": save,
            }
        )

    def finalize(self):
        self.finalized = True


class TestYamlFormatterBlackFormatting(unittest.TestCase):
    def test_black_rewrites_single_quotes_in_module_section(self):
        with TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir) / "modules"
            module_dir.mkdir(parents=True, exist_ok=True)
            bad_file = module_dir / "sample.py"
            bad_file.write_text(
                "def some_function(hello):\n  a = 'single quotes'\n",
                encoding="utf-8",
            )

            savedfiles = {}

            def fake_savedfile(user_id, fix=True, section="playground"):
                key = (int(user_id), str(section))
                if key not in savedfiles:
                    savedfiles[key] = _FakeSavedFile(section=str(section))
                return savedfiles[key]

            def fake_directory_for(area, project):
                if area.section == "playgroundmodules":
                    return str(module_dir)
                return None

            with patch.object(
                yaml_formatter,
                "SavedFile",
                side_effect=fake_savedfile,
            ), patch.object(
                yaml_formatter,
                "directory_for",
                side_effect=fake_directory_for,
            ):
                result = yaml_formatter._format_playground_python_files_with_black(
                    "Black", 10
                )

            self.assertEqual(result["processed_count"], 1)
            self.assertEqual(result["changed_count"], 1)
            self.assertEqual(result["changed_files"], ["sample.py"])
            self.assertEqual(result["error_count"], 0)

            module_area = savedfiles[(10, "playgroundmodules")]
            self.assertTrue(module_area.finalized)
            self.assertEqual(len(module_area.writes), 1)
            rewritten = module_area.writes[0]["content"]
            self.assertIn('a = "single quotes"', rewritten)
            self.assertNotIn("a = 'single quotes'", rewritten)

    def test_black_uses_playground_section_module_listing_when_available(self):
        with TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir) / "modules"
            nested_dir = module_dir / "pkg"
            nested_dir.mkdir(parents=True, exist_ok=True)
            bad_file = nested_dir / "test.py"
            bad_file.write_text(
                "def hello():\n  return 'hello'\n",
                encoding="utf-8",
            )

            class _FakePlaygroundSection:
                def __init__(self, section: str = "", project: str = "default"):
                    self.section = section
                    self.project = project
                    self.file_list = ["pkg/test.py"]
                    self.writes: list[dict[str, Any]] = []

                def get_file(self, filename):
                    return str(module_dir / filename)

                def write_file(self, filename, content, binary=False):
                    self.writes.append(
                        {
                            "filename": filename,
                            "content": content,
                            "binary": binary,
                        }
                    )
                    bad_file.write_text(content, encoding="utf-8")

            fake_section = _FakePlaygroundSection(section="modules", project="AZDopa")

            def fake_savedfile(user_id, fix=True, section="playground"):
                return _FakeSavedFile(section=str(section))

            fake_playground_module = types.SimpleNamespace(
                PlaygroundSection=lambda section="", project="default": fake_section
            )

            with patch.object(
                yaml_formatter,
                "SavedFile",
                side_effect=fake_savedfile,
            ), patch.dict(
                sys.modules,
                {"docassemble.webapp.playground": fake_playground_module},
            ):
                result = yaml_formatter._format_playground_python_files_with_black(
                    "AZDopa", 10
                )

            self.assertEqual(result["processed_count"], 1)
            self.assertEqual(result["changed_count"], 1)
            self.assertEqual(result["changed_files"], ["pkg/test.py"])
            self.assertEqual(result["error_count"], 0)
            self.assertEqual(len(fake_section.writes), 1)
            self.assertEqual(fake_section.writes[0]["filename"], "pkg/test.py")
            self.assertIn('return "hello"', fake_section.writes[0]["content"])

    def test_black_only_path_does_not_emit_playground_error(self):
        black_result = {
            "requested": True,
            "processed_count": 0,
            "changed_count": 0,
            "error_count": 1,
            "changed_files": [],
            "errors": [
                {
                    "name": "(black)",
                    "error": "The black package is not installed.",
                }
            ],
        }

        with patch.object(
            yaml_formatter,
            "_resolve_current_user_id",
            return_value=None,
        ), patch.object(
            yaml_formatter,
            "_format_playground_python_files_with_black",
            return_value=black_result,
        ):
            result = yaml_formatter.rewrite_playground_yaml_files(
                [],
                selected_playground_project="Weaver2",
                run_black_python_modules=True,
            )

        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["black"]["errors"], black_result["errors"])


class TestYamlFormatterRefTokens(unittest.TestCase):
    def test_rewrite_playground_yaml_files_uses_safe_filename_tokens(self):
        with TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "playground"
            project_dir.mkdir(parents=True, exist_ok=True)
            yaml_path = project_dir / "example.yml"
            yaml_path.write_text(
                "code: |\n    x='hello'\n",
                encoding="utf-8",
            )

            savedfiles = {}

            def fake_savedfile(user_id, fix=True, section="playground"):
                key = (int(user_id), str(section))
                if key not in savedfiles:
                    savedfiles[key] = _FakeSavedFile(section=str(section))
                return savedfiles[key]

            with patch.object(
                yaml_formatter,
                "SavedFile",
                side_effect=fake_savedfile,
            ), patch.object(
                yaml_formatter,
                "directory_for",
                return_value=str(project_dir),
            ), patch.object(
                yaml_formatter,
                "_resolve_current_user_id",
                return_value=10,
            ), patch.object(
                yaml_formatter,
                "list_formatter_playground_yaml_files",
                return_value=[
                    {
                        "label": "example.yml",
                        "token": "example.yml",
                    }
                ],
            ), patch.object(
                yaml_formatter,
                "format_yaml_text",
                return_value={
                    "changed": True,
                    "formatted_yaml": 'code: |\n  x = "hello"\n',
                    "reformatted_rows": 1,
                },
            ):
                result = yaml_formatter.rewrite_playground_yaml_files(
                    ["example.yml"],
                    selected_playground_project="demo",
                )

            self.assertEqual(result["error_count"], 0)
            self.assertEqual(result["changed_count"], 1)
            playground_area = savedfiles[(10, "playground")]
            self.assertTrue(playground_area.finalized)
            self.assertEqual(playground_area.writes[0]["filename"], "example.yml")
            self.assertIn('x = "hello"', playground_area.writes[0]["content"])


if __name__ == "__main__":
    unittest.main()
