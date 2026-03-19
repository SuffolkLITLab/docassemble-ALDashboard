import subprocess
import sys
import unittest


class TestPDFFieldLabelerImport(unittest.TestCase):
    def test_import_does_not_force_docassemble_config_load(self):
        module_name = "docassemble.ALDashboard.pdf_field_labeler"
        probe = """
import importlib
from unittest.mock import patch
import docassemble.base.config

with patch.object(
    docassemble.base.config,
    "load",
    side_effect=AssertionError("config.load should not run during import"),
):
    module = importlib.import_module("docassemble.ALDashboard.pdf_field_labeler")

assert hasattr(module, "apply_formfyxer_pdf_labeling")
""".strip()

        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
