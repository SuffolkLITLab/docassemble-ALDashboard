# do not pre-load
import unittest

from docassemble.ALDashboard.alkiln_story import (
    StoryOptions,
    rows_from_variables,
    story_from_docassemble_json,
)
from docassemble.ALDashboard.api_dashboard_utils import (
    alkiln_story_payload_from_options,
    build_openapi_spec,
)
from docassemble.ALDashboard.mcp_registry import openapi_to_mcp_tools


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
        self.assertIn("| users.name['first'] | Ada |  |", rows)
        self.assertIn("| users.name['last'] | Lovelace |  |", rows)
        self.assertIn("| started | 05/19/2026 |  |", rows)

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
        self.assertIn("| fruit['apple'] | False |  |", rows)
        self.assertIn("| fruit['banana'] | False |  |", rows)
        self.assertIn("| fruit['None'] | True |  |", rows)

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
        self.assertIn("| x | 1 |  |", result["feature_text"])

    def test_api_payload_accepts_json_text(self):
        payload = alkiln_story_payload_from_options(
            {
                "json_text": '{"variables": {"name": "Ada"}}',
                "yaml_file_name": "test.yml",
                "question_id": "done",
                "ignore_anywhere_in_var_name": [],
            }
        )
        self.assertEqual(payload["rows"], ["| name | Ada |  |"])
        self.assertIn(
            'Given I start the interview at "test.yml"', payload["feature_text"]
        )

    def test_openapi_and_mcp_include_story_endpoint(self):
        spec = build_openapi_spec()
        self.assertIn("/al/api/v1/dashboard/interview/story", spec["paths"])
        tools = openapi_to_mcp_tools(spec, namespace="aldashboard")
        names = [tool["name"] for tool in tools]
        self.assertIn("aldashboard.post_al_api_v1_dashboard_interview_story", names)


if __name__ == "__main__":
    unittest.main()
