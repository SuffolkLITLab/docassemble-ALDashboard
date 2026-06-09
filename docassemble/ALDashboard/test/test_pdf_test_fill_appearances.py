# do not pre-load
"""Regression checks for the PDF labeler's test-fill appearance pipeline."""

import ast
import importlib.resources
import unittest


def _test_fill_function() -> ast.FunctionDef:
    source = (
        importlib.resources.files("docassemble.ALDashboard") / "api_labelers.py"
    ).read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "pdf_labeler_test_fill":
            return node
    raise AssertionError("pdf_labeler_test_fill was not found")


def _calls(function: ast.FunctionDef) -> list[ast.Call]:
    return [node for node in ast.walk(function) if isinstance(node, ast.Call)]


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


class TestPdfTestFillAppearances(unittest.TestCase):
    def test_collects_and_forwards_field_appearance_settings(self):
        function = _test_fill_function()
        calls = _calls(function)
        call_names = [_call_name(call) for call in calls]

        self.assertIn("_collect_fields_with_explicit_background", call_names)
        self.assertIn("_collect_checkbox_border_widths", call_names)
        self.assertIn("restore_checkbox_appearances", call_names)

        visual_defaults = next(
            call for call in calls if _call_name(call) == "_apply_pdf_field_visual_defaults"
        )
        self.assertIn(
            "explicit_background_fields",
            {keyword.arg for keyword in visual_defaults.keywords},
        )

        restore = next(
            call for call in calls if _call_name(call) == "restore_checkbox_appearances"
        )
        self.assertIn(
            "checkbox_border_widths",
            {keyword.arg for keyword in restore.keywords},
        )

    def test_restores_checkbox_appearances_before_filling(self):
        function = _test_fill_function()
        calls_by_name = {_call_name(call): call for call in _calls(function)}
        self.assertLess(
            calls_by_name["restore_checkbox_appearances"].lineno,
            calls_by_name["fill_template"].lineno,
        )

    def test_uses_docassemble_flattened_attachment_behavior(self):
        function = _test_fill_function()
        fill_call = next(
            call for call in _calls(function) if _call_name(call) == "fill_template"
        )
        editable = next(
            keyword.value
            for keyword in fill_call.keywords
            if keyword.arg == "editable"
        )
        self.assertIsInstance(editable, ast.Constant)
        self.assertIs(editable.value, False)


if __name__ == "__main__":
    unittest.main()
