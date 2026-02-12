import unittest

import pandas as pd

from docassemble.ALDashboard.translation_validation import validate_translation_dataframe


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


if __name__ == "__main__":
    unittest.main()
