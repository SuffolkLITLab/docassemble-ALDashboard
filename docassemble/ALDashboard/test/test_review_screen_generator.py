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


if __name__ == "__main__":
    unittest.main()
