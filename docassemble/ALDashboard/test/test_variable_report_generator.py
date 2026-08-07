# do not pre-load
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from docassemble.ALDashboard.variable_report_generator import (
    extract_interview_metadata_info,
    extract_interview_questions_and_variables,
    generate_docx_report,
    generate_mako_markdown_report,
    generate_variable_report,
    _load_assemblyline_baseline,
    save_variable_report_to_playground,
    generate_and_save_playground_variable_report,
)
from docassemble.ALDashboard.api_dashboard_utils import (
    variable_report_payload_from_options,
)


class TestVariableReportGenerator(unittest.TestCase):
    def test_extract_interview_metadata_info(self):
        yaml_with_title = 'code:\n  interview_short_title = "Affidavit of Indigency"\n'
        meta = extract_interview_metadata_info([yaml_with_title])
        self.assertEqual(meta["short_title"], "Affidavit of Indigency")
        self.assertEqual(meta["display_title"], "Affidavit of Indigency Draft")
        self.assertEqual(meta["md_filename"], "affidavit_of_indigency_draft.md")
        self.assertEqual(meta["docx_filename"], "affidavit_of_indigency_draft.docx")

        meta_file = extract_interview_metadata_info(["question: | Start"], primary_filename="housing_code_checklist.yml")
        self.assertEqual(meta_file["display_title"], "Housing Code Checklist Draft")
        self.assertEqual(meta_file["md_filename"], "housing_code_checklist_draft.md")
        self.assertEqual(meta_file["docx_filename"], "housing_code_checklist_draft.docx")

    def test_assemblyline_baseline_inference(self):
        baseline = _load_assemblyline_baseline()
        self.assertIn("users", baseline)
        self.assertTrue(baseline["users"].is_list)
        self.assertEqual(baseline["users"].var_type, "ALPeopleList")
        self.assertNotIn("al_form_type", baseline)

    def test_extract_questions_and_variables_from_yaml(self):
        yaml_content = """
---
objects:
  - users: ALPeopleList
---
id: user screen
question: |
  What is your information?
fields:
  - User First Name: users[i].name.first
  - User Birthdate: users[i].birthdate
    datatype: date
  - Case Number: case_number
"""
        groups, variables = extract_interview_questions_and_variables([yaml_content], infer_assemblyline=True)
        self.assertIn("users", variables)
        self.assertTrue(variables["users"].is_list)
        self.assertIn("case_number", variables)
        self.assertFalse(variables["case_number"].is_list)
        self.assertEqual(groups[0].title, "What is your information?")

    def test_generate_mako_markdown_report(self):
        yaml_content = """
---
objects:
  - users: ALPeopleList
---
id: user screen
question: |
  What is your information?
fields:
  - First Name: users[i].name.first
  - Case Number: case_number
"""
        res = generate_variable_report([yaml_content], report_title="Test Report", infer_assemblyline=True)
        mako_md = res["mako_markdown"]
        self.assertIn("# Test Report", mako_md)
        self.assertIn("## What is your information?", mako_md)
        self.assertIn("% if showifdef('users'):", mako_md)
        self.assertIn("${ showifdef(item.attr_name('name')) }", mako_md)
        self.assertIn("${ showifdef('case_number') }", mako_md)

    def test_vertical_list_layout_threshold(self):
        yaml_content = """
---
objects:
  - users: ALPeopleList
---
id: user screen
question: |
  User Info
fields:
  - FName: users[i].name.first
  - LName: users[i].name.last
  - BDate: users[i].birthdate
  - Email: users[i].email
  - Phone: users[i].phone_number
"""
        res = generate_variable_report(
            [yaml_content],
            report_title="Test Report",
            infer_assemblyline=True,
            max_list_cols=4,
        )
        mako_md = res["mako_markdown"]
        self.assertIn("#### Person ${ i + 1 }", mako_md)

    def test_generate_docx_report(self):
        yaml_content = """
---
objects:
  - users: ALPeopleList
---
question: |
  User Info
fields:
  - First Name: users[i].name.first
  - Case Number: case_number
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_docx = os.path.join(tmpdir, "report.docx")
            res = generate_variable_report([yaml_content], output_docx_path=out_docx, infer_assemblyline=True)
            self.assertTrue(os.path.exists(res["docx_path"]))
            self.assertGreater(os.path.getsize(res["docx_path"]), 0)

    def test_api_payload_from_options(self):
        payload = variable_report_payload_from_options(
            {
                "yaml_text": "question:\n  User Info\nfields:\n  - Name: users[i].name.first\n",
                "report_title": "Custom Title",
            }
        )
        self.assertIn("Custom Title", payload["mako_markdown"])
        self.assertGreater(payload["variables_count"], 0)

    @patch("docassemble.ALDashboard.variable_report_generator._get_playground_storage")
    def test_save_variable_report_to_playground(self, mock_storage):
        mock_area = MagicMock()
        mock_storage.return_value = (mock_area, "/tmp/fake_proj")

        res = save_variable_report_to_playground(
            mako_markdown="# Report",
            selected_playground_project="default",
            save_format_choice="markdown",
            md_filename="test_report.md",
        )
        self.assertTrue(res["saved"])
        self.assertIn("test_report.md", res["saved_files"])
        mock_area.write_content.assert_called_once_with(
            "# Report",
            filename="test_report.md",
            project="default",
            save=False,
        )
        mock_area.finalize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
