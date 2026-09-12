# do not pre-load
"""Choice values must survive translation unchanged. See issue #287."""

import unittest

import pluggy

from docassemble.base.interview_source import InterviewSourceString
from docassemble.base.parse import Interview
from docassemble.base.plugin_manager import pm

from docassemble.ALDashboard.translation_stable_values import (
    collect_stable_values,
    stable_values_to_preserve,
)

try:
    # docassemble 1.10 keeps per-request state in a contextvar that only the
    # web application populates, so the tests have to stand in for the server.
    from docassemble.base.thread_context import empty_globals, global_context
except ModuleNotFoundError:  # pragma: no cover - docassemble < 1.10
    empty_globals = None
    global_context = None

hookimpl = pluggy.HookimplMarker("docassemble")


class DefaultsPlugin:
    """Supplies the defaults a docassemble server would provide with no config."""

    @hookimpl
    def get_default_language(self) -> str:
        return "en"

    @hookimpl
    def get_default_dialect(self) -> str:
        return "us"

    @hookimpl
    def get_default_locale(self) -> str:
        return "en_US.utf8"

    @hookimpl
    def get_default_country(self) -> str:
        return "US"

    @hookimpl
    def get_default_timezone(self) -> str:
        return "America/New_York"

    @hookimpl
    def get_configuration(self) -> dict:
        return {}

    @hookimpl
    def get_main_page_parts(self) -> dict:
        return {}


_plugin = DefaultsPlugin()
_context = None


def setUpModule():
    pm.register(_plugin, name="aldashboard_stable_value_test_defaults")
    global _context
    if global_context is not None:
        _context = global_context(empty_globals())
        _context.__enter__()


def tearDownModule():
    if _context is not None:
        _context.__exit__(None, None, None)
    pm.unregister(_plugin)


def parse_interview(yaml_text: str) -> Interview:
    source = InterviewSourceString(
        content=yaml_text,
        directory=None,
        package="docassemble.ALDashboard",
        path="stable_values_test.yml",
    )
    source.translating = True
    return Interview(source=source)


def translatable_segments(interview: Interview) -> set:
    segments = set()
    for question in interview.all_questions:
        segments.update(getattr(question, "translations", []))
    return segments


