# do not pre-load
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from docassemble.ALDashboard.review_screen_generator import generate_review_screen_yaml

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
        self.assertIn("question: Review your answers", output)
        self.assertIn("First name", output)

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
            if "code" in document and "generate_review_screen_yaml" in document["code"]
        )
        self.assertIn(
            "[uploaded_file.slurp() for uploaded_file in yaml_file]",
            generator_code,
        )
        self.assertNotIn("skippable_types", interview_source)
        self.assertNotIn("review_fields_temp", interview_source)


if __name__ == "__main__":
    unittest.main()
