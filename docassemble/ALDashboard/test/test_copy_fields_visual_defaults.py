"""Tests for _apply_pdf_field_visual_defaults preserve_button_appearances behavior.

These tests verify that:
- Red border color metadata (/MK[BC]) is removed from checkbox widgets.
- With preserve_button_appearances=True the /AP stream is kept for /Btn fields.
- Without the flag the old behavior is preserved (AP deleted for all widgets).
- /AP is still removed from text (/Tx) widgets regardless of the flag.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

# Prefix that stubs heavy docassemble dependencies so api_labelers can be
# imported in a bare Python process (same approach as test_api_labelers_query_params.py).
_STUB_PREFIX = textwrap.dedent("""
    import importlib
    import sys
    import types
    from flask import Flask

    fake_app = Flask("api_labelers_test")

    app_object_module = types.ModuleType("docassemble.webapp.app_object")
    app_object_module.app = fake_app
    app_object_module.csrf = types.SimpleNamespace(exempt=lambda func: func)

    class _FakePipeline:
        def set(self, *args, **kwargs): return self
        def expire(self, *args, **kwargs): return self
        def execute(self): return None

    class _FakeRedis:
        def get(self, *args, **kwargs): return None
        def pipeline(self): return _FakePipeline()

    server_module = types.ModuleType("docassemble.webapp.server")
    server_module.api_verify = lambda: False
    server_module.jsonify_with_status = lambda body, status: (body, status)
    server_module.r = _FakeRedis()

    worker_common_module = types.ModuleType("docassemble.webapp.worker_common")
    worker_common_module.workerapp = types.SimpleNamespace(
        AsyncResult=lambda *args, **kwargs: None
    )

    base_config_module = types.ModuleType("docassemble.base.config")
    base_config_module.daconfig = {}

    base_functions_module = types.ModuleType("docassemble.base.functions")
    base_functions_module.this_thread = types.SimpleNamespace(current_info={})

    base_util_module = types.ModuleType("docassemble.base.util")
    base_util_module.log = lambda *args, **kwargs: None

    sys.modules["docassemble.webapp.app_object"] = app_object_module
    sys.modules["docassemble.webapp.server"] = server_module
    sys.modules["docassemble.webapp.worker_common"] = worker_common_module
    sys.modules["docassemble.base.config"] = base_config_module
    sys.modules["docassemble.base.functions"] = base_functions_module
    sys.modules["docassemble.base.util"] = base_util_module
    sys.modules.pop("docassemble.ALDashboard.api_labelers", None)

    module = importlib.import_module("docassemble.ALDashboard.api_labelers")
    _apply_pdf_field_visual_defaults = module._apply_pdf_field_visual_defaults
""")


def _run_probe(probe_code: str) -> str:
    """Run *probe_code* inside a stubbed subprocess and return stdout."""
    script = _STUB_PREFIX + "\n" + textwrap.dedent(probe_code)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Subprocess failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip()


class TestApplyPDFFieldVisualDefaultsButtonPreservation(unittest.TestCase):
    """_apply_pdf_field_visual_defaults: preserve_button_appearances behavior."""

    def _make_pdf_with_checkbox(self, has_ap: bool = True, has_red_border: bool = True) -> str:
        """Create a minimal PDF with a checkbox widget.  Returns the file path."""
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))

        mk = Dictionary()
        if has_red_border:
            mk["/BC"] = Array([1, 0, 0])  # red border colour

        field_dict: dict = {
            "/FT": Name("/Btn"),
            "/T": String("my_checkbox"),
            "/V": Name("/Off"),
            "/AS": Name("/Off"),
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/Rect": Array([10, 10, 30, 30]),
            "/MK": mk,
        }
        if has_ap:
            checked_stream = pikepdf.Stream(pdf, b"q Q")
            off_stream = pikepdf.Stream(pdf, b"q Q")
            field_dict["/AP"] = Dictionary(
                {"/N": Dictionary({"/Yes": checked_stream, "/Off": off_stream})}
            )

        field = pdf.make_indirect(Dictionary(field_dict))
        page.obj["/Annots"] = Array([field])
        pdf.Root["/AcroForm"] = Dictionary({"/Fields": Array([field])})
        pdf.save(pdf_path)
        pdf.close()
        return pdf_path

    def _make_pdf_with_text_field(self, has_ap: bool = True) -> str:
        """Create a minimal PDF with a text widget.  Returns the file path."""
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))

        field_dict: dict = {
            "/FT": Name("/Tx"),
            "/T": String("my_text"),
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/Rect": Array([10, 50, 200, 70]),
            "/MK": Dictionary({"/BC": Array([1, 0, 0])}),
        }
        if has_ap:
            ap_stream = pikepdf.Stream(pdf, b"q Q")
            field_dict["/AP"] = Dictionary({"/N": ap_stream})

        field = pdf.make_indirect(Dictionary(field_dict))
        page.obj["/Annots"] = Array([field])
        pdf.Root["/AcroForm"] = Dictionary({"/Fields": Array([field])})
        pdf.save(pdf_path)
        pdf.close()
        return pdf_path

    # ------------------------------------------------------------------ #
    # Helpers to run the function and inspect the output                  #
    # ------------------------------------------------------------------ #

    def _run_visual_defaults(
        self, pdf_path: str, *, preserve_button_appearances: bool
    ) -> str:
        flag = "True" if preserve_button_appearances else "False"
        probe = f"""
