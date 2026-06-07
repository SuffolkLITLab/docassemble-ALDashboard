# do not pre-load
"""Tests for the pdf_repair module.

These tests exercise logic that does NOT require Ghostscript or ocrmypdf
to be installed.  External-tool tests use ``unittest.mock`` to patch
``subprocess.run`` and ``shutil.which``.
"""

import os
import tempfile
import unittest
from unittest import mock
from typing import Any, Dict, List

from docassemble.ALDashboard.pdf_repair import (
    PDFRepairError,
    REPAIR_ACTIONS,
    _assert_pdf,
    _copy_if_same,
    _require_executable,
    auto_repair,
    ghostscript_reprint,
    list_repair_actions,
    ocr_pdf,
    normalize_signature_fields,
    qpdf_repair,
    repair_metadata,
    restore_checkbox_appearances,
    run_repair,
    unlock_pdf,
)


def _make_minimal_pdf(path: str) -> None:
    """Write the smallest recognisable PDF header for assertion helpers."""
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.4 minimal\n")


def _make_openable_pdf(path: str) -> None:
    """Write a minimal PDF that pikepdf can open."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)
    pdf.close()


class TestHelpers(unittest.TestCase):
    def test_require_executable_found(self):
        with mock.patch("shutil.which", return_value="/usr/bin/python3"):
            self.assertEqual(_require_executable("python3"), "/usr/bin/python3")

    def test_require_executable_missing(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(PDFRepairError):
                _require_executable("nonexistent_tool_xyz")

    def test_assert_pdf_valid(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.7 test")
            tmp.flush()
            path = tmp.name
        try:
            _assert_pdf(path)  # should not raise
        finally:
            os.remove(path)

    def test_assert_pdf_not_a_pdf(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"NOT A PDF")
            tmp.flush()
            path = tmp.name
        try:
            with self.assertRaises(PDFRepairError):
                _assert_pdf(path)
        finally:
            os.remove(path)

    def test_assert_pdf_missing_file(self):
        with self.assertRaises(PDFRepairError):
            _assert_pdf("/tmp/nonexistent_file_for_test.pdf")

    def test_copy_if_same_does_nothing_on_same(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4")
            tmp.flush()
            path = tmp.name
        try:
            _copy_if_same(path, path)  # should not raise
            self.assertTrue(os.path.exists(path))
        finally:
            os.remove(path)

    def test_copy_if_same_copies(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as src:
            src.write(b"%PDF-data")
            src.flush()
            src_path = src.name
        dst_path = src_path + ".copy"
        try:
            _copy_if_same(src_path, dst_path)
            with open(dst_path, "rb") as fh:
                self.assertEqual(fh.read(), b"%PDF-data")
        finally:
            os.remove(src_path)
            if os.path.exists(dst_path):
                os.remove(dst_path)


class TestListRepairActions(unittest.TestCase):
    def test_returns_all_actions(self):
        actions = list_repair_actions()
        names = {a["action"] for a in actions}
        self.assertEqual(
            names,
            {
                "auto",
                "ghostscript_reprint",
                "qpdf_repair",
                "restore_checkbox_appearances",
                "unlock",
                "repair_metadata",
                "ocr",
            },
        )
        for action in actions:
            self.assertIn("description", action)
            self.assertTrue(len(action["description"]) > 0)


class TestRunRepairDispatch(unittest.TestCase):
    def test_unknown_action(self):
        with self.assertRaises(PDFRepairError) as ctx:
            run_repair("bogus", "/tmp/in.pdf", "/tmp/out.pdf")
        self.assertIn("bogus", str(ctx.exception))

    def test_dispatch_calls_function(self):
        sentinel = {"action": "mock_action"}
        fake_func = mock.MagicMock(return_value=sentinel)
        with mock.patch.dict(REPAIR_ACTIONS, {"mock_action": fake_func}):
            result = run_repair(
                "mock_action", "/tmp/in.pdf", "/tmp/out.pdf", options={"foo": "bar"}
            )
        fake_func.assert_called_once_with("/tmp/in.pdf", "/tmp/out.pdf", foo="bar")
        self.assertEqual(result, sentinel)


class TestGhostscriptReprint(unittest.TestCase):
    @mock.patch("shutil.which", return_value="/usr/bin/gs")
    @mock.patch("subprocess.run")
    def test_basic_reprint(self, mock_run, _mock_which):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            _make_minimal_pdf(inp.name)
            in_path = inp.name
        out_path = in_path + ".out.pdf"
        try:

            def side_effect(*args, **kwargs):
                # Simulate gs writing a valid PDF
                output_file = None
                for arg in args[0]:
                    if arg.startswith("-sOutputFile="):
                        output_file = arg.split("=", 1)[1]
                        break
                if output_file:
                    _make_minimal_pdf(output_file)
                return mock.MagicMock(returncode=0, stderr="")

            mock_run.side_effect = side_effect
            result = ghostscript_reprint(in_path, out_path, preserve_fields=False)
            self.assertEqual(result["action"], "ghostscript_reprint")
            self.assertFalse(result["preserve_fields"])
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    @mock.patch("shutil.which", return_value="/usr/bin/gs")
    @mock.patch("subprocess.run")
    def test_gs_failure(self, mock_run, _mock_which):
        mock_run.return_value = mock.MagicMock(returncode=1, stderr="gs error")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            _make_minimal_pdf(inp.name)
            in_path = inp.name
        try:
            with self.assertRaises(PDFRepairError):
                ghostscript_reprint(in_path, in_path + ".out")
        finally:
            os.remove(in_path)


class TestOCR(unittest.TestCase):
    @mock.patch(
        "docassemble.ALDashboard.pdf_repair._require_executable",
        return_value="ocrmypdf",
    )
    @mock.patch("subprocess.run")
    def test_ocr_success(self, mock_run, _mock_req):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            _make_minimal_pdf(inp.name)
            in_path = inp.name
        out_path = in_path + ".ocr.pdf"
        try:

            def side_effect(cmd, **kwargs):
                # Write a valid PDF as output at cmd[-1]
                _make_minimal_pdf(cmd[-1])
                return mock.MagicMock(returncode=0, stderr="")

            mock_run.side_effect = side_effect
            result = ocr_pdf(in_path, out_path, language="eng")
            self.assertEqual(result["action"], "ocr")
            self.assertEqual(result["language"], "eng")
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    @mock.patch(
        "docassemble.ALDashboard.pdf_repair._require_executable",
        return_value="ocrmypdf",
    )
    @mock.patch("subprocess.run")
    def test_ocr_failure(self, mock_run, _mock_req):
        mock_run.return_value = mock.MagicMock(returncode=2, stderr="ocr error")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            _make_minimal_pdf(inp.name)
            in_path = inp.name
        try:
            with self.assertRaises(PDFRepairError):
                ocr_pdf(in_path, in_path + ".ocr.pdf")
        finally:
            os.remove(in_path)

    @mock.patch(
        "docassemble.ALDashboard.pdf_repair._require_executable",
        return_value="ocrmypdf",
    )
    @mock.patch(
        "subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired("ocrmypdf", 300),
    )
    def test_ocr_timeout(self, _mock_run, _mock_req):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            _make_minimal_pdf(inp.name)
            in_path = inp.name
        try:
            with self.assertRaises(PDFRepairError) as ctx:
                ocr_pdf(in_path, in_path + ".ocr.pdf")
            self.assertIn("timed out", str(ctx.exception))
        finally:
            os.remove(in_path)


class TestQpdfRepair(unittest.TestCase):
    def test_qpdf_repair_with_pikepdf(self):
        """Integration test - requires pikepdf installed."""
        try:
            import pikepdf
        except ImportError:
            self.skipTest("pikepdf not installed")

        # Create a real minimal PDF with pikepdf
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".repaired.pdf"
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(in_path)
            pdf.close()

            result = qpdf_repair(in_path, out_path)
            self.assertEqual(result["action"], "qpdf_repair")
            self.assertEqual(result["original_page_count"], 1)
            self.assertEqual(result["repaired_page_count"], 1)
            self.assertTrue(os.path.isfile(out_path))
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)


class TestRestoreCheckboxAppearances(unittest.TestCase):
    def _make_form_pdf(
        self,
        path: str,
        *,
        checkbox_has_ap: bool = False,
        checkbox_has_partial_ap: bool = False,
        has_need_appearances: bool = False,
    ) -> None:
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))

        checkbox = Dictionary(
            {
                "/FT": Name("/Btn"),
                "/Ff": 0,
                "/T": String("needs_appearance"),
                "/V": Name("/Off"),
                "/AS": Name("/Off"),
                "/Type": Name("/Annot"),
                "/Subtype": Name("/Widget"),
                "/Rect": Array([10, 10, 30, 30]),
                "/MK": Dictionary({"/CA": String("5")}),
            }
        )
        if checkbox_has_ap:
            checkbox["/AP"] = Dictionary(
                {
                    "/N": Dictionary(
                        {
                            "/Off": pikepdf.Stream(pdf, b"q Q"),
                            "/Yes": pikepdf.Stream(pdf, b"q Q"),
                        }
                    )
                }
            )
        elif checkbox_has_partial_ap:
            checkbox["/AP"] = Dictionary(
                {
                    "/N": Dictionary(
                        {
                            "/Off": pikepdf.Stream(pdf, b"q Q"),
                        }
                    )
                }
            )
        checkbox_ref = pdf.make_indirect(checkbox)

        text_field = pdf.make_indirect(
            Dictionary(
                {
                    "/FT": Name("/Tx"),
                    "/T": String("text_field"),
                    "/Type": Name("/Annot"),
                    "/Subtype": Name("/Widget"),
                    "/Rect": Array([40, 10, 120, 30]),
                }
            )
        )

        radio = pdf.make_indirect(
            Dictionary(
                {
                    "/FT": Name("/Btn"),
                    "/Ff": 1 << 15,
                    "/T": String("radio_field"),
                    "/Type": Name("/Annot"),
                    "/Subtype": Name("/Widget"),
                    "/Rect": Array([130, 10, 150, 30]),
                }
            )
        )

        page.obj["/Annots"] = Array([checkbox_ref, text_field, radio])
        acroform = Dictionary({"/Fields": Array([checkbox_ref, text_field, radio])})
        if has_need_appearances:
            acroform["/NeedAppearances"] = True
        pdf.Root["/AcroForm"] = acroform
        pdf.save(path)
        pdf.close()

    def _read_widget_flags(self, path: str) -> List[Dict[str, Any]]:
        import pikepdf

        with pikepdf.open(path) as pdf:
            widgets = []
            annots: Any = pdf.pages[0]["/Annots"]
            for annot in annots:
                widgets.append(
                    {
                        "name": str(annot.get("/T", "")),
                        "type": str(annot.get("/FT", "")),
                        "flags": int(annot.get("/Ff", 0) or 0),
                        "has_ap": "/AP" in annot,
                        "normal_states": sorted(
                            str(key)
                            for key in (
                                annot.get("/AP", {}).get("/N", {}).keys()
                                if "/AP" in annot
                                and hasattr(annot.get("/AP"), "get")
                                and hasattr(annot.get("/AP").get("/N"), "keys")
                                else []
                            )
                        ),
                        "yes_stream": (
                            annot["/AP"]["/N"]["/Yes"].read_bytes().decode("ascii")
                            if "/AP" in annot
                            and "/N" in annot["/AP"]
                            and "/Yes" in annot["/AP"]["/N"]
                            else ""
                        ),
                        "off_stream": (
                            annot["/AP"]["/N"]["/Off"].read_bytes().decode("ascii")
                            if "/AP" in annot
                            and "/N" in annot["/AP"]
                            and "/Off" in annot["/AP"]["/N"]
                            else ""
                        ),
                        "mark_caption": (
                            str(annot["/MK"]["/CA"])
                            if "/MK" in annot
                            and isinstance(annot["/MK"], pikepdf.Dictionary)
                            and "/CA" in annot["/MK"]
                            else ""
                        ),
                    }
                )
        return widgets

    def _has_need_appearances(self, path: str) -> bool:
        import pikepdf

        with pikepdf.open(path) as pdf:
            acroform = pdf.Root.get("/AcroForm")
            return isinstance(acroform, pikepdf.Dictionary) and "/NeedAppearances" in acroform

    def test_restores_only_missing_checkbox_appearances(self):
        try:
            import pikepdf  # noqa: F401
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".appearances.pdf"
        try:
            self._make_form_pdf(in_path)
            result = restore_checkbox_appearances(in_path, out_path)

            self.assertEqual(result["action"], "restore_checkbox_appearances")
            self.assertEqual(result["checkbox_fields_checked"], 1)
            self.assertEqual(result["appearances_restored"], 1)

            widgets = {item["name"]: item for item in self._read_widget_flags(out_path)}
            self.assertTrue(widgets["needs_appearance"]["has_ap"])
            self.assertEqual(
                widgets["needs_appearance"]["normal_states"], ["/Off", "/Yes"]
            )
            self.assertIn("3.000 3.000 m", widgets["needs_appearance"]["yes_stream"])
            self.assertIn("17.000 17.000 l", widgets["needs_appearance"]["yes_stream"])
            self.assertIn("17.000 3.000 m", widgets["needs_appearance"]["yes_stream"])
            self.assertIn("3.000 17.000 l", widgets["needs_appearance"]["yes_stream"])
            self.assertNotIn(" re", widgets["needs_appearance"]["yes_stream"])
            self.assertNotIn(" re", widgets["needs_appearance"]["off_stream"])
            self.assertEqual(widgets["needs_appearance"]["mark_caption"], "8")
            self.assertFalse(widgets["text_field"]["has_ap"])
            self.assertFalse(widgets["radio_field"]["has_ap"])
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_skips_checkbox_with_existing_appearances(self):
        try:
            import pikepdf  # noqa: F401
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".appearances.pdf"
        try:
            self._make_form_pdf(in_path, checkbox_has_ap=True)
            result = restore_checkbox_appearances(in_path, out_path)

            self.assertEqual(result["checkbox_fields_checked"], 1)
            self.assertEqual(result["appearances_restored"], 0)
            self.assertEqual(result["existing_appearances_skipped"], 1)
            widgets = {item["name"]: item for item in self._read_widget_flags(out_path)}
            self.assertEqual(widgets["needs_appearance"]["mark_caption"], "8")
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_repairs_partial_checkbox_appearances(self):
        try:
            import pikepdf  # noqa: F401
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".appearances.pdf"
        try:
            self._make_form_pdf(in_path, checkbox_has_partial_ap=True)
            result = restore_checkbox_appearances(in_path, out_path)

            self.assertEqual(result["checkbox_fields_checked"], 1)
            self.assertEqual(result["appearances_restored"], 1)
            self.assertEqual(result["existing_appearances_skipped"], 0)

            widgets = {item["name"]: item for item in self._read_widget_flags(out_path)}
            self.assertEqual(
                widgets["needs_appearance"]["normal_states"], ["/Off", "/Yes"]
            )
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_clears_need_appearances_after_checkbox_repair(self):
        try:
            import pikepdf  # noqa: F401
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".appearances.pdf"
        try:
            self._make_form_pdf(
                in_path,
                checkbox_has_ap=True,
                has_need_appearances=True,
            )
            result = restore_checkbox_appearances(in_path, out_path)

            self.assertEqual(result["checkbox_fields_checked"], 1)
            self.assertEqual(result["existing_appearances_skipped"], 1)
            self.assertFalse(self._has_need_appearances(out_path))
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_restores_opt_in_checkbox_border(self):
        try:
            import pikepdf  # noqa: F401
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".appearances.pdf"
        try:
            self._make_form_pdf(in_path)
            result = restore_checkbox_appearances(
                in_path,
                out_path,
                checkbox_border_widths={"needs_appearance": "medium"},
            )

            self.assertEqual(result["appearances_restored"], 1)

            widgets = {item["name"]: item for item in self._read_widget_flags(out_path)}
            self.assertIn("2.000 w", widgets["needs_appearance"]["off_stream"])
            self.assertIn(
                "1.000 1.000 18.000 18.000 re",
                widgets["needs_appearance"]["off_stream"],
            )
            self.assertIn("2.000 w", widgets["needs_appearance"]["yes_stream"])
            self.assertIn(
                "1.000 1.000 18.000 18.000 re",
                widgets["needs_appearance"]["yes_stream"],
            )
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)


class TestNormalizeSignatureFields(unittest.TestCase):
    def _make_text_signature_pdf(self, path: str) -> None:
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        signature_as_text = pdf.make_indirect(
            Dictionary(
                {
                    "/FT": Name("/Tx"),
                    "/T": String("users1_signature"),
                    "/Type": Name("/Annot"),
                    "/Subtype": Name("/Widget"),
                    "/Rect": Array([72, 600, 220, 630]),
                    "/V": String(""),
                    "/DV": String(""),
                    "/DA": String("/Helv 12 Tf 0 0 0 rg"),
                }
            )
        )
        ordinary_text = pdf.make_indirect(
            Dictionary(
                {
                    "/FT": Name("/Tx"),
                    "/T": String("users1_name"),
                    "/Type": Name("/Annot"),
                    "/Subtype": Name("/Widget"),
                    "/Rect": Array([72, 560, 220, 580]),
                    "/V": String(""),
                }
            )
        )
        page.obj["/Annots"] = Array([signature_as_text, ordinary_text])
        pdf.Root["/AcroForm"] = Dictionary(
            {"/Fields": Array([signature_as_text, ordinary_text])}
        )
        pdf.save(path)
        pdf.close()

    def test_converts_target_text_widget_to_signature_field(self):
        try:
            import pikepdf
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".signature.pdf"
        try:
            self._make_text_signature_pdf(in_path)
            result = normalize_signature_fields(
                in_path,
                out_path,
                ["users1_signature"],
            )

            self.assertEqual(result["fields_converted"], 1)

            with pikepdf.open(out_path) as pdf:
                fields = {
                    str(field.get("/T")): field for field in pdf.Root.AcroForm.Fields
                }
                self.assertEqual(str(fields["users1_signature"].get("/FT")), "/Sig")
                self.assertNotIn("/V", fields["users1_signature"])
                self.assertNotIn("/DV", fields["users1_signature"])
                self.assertEqual(str(fields["users1_name"].get("/FT")), "/Tx")
                self.assertIn("/V", fields["users1_name"])
                self.assertEqual(int(pdf.Root.AcroForm.get("/SigFlags", 0)), 3)
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)


class TestUnlockPDF(unittest.TestCase):
    def test_unlock_unencrypted(self):
        """Unlocking an unencrypted PDF should succeed."""
        try:
            import pikepdf
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".unlocked.pdf"
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(in_path)
            pdf.close()

            result = unlock_pdf(in_path, out_path)
            self.assertEqual(result["action"], "unlock")
            self.assertFalse(result["password_was_supplied"])
            self.assertTrue(os.path.isfile(out_path))
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)


class TestRepairMetadata(unittest.TestCase):
    def test_metadata_repair_pikepdf(self):
        """Repair metadata on a valid PDF with pikepdf."""
        try:
            import pikepdf
        except ImportError:
            self.skipTest("pikepdf not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            in_path = inp.name
        out_path = in_path + ".meta.pdf"
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(in_path)
            pdf.close()

            result = repair_metadata(in_path, out_path)
            self.assertEqual(result["action"], "repair_metadata")
            self.assertIn("method", result)
            self.assertTrue(os.path.isfile(out_path))
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)


CORRUPTED_PDF = os.path.join(os.path.dirname(__file__), "corrupted.pdf")


class TestAutoRepair(unittest.TestCase):
    """Tests for the auto-repair cascade."""

    def test_auto_repair_on_corrupted_pdf(self):
        """auto_repair should continue past failures and report the winning strategy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = os.path.join(tmpdir, "input.pdf")
            out_path = os.path.join(tmpdir, "auto_repair_output.pdf")
            _make_openable_pdf(in_path)

            def fake_qpdf(_input_path, _output_path):
                raise PDFRepairError("qpdf failed")

            def fake_ghostscript(_input_path, output_path):
                _make_openable_pdf(output_path)
                return {
                    "action": "ghostscript_reprint",
                    "preserve_fields": False,
                }

            with mock.patch.dict(
                REPAIR_ACTIONS,
                {
                    "qpdf_repair": fake_qpdf,
                    "ghostscript_reprint": fake_ghostscript,
                },
            ):
                result = auto_repair(in_path, out_path)

            self.assertEqual(result["action"], "auto")
            self.assertEqual(result["strategy_used"], "ghostscript_reprint")
            self.assertEqual(
                result["strategies_tried"],
                ["qpdf_repair", "ghostscript_reprint"],
            )
            self.assertGreater(result["page_count"], 0)
            self.assertTrue(os.path.isfile(out_path))

    def test_auto_repair_via_run_repair(self):
        """run_repair('auto', ...) should dispatch through the auto cascade."""
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = os.path.join(tmpdir, "input.pdf")
            out_path = os.path.join(tmpdir, "run_repair_auto_output.pdf")
            _make_openable_pdf(in_path)

            def fake_qpdf(_input_path, _output_path):
                raise PDFRepairError("qpdf failed")

            def fake_ghostscript(_input_path, output_path):
                _make_openable_pdf(output_path)
                return {
                    "action": "ghostscript_reprint",
                    "preserve_fields": False,
                }

            with mock.patch.dict(
                REPAIR_ACTIONS,
                {
                    "qpdf_repair": fake_qpdf,
                    "ghostscript_reprint": fake_ghostscript,
                },
            ):
                result = run_repair("auto", in_path, out_path)

            self.assertEqual(result["action"], "auto")
            self.assertEqual(result["strategy_used"], "ghostscript_reprint")
            self.assertTrue(os.path.isfile(out_path))

    def test_auto_in_repair_actions(self):
        """'auto' should be listed in REPAIR_ACTIONS."""
        self.assertIn("auto", REPAIR_ACTIONS)

    def test_auto_in_list_repair_actions(self):
        """list_repair_actions() should include auto."""
        actions = list_repair_actions()
        names = [a["action"] for a in actions]
        self.assertIn("auto", names)


if __name__ == "__main__":
    unittest.main()
