import unittest
from unittest.mock import patch
import tempfile
import os

from docassemble.ALDashboard.interview_linter import (
    get_all_text,
    get_misspelled_words,
    get_user_facing_text,
    lint_interview_content,
    lint_multiple_sources,
    load_interview,
    load_llm_prompt_templates,
    normalize_lint_mode,
    readability_consensus_assessment,
    run_deterministic_rules,
    run_llm_rules,
)


class TestInterviewLinterRules(unittest.TestCase):
    def _findings(self, yaml_content, lint_mode="full"):
        docs = load_interview(yaml_content)
        texts = get_all_text(docs)
        return run_deterministic_rules(docs, texts, yaml_content, lint_mode=lint_mode)

    def _rule_ids(self, yaml_content, lint_mode="full"):
        return {
            finding["rule_id"] for finding in self._findings(yaml_content, lint_mode)
        }

    def test_missing_question_id(self):
        yaml_content = """
---
question: Missing id
fields:
  - Name: user_name
"""
        findings = self._findings(yaml_content)
        self.assertIn(
            "missing-question-id", {finding["rule_id"] for finding in findings}
        )
        missing_id = next(f for f in findings if f["rule_id"] == "missing-question-id")
        self.assertTrue(missing_id.get("problematic_text"))

    def test_deterministic_finding_has_confidence(self):
        yaml_content = """
---
question: Missing id
fields:
  - Name: user_name
"""
        findings = self._findings(yaml_content)
        missing_id = next(f for f in findings if f["rule_id"] == "missing-question-id")
        self.assertEqual(missing_id.get("confidence"), "definite")

    def test_multiple_mandatory_blocks(self):
        yaml_content = """
---
mandatory: True
code: x = 1
---
mandatory: True
code: y = 2
"""
        self.assertIn("multiple-mandatory-blocks", self._rule_ids(yaml_content))

    def test_yesno_shortcut(self):
        yaml_content = """
---
id: q1
question: Do you agree?
yesno: agrees
"""
        self.assertIn("avoid-yesno-shortcuts", self._rule_ids(yaml_content))

    def test_combobox_usage(self):
        yaml_content = """
---
id: q1
question: Pick one
fields:
  - Option: selected
    datatype: combobox
"""
        self.assertIn("avoid-combobox", self._rule_ids(yaml_content))

    def test_subquestion_h1(self):
        yaml_content = """
---
id: q1
question: Heading
subquestion: |
  # Bad H1
"""
        self.assertIn("subquestion-h1", self._rule_ids(yaml_content))

    def test_skipped_heading_level(self):
        yaml_content = """
---
id: q1
question: Main
subquestion: |
  ## Section
  #### Too deep
"""
        self.assertIn("skipped-heading-level", self._rule_ids(yaml_content))

    def test_choices_without_stable_values(self):
        yaml_content = """
---
id: q1
question: Choose
fields:
  - Color: color
    choices:
      - Red
      - Blue
"""
        self.assertIn("choices-without-stable-values", self._rule_ids(yaml_content))

    def test_choices_shorthand_mapping_is_allowed(self):
        yaml_content = """
---
id: q1
question: Choose
fields:
  - Color: color
    choices:
      - Red: red_value
      - Blue: blue_value
"""
        self.assertNotIn("choices-without-stable-values", self._rule_ids(yaml_content))

    def test_language_en_flag(self):
        yaml_content = """
---
id: q1
language: en
question: Hello
"""
        self.assertIn("remove-language-en", self._rule_ids(yaml_content))

    def test_hardcoded_user_text_in_code(self):
        yaml_content = """
---
id: q1
code: |
  warning_text = "You should complete all required fields before moving on"
"""
        findings = self._findings(yaml_content)
        self.assertIn(
            "hardcoded-user-text-in-code", {finding["rule_id"] for finding in findings}
        )
        hardcoded = next(
            f for f in findings if f["rule_id"] == "hardcoded-user-text-in-code"
        )
        self.assertIn(
            "You should complete all required fields",
            hardcoded.get("problematic_text", ""),
        )

    def test_image_missing_alt_markdown(self):
        yaml_content = """
---
id: q1
question: |
  ![](docassemble.demo:data/static/logo.png)
"""
        findings = self._findings(yaml_content)
        self.assertIn(
            "image-missing-alt-text", {finding["rule_id"] for finding in findings}
        )
        image_finding = next(
            f for f in findings if f["rule_id"] == "image-missing-alt-text"
        )
        self.assertIn("![](", image_finding.get("problematic_text", ""))

    def test_image_missing_alt_file_tag(self):
        yaml_content = """
---
id: q1
question: |
  [FILE docassemble.demo:data/static/al_logo.svg, 100vw]
"""
        self.assertIn("image-missing-alt-text", self._rule_ids(yaml_content))

    def test_image_file_tag_alt_with_none_width_allowed(self):
        yaml_content = """
---
id: q1
question: |
  [FILE docassemble.demo:data/static/al_logo.svg, None, Assembly Line logo]
"""
        self.assertNotIn("image-missing-alt-text", self._rule_ids(yaml_content))

    def test_image_missing_alt_html(self):
        yaml_content = """
---
id: q1
subquestion: |
  <img src="/packagestatic/demo/logo.png">
"""
        self.assertIn("image-missing-alt-text", self._rule_ids(yaml_content))

    def test_field_missing_label(self):
        yaml_content = """
---
id: q1
question: Your email
fields:
  - field: user_email
    datatype: email
"""
        self.assertIn("field-missing-label", self._rule_ids(yaml_content))

    def test_no_label_is_treated_as_missing_label(self):
        yaml_content = """
---
id: q1
question: Your info
fields:
  - no label: user_info
    datatype: text
"""
        self.assertIn("field-missing-label", self._rule_ids(yaml_content))

    def test_non_descriptive_field_label(self):
        yaml_content = """
---
id: q1
question: Please answer
fields:
  - label: Value
    field: user_value
"""
        self.assertIn("non-descriptive-field-label", self._rule_ids(yaml_content))

    def test_blank_choice_label(self):
        yaml_content = """
---
id: q1
question: Pick one
fields:
  - Choice: user_choice
    choices:
      - "": empty_value
      - Good option: good_value
"""
        self.assertIn("blank-choice-label", self._rule_ids(yaml_content))

    def test_duplicate_field_labels(self):
        yaml_content = """
---
id: q1
question: Contact info
fields:
  - Email: user_email
  - Email: partner_email
"""
        self.assertIn("duplicate-field-label", self._rule_ids(yaml_content))

    def test_missing_screen_title(self):
        yaml_content = """
---
id: q1
subquestion: |
  This screen has instructions but no explicit question title. It should be reviewed.
fields:
  - Name: user_name
"""
        self.assertIn("missing-screen-title", self._rule_ids(yaml_content))

    def test_color_only_instructions(self):
        yaml_content = """
---
id: q1
question: |
  Fields in red are required.
"""
        self.assertIn("color-only-instructions", self._rule_ids(yaml_content))

    def test_inline_color_styling(self):
        yaml_content = """
---
id: q1
subquestion: |
  <span style="color: #f00;">Important</span>
"""
        self.assertIn("inline-color-styling", self._rule_ids(yaml_content))

    def test_non_descriptive_link_text(self):
        yaml_content = """
---
id: q1
question: |
  [Click here](https://example.com/forms)
"""
        self.assertIn("non-descriptive-link-text", self._rule_ids(yaml_content))

    def test_empty_link_text(self):
        yaml_content = """
---
id: q1
question: |
  [](https://example.com/blank)
"""
        self.assertIn("empty-link-text", self._rule_ids(yaml_content))

    def test_ambiguous_link_destinations(self):
        yaml_content = """
---
id: q1
subquestion: |
  [Learn more](https://example.com/one)
  [Learn more](https://example.com/two)
"""
        self.assertIn("ambiguous-link-destinations", self._rule_ids(yaml_content))

    def test_new_tab_without_warning(self):
        yaml_content = """
---
id: q1
subquestion: |
  <a href="https://example.com/forms" target="_blank">Form page</a>
"""
        self.assertIn("opens-new-tab-without-warning", self._rule_ids(yaml_content))

    def test_svg_missing_accessible_name(self):
        yaml_content = """
---
id: q1
subquestion: |
  <svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"></circle></svg>
"""
        self.assertIn("svg-missing-accessible-name", self._rule_ids(yaml_content))

    def test_table_missing_headers(self):
        yaml_content = """
---
id: q1
subquestion: |
  <table>
    <tr><td>A</td><td>B</td></tr>
    <tr><td>1</td><td>2</td></tr>
  </table>
"""
        self.assertIn("table-missing-headers", self._rule_ids(yaml_content))

    def test_positive_tabindex(self):
        yaml_content = """
---
id: q1
subquestion: |
  <input type="text" tabindex="2">
"""
        self.assertIn("positive-tabindex", self._rule_ids(yaml_content))

    def test_clickable_non_control_html(self):
        yaml_content = """
---
id: q1
subquestion: |
  <div onclick="openPanel()">Open details</div>
"""
        self.assertIn("clickable-non-control-html", self._rule_ids(yaml_content))

    def test_required_field_not_indicated(self):
        yaml_content = """
---
id: q1
question: Contact details
fields:
  - Email address: user_email
    required: True
"""
        self.assertIn("required-field-not-indicated", self._rule_ids(yaml_content))

    def test_validation_without_guidance(self):
        yaml_content = """
---
id: q1
question: PIN
fields:
  - PIN: user_pin
    pattern: "^[0-9]{4}$"
"""
        self.assertIn("validation-without-guidance", self._rule_ids(yaml_content))

    def test_maxlength_does_not_trigger_validation_without_guidance(self):
        yaml_content = """
---
id: q1
question: Name
fields:
  - Name: user_name
    maxlength: 24
"""
        self.assertNotIn("validation-without-guidance", self._rule_ids(yaml_content))

    def test_generic_validation_message(self):
        yaml_content = """
---
id: q1
question: Number
fields:
  - Age: user_age
    validation messages:
      min: Invalid input
"""
        self.assertIn("generic-validation-message", self._rule_ids(yaml_content))

    def test_ambiguous_button_text(self):
        yaml_content = """
---
id: q1
question: Continue?
buttons:
  - Go: next_screen
"""
        self.assertIn("ambiguous-button-text", self._rule_ids(yaml_content))

    def test_descriptive_link_text_allowed(self):
        yaml_content = """
---
id: q1
question: |
  [Download the filing checklist](https://example.com/checklist)
"""
        self.assertNotIn("non-descriptive-link-text", self._rule_ids(yaml_content))

    def test_long_sentence(self):
        yaml_content = """
---
id: q1
question: |
  This sentence intentionally contains many words to exceed the threshold and make sure the linter flags readability concerns for this very long sentence.
"""
        self.assertIn("long-sentences", self._rule_ids(yaml_content))

    def test_compound_questions(self):
        yaml_content = """
---
id: q1
question: |
  Do you want to continue or stop?
"""
        self.assertIn("compound-questions", self._rule_ids(yaml_content))

    def test_plain_language_replacements_single_word(self):
        yaml_content = """
---
id: q1
question: |
  We will commence the review now.
"""
        findings = self._findings(yaml_content)
        matching = [
            finding
            for finding in findings
            if finding["rule_id"] == "plain-language-replacements"
        ]
        self.assertTrue(matching)
        self.assertTrue(
            any(
                "commence" in finding.get("problematic_text", "").lower()
                for finding in matching
            )
        )
        self.assertTrue(all(finding["severity"] == "yellow" for finding in matching))

    def test_plain_language_replacements_phrase(self):
        yaml_content = """
---
id: q1
question: |
  This step is in accordance with the court order.
"""
        findings = self._findings(yaml_content)
        matching = [
            finding
            for finding in findings
            if finding["rule_id"] == "plain-language-replacements"
        ]
        self.assertTrue(matching)
        self.assertTrue(
            any(
                "in accordance with" in finding.get("problematic_text", "").lower()
                for finding in matching
            )
        )

    def test_overlong_labels(self):
        yaml_content = """
---
id: q1
question: |
  This is a deliberately oversized question heading that should trigger the warning because it exceeds the expected concise heading length for user-facing interview screens and keeps going.
"""
        self.assertIn("overlong-question-label", self._rule_ids(yaml_content))

    def test_too_many_fields(self):
        yaml_content = """
---
id: q1
question: Too many fields
fields:
  - A: a
  - B: b
  - C: c
  - D: d
  - E: e
  - F: f
  - G: g
"""
        self.assertIn("too-many-fields-on-screen", self._rule_ids(yaml_content))

    def test_wall_of_text(self):
        long_text = " ".join(["word"] * 130)
        yaml_content = f"""
---
id: q1
question: Main
subquestion: |
  {long_text}
"""
        self.assertIn("wall-of-text", self._rule_ids(yaml_content))

    def test_complex_screen_missing_help(self):
        yaml_content = """
---
id: q1
question: Complex
fields:
  - A: a
  - B: b
  - C: c
  - D: d
  - E: e
"""
        self.assertIn("complex-screen-missing-help", self._rule_ids(yaml_content))

    def test_spellcheck_ignores_invariant_choice_values(self):
        yaml_content = """
---
id: q1
question: |
  Select the placement type.
fields:
  - Placement type: placement_type
    choices:
      - Adult caregiver: adult_caregiver
      - No court case: no_court_case
      - DFPS approval: dfps
"""
        result = lint_interview_content(yaml_content)
        misspelled = set(result["misspelled"])
        self.assertNotIn("adult_caregiver", misspelled)
        self.assertNotIn("no_court_case", misspelled)
        self.assertNotIn("dfps", misspelled)

        docs = load_interview(yaml_content)
        user_facing = " ".join(get_user_facing_text(docs))
        self.assertNotIn("adult_caregiver", user_facing)
        self.assertNotIn("no_court_case", user_facing)

    def test_string_choice_requires_colon_value_pair(self):
        yaml_content = """
---
id: q1
question: Choose one
choices:
  - Good option
  - Better option: better_option
"""
        self.assertIn("choices-without-stable-values", self._rule_ids(yaml_content))

    def test_missing_metadata_fields(self):
        yaml_content = """
---
metadata:
  title: Test title
"""
        self.assertIn("missing-metadata-fields", self._rule_ids(yaml_content))

    def test_placeholder_language(self):
        yaml_content = """
---
id: q1
question: This is placeholder text.
"""
        self.assertIn("placeholder-language", self._rule_ids(yaml_content))

    def test_missing_exit_criteria_screen(self):
        yaml_content = """
---
metadata:
  can_I_use_this_form: |
    Ask if the user qualifies.
---
id: qualify
question: Are you eligible?
fields:
  - Eligible: user_eligible
    datatype: yesno
"""
        self.assertIn("missing-exit-criteria-screen", self._rule_ids(yaml_content))

    def test_missing_custom_theme(self):
        yaml_content = """
---
id: q1
question: Hello
"""
        self.assertIn("missing-custom-theme", self._rule_ids(yaml_content))

    def test_theme_include_passes(self):
        yaml_content = """
---
include:
  - docassemble.LITLabTheme:litlab_theme.yml
---
id: q1
question: Hello
"""
        self.assertNotIn("missing-custom-theme", self._rule_ids(yaml_content))

    def test_review_screen_missing_edit_links(self):
        yaml_content = """
---
id: q1
question: Pick one
fields:
  - Proceed: proceed_now
    datatype: yesno
---
id: review screen
question: Review your answers
subquestion: |
  This is a summary.
"""
        self.assertIn("review-screen-missing-edit-links", self._rule_ids(yaml_content))

    def test_review_screen_missing_key_choice_edits(self):
        yaml_content = """
---
id: q1
question: Pick one
fields:
  - Proceed: proceed_now
    datatype: yesno
---
id: review screen
question: Review your answers
review:
  - Edit: users[0].name.first
    button: |
      Name: ${ users[0].name.first }
"""
        self.assertIn(
            "review-screen-missing-key-choice-edits", self._rule_ids(yaml_content)
        )

    def test_variable_root_not_snake_case(self):
        yaml_content = """
---
id: q1
question: Name
fields:
  - First name: FirstName
"""
        self.assertIn("variable-root-not-snake-case", self._rule_ids(yaml_content))

    def test_prefer_person_objects(self):
        yaml_content = """
---
id: q1
question: Person info
fields:
  - First name: first_name
  - Last name: last_name
  - Street: street_address
  - City: city
  - State: state
"""
        self.assertIn("prefer-person-objects", self._rule_ids(yaml_content))

    def test_yaml_errors_reported_before_style_checks(self):
        yaml_content = """
---
id q1
question: Bad block
fields:
  - Name: user_name
"""
        result = lint_interview_content(yaml_content)
        self.assertTrue(result.get("yaml_errors"))
        self.assertIn(
            "yaml-parse-errors", {finding["rule_id"] for finding in result["findings"]}
        )
        self.assertNotIn(
            "missing-custom-theme",
            {finding["rule_id"] for finding in result["findings"]},
        )

    def test_valid_yaml_has_no_yaml_errors(self):
        yaml_content = """
---
id: q1
question: Hello
"""
        result = lint_interview_content(yaml_content)
        self.assertEqual(result.get("yaml_errors"), [])

    def test_wcag_mode_limits_rules(self):
        yaml_content = """
---
question: Missing id
subquestion: |
  ## Section title
  #### Skipped heading
fields:
  - Option: selected_option
    datatype: combobox
"""
        wcag_rule_ids = self._rule_ids(yaml_content, lint_mode="wcag-basic")
        self.assertIn("skipped-heading-level", wcag_rule_ids)
        self.assertIn("avoid-combobox", wcag_rule_ids)
        self.assertNotIn("missing-question-id", wcag_rule_ids)

    def test_lint_interview_content_sets_lint_mode(self):
        yaml_content = """
---
id: q1
question: Hello
"""
        result = lint_interview_content(yaml_content, lint_mode="wcag")
        self.assertEqual(result.get("lint_mode"), "wcag-basic")

    def test_normalize_lint_mode_invalid_defaults(self):
        self.assertEqual(normalize_lint_mode("not-a-mode"), "full")


