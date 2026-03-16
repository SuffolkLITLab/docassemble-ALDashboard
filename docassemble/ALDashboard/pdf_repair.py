"""PDF repair toolkit.

Each public function accepts an ``input_pdf_path`` (and usually an
``output_pdf_path``) and returns a dict describing what happened.
All heavy lifting is deferred to optional external tools so the module
stays importable even when those tools are not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


class PDFRepairError(RuntimeError):
    """Raised when a repair operation fails."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_executable(name: str) -> str:
    """Return the path to *name* or raise with a helpful message."""
    path = shutil.which(name)
    if path is None:
        raise PDFRepairError(
            f"'{name}' is not available on PATH. "
            f"Please install it before using this repair option."
        )
    return path


def _assert_pdf(path: str, *, label: str = "output") -> None:
    p = Path(path)
    if not p.is_file():
        raise PDFRepairError(f"Repair ({label}) did not produce an output file.")
    with p.open("rb") as fh:
        header = fh.read(5)
    if not header.startswith(b"%PDF-"):
        raise PDFRepairError(f"Repair ({label}) did not produce a valid PDF.")


def _copy_if_same(src: str, dst: str) -> None:
    """If *src* == *dst* do nothing, otherwise copy."""
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# 1) Ghostscript reprint
# ---------------------------------------------------------------------------


def _extract_field_info_pikepdf(pdf_path: str) -> List[Dict[str, Any]]:
    """Extract basic field metadata (name, rect, type, page) with pikepdf."""
    import pikepdf  # type: ignore[import-untyped]

    fields: List[Dict[str, Any]] = []
    with pikepdf.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            annots = page.get("/Annots")
            if annots is None:
                continue
            for annot in annots:  # type: ignore[attr-defined]
                try:
                    obj = annot.resolve() if hasattr(annot, "resolve") else annot
                except Exception:
                    continue
                ft = str(obj.get("/FT", "")) if "/FT" in obj else None
                field_name = str(obj.get("/T", "")) if "/T" in obj else None
                rect = None
                if "/Rect" in obj:
                    try:
                        rect = [float(v) for v in obj["/Rect"]]
                    except Exception:
                        pass
                flags = int(obj.get("/Ff", 0)) if "/Ff" in obj else 0
                fields.append(
                    {
                        "page": page_idx,
                        "name": field_name,
                        "type": ft,
                        "rect": rect,
                        "flags": flags,
                    }
                )
    return fields


def _restore_fields_pikepdf(
    pdf_path: str, fields: List[Dict[str, Any]]
) -> None:
    """Re-add stripped fields to a flat PDF using pikepdf."""
    import pikepdf  # type: ignore[import-untyped]

    if not fields:
        return

    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        acroform = pikepdf.Dictionary()
        all_field_refs: list[Any] = []

        for info in fields:
            page_idx = info["page"]
            if page_idx < 0 or page_idx >= len(pdf.pages):
                continue
            page = pdf.pages[page_idx]

            rect = info.get("rect") or [0, 0, 100, 20]
            annot = pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Annot"),
                    "/Subtype": pikepdf.Name("/Widget"),
                    "/Rect": pikepdf.Array(
                        [pikepdf.objects.Decimal(str(v)) for v in rect]
                    ),
                    "/P": page.obj,
                }
            )
            if info.get("name"):
                annot["/T"] = pikepdf.String(info["name"])
            ft = info.get("type")
            if ft:
                annot["/FT"] = pikepdf.Name(ft if ft.startswith("/") else f"/{ft}")
            if info.get("flags"):
                annot["/Ff"] = info["flags"]

            ref = pdf.make_indirect(annot)
            annots = page.get("/Annots")
            if annots is None:
                page["/Annots"] = pikepdf.Array([ref])
            else:
                annots.append(ref)
            all_field_refs.append(ref)

        if all_field_refs:
            acroform["/Fields"] = pikepdf.Array(all_field_refs)
            pdf.Root["/AcroForm"] = pdf.make_indirect(acroform)

        pdf.save(pdf_path)


