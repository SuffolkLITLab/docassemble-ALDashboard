"""Tests for the pdf_repair module.

These tests exercise logic that does NOT require Ghostscript or ocrmypdf
to be installed.  External-tool tests use ``unittest.mock`` to patch
``subprocess.run`` and ``shutil.which``.
"""

import os
import tempfile
import unittest
from unittest import mock

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
    qpdf_repair,
    repair_metadata,
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
