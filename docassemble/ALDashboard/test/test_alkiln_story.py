# do not pre-load
import base64
import json
import tempfile
import unittest
from pathlib import Path

from docassemble.ALDashboard.alkiln_story import (
    StoryOptions,
    build_feature_preview_markdown,
    detect_yaml_ending_screen,
    load_docassemble_json_text,
    rows_from_variables,
    rows_from_yaml_heuristics,
    story_from_docassemble_yaml,
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

    def test_rows_from_variables_skips_type_annotation_imports(self):
        rows = rows_from_variables(
            {
                "Dict": None,
                "Tuple": None,
                "Fields": None,
                "Optional": None,
                "List": None,
                "Union": None,
                "Iterable": None,
                "Callable": None,
                "name": "Ada",
            },
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertEqual(rows, ["| name | Ada |"])

    def test_rows_from_variables_skips_file_helper_objects(self):
        rows = rows_from_variables(
            {
                "uploaded_file": {
                    "_class": "docassemble.base.util.DAFile",
                    "filename": "secret.pdf",
                    "number": 42,
                    "ok": True,
                },
                "logo": {
                    "_class": "docassemble.base.util.DAStaticFile",
                    "filename": "logo.png",
                    "package": "docassemble.demo",
                },
                "name": "Ada",
            },
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertEqual(rows, ["| name | Ada |"])

    def test_rows_from_variables_skips_reference_cache_roots(self):
        rows = rows_from_variables(
            {
                "legalserver_data": {"documents": [{"name": "Cached PDF"}]},
                "valid_housing_courts": [{"name": "Housing Court"}],
                "all_reserved_names": {"elements": {"x": True}},
                "name": "Ada",
            },
        )
        self.assertEqual(rows, ["| name | Ada |"])

    def test_rows_from_variables_skips_documented_top_level_reserved_names(self):
        rows = rows_from_variables(
            {
                "device": "phone",
                "session_tags": ["draft"],
                "start_time": "2026-05-19T10:30:00-04:00",
                "user_lat_lon": "42,-71",
                "name": "Ada",
                "child": {"name": "Grace"},
            },
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| name | Ada |", rows)
        self.assertIn("| child['name'] | Grace |", rows)
        self.assertNotIn("| device | phone |", rows)
        self.assertNotIn("| session_tags[0] | draft |", rows)
        self.assertNotIn("| user_lat_lon | 42,-71 |", rows)

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
        self.assertEqual(
            result["preview_markdown"],
            build_feature_preview_markdown(result["feature_text"]),
        )

    def test_build_feature_preview_markdown_preserves_blank_lines(self):
        preview = build_feature_preview_markdown(
            "Feature: Story\n\nScenario: Preview\n  Given x < y"
        )
        self.assertTrue(all(line.startswith("    ") for line in preview.split("\n")))
        self.assertIn("    \n", preview)
        self.assertIn("    Scenario: Preview", preview)
        self.assertIn("    Feature: Story", preview)

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

    def test_rows_from_yaml_heuristics_extracts_fields_and_continue_button(self):
        yaml_text = """---
question: Name
fields:
  - First name: users[0].name.first
  - Last name: users[0].name.last
  - Is active?: is_active
    datatype: yesno
---
question: Done
continue button field: saw_done_screen
"""
        rows = rows_from_yaml_heuristics(
            yaml_text,
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| users[0].name.first | Jane |", rows)
        self.assertIn("| users[0].name.last | Smith |", rows)
        self.assertIn("| is_active | True |", rows)
        self.assertIn("| saw_done_screen | True |", rows)

    def test_rows_from_yaml_heuristics_normalizes_today_default(self):
        yaml_text = """---
question: Signature
fields:
  - Signature date: principal_signature_date
    datatype: date
    default: ${ today() }
"""
        rows = rows_from_yaml_heuristics(
            yaml_text,
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| principal_signature_date | today |", rows)
        self.assertNotIn("| principal_signature_date | ${ today() } |", rows)

    def test_rows_from_yaml_heuristics_uses_examples_and_minlength(self):
        yaml_text = """---
question: Vehicle
fields:
  - Vehicle year: vehicle_year
  - Vehicle make: vehicle_make
    under text: "example: Kia"
  - Vehicle Identification Number (VIN): VIN
    minlength: 17
"""
        rows = rows_from_yaml_heuristics(
            yaml_text,
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| vehicle_year | 2023 |", rows)
        self.assertIn("| vehicle_make | Kia |", rows)
        self.assertIn("| VIN | 11111111111111111 |", rows)

    def test_rows_from_yaml_heuristics_supports_peoplelist_fields_helpers(self):
        yaml_text = """---
fields:
  - code: |
      users[i].name_fields(show_title=True)
  - code: |
      users[i].address_fields(show_country=True, show_county=True, allow_no_address=True, ask_if_impounded=True)
  - code: |
      users[i].gender_fields()
  - code: |
      users[i].language_fields()
  - code: |
      users[i].pronoun_fields()
---
fields:
  - code: |
      other_parties[i].name_fields(person_or_business='business')
"""
        rows = rows_from_yaml_heuristics(
            yaml_text,
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| users[0].name.title | Mr. |", rows)
        self.assertIn("| users[0].name.first | Jane |", rows)
        self.assertIn("| users[0].name.suffix | Jr. |", rows)
        self.assertIn("| users[0].address.has_no_address | False |", rows)
        self.assertIn("| users[0].address.country | US |", rows)
        self.assertIn("| users[0].address.county | Suffolk |", rows)
        self.assertIn("| users[0].address.impounded | False |", rows)
        self.assertIn("| users[0].gender | female |", rows)
        self.assertIn("| users[0].language | en |", rows)
        self.assertIn("| users[0].pronouns['he/him/his'] | True |", rows)
        self.assertIn("| other_parties[0].name.first | Acme LLC |", rows)
        self.assertNotIn("| other_parties[0].name.last | Smith |", rows)

    def test_rows_from_yaml_heuristics_adds_assemblyline_people_for_gather(self):
        yaml_text = """---
objects:
  - children: ALPeopleList.using(ask_number=True)
---
mandatory: True
code: |
  users.gather()
  children.gather()
---
event: download
question: Download
"""
        rows = rows_from_yaml_heuristics(
            yaml_text,
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| users.target_number | 1 |", rows)
        self.assertIn("| users[0].name.first | Jane |", rows)
        self.assertIn("| children.target_number | 1 |", rows)
        self.assertIn("| children[0].name.last | Smith |", rows)

    def test_rows_from_yaml_heuristics_handles_single_field_sets_and_checkboxes(self):
        yaml_text = """---
question: Role
field: person_answering
datatype: radio
choices:
  - Tenant: tenant
  - Attorney: attorney
---
question: Terms
fields:
  - Accept terms: acknowledged_information_use
    datatype: checkboxes
    choices:
      - I accept the terms of use.
      - Email me updates.
---
question: Address
sets:
  - users[0].address.address
"""
        rows = rows_from_yaml_heuristics(
            yaml_text,
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| person_answering | tenant |", rows)
        self.assertIn(
            "| acknowledged_information_use['I accept the terms of use.'] | True |",
            rows,
        )
        self.assertIn(
            "| acknowledged_information_use['Email me updates.'] | False |",
            rows,
        )
        self.assertIn("| users[0].address.address | 123 Main St |", rows)
        self.assertIn("| users[0].address.city | Boston |", rows)
        self.assertIn("| users[0].address.state | MA |", rows)
        self.assertIn("| users[0].address.zip | 02108 |", rows)

    def test_rows_from_yaml_heuristics_extracts_direct_code_references(self):
        yaml_text = """---
objects:
  - users[i].attorney: ALPeopleList.using(ask_number=True)
---
mandatory: True
code: |
  users[0].address.address
  users[0].signature
  users[0].attorney.target_number = 1
  users[0].attorney[0].address.address
"""
        rows = rows_from_yaml_heuristics(
            yaml_text,
            options=StoryOptions(ignore_anywhere_in_var_name=[]),
        )
        self.assertIn("| users[0].address.address | 123 Main St |", rows)
        self.assertIn("| users[0].address.city | Boston |", rows)
        self.assertIn("| users[0].signature | /placeholder_signature.png |", rows)
        self.assertIn("| users[0].attorney.target_number | 1 |", rows)
        self.assertIn("| users[0].attorney[0].name.first | Jane |", rows)
        self.assertIn("| users[0].attorney[0].address.address | 123 Main St |", rows)

    def test_story_from_docassemble_yaml_loads_local_includes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            shared_path = temp_path / "shared.yml"
            shared_path.write_text(
                """---
question: Shared
fields:
  - Acknowledge: acknowledged_information_use
    datatype: yesno
""",
                encoding="utf-8",
            )
            main_path = temp_path / "main.yml"
            main_path.write_text(
                """---
include:
  - shared.yml
---
mandatory: True
code: |
  users[0].address.address
---
event: final_screen
question: Done
""",
                encoding="utf-8",
            )
            result = story_from_docassemble_yaml(
                main_path.read_text(encoding="utf-8"),
                filename=str(main_path),
                options=StoryOptions(
                    yaml_file_name="main.yml",
                    question_id="final_screen",
                    ignore_anywhere_in_var_name=[],
                ),
            )
        self.assertIn("| acknowledged_information_use | True |", result["rows"])
        self.assertIn("| users[0].address.address | 123 Main St |", result["rows"])
        self.assertIn("| users[0].address.city | Boston |", result["rows"])

    def test_story_from_docassemble_yaml_detects_filename_and_ending_screen(self):
        yaml_text = """---
id: intro
question: Intro
---
event: final_screen
question: Done
"""
        result = story_from_docassemble_yaml(
            yaml_text,
            filename="/tmp/example_interview.yml",
            options=StoryOptions(
                yaml_file_name="example_interview.yml",
                question_id=detect_yaml_ending_screen(yaml_text),
                ignore_anywhere_in_var_name=[],
            ),
        )
        self.assertEqual(result["yaml_file_name"], "example_interview.yml")
        self.assertEqual(result["question_id"], "final_screen")
        self.assertIn(
            'Given I start the interview at "example_interview.yml"',
            result["feature_text"],
        )
        self.assertIn('And the user gets to "final_screen"', result["feature_text"])

    def test_detect_yaml_ending_screen_prefers_sanitized_id(self):
        yaml_text = """---
id: download lemon_law_letter
event: lemon_law_letter_download
question: Done
"""
        self.assertEqual(
            detect_yaml_ending_screen(yaml_text),
            "download lemon_law_letter",
        )

    def test_api_payload_accepts_yaml_text(self):
        payload = alkiln_story_payload_from_options(
            {
                "yaml_text": "---\nfields:\n  - Email: user_email\n    input type: email\n---\nevent: done\nquestion: Done\n",
                "filename": "intake.yml",
                "ignore_anywhere_in_var_name": [],
            }
        )
        self.assertEqual(payload["yaml_file_name"], "intake.yml")
        self.assertEqual(payload["question_id"], "done")
        self.assertIn("| user_email | user@example.com |", payload["rows"])

    def test_api_payload_accepts_yaml_file_content_base64(self):
        yaml_text = (
            "---\nfields:\n  - Name: user_name\n---\nid: final\nquestion: Done\n"
        )
        payload = alkiln_story_payload_from_options(
            {
                "filename": "uploaded_interview.yml",
                "file_content_base64": base64.b64encode(
                    yaml_text.encode("utf-8")
                ).decode("ascii"),
                "ignore_anywhere_in_var_name": [],
            }
        )
        self.assertEqual(payload["yaml_file_name"], "uploaded_interview.yml")
        self.assertEqual(payload["question_id"], "final")
        self.assertIn("| user_name | Sample answer |", payload["rows"])

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
        self.assertIn("/al/api/v1/dashboard/kiln/story", spec["paths"])
        self.assertNotIn("/al/api/v1/dashboard/interview/story", spec["paths"])
        self.assertNotIn("/al/api/v1/dashboard/interview/kiln-fixture", spec["paths"])
        tools = openapi_to_mcp_tools(spec, namespace="aldashboard")
        names = [tool["name"] for tool in tools]
        self.assertIn("aldashboard.post_al_api_v1_dashboard_kiln_story", names)


if __name__ == "__main__":
    unittest.main()
