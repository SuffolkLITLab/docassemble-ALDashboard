# do not pre-load
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ruamel.yaml import YAML

from docassemble.ALDashboard.review_screen_generator import (
    _review_output_filename,
    generate_and_save_playground_review_screen,
    generate_review_screen_yaml,
    save_review_screen_to_playground,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class TestReviewScreenGenerator(unittest.TestCase):
    def test_generates_review_yaml(self):
        sample = """
---
question: What is your name?
fields:
  - First name: users[0].name.first
  - Last name: users[0].name.last
---
sections:
  - intro: Intro
"""
        output = generate_review_screen_yaml([sample])
        self.assertIn("id: review screen", output)
        self.assertIn("question: |\n  Review your answers", output)
        self.assertIn("First name", output)
        # AssemblyLine's house style: list items indented under their key, the
        # way every hand-written review screen in the ecosystem looks.
        self.assertIn("\n  - Edit: ", output)

    def test_mapping_form_non_list_object_is_safely_skipped(self):
        sample = """
---
objects:
  children[i].lived_with: ALIndividual
"""
        output = generate_review_screen_yaml([sample])

        self.assertNotIn("id: revisit children[i].lived_with", output)
        self.assertNotIn("children[i].lived_with.revisit", output)

    def test_preserves_list_form_objects(self):
        sample = """
---
objects:
  - users: ALPeopleList.using(ask_number=False)
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn("id: revisit users", output)
        self.assertIn("users.revisit", output)

    def test_mapping_form_list_object_generates_revisit_block(self):
        sample = """
---
objects:
  children: ChildList
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn("id: revisit children", output)
        self.assertIn("children.revisit", output)

    def test_custom_list_class_match_is_case_insensitive(self):
        sample = """
---
objects:
  offices: example.package.CustomLIST
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn("id: revisit offices", output)

    def test_generated_yaml_is_valid_with_long_column_labels(self):
        sample = """
---
objects:
  children: ChildList
---
question: Tax dependency
fields:
  - Who should be allowed to claim ${ children[i].name_full() } as a tax deduction in even years?: children[i].tax_dependency_even
  - "I think I was unlawfully discriminated against by the property owners or agents.": children[i].discrimination_q
"""
        output = generate_review_screen_yaml([sample])

        list(YAML(typ="safe", pure=True).load_all(output))

    def test_generated_multiline_content_uses_literal_yaml_blocks(self):
        sample = """
---
objects:
  children: ChildList
---
question: What is your name?
fields:
  - First name: users[0].name.first
  - Eligible: is_eligible
    datatype: yesno
"""
        output = generate_review_screen_yaml([sample])

        self.assertNotIn(r"\n", output)
        self.assertIn("button: |", output)
        self.assertIn("subquestion: |", output)
        self.assertIn("First name: ${ showifdef('users[0].name.first') }", output)

    def test_show_if_code_generates_matching_if_and_endif(self):
        sample = """
---
question: What court was your case decided in?
fields:
  - no label: trial_court
    datatype: object
    show if:
      code: |
        len(all_matches)
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn("% if len(all_matches):", output)
        self.assertEqual(output.count("% if "), output.count("% endif"))

    def test_show_if_boolean_and_null_values_are_python_literals(self):
        sample = """
---
question: Conditional fields
fields:
  - Hardship: has_hardship
    show if:
      variable: non_payment
      is: False
  - Other court: other_court
    show if:
      variable: trial_court
      is: null
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn("% if showifdef('non_payment') == False:", output)
        self.assertIn("% if showifdef('trial_court') == None:", output)
        self.assertNotIn('== "False"', output)
        self.assertNotIn('== "None"', output)

    def test_duplicate_list_declarations_create_one_revisit_block(self):
        sample = """
---
objects:
  children: ChildList
---
objects:
  - children: ChildList
"""
        output = generate_review_screen_yaml([sample])

        self.assertEqual(output.count("id: revisit children"), 1)

    def test_symbolically_indexed_list_object_does_not_create_top_level_revisit_block(
        self,
    ):
        sample = """
---
objects:
  users[i].jobs: ALJobList
"""
        output = generate_review_screen_yaml([sample])

        self.assertNotIn("id: revisit users[i].jobs", output)

    def test_concretely_indexed_list_object_does_not_create_top_level_revisit_block(
        self,
    ):
        sample = """
---
objects:
  users[0].jobs: ALJobList
"""
        output = generate_review_screen_yaml([sample])

        self.assertNotIn("id: revisit users[0].jobs", output)
        self.assertNotIn("users[0].jobs.revisit", output)

    def test_nested_list_object_does_not_create_top_level_revisit_block(self):
        sample = """
---
objects:
  user.jobs: ALJobList
"""
        output = generate_review_screen_yaml([sample])

        self.assertNotIn("id: revisit user.jobs", output)
        self.assertNotIn("user.jobs.revisit", output)

    def test_the_review_screen_can_take_the_interviews_own_names(self):
        """A drafted screen has to keep the event the download screen links to."""
        sample = """
---
question: What is your name?
fields:
  - First name: users[0].name.first
"""
        output = generate_review_screen_yaml(
            [sample],
            review_id="my form review screen",
            review_event_name="review_my_form",
            review_question="Look over your answers",
        )

        self.assertIn("id: my form review screen", output)
        self.assertIn("event: review_my_form", output)
        self.assertIn("question: |\n  Look over your answers", output)
        self.assertNotIn("review_form", output)

    def test_a_name_or_an_address_is_one_line_not_six(self):
        sample = """
---
question: Your details
fields:
  - code: |
      client.name_fields()
  - code: |
      client.address_fields()
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn(
            "Name: ${ client if defined('client.name.first') else '' }", output
        )
        self.assertIn(
            "Address: ${ client.address.block() "
            "if defined('client.address.address') else '' }",
            output,
        )
        self.assertNotIn("Middle:", output)
        self.assertNotIn("Apartment or Unit:", output)

    def test_no_value_on_the_screen_forces_a_variable_to_be_defined(self):
        sample = """
---
question: Money and yes/no
fields:
  - Rent: rent_amount
    datatype: currency
  - Served: was_served
    datatype: yesno
  - Notes: notes
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn(
            "${ currency(rent_amount) if defined('rent_amount') else '' }", output
        )
        self.assertIn(
            "${ word(yesno(was_served)) if defined('was_served') else '' }", output
        )
        self.assertIn("${ showifdef('notes') }", output)

    def test_a_review_table_never_asks_for_a_signature(self):
        sample = """
---
objects:
  - plaintiffs: ALPeopleList
---
question: Signatures
fields:
  - Signature: plaintiffs[i].signature
    datatype: signature
  - Phone: plaintiffs[i].phone_number
"""
        output = generate_review_screen_yaml([sample])
        table = output.split("table: plaintiffs.table", 1)[1]
        edits = table.split("edit:", 1)[1]

        self.assertNotIn("- signature", edits)
        self.assertIn("- phone_number", edits)
        # It is still worth showing; it is just not something to demand.
        self.assertIn("row_item.signature", table)

    def test_interview_uses_shared_generator_module(self):
        interview_path = PACKAGE_ROOT / "data/questions/review_screen_generator.yml"
        interview_source = interview_path.read_text(encoding="utf-8")
        documents = list(YAML(typ="safe", pure=True).load_all(interview_source))

        self.assertIn(
            ".review_screen_generator",
            next(
                document["modules"] for document in documents if "modules" in document
            ),
        )
        generator_code = next(
            document["code"]
            for document in documents
            if "code" in document
            and "generate_and_save_playground_review_screen" in document["code"]
        )
        self.assertIn(
            "selected_playground_files.true_values()",
            generator_code,
        )
        self.assertIn("default: review.yml", interview_source)
        self.assertIn("datatype: checkboxes", interview_source)
        self.assertIn("all of the above: True", interview_source)
        self.assertIn('review_source_mode == "upload"', interview_source)
        self.assertIn("save_review_to_playground", interview_source)
        self.assertNotIn("skippable_types", interview_source)
        self.assertNotIn("review_fields_temp", interview_source)

    def test_review_output_filename_defaults_and_adds_extension(self):
        self.assertEqual(_review_output_filename(""), "review.yml")
        self.assertEqual(_review_output_filename("custom"), "custom.yml")
        self.assertEqual(_review_output_filename("custom.yaml"), "custom.yaml")

    def test_review_output_filename_rejects_directories(self):
        with self.assertRaises(ValueError):
            _review_output_filename("../review.yml")
        with self.assertRaises(ValueError):
            _review_output_filename(r"..\review.yml")

    @patch(
        "docassemble.ALDashboard.review_screen_generator.list_review_playground_yaml_files"
    )
    @patch(
        "docassemble.ALDashboard.review_screen_generator._get_review_playground_storage"
    )
    def test_generates_and_saves_selected_playground_files(
        self,
        get_playground_storage,
        list_yaml_files,
    ):
        fixture_dir = PACKAGE_ROOT / "test"
        list_yaml_files.return_value = [
            {"label": "api_review_input.yml", "token": "api_review_input.yml"}
        ]
        saved_file = MagicMock()
        get_playground_storage.return_value = (saved_file, str(fixture_dir))

        result = generate_and_save_playground_review_screen(
            ["api_review_input.yml"],
            selected_playground_project="sample",
            output_filename="my_review",
        )

        self.assertTrue(result["saved"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["output_filename"], "my_review.yml")
        self.assertIn("id: review screen", result["generated_yaml"])
        saved_file.write_content.assert_called_once_with(
            result["generated_yaml"],
            filename="my_review.yml",
            project="sample",
            save=False,
        )
        saved_file.finalize.assert_called_once_with()

    @patch(
        "docassemble.ALDashboard.review_screen_generator.list_review_playground_yaml_files"
    )
    @patch(
        "docassemble.ALDashboard.review_screen_generator._get_review_playground_storage"
    )
    def test_generates_from_playground_without_saving(
        self,
        get_playground_storage,
        list_yaml_files,
    ):
        fixture_dir = PACKAGE_ROOT / "test"
        list_yaml_files.return_value = [
            {"label": "api_review_input.yml", "token": "api_review_input.yml"}
        ]
        playground_area = MagicMock()
        get_playground_storage.return_value = (playground_area, str(fixture_dir))

        result = generate_and_save_playground_review_screen(
            ["api_review_input.yml"],
            selected_playground_project="sample",
            save_to_playground=False,
        )

        self.assertFalse(result["saved"])
        self.assertFalse(result["save_requested"])
        self.assertIsNone(result["error"])
        self.assertIn("id: review screen", result["generated_yaml"])
        playground_area.write_content.assert_not_called()
        playground_area.finalize.assert_not_called()

    @patch(
        "docassemble.ALDashboard.review_screen_generator._get_review_playground_storage"
    )
    def test_saves_previously_generated_review_yaml(
        self,
        get_playground_storage,
    ):
        playground_area = MagicMock()
        get_playground_storage.return_value = (playground_area, "/tmp/project")

        result = save_review_screen_to_playground(
            "id: review screen\n",
            selected_playground_project="sample",
            output_filename="custom",
        )

        self.assertTrue(result["saved"])
        self.assertEqual(result["output_filename"], "custom.yml")
        playground_area.write_content.assert_called_once_with(
            "id: review screen\n",
            filename="custom.yml",
            project="sample",
            save=False,
        )
        playground_area.finalize.assert_called_once_with()

    def test_generate_and_save_requires_selected_files(self):
        result = generate_and_save_playground_review_screen(
            [],
            selected_playground_project="default",
        )

        self.assertFalse(result["saved"])
        self.assertEqual(result["error"], "Select at least one YAML file.")


if __name__ == "__main__":
    unittest.main()
