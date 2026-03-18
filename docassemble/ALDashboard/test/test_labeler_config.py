import tempfile
import unittest
from pathlib import Path

from docassemble.ALDashboard.labeler_config import (
    build_default_prompt_library,
    get_docx_prompt_profile,
    get_pdf_labeler_ui_config,
    load_labeler_prompt_library,
)


class TestLabelerConfig(unittest.TestCase):
    def test_load_labeler_prompt_library_merges_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "custom_labeler.yml"
            config_path.write_text(
                """
branding:
  docx_header_title: Alternate DOCX Labeler
docx:
  default_prompt_profile: alternate
  prompt_profiles:
    alternate:
      label: Alternate profile
      help_text: Alternate help
      role_description: Custom role description
      rules_addendum: Custom rules
      temperature: 0.2
pdf:
  field_name_library:
    text:
      - custom_text_field
""".strip(),
                encoding="utf-8",
            )

            library = load_labeler_prompt_library(str(config_path))

        self.assertEqual(
            library["branding"]["docx_header_title"], "Alternate DOCX Labeler"
        )
        self.assertEqual(library["docx"]["default_prompt_profile"], "alternate")
        self.assertEqual(
            library["docx"]["prompt_profiles"]["alternate"]["role_description"],
            "Custom role description",
        )
        self.assertEqual(
            library["pdf"]["field_name_library"]["text"][0],
            "custom_text_field",
        )
        self.assertIn(
            "users",
            library["docx"]["variable_tree"],
            "default variable tree should remain available after merge",
        )

    def test_get_docx_prompt_profile_falls_back_to_defaults(self):
        defaults = build_default_prompt_library()
        profile = get_docx_prompt_profile("missing_profile")

        self.assertEqual(profile["name"], defaults["docx"]["default_prompt_profile"])
        self.assertIn("return a JSON structure", profile["role_description"])

    def test_pdf_labeler_defaults_use_pdf_style_names(self):
        defaults = build_default_prompt_library()
        self.assertEqual(
            defaults["pdf"]["field_name_library"]["text"][0],
            "users1_name_first",
        )
        self.assertIn(
            "users1_name_full",
            defaults["pdf"]["field_name_library"]["text"],
        )
        self.assertIn(
            "attorney_signature",
            defaults["pdf"]["field_name_library"]["signature"],
        )

    def test_get_pdf_labeler_ui_config_returns_sanitized_bootstrap_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "custom_labeler.yml"
            config_path.write_text(
                """
branding:
  pdf_header_title: Alternate PDF Labeler
pdf:
  field_name_library:
    text:
      - custom_pdf_name
      - ""
    signature:
      - users1_signature
""".strip(),
                encoding="utf-8",
            )

            config = get_pdf_labeler_ui_config(prompt_library_path=str(config_path))

        self.assertEqual(
            config["branding"]["pdf_header_title"], "Alternate PDF Labeler"
        )
        self.assertEqual(config["field_name_library"]["text"], ["custom_pdf_name"])
        self.assertEqual(
            config["field_name_library"]["signature"], ["users1_signature"]
        )


if __name__ == "__main__":
    unittest.main()