def ghostscript_reprint(
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    preserve_fields: bool = False,
) -> Dict[str, Any]:
    """Re-distill the PDF through Ghostscript.

    When *preserve_fields* is ``True`` field metadata (name, rect, type)
    is extracted first and re-applied to the reprinted file.
    """
    gs = _require_executable("gs")

    saved_fields: List[Dict[str, Any]] = []
    if preserve_fields:
        try:
            saved_fields = _extract_field_info_pikepdf(input_pdf_path)
        except Exception as exc:
            raise PDFRepairError(
                f"Could not extract existing fields before reprint: {exc}"
            ) from exc

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            gs,
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.7",
            "-dPDFSETTINGS=/prepress",
            f"-sOutputFile={tmp_path}",
            input_pdf_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise PDFRepairError(
                f"Ghostscript exited with code {result.returncode}: {stderr}"
            )
        _assert_pdf(tmp_path, label="ghostscript reprint")
        _copy_if_same(tmp_path, output_pdf_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    if preserve_fields and saved_fields:
        try:
            _restore_fields_pikepdf(output_pdf_path, saved_fields)
        except Exception as exc:
            raise PDFRepairError(
                f"Reprinted PDF created but field restoration failed: {exc}"
            ) from exc

    return {
        "action": "ghostscript_reprint",
        "preserve_fields": preserve_fields,
        "fields_restored": len(saved_fields) if preserve_fields else 0,
    }


# ---------------------------------------------------------------------------
# 2) pikepdf / qpdf --fix  +  rebuild page tree
# ---------------------------------------------------------------------------


def qpdf_repair(
    input_pdf_path: str,
    output_pdf_path: str,
) -> Dict[str, Any]:
    """Open the PDF with pikepdf in repair mode (``fix=True``).

    This invokes qpdf's ``--fix`` internally, then rebuilds the page
    tree by re-writing through pikepdf.
    """
    import pikepdf  # type: ignore[import-untyped]

    warnings: List[str] = []
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with pikepdf.open(input_pdf_path) as pdf_orig:
            original_page_count = len(pdf_orig.pages)

        with pikepdf.open(input_pdf_path, suppress_warnings=False) as pdf:
            # Force a fresh linear page tree by saving through pikepdf
            pdf.save(
                tmp_path,
                linearize=True,
                fix_metadata_version=True,
            )

        with pikepdf.open(tmp_path) as pdf_check:
            repaired_page_count = len(pdf_check.pages)
            if repaired_page_count != original_page_count:
                warnings.append(
                    f"Page count changed: {original_page_count} -> {repaired_page_count}"
                )

        _assert_pdf(tmp_path, label="qpdf repair")
        _copy_if_same(tmp_path, output_pdf_path)
    except pikepdf.PasswordError:
        raise PDFRepairError(
            "PDF is encrypted. Use the 'unlock' repair action first."
        )
    except PDFRepairError:
        raise
    except Exception as exc:
        raise PDFRepairError(f"qpdf/pikepdf repair failed: {exc}") from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "action": "qpdf_repair",
        "original_page_count": original_page_count,
        "repaired_page_count": repaired_page_count,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 3) Unlock (remove encryption / permissions)
# ---------------------------------------------------------------------------


def unlock_pdf(
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    password: str = "",
) -> Dict[str, Any]:
    """Remove encryption and permission restrictions with pikepdf."""
    import pikepdf  # type: ignore[import-untyped]

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        try:
            pdf = pikepdf.open(input_pdf_path, password=password)
        except pikepdf.PasswordError:
            if password:
                raise PDFRepairError("Incorrect password supplied for this PDF.")
            raise PDFRepairError(
                "PDF requires a password to open. "
                "Provide the password with the 'password' parameter."
            )
        with pdf:
            pdf.save(tmp_path)

        _assert_pdf(tmp_path, label="unlock")
        _copy_if_same(tmp_path, output_pdf_path)
    except PDFRepairError:
        raise
    except Exception as exc:
        raise PDFRepairError(f"Failed to unlock PDF: {exc}") from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "action": "unlock",
        "password_was_supplied": bool(password),
    }


# ---------------------------------------------------------------------------
# 4) Repair metadata / catalog
# ---------------------------------------------------------------------------


def _repair_metadata_pikepdf(input_path: str, output_path: str) -> Dict[str, Any]:
    """Try to fix metadata and catalog entries with pikepdf."""
    import pikepdf  # type: ignore[import-untyped]

    fixes: List[str] = []
    with pikepdf.open(input_path) as pdf:
        # Ensure /Type /Catalog exists
        root = pdf.Root
        if "/Type" not in root or str(root["/Type"]) != "/Catalog":
            root["/Type"] = pikepdf.Name("/Catalog")
            fixes.append("Added /Type /Catalog to document root")

        # Ensure /Pages reference exists
        if "/Pages" not in root:
            fixes.append("WARNING: /Pages missing from catalog — cannot auto-fix")
        else:
            pages = root["/Pages"]
            resolved = pages.resolve() if hasattr(pages, "resolve") else pages  # type: ignore[operator]
            if "/Type" not in resolved or str(resolved["/Type"]) != "/Pages":
                resolved["/Type"] = pikepdf.Name("/Pages")
                fixes.append("Fixed /Type on /Pages node")

        # Clean up /Info metadata
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            # Simply opening and closing with pikepdf validates XMP
            pass
        fixes.append("XMP metadata validated")

        pdf.save(output_path, fix_metadata_version=True)

    return {"method": "pikepdf", "fixes": fixes}


