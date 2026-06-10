# do not pre-load
import unittest

from docassemble.ALDashboard.review_screen_generator import generate_review_screen_yaml


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

    def test_supports_mapping_form_objects(self):
        sample = """
---
objects:
  children[i].lived_with: ALIndividual
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn("id: revisit children[i].lived_with", output)
        self.assertIn("children[i].lived_with.revisit", output)

    def test_preserves_list_form_objects(self):
        sample = """
---
objects:
  - users: ALPeopleList
"""
        output = generate_review_screen_yaml([sample])

        self.assertIn("id: revisit users", output)
        self.assertIn("users.revisit", output)


if __name__ == "__main__":
    unittest.main()
