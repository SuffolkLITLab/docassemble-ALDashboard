# do not pre-load
"""Accessible names for form controls: problems found and names suggested."""

import os
import unittest

from docassemble.ALDashboard.pdf_accessibility import inspect_pdf_accessibility
from docassemble.ALDashboard.pdf_field_labels import (
    field_label_problem,
    simplify_field_label,
    suggest_field_labels,
)

_DOCKETING = os.path.join(
    os.path.dirname(__file__), "civil_docketing_statement_polished_repaired.pdf"
)


class TestSimplifyFieldLabel(unittest.TestCase):
    def test_instructions_become_the_information_they_ask_for(self):
        cases = {
            "Type name of county": "County",
            "Type the Mother's first name": "Mother's first name",
            "Type Father's zip code": "Father's zip code",
            "Please enter your date of birth:": "Your date of birth",
            "Fill in the case number": "Case number",
            "Check if you are the Plaintiff": "You are the Plaintiff",
            "Check this box if the mother is deceased": "The mother is deceased",
            "Print your name here": "Your name",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(simplify_field_label(given), expected)

    def test_a_long_name_of_phrase_stays_a_name(self):
        self.assertEqual(
            simplify_field_label("Name of the person who filed the first case"),
            "Name of the person who filed the first case",
        )

    def test_operating_instructions_say_nothing_about_the_field(self):
        for given in (
            "Click or hit spacebar to check this box",
            "Check this box",
            "Click here",
            "Type here",
        ):
            with self.subTest(given=given):
                self.assertEqual(simplify_field_label(given), "")

    def test_a_real_name_is_only_tidied(self):
        self.assertEqual(simplify_field_label("Date of birth *:"), "Date of birth")
        self.assertEqual(simplify_field_label("County"), "County")


class TestFieldLabelProblem(unittest.TestCase):
    def test_each_kind_of_bad_name_is_told_apart(self):
        cases = [
            ("", "county", "missing"),
            ("Text12", "Text12", "placeholder"),
            ("undefined", "court_name", "placeholder"),
            ("Click or hit spacebar to check this box", "Check Box 1", "placeholder"),
            ("users1 name", "users1_name", "derived"),
            ("Type name of county", "County name", "instruction"),
            ("County", "County name", ""),
            # A readable field name used as the tooltip is a fine name.
            ("Previous city 1", "Previous city 1", ""),
            ("Signature", "user_signature", ""),
        ]
        for tooltip, name, expected in cases:
            with self.subTest(tooltip=tooltip):
                self.assertEqual(field_label_problem(tooltip, name), expected)


def _word(text, x, y, width, height=0.014):
    return {"text": text, "box": {"x": x, "y": y, "width": width, "height": height}}


def _box(name, x, y, *, kind="checkbox", tooltip="", width=0.018, height=0.014):
    return {
        "name": name,
        "type": kind,
        "pageIndex": 0,
        "tooltip": tooltip,
        "box": {"x": x, "y": y, "width": width, "height": height},
    }


class TestOptionGroups(unittest.TestCase):
    def setUp(self):
        # "Is the mother married to the father?  [ ] Yes  [ ] No"
        self.words = {
            0: [
                _word("Is", 0.10, 0.30, 0.02),
                _word("the", 0.125, 0.30, 0.03),
                _word("mother", 0.16, 0.30, 0.06),
                _word("married?", 0.225, 0.30, 0.07),
                _word("Yes", 0.43, 0.30, 0.03),
                _word("No", 0.52, 0.30, 0.02),
            ]
        }

    def test_each_box_is_named_by_the_question_and_its_answer(self):
        labels = suggest_field_labels(
            [_box("Check Box 1", 0.40, 0.30), _box("Check Box 2", 0.49, 0.30)],
            self.words,
        )
        self.assertEqual(labels["Check Box 1"]["suggested"], "Is the mother married? Yes")
        self.assertEqual(labels["Check Box 2"]["suggested"], "Is the mother married? No")
        self.assertEqual(labels["Check Box 1"]["source"], "option-group")
        self.assertEqual(
            labels["Check Box 2"]["group"]["members"], ["Check Box 1", "Check Box 2"]
        )

    def test_a_shared_name_without_the_answer_is_a_problem(self):
        labels = suggest_field_labels(
            [
                _box("married_yes", 0.40, 0.30, tooltip="Mother married to father"),
                _box("married_no", 0.49, 0.30, tooltip="Mother married to father"),
            ],
            self.words,
        )
        self.assertEqual(labels["married_yes"]["problem"], "option-missing")
        self.assertEqual(labels["married_no"]["suggested"], "Is the mother married? No")

    def test_a_name_that_already_gives_the_answer_is_kept(self):
        labels = suggest_field_labels(
            [
                _box("married_yes", 0.40, 0.30, tooltip="Mother married to father: yes"),
                _box("married_no", 0.49, 0.30, tooltip="Mother married to father: no"),
            ],
            self.words,
        )
        self.assertEqual(labels["married_yes"]["problem"], "")
        self.assertEqual(labels["married_yes"]["suggested"], "Mother married to father: yes")

    def test_smaller_print_after_an_answer_belongs_to_the_next_blank(self):
        words = {
            0: [
                _word("Lives", 0.10, 0.50, 0.04),
                _word("with?", 0.145, 0.50, 0.04),
                _word("Mother", 0.22, 0.50, 0.05),
                _word("Other", 0.32, 0.50, 0.04),
                _word("(name)", 0.365, 0.501, 0.04, height=0.012),
            ]
        }
        labels = suggest_field_labels(
            [
                _box("a", 0.195, 0.50),
                _box("b", 0.295, 0.50),
                _box("name", 0.41, 0.498, kind="text", width=0.2, height=0.016),
            ],
            words,
        )
        self.assertEqual(labels["b"]["suggested"], "Lives with? Other")

    def test_a_question_that_wraps_takes_its_first_line(self):
        words = {
            0: [
                _word("Have", 0.10, 0.20, 0.04),
                _word("there", 0.145, 0.20, 0.04),
                _word("been", 0.19, 0.20, 0.04),
                _word("other", 0.235, 0.20, 0.04),
                _word("cases", 0.28, 0.20, 0.05),
                _word("about", 0.10, 0.215, 0.04),
                _word("this?", 0.145, 0.215, 0.04),
                _word("Yes", 0.22, 0.215, 0.03),
                _word("No", 0.28, 0.215, 0.02),
            ]
        }
        labels = suggest_field_labels(
            [_box("y", 0.195, 0.215), _box("n", 0.255, 0.215)], words
        )
        self.assertEqual(
            labels["y"]["suggested"], "Have there been other cases about this? Yes"
        )


class TestTextFieldLabels(unittest.TestCase):
    def test_an_instruction_tooltip_is_cleaned_before_looking_nearby(self):
        labels = suggest_field_labels(
            [_box("County name", 0.3, 0.1, kind="text", tooltip="Type name of county", width=0.2)],
            {0: [_word("Court", 0.1, 0.1, 0.05), _word("of:", 0.155, 0.1, 0.03)]},
        )
        self.assertEqual(labels["County name"]["problem"], "instruction")
        self.assertEqual(labels["County name"]["suggested"], "County")
        self.assertEqual(labels["County name"]["source"], "existing")

    def test_a_blank_with_no_name_takes_the_label_level_with_it(self):
        labels = suggest_field_labels(
            [_box("plaintiff", 0.15, 0.13, kind="text", width=0.5, height=0.03)],
            {
                0: [
                    _word("Caption", 0.05, 0.119, 0.05, height=0.011),
                    _word("used", 0.10, 0.119, 0.03, height=0.011),
                    _word("Plaintiff(s):", 0.05, 0.145, 0.08, height=0.011),
                ]
            },
        )
        self.assertEqual(labels["plaintiff"]["suggested"], "Plaintiff(s)")
        self.assertEqual(labels["plaintiff"]["source"], "nearby")


class TestInspectionFieldLabels(unittest.TestCase):
    def test_yes_and_no_boxes_on_a_real_form_are_paired_with_their_question(self):
        labels = inspect_pdf_accessibility(_DOCKETING)["field_labels"]
        self.assertEqual(
            labels["related_appeals_exist_yes"]["suggested"],
            "Do you know of any pending or anticipated appeals raising related issues? Yes",
        )
        self.assertEqual(
            labels["motion_for_judgment_is_filed_no"]["suggested"],
            "Motion for Judgment (Rule 50(b)): No",
        )
        self.assertEqual(labels["plaintiff"]["suggested"], "Plaintiff(s)")


if __name__ == "__main__":
    unittest.main()