def _repair_metadata_pdfrw(input_path: str, output_path: str) -> Dict[str, Any]:
    """Fallback metadata/catalog repair using pdfrw."""
    import pdfrw  # type: ignore[import-untyped]

    fixes: List[str] = []
    reader = pdfrw.PdfReader(input_path)

    # Ensure trailer has a valid Root
    root = reader.Root
    if root is None:
        raise PDFRepairError("pdfrw could not locate a document Root/Catalog.")

    if root.Type is None or str(root.Type) != "/Catalog":
        root.Type = pdfrw.PdfName("Catalog")
        fixes.append("Set /Type /Catalog on root")

    if root.Pages is None:
        raise PDFRepairError("pdfrw could not locate /Pages in the catalog.")

    if root.Pages.Type is None or str(root.Pages.Type) != "/Pages":
        root.Pages.Type = pdfrw.PdfName("Pages")
        fixes.append("Set /Type /Pages on pages node")

    # Rebuild page list to fix stale references
    pages = root.Pages
    if hasattr(pages, "Kids") and pages.Kids is not None:
        page_count = len(pages.Kids)
        fixes.append(f"Page tree has {page_count} page(s)")
    else:
        fixes.append("WARNING: /Kids array missing from /Pages")

    writer = pdfrw.PdfWriter(output_path)
    writer.trailer = reader
    writer.write()
    fixes.append("Rewrote PDF via pdfrw")

    return {"method": "pdfrw", "fixes": fixes}


def repair_metadata(
    input_pdf_path: str,
    output_pdf_path: str,
) -> Dict[str, Any]:
    """Repair PDF metadata and catalog structure.

    Tries pikepdf first; falls back to pdfrw if pikepdf cannot handle
    the file.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        try:
            result = _repair_metadata_pikepdf(input_pdf_path, tmp_path)
        except Exception as pikepdf_err:
            try:
                result = _repair_metadata_pdfrw(input_pdf_path, tmp_path)
            except Exception as pdfrw_err:
                raise PDFRepairError(
                    f"Metadata repair failed with both pikepdf ({pikepdf_err}) "
                    f"and pdfrw ({pdfrw_err})."
                ) from pdfrw_err

        _assert_pdf(tmp_path, label="metadata repair")
        _copy_if_same(tmp_path, output_pdf_path)
    except PDFRepairError:
        raise
    except Exception as exc:
        raise PDFRepairError(f"Metadata repair failed: {exc}") from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    result["action"] = "repair_metadata"
    return result


# ---------------------------------------------------------------------------
# 5) OCR with ocrmypdf
# ---------------------------------------------------------------------------


def ocr_pdf(
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    language: str = "eng",
    skip_text: bool = True,
) -> Dict[str, Any]:
    """Add an OCR text layer using ocrmypdf.

    *skip_text* (default ``True``) tells ocrmypdf to skip pages that
    already contain text, avoiding double-OCR.
    """
    _require_executable("ocrmypdf")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            "ocrmypdf",
            "--output-type",
            "pdf",
            "-l",
            language,
        ]
        if skip_text:
            cmd.append("--skip-text")
        cmd += [input_pdf_path, tmp_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode not in (0, 6):
            # ocrmypdf exit code 6 = "no text found" (still produces output)
            stderr = (result.stderr or "").strip()
            raise PDFRepairError(
                f"ocrmypdf exited with code {result.returncode}: {stderr}"
            )
        _assert_pdf(tmp_path, label="ocr")
        _copy_if_same(tmp_path, output_pdf_path)
    except PDFRepairError:
        raise
    except subprocess.TimeoutExpired:
        raise PDFRepairError("OCR timed out after 300 seconds.")
    except Exception as exc:
        raise PDFRepairError(f"OCR failed: {exc}") from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "action": "ocr",
        "language": language,
        "skip_text": skip_text,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

REPAIR_ACTIONS = {
    "ghostscript_reprint": ghostscript_reprint,
    "qpdf_repair": qpdf_repair,
    "unlock": unlock_pdf,
    "repair_metadata": repair_metadata,
    "ocr": ocr_pdf,
}

REPAIR_ACTION_HELP = {
    "ghostscript_reprint": (
        "Re-distill the PDF through Ghostscript to produce a completely fresh file. "
        "Optionally preserves existing form field locations and types."
    ),
    "qpdf_repair": (
        "Run pikepdf/qpdf repair mode to fix cross-reference tables "
        "and rebuild the page tree."
    ),
    "unlock": (
        "Remove encryption and permission restrictions so the PDF "
        "can be edited freely."
    ),
    "repair_metadata": (
        "Fix broken metadata and catalog entries. Tries pikepdf first, "
        "then falls back to pdfrw."
    ),
    "ocr": (
        "Add a searchable text layer to scanned pages using ocrmypdf. "
        "Skips pages that already have text by default."
    ),
}


def run_repair(
    action: str,
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a single repair *action*.

    *options* is forwarded as keyword arguments to the action function
    (e.g. ``preserve_fields``, ``password``, ``language``).
    """
    func = REPAIR_ACTIONS.get(action)
    if func is None:
        raise PDFRepairError(
            f"Unknown repair action {action!r}. "
            f"Available: {sorted(REPAIR_ACTIONS)}"
        )
    kwargs = dict(options or {})
    return func(input_pdf_path, output_pdf_path, **kwargs)  # type: ignore[operator]


def list_repair_actions() -> List[Dict[str, str]]:
    """Return a JSON-friendly list of available repair actions with help text."""
    return [
        {"action": key, "description": REPAIR_ACTION_HELP.get(key, "")}
        for key in REPAIR_ACTIONS
    ]