import json, os, sys
pdf_path = {pdf_path!r}
_apply_pdf_field_visual_defaults(
    pdf_path,
    preserve_button_appearances={flag},
)
print("done")
"""
        return _run_probe(probe)

    def _read_first_widget(self, pdf_path: str) -> dict:
        import pikepdf
        with pikepdf.open(pdf_path) as pdf:
            annot = pdf.pages[0]["/Annots"][0]
            result = {
                "has_ap": "/AP" in annot,
                "has_mk_bc": (
                    "/MK" in annot
                    and isinstance(annot["/MK"], pikepdf.Dictionary)
                    and "/BC" in annot["/MK"]
                ),
            }
        return result

    # ------------------------------------------------------------------ #
    # Tests                                                               #
    # ------------------------------------------------------------------ #

    def test_checkbox_red_border_removed(self):
        """Red /MK[BC] is stripped from checkbox widgets."""
        pdf_path = self._make_pdf_with_checkbox(has_ap=True, has_red_border=True)
        try:
            self._run_visual_defaults(pdf_path, preserve_button_appearances=True)
            widget = self._read_first_widget(pdf_path)
            self.assertFalse(widget["has_mk_bc"], "/MK[BC] (red border) should have been removed")
        finally:
            os.unlink(pdf_path)

    def test_checkbox_ap_preserved_with_flag(self):
        """With preserve_button_appearances=True, /AP stream is kept for /Btn widgets."""
        pdf_path = self._make_pdf_with_checkbox(has_ap=True, has_red_border=True)
        try:
            self._run_visual_defaults(pdf_path, preserve_button_appearances=True)
            widget = self._read_first_widget(pdf_path)
            self.assertTrue(widget["has_ap"], "/AP should be preserved for checkbox when preserve_button_appearances=True")
        finally:
            os.unlink(pdf_path)

    def test_checkbox_ap_deleted_without_flag(self):
        """Without the flag (old behavior), /AP is deleted for all widgets."""
        pdf_path = self._make_pdf_with_checkbox(has_ap=True, has_red_border=True)
        try:
            self._run_visual_defaults(pdf_path, preserve_button_appearances=False)
            widget = self._read_first_widget(pdf_path)
            self.assertFalse(widget["has_ap"], "/AP should be deleted when preserve_button_appearances=False")
        finally:
            os.unlink(pdf_path)

    def test_text_field_ap_always_deleted(self):
        """/AP is removed from text (/Tx) widgets regardless of the flag."""
        pdf_path = self._make_pdf_with_text_field(has_ap=True)
        try:
            self._run_visual_defaults(pdf_path, preserve_button_appearances=True)
            widget = self._read_first_widget(pdf_path)
            self.assertFalse(widget["has_ap"], "/AP should be removed from text fields even with preserve_button_appearances=True")
        finally:
            os.unlink(pdf_path)

    def test_text_field_red_border_removed(self):
        """Red /MK[BC] is stripped from text field widgets too."""
        pdf_path = self._make_pdf_with_text_field(has_ap=True)
        try:
            self._run_visual_defaults(pdf_path, preserve_button_appearances=True)
            widget = self._read_first_widget(pdf_path)
            self.assertFalse(widget["has_mk_bc"], "/MK[BC] should be removed from text fields")
        finally:
            os.unlink(pdf_path)


if __name__ == "__main__":
    unittest.main()
