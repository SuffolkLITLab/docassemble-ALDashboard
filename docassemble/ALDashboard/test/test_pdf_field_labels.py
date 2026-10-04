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
            "Print your name here": "Printed name",
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


class TestCourtCaptionPlacement(unittest.TestCase):
    def test_docket_caption_beats_the_adjacent_court_heading(self):
        field = _box("docket_number", .734, .054, kind="text", width=.211, height=.025)
        words = {0: [
            _word("JUVENILE COURT DEPARTMENT", .346, .059, .287, .019),
            _word("DOCKET NO.", .727, .0355, .092, .015),
        ]}
        self.assertEqual(suggest_field_labels([field], words)["docket_number"]["suggested"], "DOCKET NO")

    def test_captions_touching_the_blank_are_found_without_borrowing_previous_row(self):
        fields = [
            _box("date", .08, .465, kind="text", width=.3, height=.025),
            _box("title", .5, .54, kind="text", width=.4, height=.025),
        ]
        words = {0: [
            _word("Date", .081, .4895, .035, .017),
            _word("Signature", .501, .488, .07, .017),
            _word("Title", .5, .5645, .04, .017),
        ]}
        result = suggest_field_labels(fields, words)
        self.assertEqual(result["date"]["suggested"], "Date")
        self.assertEqual(result["title"]["suggested"], "Title")

    def test_tall_answer_box_does_not_collect_the_signature_row_as_a_qualifier(self):
        field = _box("reasons", .075, .355, kind="text", width=.84, height=.11)
        words = {0: [
            _word("Reasons", .106, .334, .3, .02),
            _word("Date", .081, .49, .035, .017),
            _word("Print Name", .5, .53, .08, .017),
            _word("Signature", .501, .488, .07, .017),
        ]}
        self.assertEqual(suggest_field_labels([field], words)["reasons"]["suggested"], "Reasons")


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


