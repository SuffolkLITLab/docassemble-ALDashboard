import unittest

import pandas as pd

from docassemble.ALDashboard.translation_validation import (
    validate_translation_dataframe,
)


class TestTranslationValidation(unittest.TestCase):
    def test_missing_required_column(self):
        df = pd.DataFrame({"wrong": ["x"]})
        result = validate_translation_dataframe(df)
        self.assertEqual(result["summary"]["error_count"], 1)

    def test_detects_empty_row_and_mako_issue(self):
        df = pd.DataFrame(
            {
                "question_id": ["q1", "q2"],
                "tr_text": ["", "${ bad_syntax"],
            }
        )
        result = validate_translation_dataframe(df)
        self.assertGreaterEqual(result["summary"]["error_count"], 1)
        self.assertEqual(result["summary"]["empty_row_count"], 1)
        messages = [error["message"] for error in result["errors"]]
        self.assertTrue(any("Mako syntax error:" in msg for msg in messages))
        self.assertTrue(all("Traceback" not in msg for msg in messages))

    def test_does_not_execute_template_code_during_validation(self):
        df = pd.DataFrame(
            {
                "question_id": ["q1"],
                "tr_text": [
                    "<% raise Exception('should not execute during syntax check') %>"
                ],
            }
        )
        result = validate_translation_dataframe(df)
        self.assertEqual(result["summary"]["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
