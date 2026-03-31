import importlib
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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

    fake_interview_linter = types.ModuleType(
        "docassemble.ALDashboard.interview_linter"
    )
    fake_interview_linter.list_playground_yaml_files = lambda project: []

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


class TestYamlFormatterBlackStatus(unittest.TestCase):
    def test_get_black_release_status_when_update_available(self):
        with patch.object(yaml_formatter.metadata, "version") as mock_metadata_version, patch.object(
            yaml_formatter, "_fetch_latest_black_version"
        ) as mock_fetch_latest:
            mock_metadata_version.return_value = "24.1.0"
            mock_fetch_latest.return_value = "24.3.0"

            status = yaml_formatter.get_black_release_status()

        self.assertEqual(status["installed_version"], "24.1.0")
        self.assertEqual(status["latest_version"], "24.3.0")
        self.assertTrue(status["update_available"])
        self.assertIsNone(status["error"])


class _FakeSavedFile:
    def __init__(self, section: str):
        self.section = section
        self.writes = []
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


if __name__ == "__main__":
    unittest.main()