class TestStableChoiceValues(unittest.TestCase):
    def test_mapping_choices_pin_the_stored_value(self):
        """A `choices:` mapping is the shape that leaks values to translators."""
        interview = parse_interview("""
question: What kind of thing is it?
fields:
  - Kind: thing_kind
    datatype: radio
    choices:
      Other: other
      Apple: apple
""")
        # docassemble really does offer these values up for translation.
        self.assertIn("other", translatable_segments(interview))
        to_preserve = stable_values_to_preserve(interview)
        self.assertIn("other", to_preserve)
        self.assertIn("apple", to_preserve)
        # The labels stay translatable.
        self.assertNotIn("Other", to_preserve)
        self.assertNotIn("Apple", to_preserve)

    def test_list_choices_pin_the_stored_value(self):
        """`- Other: other` keeps the value out of translations, and we still pin it."""
        interview = parse_interview("""
question: What kind of thing is it?
fields:
  - Kind: thing_kind
    datatype: radio
    choices:
      - Other: other
      - Apple: apple
""")
        to_preserve = stable_values_to_preserve(interview)
        self.assertIn("other", to_preserve)
        self.assertIn("apple", to_preserve)
        self.assertNotIn("Other", to_preserve)

    def test_sequence_choices_pin_the_stored_value(self):
        interview = parse_interview("""
question: Pick one
fields:
  - Pick: seq_pick
    datatype: radio
    choices:
      - [seq_value, Sequence label]
""")
        self.assertIn("seq_value", translatable_segments(interview))
        to_preserve = stable_values_to_preserve(interview)
        self.assertIn("seq_value", to_preserve)
        self.assertNotIn("Sequence label", to_preserve)

    def test_buttons_pin_the_stored_value(self):
        interview = parse_interview("""
question: Pick one
field: button_pick
buttons:
  - Yes indeed: yes_value
  - No way: no_value
""")
        to_preserve = stable_values_to_preserve(interview)
        self.assertIn("yes_value", to_preserve)
        self.assertIn("no_value", to_preserve)
        self.assertNotIn("Yes indeed", to_preserve)

    def test_show_if_comparison_value_is_pinned(self):
        interview = parse_interview("""
question: Tell me more
fields:
  - Gate: gate_var
    datatype: radio
    choices:
      - Yes it is: affirmative
  - Dependent: dep_var
    show if:
      variable: gate_var
      is: affirmative
""")
        self.assertIn("affirmative", translatable_segments(interview))
        self.assertIn("affirmative", stable_values_to_preserve(interview))

    def test_action_button_action_and_arguments_are_pinned(self):
        """An action button dispatches its action name and arguments to code."""
        interview = parse_interview("""
question: What next?
subquestion: Choose something
action buttons:
  - label: Start over
    action: some_action
    color: primary
    arguments:
      stage: review_stage
""")
        segments = translatable_segments(interview)
        # docassemble really does offer both of these up for translation.
        self.assertIn("some_action", segments)
        self.assertIn("review_stage", segments)
        to_preserve = stable_values_to_preserve(interview)
        self.assertIn("some_action", to_preserve)
        self.assertIn("review_stage", to_preserve)
        # The button's label is the only part a user reads.
        self.assertNotIn("Start over", to_preserve)

    def test_action_button_label_is_left_translatable(self):
        """A label that matches an action name elsewhere still gets translated."""
        interview = parse_interview("""
question: What next?
subquestion: Choose something
action buttons:
  - label: Review
    action: Review
""")
        self.assertNotIn("Review", stable_values_to_preserve(interview))

    def test_value_that_is_also_a_label_is_left_translatable(self):
        """A bare choice stores the label itself, so pinning it would lose the label."""
        interview = parse_interview("""
question: What color?
fields:
  - Color: color
    datatype: radio
    choices:
      - Red
      - Blue
""")
        to_preserve = stable_values_to_preserve(interview)
        self.assertNotIn("Red", to_preserve)
        self.assertNotIn("Blue", to_preserve)

    def test_value_used_as_a_label_elsewhere_is_left_translatable(self):
        interview = parse_interview("""
question: What kind of thing is it?
fields:
  - Kind: thing_kind
    datatype: radio
    choices:
      - Other: other
---
question: Pick a word
fields:
  - Word: word_pick
    datatype: radio
    choices:
      - other: some_other_value
""")
        self.assertNotIn("other", stable_values_to_preserve(interview))

    def test_numeric_values_are_not_pinned(self):
        """docassemble never offers digits for translation, so there is nothing to pin."""
        interview = parse_interview("""
question: How many?
fields:
  - Count: how_many
    datatype: radio
    choices:
      - One: 1
      - Two: 2
""")
        to_preserve = stable_values_to_preserve(interview)
        self.assertNotIn("1", to_preserve)
        self.assertNotIn("2", to_preserve)

    def test_code_driven_choices_are_skipped(self):
        interview = parse_interview("""
question: Pick one
fields:
  - Pick: code_pick
    datatype: radio
    code: |
      some_list_of_choices
""")
        self.assertEqual(stable_values_to_preserve(interview), {})

    def test_collect_reports_the_source_of_each_value(self):
        interview = parse_interview("""
id: thing kind
question: What kind of thing is it?
fields:
  - Kind: thing_kind
    datatype: radio
    choices:
      - Other: other
""")
        values, _display_texts = collect_stable_values(interview)
        by_text = {value.text: value for value in values}
        self.assertEqual(by_text["other"].question_id, "thing kind")
        self.assertEqual(by_text["other"].language, "en")
        self.assertEqual(
            by_text["other"].interview_name,
            "docassemble.ALDashboard:data/questions/stable_values_test.yml",
        )


if __name__ == "__main__":
    unittest.main()