class TestInterviewLinterLLM(unittest.TestCase):
    def test_prompt_templates_load(self):
        prompts = load_llm_prompt_templates()
        self.assertIn("llm_rules", prompts)
        self.assertGreaterEqual(len(prompts["llm_rules"]), 1)

    @patch("docassemble.ALDashboard.interview_linter.chat_completion")
    def test_run_llm_rules_uses_configured_prompts(self, mock_chat):
        mock_chat.return_value = {
            "findings": [
                {
                    "rule_id": "tone-and-respect",
                    "severity": "yellow",
                    "message": "Potentially directive phrasing.",
                    "screen_id": "q1",
                    "problematic_text": "Please do this now.",
                }
            ]
        }
        yaml_content = """
---
id: q1
question: Please do this now.
"""
        docs = load_interview(yaml_content)
        texts = get_all_text(docs)
        findings = run_llm_rules(docs, texts, enabled_rules=["tone-and-respect"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["source"], "llm")
        self.assertEqual(findings[0]["rule_id"], "tone-and-respect")
        self.assertEqual(findings[0]["screen_id"], "q1")
        self.assertTrue(findings[0].get("problematic_text"))

    @patch("docassemble.ALDashboard.interview_linter.chat_completion")
    def test_lint_output_adds_screen_link_for_llm_findings(self, mock_chat):
        mock_chat.return_value = {
            "findings": [
                {
                    "rule_id": "tone-and-respect",
                    "severity": "yellow",
                    "message": "Potentially directive phrasing.",
                    "screen_id": "q1",
                    "problematic_text": "Please do this now.",
                }
            ]
        }
        yaml_content = """
---
id: q1
question: Please do this now.
"""
        result = lint_interview_content(yaml_content, include_llm=True)
        llm_findings = [f for f in result["findings"] if f.get("source") == "llm"]
        self.assertTrue(llm_findings)
        self.assertEqual(llm_findings[0]["screen_link"], "#screen-q1")

    def test_lint_output_groups_by_severity(self):
        yaml_content = """
---
question: Missing id
fields:
  - Name: user_name
"""
        result = lint_interview_content(yaml_content)
        self.assertIn("findings", result)
        self.assertIn("findings_by_severity", result)
        self.assertIn("red", result["findings_by_severity"])
        self.assertGreaterEqual(len(result["findings_by_severity"]["red"]), 1)


class TestReadabilityConsensus(unittest.TestCase):
    @patch("docassemble.ALDashboard.interview_linter.textstat.text_standard")
    def test_readability_yellow_threshold(self, mock_text_standard):
        mock_text_standard.return_value = "8th and 9th grade"
        result = readability_consensus_assessment("dummy")
        self.assertEqual(result["severity"], "yellow")

    @patch("docassemble.ALDashboard.interview_linter.textstat.text_standard")
    def test_readability_red_threshold(self, mock_text_standard):
        mock_text_standard.return_value = "11th and 12th grade"
        result = readability_consensus_assessment("dummy")
        self.assertEqual(result["severity"], "red")


class TestSpellcheckLanguages(unittest.TestCase):
    @patch("docassemble.ALDashboard.interview_linter.SpellChecker")
    def test_misspelled_words_uses_intersection_for_multiple_languages(
        self, mock_spell
    ):
        language_unknown = {
            "en": {"hola", "formulario"},
            "es": {"the", "form"},
        }

        class _FakeSpell:
            def __init__(self, language="en"):
                self.language = language

            def unknown(self, words):
                return language_unknown.get(self.language, set())

        mock_spell.side_effect = lambda language="en": _FakeSpell(language=language)
        misspelled = get_misspelled_words("the form hola formulario", language="en,es")
        self.assertEqual(misspelled, set())


class TestLintMultipleSources(unittest.TestCase):
    def test_lint_multiple_sources_processes_multiple_files(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False
        ) as f1, tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f2:
            f1.write("---\nid: q1\nquestion: Hello world\n")
            f2.write("---\nid: q2\nquestion: Another screen\n")
            path1 = f1.name
            path2 = f2.name
        try:
            reports = lint_multiple_sources(
                [
                    {"name": "file1", "token": path1},
                    {"name": "file2", "token": path2},
                ]
            )
            self.assertEqual(len(reports), 2)
            self.assertTrue(all(report["error"] is None for report in reports))
            self.assertTrue(all(report["result"] is not None for report in reports))
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_lint_multiple_sources_reports_missing_path(self):
        reports = lint_multiple_sources(
            [{"name": "missing", "token": "/no/such/file.yml"}]
        )
        self.assertEqual(len(reports), 1)
        self.assertIsNotNone(reports[0]["error"])
        self.assertIsNone(reports[0]["result"])


if __name__ == "__main__":
    unittest.main()
