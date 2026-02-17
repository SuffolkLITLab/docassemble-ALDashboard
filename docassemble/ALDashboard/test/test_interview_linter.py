import unittest

from docassemble.ALDashboard.interview_linter import lint_interview_content


class TestInterviewLinter(unittest.TestCase):
    def test_lint_interview_content_returns_expected_sections(self):
        yaml_content = """
---
id: sample
question: |
  This is a very simple test question.
subquestion: |
  Please choose yes/no.
fields:
  - Do you want help?: wants_help
    datatype: yesno
"""
        results = lint_interview_content(yaml_content)

        self.assertIn("interview_scores", results)
        self.assertIn("misspelled", results)
        self.assertIn("headings_warnings", results)
        self.assertIn("style_warnings", results)
        self.assertIn("interview_texts", results)

        self.assertGreater(len(results["interview_scores"]), 0)
        self.assertTrue(any("please" in warning["message"].lower() for warning in results["style_warnings"]))


if __name__ == "__main__":
    unittest.main()
