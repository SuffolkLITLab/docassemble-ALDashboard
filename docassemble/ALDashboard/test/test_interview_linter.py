# do not pre-load
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
    normalize_lint_mode,
    readability_consensus_assessment,
    run_deterministic_rules,
)


class FakeDAYamlFinding:
    message_id = "style_new_future_rule"
    severity = "warning"
    message = "Future rule emitted by DAYamlChecker."
    summary = "Future rule"
    code = "WS999"
    finding_class = "style"
    line_number = 3
    context = {"screen_id": "q1", "snippet": "problem text"}


class TestInterviewLinterDAYamlCheckerAdapter(unittest.TestCase):
    def test_current_dayamlchecker_finding_is_exposed_with_structured_metadata(self):
        yaml_content = """
---
question: Missing id
fields:
  - Name: user_name
---
id: q2
question: Another screen
"""
        findings = self._findings(yaml_content)
        missing_id = next(
            finding
            for finding in findings
            if finding["message_id"] == "missing_question_id"
        )
        self.assertEqual(missing_id["rule_id"], "missing-question-id")
        self.assertEqual(missing_id["source"], "dayamlchecker")
        self.assertEqual(missing_id["code"], "EG414")
        self.assertEqual(missing_id["finding_class"], "general")
        self.assertTrue(missing_id["screen_id"])

    @patch("docassemble.ALDashboard.interview_linter._collect_dayamlchecker_findings")
    def test_dashboard_adapts_unknown_dayamlchecker_rule(self, mock_collect):
        mock_collect.side_effect = [[FakeDAYamlFinding()], []]
        yaml_content = """
---
id: q1
question: Hello
"""
        findings = self._findings(yaml_content)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "style-new-future-rule")
        self.assertEqual(findings[0]["message_id"], "style_new_future_rule")
        self.assertEqual(findings[0]["severity"], "yellow")
        self.assertEqual(findings[0]["summary"], "Future rule")
        self.assertEqual(findings[0]["code"], "WS999")
        self.assertEqual(findings[0]["finding_class"], "style")
        self.assertEqual(findings[0]["screen_id"], "q1")
        self.assertEqual(findings[0]["problematic_text"], "problem text")

    @patch("docassemble.ALDashboard.interview_linter._collect_dayamlchecker_findings")
    def test_full_mode_keeps_dayamlchecker_translatability_findings(self, mock_collect):
        translatability_finding = type(
            "FakeTranslatabilityFinding",
            (),
            {
                "message_id": "translatability_future_rule",
                "severity": "warning",
                "message": "Translatability issue.",
                "summary": "Translatability issue",
                "code": "WT999",
                "finding_class": "translatability",
                "line_number": 3,
                "context": {"screen_id": "q1"},
            },
        )()
        mock_collect.side_effect = [[translatability_finding], []]
        yaml_content = """
---
id: q1
question: Hello
"""
        findings = self._findings(yaml_content)
        self.assertEqual(
            [finding["rule_id"] for finding in findings],
            ["translatability-future-rule"],
        )

    @patch("docassemble.ALDashboard.interview_linter._collect_dayamlchecker_findings")
    def test_wcag_mode_only_uses_accessibility_findings(self, mock_collect):
        style_finding = FakeDAYamlFinding()
        accessibility_finding = type(
            "FakeAccessibilityFinding",
            (),
            {
                "message_id": "accessibility_future_rule",
                "severity": "error",
                "message": "Accessibility issue.",
                "summary": "Accessibility issue",
                "code": "EA999",
                "finding_class": "accessibility",
                "line_number": 3,
                "context": {"screen_id": "q1"},
            },
        )()
        mock_collect.return_value = [style_finding, accessibility_finding]
        yaml_content = """
---
id: q1
question: Hello
"""
        findings = self._findings(yaml_content, lint_mode="wcag-basic")
        self.assertEqual(
            [finding["rule_id"] for finding in findings],
            ["accessibility-future-rule"],
        )

    @patch("docassemble.ALDashboard.interview_linter._collect_dayamlchecker_findings")
    def test_lint_interview_content_reuses_wcag_validation_findings(self, mock_collect):
        accessibility_finding = type(
            "FakeAccessibilityFinding",
            (),
            {
                "message_id": "accessibility_future_rule",
                "severity": "error",
                "message": "Accessibility issue.",
                "summary": "Accessibility issue",
                "code": "EA999",
                "finding_class": "accessibility",
                "line_number": 3,
                "context": {"screen_id": "q1"},
            },
        )()
        mock_collect.return_value = [accessibility_finding]
        yaml_content = """
---
id: q1
question: Hello
"""
        result = lint_interview_content(yaml_content, lint_mode="wcag-basic")
        self.assertEqual(
            [finding["rule_id"] for finding in result["findings"]],
            ["accessibility-future-rule"],
        )
        self.assertEqual(mock_collect.call_count, 1)
        self.assertEqual(
            mock_collect.call_args.kwargs["lint_mode"], "accessibility"
        )

    @patch("docassemble.ALDashboard.interview_linter._collect_dayamlchecker_findings")
    def test_lint_interview_content_keeps_current_report_shape(self, mock_collect):
        mock_collect.side_effect = [[FakeDAYamlFinding()], []]
        yaml_content = """
---
id: q1
question: Hello
"""
        result = lint_interview_content(yaml_content)
        self.assertEqual(result.get("lint_mode"), "full")
        self.assertIn("findings", result)
        self.assertIn("findings_by_severity", result)
        self.assertEqual(result["findings"][0]["rule_id"], "style-new-future-rule")
        self.assertEqual(len(result["findings_by_severity"]["yellow"]), 1)
        self.assertEqual(mock_collect.call_count, 2)
        self.assertTrue(mock_collect.call_args_list[0].kwargs["include_style"])

    def _findings(self, yaml_content, lint_mode="full"):
        docs = load_interview(yaml_content)
        texts = get_all_text(docs)
        return run_deterministic_rules(docs, texts, yaml_content, lint_mode=lint_mode)



class TestInterviewLinterStyleDelegation(unittest.TestCase):
    @patch("docassemble.ALDashboard.interview_linter._collect_dayamlchecker_findings")
    def test_ai_style_checks_are_delegated_to_dayamlchecker(self, mock_collect):
        mock_collect.side_effect = [[FakeDAYamlFinding()], []]
        yaml_content = """
---
id: q1
question: Hello
"""
        docs = load_interview(yaml_content)
        findings = run_deterministic_rules(
            docs,
            get_all_text(docs),
            yaml_content,
            include_llm=True,
        )
        self.assertEqual(findings[0]["source"], "dayamlchecker")
        self.assertTrue(mock_collect.call_args_list[0].kwargs["include_style_llm"])

    @patch("docassemble.ALDashboard.interview_linter._collect_dayamlchecker_findings")
    def test_lint_output_groups_by_severity(self, mock_collect):
        mock_collect.side_effect = [[FakeDAYamlFinding()], []]
        yaml_content = """
---
id: q1
question: Hello
"""
        result = lint_interview_content(yaml_content)
        self.assertIn("findings", result)
        self.assertIn("findings_by_severity", result)
        self.assertIn("yellow", result["findings_by_severity"])
        self.assertGreaterEqual(len(result["findings_by_severity"]["yellow"]), 1)


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
        with (
            tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f1,
            tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f2,
        ):
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