class TestCorpusRegressions(unittest.TestCase):
    """Cases found by running these rules over the interview-template corpus."""

    def test_a_plain_name_of_label_is_already_a_name(self):
        for label in ("Name of Petitioner/Tenant", "NAME OF PLAINTIFF’S SCHOOL"):
            with self.subTest(label=label):
                self.assertEqual(field_label_problem(label, "x"), "")
                self.assertEqual(simplify_field_label(label), label)

    def test_print_name_is_the_printed_name_not_just_a_name(self):
        self.assertEqual(simplify_field_label("Print Name"), "Printed name")

    def test_an_identifier_read_aloud_is_not_a_name(self):
        self.assertEqual(field_label_problem("court_division_bmc", "court_bmc"), "derived")
        self.assertEqual(field_label_problem("NAME_Row_1", "AGE_Row_1"), "derived")

    def test_a_name_with_the_answer_and_its_directions_is_kept(self):
        words = {
            0: [
                _word("Decided?", 0.10, 0.30, 0.06),
                _word("Yes", 0.20, 0.30, 0.03),
                _word("(go", 0.235, 0.30, 0.02),
                _word("to", 0.258, 0.30, 0.012),
                _word("#10)", 0.273, 0.30, 0.03),
                _word("No", 0.34, 0.30, 0.02),
            ]
        }
        labels = suggest_field_labels(
            [
                _box("y", 0.175, 0.30, tooltip="Yes (if yes, go to #10"),
                _box("n", 0.315, 0.30, tooltip="No"),
            ],
            words,
        )
        self.assertEqual(labels["y"]["problem"], "")
        self.assertEqual(labels["y"]["group"]["option"], "Yes (go to #10)")

    def test_answers_printed_before_their_boxes(self):
        # "Own Home? Yes [ ] No [ ] Market Value $"
        words = {
            0: [
                _word("Own", 0.10, 0.10, 0.03),
                _word("Home?", 0.135, 0.10, 0.045),
                _word("Yes", 0.19, 0.10, 0.03),
                _word("No", 0.245, 0.10, 0.02),
                _word("Market", 0.30, 0.10, 0.05),
                _word("Value", 0.355, 0.10, 0.04),
            ]
        }
        labels = suggest_field_labels(
            [_box("home_yes", 0.221, 0.10), _box("home_no", 0.266, 0.10)], words
        )
        self.assertEqual(labels["home_yes"]["suggested"], "Own Home? Yes")
        self.assertEqual(labels["home_no"]["suggested"], "Own Home? No")

    def test_a_long_answer_does_not_flip_the_row_to_the_other_side(self):
        words = {
            0: [
                _word("This", 0.10, 0.20, 0.03),
                _word("is", 0.135, 0.20, 0.015),
                _word("the", 0.155, 0.20, 0.02),
                _word("60", 0.205, 0.20, 0.015),
                _word("Day", 0.225, 0.20, 0.025),
                _word("Report", 0.255, 0.20, 0.04),
            ]
            + [
                _word(text, 0.35 + index * 0.06, 0.20, 0.05)
                for index, text in enumerate(
                    "Annual Report for the reporting period of".split()
                )
            ]
        }
        labels = suggest_field_labels(
            [_box("sixty", 0.182, 0.20), _box("annual", 0.327, 0.20)], words
        )
        self.assertEqual(labels["sixty"]["suggested"], "This is the: 60 Day Report")
        self.assertEqual(labels["annual"]["group"]["option"], "Annual Report for the reporting period of")

    def test_boxes_drawn_over_words_are_not_answer_boxes(self):
        # "per week/month (circle one)" with a box over each word.
        words = {
            0: [
                _word("I", 0.50, 0.40, 0.01),
                _word("pay", 0.515, 0.40, 0.03),
                _word("per", 0.60, 0.40, 0.025),
                _word("week/month", 0.63, 0.40, 0.09),
                _word("(circle", 0.73, 0.40, 0.05),
                _word("one).", 0.785, 0.40, 0.04),
            ]
        }
        labels = suggest_field_labels(
            [_box("per_week", 0.632, 0.40), _box("per_month", 0.68, 0.40)], words
        )
        self.assertNotIn("group", labels["per_week"])

    def test_a_box_drawn_as_a_symbol_glyph_is_still_an_answer_box(self):
        # Forms that draw the empty box as a Wingdings "F" under the widget.
        words = {
            0: [
                _word("Married?", 0.10, 0.30, 0.07),
                _word("F", 0.401, 0.30, 0.016),
                _word("Yes", 0.43, 0.30, 0.03),
                _word("F", 0.491, 0.30, 0.016),
                _word("No", 0.52, 0.30, 0.02),
            ]
        }
        labels = suggest_field_labels(
            [_box("yes", 0.40, 0.30), _box("no", 0.49, 0.30)], words
        )
        self.assertEqual(labels["no"]["suggested"], "Married? No")

    def test_a_question_on_the_line_above_its_answers(self):
        words = {
            0: [
                _word("Do", 0.10, 0.50, 0.02),
                _word("you", 0.125, 0.50, 0.03),
                _word("own", 0.16, 0.50, 0.03),
                _word("land?", 0.195, 0.50, 0.04),
                _word("Yes", 0.13, 0.52, 0.03),
                _word("No", 0.20, 0.52, 0.02),
            ]
        }
        labels = suggest_field_labels(
            [_box("land_yes", 0.105, 0.52), _box("land_no", 0.175, 0.52)], words
        )
        self.assertEqual(labels["land_no"]["suggested"], "Do you own land? No")

    def test_a_dollar_sign_is_not_the_label_of_the_blank_after_it(self):
        labels = suggest_field_labels(
            [_box("income", 0.42, 0.70, kind="text", width=0.15, height=0.02)],
            {
                0: [
                    _word("J.", 0.08, 0.70, 0.015),
                    _word("Income", 0.10, 0.70, 0.05),
                    _word("from", 0.155, 0.70, 0.03),
                    _word("Assets", 0.19, 0.70, 0.045),
                    _word("$", 0.40, 0.70, 0.01),
                ]
            },
        )
        self.assertEqual(labels["income"]["suggested"], "Income from Assets")


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
