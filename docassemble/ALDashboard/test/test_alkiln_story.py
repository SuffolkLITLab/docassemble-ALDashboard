# do not pre-load
import json
import unittest
from pathlib import Path

from docassemble.ALDashboard.alkiln_story import (
    StoryOptions,
    load_docassemble_json_text,
    rows_from_variables,
    story_from_docassemble_json,
)
from docassemble.ALDashboard.api_dashboard_utils import (
    alkiln_story_payload_from_options,
    build_openapi_spec,
)
from docassemble.ALDashboard.mcp_registry import openapi_to_mcp_tools

FIXTURES_DIR = Path(__file__).parent


def read_fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestALKilnStory(unittest.TestCase):
    def test_rows_from_variables_handles_nested_objects_and_dates(self):
        rows = rows_from_variables(
            {
                "users": {
                    "_class": "docassemble.base.util.Individual",
                    "name": {"first": "Ada", "last": "Lovelace"},
                },
                "started": "2026-05-19T10:30:00-04:00",
            },
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| users.name['first'] | Ada |", rows)
        self.assertIn("| users.name['last'] | Lovelace |", rows)
        self.assertIn("| started | 05/19/2026 |", rows)

    def test_rows_from_variables_handles_checkbox_elements_and_none(self):
        rows = rows_from_variables(
            {
                "fruit": {
                    "_class": "docassemble.base.util.DADict",
                    "elements": {"apple": False, "banana": False},
                }
            },
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| fruit['apple'] | False |", rows)
        self.assertIn("| fruit['banana'] | False |", rows)
        self.assertIn("| fruit['None'] | True |", rows)

    def test_rows_from_variables_synthesizes_target_number_for_lists(self):
        rows = rows_from_variables(
            {
                "users": {
                    "_class": "docassemble.base.core.DAList",
                    "elements": [
                        {
                            "_class": "docassemble.base.util.Individual",
                            "name": {"first": "Ada"},
                        },
                        {
                            "_class": "docassemble.base.util.Individual",
                            "name": {"first": "Grace"},
                        },
                    ],
                    "there_are_any": True,
                }
            },
            options=StoryOptions(ignore_anywhere_in_var_name=["_class"]),
        )
        self.assertIn("| users.target_number | 2 |", rows)
        self.assertIn("| users[0].name['first'] | Ada |", rows)
        self.assertIn("| users[1].name['first'] | Grace |", rows)
        self.assertNotIn("| users.there_are_any | True |", rows)

    def test_rows_from_variables_can_emit_legacy_trigger_column(self):
        rows = rows_from_variables(
            {"name": "Ada"},
            options=StoryOptions(
                ignore_anywhere_in_var_name=[],
                include_trigger_column=True,
            ),
        )
        self.assertEqual(rows, ["| name | Ada |  |"])

    def test_story_from_docassemble_json_builds_feature_text(self):
        result = story_from_docassemble_json(
            {"i": "docassemble.demo:data/questions/main.yml", "variables": {"x": 1}},
            options=StoryOptions(
                feature_description="Feature title",
                scenario_description="Scenario title",
                yaml_file_name="main.yml",
                question_id="review",
                ignore_anywhere_in_var_name=[],
            ),
        )
        self.assertEqual(result["row_count"], 1)
        self.assertIn(
            'Given I start the interview at "main.yml"', result["feature_text"]
        )
        self.assertIn(
            'And the user gets to "review" with this data:', result["feature_text"]
        )
        self.assertIn("| var | value |", result["feature_text"])
        self.assertIn("| x | 1 |", result["feature_text"])

    def test_api_payload_accepts_json_text(self):
        payload = alkiln_story_payload_from_options(
            {
                "json_text": '{"variables": {"name": "Ada"}}',
                "yaml_file_name": "test.yml",
                "question_id": "done",
                "ignore_anywhere_in_var_name": [],
            }
        )
        self.assertEqual(payload["rows"], ["| name | Ada |"])
        self.assertIn(
            'Given I start the interview at "test.yml"', payload["feature_text"]
        )

    def test_load_docassemble_json_text_accepts_fixture_textarea_newlines(self):
        json_text = read_fixture("fixture_interview_answers_textarea_newlines.json")
        textarea_text = json_text.replace("\\\\r\\\\n", "\r\n")
        with self.assertRaises(ValueError):
            # The strict parser path is expected to reject this textarea-shaped
            # variant before the fallback accepts it below.
            json.loads(textarea_text)

        data = load_docassemble_json_text(textarea_text)
        self.assertEqual(data["note"], "Line one\r\nLine two")

        payload = alkiln_story_payload_from_options({"json_text": textarea_text})
        self.assertGreater(payload["row_count"], 0)
        self.assertIn("| var | value |", payload["feature_text"])

    def test_load_docassemble_json_text_repairs_unescaped_inner_quotes(self):
        json_text = read_fixture("fixture_interview_answers_unescaped_quotes.json")
        textarea_text = json_text.replace('\\"', '"')
        with self.assertRaises(ValueError):
            json.loads(textarea_text)

        data = load_docassemble_json_text(textarea_text)
        self.assertIn(
            'Title reads "48 hours notice to vacate premises"',
            data["eviction_notice"]["alt_text"],
        )
        payload = alkiln_story_payload_from_options({"json_text": textarea_text})
        self.assertIn("| accepted | True |", payload["feature_text"])

    def test_trimmed_real_fixture_excludes_verbose_court_information(self):
        json_text = read_fixture("fixture_interview_answers.json")
        data = load_docassemble_json_text(json_text)
        self.assertNotIn("courts", data)
        self.assertNotIn("appeals_court", data)
        self.assertLess(len(json_text), 4000)

        payload = alkiln_story_payload_from_options({"json_text": json_text})
        self.assertIn("| users.target_number | 1 |", payload["feature_text"])
        self.assertIn("| eviction_date | 03/10/2026 |", payload["feature_text"])
        self.assertNotIn("Massachusetts Appeals Court", payload["feature_text"])

    def test_list_fixture_covers_target_numbers_and_checkbox_none(self):
        json_text = read_fixture("fixture_interview_answers_lists.json")
        payload = alkiln_story_payload_from_options(
            {"json_text": json_text, "ignore_anywhere_in_var_name": ["_class"]}
        )
        rows = payload["rows"]
        self.assertIn("| users.target_number | 2 |", rows)
        self.assertIn("| users[0].name.first | Ada |", rows)
        self.assertIn("| users[1].name.last | Hopper |", rows)
        self.assertIn("| choices['None'] | True |", rows)

    def test_openapi_and_mcp_include_story_endpoint(self):
        spec = build_openapi_spec()
        self.assertIn("/al/api/v1/dashboard/interview/story", spec["paths"])
        tools = openapi_to_mcp_tools(spec, namespace="aldashboard")
        names = [tool["name"] for tool in tools]
        self.assertIn("aldashboard.post_al_api_v1_dashboard_interview_story", names)


if __name__ == "__main__":
    unittest.main()
