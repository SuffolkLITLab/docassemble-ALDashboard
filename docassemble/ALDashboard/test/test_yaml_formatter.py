import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from docassemble.ALDashboard import yaml_formatter


class TestYamlFormatterBlackStatus(unittest.TestCase):
    @patch("docassemble.ALDashboard.yaml_formatter._fetch_latest_black_version")
    @patch("docassemble.ALDashboard.yaml_formatter.metadata.version")
    def test_get_black_release_status_when_update_available(
        self, mock_metadata_version, mock_fetch_latest
    ):
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

            with patch(
                "docassemble.ALDashboard.yaml_formatter.SavedFile",
                side_effect=fake_savedfile,
            ), patch(
                "docassemble.ALDashboard.yaml_formatter.directory_for",
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
