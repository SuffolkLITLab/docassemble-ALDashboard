"""PDF repair toolkit.

Each public function accepts an ``input_pdf_path`` (and usually an
``output_pdf_path``) and returns a dict describing what happened.
All heavy lifting is deferred to optional external tools so the module
stays importable even when those tools are not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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
    """Validate that a repair step wrote a file with a PDF header.

    Args:
        path: Path to the output file to validate.
        label: Human-readable label for the repair step.

    Raises:
        PDFRepairError: If the output file is missing or not a valid PDF.
    """
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


def _get_pdf_field_parent(field_obj: Any) -> Optional[Any]:
    """Return the nearest named field container for a widget annotation."""
    if not hasattr(field_obj, "get"):
        return None
    if "/T" in field_obj:
        return field_obj
    parent = field_obj.get("/Parent")
    if parent is None:
        return None
    try:
        parent_obj = parent.resolve() if hasattr(parent, "resolve") else parent
    except Exception:  # nosec B112
        return None
    return _get_pdf_field_parent(parent_obj)


def _pdf_obj_value(obj: Any, key: str, default: Any = None) -> Any:
    """Read a PDF dictionary key with a small guard against malformed objects."""
    try:
        if hasattr(obj, "get"):
            return obj.get(key, default)
    except Exception:  # nosec B112
        return default
    return default


def _is_checkbox_like_button(widget: Any, parent: Optional[Any]) -> bool:
    """Return True for checkbox-style button fields, excluding radio/push buttons."""
    ft = _pdf_obj_value(widget, "/FT", _pdf_obj_value(parent, "/FT"))
    if str(ft) != "/Btn":
        return False

    try:
        flags = int(
            _pdf_obj_value(widget, "/Ff", _pdf_obj_value(parent, "/Ff", 0)) or 0
        )
    except (TypeError, ValueError):
        flags = 0

    pushbutton_flag = 1 << 16
    radio_flag = 1 << 15
    return not bool(flags & (pushbutton_flag | radio_flag))


def _normal_appearance_dict(widget: Any) -> Optional[Any]:
    """Return a widget's normal appearance dictionary when it already has one."""
    ap = _pdf_obj_value(widget, "/AP")
    if ap is None or not hasattr(ap, "get"):
        return None
    normal = ap.get("/N")
    if normal is None or not hasattr(normal, "keys"):
        return None
    return normal


def _checkbox_checked_state(
    widget: Any, parent: Optional[Any], pikepdf_module: Any
) -> Any:
    """Choose the checked export state that should have a matching appearance."""
    for key in ("/AS", "/V", "/DV"):
        value = _pdf_obj_value(widget, key, _pdf_obj_value(parent, key))
        if value is not None and str(value) and str(value) != "/Off":
            return pikepdf_module.Name(str(value))
    return pikepdf_module.Name("/Yes")


_CHECKBOX_CAPTION_TO_STYLE = {
    "4": "check",
    "5": "cross",
    "l": "circle",
    "N": "star",
    "u": "diamond",
}
_CHECKBOX_STYLE_TO_CAPTION = {
    style: caption for caption, style in _CHECKBOX_CAPTION_TO_STYLE.items()
}


def _checkbox_mark_style(widget: Any, parent: Optional[Any]) -> str:
    """Infer the checkbox mark style from reportlab/Acrobat button metadata."""
    for obj in (widget, parent):
        mk = _pdf_obj_value(obj, "/MK")
        if mk is None or not hasattr(mk, "get"):
            continue
        caption = mk.get("/CA")
        if caption is None:
            continue
        style = _CHECKBOX_CAPTION_TO_STYLE.get(str(caption))
        if style:
            return style
    return "check"


def _set_checkbox_mark_style(
    widget: Any, parent: Optional[Any], style: str, pikepdf_module: Any
) -> None:
    """Write checkbox caption metadata that matches the synthesized mark style."""
    caption = _CHECKBOX_STYLE_TO_CAPTION.get(style)
    if not caption:
        return
    for obj in (widget, parent):
        if obj is None or not hasattr(obj, "get"):
            continue
        mk = obj.get("/MK")
        if not isinstance(mk, pikepdf_module.Dictionary):
            mk = pikepdf_module.Dictionary()
            obj["/MK"] = mk
        mk["/CA"] = pikepdf_module.String(caption)


_CHECKBOX_BORDER_WIDTHS = {
    "thin": 1.0,
    "medium": 2.0,
    "thick": 3.0,
}


def _checkbox_border_width(value: Any) -> float:
    """Normalize a checkbox border-width option into points."""
    if value is None or value is False:
        return 0.0
    if value is True:
        return _CHECKBOX_BORDER_WIDTHS["thin"]
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    normalized = str(value or "").strip().lower()
    if normalized in {"", "none", "off", "false", "0"}:
        return 0.0
    return _CHECKBOX_BORDER_WIDTHS.get(normalized, _CHECKBOX_BORDER_WIDTHS["thin"])


def _checkbox_mark_ops(
    style: str, width: float, height: float, border_width: float
) -> str:
    """Return PDF path operations for a borderless checkbox mark."""
    inset = max(3.0, border_width + 2.0)
    left = inset
    right = max(width - inset, left)
    bottom = inset
    top = max(height - inset, bottom)
    mid_x = width / 2.0
    mid_y = height / 2.0
    r_x = max((right - left) / 2.0, 1.0)
    r_y = max((top - bottom) / 2.0, 1.0)

    if style == "cross":
        return (
            f"{left:.3f} {bottom:.3f} m\n"
            f"{right:.3f} {top:.3f} l\n"
            f"{right:.3f} {bottom:.3f} m\n"
            f"{left:.3f} {top:.3f} l\n"
        )
    if style == "circle":
        c = 0.5522847498
        return (
            f"{mid_x + r_x:.3f} {mid_y:.3f} m\n"
            f"{mid_x + r_x:.3f} {mid_y + c * r_y:.3f} "
            f"{mid_x + c * r_x:.3f} {mid_y + r_y:.3f} "
            f"{mid_x:.3f} {mid_y + r_y:.3f} c\n"
            f"{mid_x - c * r_x:.3f} {mid_y + r_y:.3f} "
            f"{mid_x - r_x:.3f} {mid_y + c * r_y:.3f} "
            f"{mid_x - r_x:.3f} {mid_y:.3f} c\n"
            f"{mid_x - r_x:.3f} {mid_y - c * r_y:.3f} "
            f"{mid_x - c * r_x:.3f} {mid_y - r_y:.3f} "
            f"{mid_x:.3f} {mid_y - r_y:.3f} c\n"
            f"{mid_x + c * r_x:.3f} {mid_y - r_y:.3f} "
            f"{mid_x + r_x:.3f} {mid_y - c * r_y:.3f} "
            f"{mid_x + r_x:.3f} {mid_y:.3f} c\n"
        )
    if style == "diamond":
        return (
            f"{mid_x:.3f} {top:.3f} m\n"
            f"{right:.3f} {mid_y:.3f} l\n"
            f"{mid_x:.3f} {bottom:.3f} l\n"
            f"{left:.3f} {mid_y:.3f} l\n"
            "h\n"
        )
    if style == "star":
        points = [
            (0.50, 1.00),
            (0.62, 0.62),
            (1.00, 0.62),
            (0.69, 0.40),
            (0.81, 0.00),
            (0.50, 0.25),
            (0.19, 0.00),
            (0.31, 0.40),
            (0.00, 0.62),
            (0.38, 0.62),
        ]
        ops = []
        for index, (px, py) in enumerate(points):
            x = left + px * (right - left)
            y = bottom + py * (top - bottom)
            ops.append(f"{x:.3f} {y:.3f} {'m' if index == 0 else 'l'}")
        ops.append("h")
        return "\n".join(ops) + "\n"
    return (
        f"{left:.3f} {mid_y:.3f} m\n"
        f"{width * 0.42:.3f} {height * 0.20:.3f} l\n"
        f"{right:.3f} {top:.3f} l\n"
    )


def _make_checkbox_appearance_streams(
    pdf: Any,
    width: float,
    height: float,
    *,
    style: str = "check",
    border_width: float = 0.0,
) -> Dict[str, Any]:
    """Create minimal Off/checked appearance XObjects for a checkbox widget."""
    import pikepdf

    border_ops = ""
    if border_width > 0:
        half = border_width / 2.0
        box_width = max(width - border_width, 0.0)
        box_height = max(height - border_width, 0.0)
        border_ops = (
            "0 0 0 RG\n"
            f"{border_width:.3f} w\n"
            f"{half:.3f} {half:.3f} {box_width:.3f} {box_height:.3f} re\n"
            "S\n"
        )

    off_stream = pikepdf.Stream(pdf, f"q\n{border_ops}Q\n".encode("ascii"))
    off_stream["/Type"] = pikepdf.Name("/XObject")
    off_stream["/Subtype"] = pikepdf.Name("/Form")
    off_stream["/FormType"] = 1
    off_stream["/BBox"] = pikepdf.Array([0, 0, width, height])
    off_stream["/Matrix"] = pikepdf.Array([1, 0, 0, 1, 0, 0])

    checked_ops = (
        "q\n"
        f"{border_ops}"
        "0 0 0 RG\n"
        "1.8 w\n"
        f"{_checkbox_mark_ops(style, width, height, border_width)}"
        "S\n"
        "Q\n"
    ).encode("ascii")
    checked_stream = pikepdf.Stream(pdf, checked_ops)
    checked_stream["/Type"] = pikepdf.Name("/XObject")
    checked_stream["/Subtype"] = pikepdf.Name("/Form")
    checked_stream["/FormType"] = 1
    checked_stream["/BBox"] = pikepdf.Array([0, 0, width, height])
    checked_stream["/Matrix"] = pikepdf.Array([1, 0, 0, 1, 0, 0])
    return {"off": off_stream, "checked": checked_stream}


def restore_checkbox_appearances(
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    checkbox_border_widths: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Restore missing checkbox appearance streams without touching text fields.

    This repair is intentionally narrow: it only synthesizes normal appearances
    for checkbox-like ``/Btn`` widget annotations that are missing ``/AP``.
    Text-field appearances are left alone because deleting those streams is how
    the labeler avoids reportlab's opaque blue backgrounds and thick borders.
    """
    import pikepdf

    restored = 0
    checked = 0
    skipped_existing = 0
    skipped_non_checkbox = 0
    border_widths = {
        str(name): _checkbox_border_width(value)
        for name, value in (checkbox_border_widths or {}).items()
        if str(name).strip()
    }

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with pikepdf.open(input_pdf_path) as pdf:
            for page in pdf.pages:
                annots = page.get("/Annots")
                if annots is None:
                    continue
                for annot in annots:  # type: ignore[attr-defined]
                    try:
                        widget = annot.resolve() if hasattr(annot, "resolve") else annot
                    except Exception:  # nosec B112
                        continue
                    try:
                        if widget.get("/Subtype") != pikepdf.Name("/Widget"):
                            continue
                    except Exception:  # nosec B112
                        continue

                    parent = _get_pdf_field_parent(widget)
                    if not _is_checkbox_like_button(widget, parent):
                        skipped_non_checkbox += 1
                        continue
                    checked += 1

                    checked_state = _checkbox_checked_state(widget, parent, pikepdf)
                    normal_ap = _normal_appearance_dict(widget)
                    if (
                        normal_ap is not None
                        and "/Off" in normal_ap
                        and str(checked_state) in normal_ap
                    ):
                        skipped_existing += 1
                        continue

                    rect = widget.get("/Rect", [0, 0, 12, 12])
                    try:
                        width = max(float(rect[2]) - float(rect[0]), 1.0)
                        height = max(float(rect[3]) - float(rect[1]), 1.0)
                    except (TypeError, ValueError, IndexError):
                        width = height = 12.0

                    mark_style = _checkbox_mark_style(widget, parent)
                    field_name_obj = _pdf_obj_value(
                        parent, "/T", _pdf_obj_value(widget, "/T", "")
                    )
                    field_name = str(field_name_obj or "")
                    border_width = border_widths.get(field_name, 0.0)
                    streams = _make_checkbox_appearance_streams(
                        pdf,
                        width,
                        height,
                        style=mark_style,
                        border_width=border_width,
                    )
                    normal_states = pikepdf.Dictionary(
                        {"/Off": pdf.make_indirect(streams["off"])}
                    )
                    normal_states[str(checked_state)] = pdf.make_indirect(
                        streams["checked"]
                    )
                    widget["/AP"] = pikepdf.Dictionary({"/N": normal_states})
                    _set_checkbox_mark_style(widget, parent, mark_style, pikepdf)
                    restored += 1

            acroform = pdf.Root.get("/AcroForm")
            if isinstance(acroform, pikepdf.Dictionary) and "/NeedAppearances" in acroform:
                del acroform["/NeedAppearances"]
            pdf.save(tmp_path)

        _assert_pdf(tmp_path, label="restore checkbox appearances")
        _copy_if_same(tmp_path, output_pdf_path)
    except PDFRepairError:
        raise
    except Exception as exc:
        raise PDFRepairError(f"Checkbox appearance repair failed: {exc}") from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "action": "restore_checkbox_appearances",
        "checkbox_fields_checked": checked,
        "appearances_restored": restored,
        "existing_appearances_skipped": skipped_existing,
        "non_checkbox_widgets_skipped": skipped_non_checkbox,
    }


def normalize_signature_fields(
    input_pdf_path: str,
    output_pdf_path: str,
    signature_field_names: Iterable[str],
) -> Dict[str, Any]:
    """Convert ReportLab-created text widgets into real signature fields.

    FormFyxer currently asks ReportLab to draw signature fields as text fields
    because ReportLab does not expose a signature widget API. The labeler still
    needs exported PDFs to contain ``/FT /Sig`` so external PDF editors recognize
    those widgets as signature fields.
    """
    import pikepdf

    targets = {str(name).strip() for name in signature_field_names if str(name).strip()}
    if not targets:
        if input_pdf_path != output_pdf_path:
            shutil.copy2(input_pdf_path, output_pdf_path)
        return {"action": "normalize_signature_fields", "fields_converted": 0}

    converted = 0

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    def _normalize_obj(obj: Any) -> bool:
        nonlocal converted
        if obj is None or not hasattr(obj, "get"):
            return False
        field_name = str(obj.get("/T", "") or "").strip()
        if field_name not in targets:
            return False
        if str(obj.get("/FT", "")) != "/Sig":
            converted += 1
        obj["/FT"] = pikepdf.Name("/Sig")
        for key in ("/V", "/DV", "/MaxLen", "/Q", "/DS", "/RV"):
            if key in obj:
                del obj[key]
        return True

    try:
        with pikepdf.open(input_pdf_path) as pdf:
            for page in pdf.pages:
                annots = page.get("/Annots")
                if annots is None:
                    continue
                for annot in annots:  # type: ignore[attr-defined]
                    try:
                        widget = annot.resolve() if hasattr(annot, "resolve") else annot
                    except Exception:  # nosec B112
                        continue
                    try:
                        if widget.get("/Subtype") != pikepdf.Name("/Widget"):
                            continue
                    except Exception:  # nosec B112
                        continue
                    parent = _get_pdf_field_parent(widget)
                    if _normalize_obj(parent):
                        if parent is not widget and hasattr(widget, "get"):
                            widget["/FT"] = pikepdf.Name("/Sig")
                    else:
                        _normalize_obj(widget)

            if converted and hasattr(pdf.Root, "AcroForm"):
                try:
                    existing_flags = int(pdf.Root.AcroForm.get("/SigFlags") or 0)
                except (TypeError, ValueError):
                    existing_flags = 0
                pdf.Root.AcroForm["/SigFlags"] = existing_flags | 3

            pdf.save(tmp_path)

        _assert_pdf(tmp_path, label="normalize signature fields")
        _copy_if_same(tmp_path, output_pdf_path)
    except PDFRepairError:
        raise
    except Exception as exc:
        raise PDFRepairError(f"Signature field normalization failed: {exc}") from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "action": "normalize_signature_fields",
        "fields_converted": converted,
    }


# ---------------------------------------------------------------------------
# 1) Ghostscript reprint
# ---------------------------------------------------------------------------


def _extract_field_info_pikepdf(pdf_path: str) -> List[Dict[str, Any]]:
    """Extract field metadata (name, rect, type, page, appearance, value) with pikepdf."""
    import pikepdf

    fields: List[Dict[str, Any]] = []
    with pikepdf.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            annots = page.get("/Annots")
            if annots is None:
                continue
            for annot in annots:  # type: ignore[attr-defined]
                try:
                    obj = annot.resolve() if hasattr(annot, "resolve") else annot
                except Exception:  # nosec B112
                    continue
                ft = str(obj.get("/FT", "")) if "/FT" in obj else None
                field_name = str(obj.get("/T", "")) if "/T" in obj else None
                rect = None
                if "/Rect" in obj:
                    try:
                        rect = [float(v) for v in obj["/Rect"]]
                    except (ValueError, TypeError):
                        pass
                flags = int(obj.get("/Ff", 0)) if "/Ff" in obj else 0
                da = str(obj.get("/DA", "")) if "/DA" in obj else None
                value = str(obj.get("/V", "")) if "/V" in obj else None
                # Capture /AS (appearance state) for checkboxes / radios
                as_state = str(obj.get("/AS", "")) if "/AS" in obj else None
                fields.append(
                    {
                        "page": page_idx,
                        "name": field_name,
                        "type": ft,
                        "rect": rect,
                        "flags": flags,
                        "da": da,
                        "value": value,
                        "as": as_state,
                    }
                )
    return fields


def _restore_fields_pikepdf(pdf_path: str, fields: List[Dict[str, Any]]) -> None:
    """Re-add stripped fields to a flat PDF using pikepdf."""
    import pikepdf

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
            # Restore default appearance string (font, size, color)
            if info.get("da"):
                annot["/DA"] = pikepdf.String(info["da"])
            # Restore value
            if info.get("value"):
                annot["/V"] = pikepdf.String(info["value"])
            # Restore appearance state for checkboxes/radios
            if info.get("as"):
                as_val = info["as"]
                annot["/AS"] = pikepdf.Name(
                    as_val if as_val.startswith("/") else f"/{as_val}"
                )

            ref = pdf.make_indirect(annot)
            annots = page.get("/Annots")
            if annots is None:
                page["/Annots"] = pikepdf.Array([ref])
            else:
                annots.append(ref)
            all_field_refs.append(ref)

        if all_field_refs:
            acroform["/Fields"] = pikepdf.Array(all_field_refs)
            # Tell PDF readers to regenerate field appearances
            acroform["/NeedAppearances"] = True
            pdf.Root["/AcroForm"] = pdf.make_indirect(acroform)

        pdf.save(pdf_path)


_VALID_GS_PDF_SETTINGS = {
    "screen",
    "ebook",
    "printer",
    "prepress",
    "default",
}


def ghostscript_reprint(
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    preserve_fields: bool = False,
    pdf_optimization: str = "prepress",
) -> Dict[str, Any]:
    """Re-distill the PDF through Ghostscript.

    Args:
        input_pdf_path: Source PDF path.
        output_pdf_path: Destination path for the repaired PDF.
        preserve_fields: Whether to restore AcroForm fields after re-distilling.
        pdf_optimization: Ghostscript ``PDFSETTINGS`` profile to apply.

    Returns:
        Dict[str, Any]: Metadata describing the repair result and any warnings.
    """
    gs = _require_executable("gs")

    if pdf_optimization not in _VALID_GS_PDF_SETTINGS:
        pdf_optimization = "prepress"

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
            f"-dPDFSETTINGS=/{pdf_optimization}",
            f"-sOutputFile={tmp_path}",
            input_pdf_path,
        ]
        result = subprocess.run(  # nosec B603
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
        "pdf_optimization": pdf_optimization,
    }


# ---------------------------------------------------------------------------
# 2) pikepdf / qpdf --fix  +  rebuild page tree
# ---------------------------------------------------------------------------


def qpdf_repair(
    input_pdf_path: str,
    output_pdf_path: str,
) -> Dict[str, Any]:
    """Open the PDF with pikepdf in repair mode and rebuild page structure.

    Args:
        input_pdf_path: Source PDF path.
        output_pdf_path: Destination path for the repaired PDF.

    Returns:
        Dict[str, Any]: Metadata describing the repair result and page counts.
    """
    import pikepdf

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
        raise PDFRepairError("PDF is encrypted. Use the 'unlock' repair action first.")
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
    """Remove encryption and permission restrictions with pikepdf.

    Args:
        input_pdf_path: Source encrypted PDF path.
        output_pdf_path: Destination path for the unlocked PDF.
        password: Password to use when opening the PDF.

    Returns:
        Dict[str, Any]: Metadata describing the unlock result.
    """
    import pikepdf

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
    import pikepdf

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
    import pdfrw

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

    Args:
        input_pdf_path: Source PDF path.
        output_pdf_path: Destination path for the repaired PDF.

    Returns:
        Dict[str, Any]: Metadata describing the repair method and fixes applied.
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

    Args:
        input_pdf_path: Source PDF path.
        output_pdf_path: Destination path for the OCR'd PDF.
        language: OCR language code passed to ocrmypdf.
        skip_text: Whether to skip pages that already contain text.

    Returns:
        Dict[str, Any]: Metadata describing the OCR operation.
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

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )  # nosec B603
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
# 6) Auto-repair cascade
# ---------------------------------------------------------------------------

_AUTO_REPAIR_SEQUENCE: List[str] = [
    "qpdf_repair",
    "ghostscript_reprint",
    "repair_metadata",
]


def auto_repair(
    input_pdf_path: str,
    output_pdf_path: str,
) -> Dict[str, Any]:
    """Try multiple repair strategies in sequence until one produces a valid PDF.

    Args:
        input_pdf_path: Source PDF path.
        output_pdf_path: Destination path for the repaired PDF.

    Returns:
        Dict[str, Any]: The winning repair result plus attempted-strategy errors.
    """
    import pikepdf

    errors: List[Dict[str, str]] = []

    for strategy in _AUTO_REPAIR_SEQUENCE:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            candidate_path = tmp.name

        try:
            func = REPAIR_ACTIONS[strategy]
            result = func(input_pdf_path, candidate_path)

            # Validate: must be openable and have at least one page
            with pikepdf.open(candidate_path) as pdf_check:
                page_count = len(pdf_check.pages)

            _copy_if_same(candidate_path, output_pdf_path)
            result["action"] = "auto"
            result["strategy_used"] = strategy
            result["strategies_tried"] = [e["strategy"] for e in errors] + [strategy]
            result["page_count"] = page_count
            return result
        except Exception as exc:
            errors.append({"strategy": strategy, "error": str(exc)})
        finally:
            if os.path.exists(candidate_path):
                os.remove(candidate_path)

    summary = "; ".join(f"{e['strategy']}: {e['error']}" for e in errors)
    raise PDFRepairError(f"Auto-repair failed — all strategies exhausted. {summary}")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

REPAIR_ACTIONS: Dict[str, Any] = {
    "auto": auto_repair,
    "ghostscript_reprint": ghostscript_reprint,
    "qpdf_repair": qpdf_repair,
    "restore_checkbox_appearances": restore_checkbox_appearances,
    "unlock": unlock_pdf,
    "repair_metadata": repair_metadata,
    "ocr": ocr_pdf,
}

REPAIR_ACTION_HELP = {
    "auto": (
        "Automatically try multiple repair strategies in sequence "
        "(qpdf, Ghostscript, metadata) until one produces a valid PDF."
    ),
    "ghostscript_reprint": (
        "Re-distill the PDF through Ghostscript to produce a completely fresh file. "
        "Optionally preserves existing form field locations and types."
    ),
    "qpdf_repair": (
        "Run pikepdf/qpdf repair mode to fix cross-reference tables "
        "and rebuild the page tree."
    ),
    "restore_checkbox_appearances": (
        "Restore missing checkbox appearance streams only for checkbox fields. "
        "Leaves text field appearances unchanged."
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

    Args:
        action: Repair action name from ``REPAIR_ACTIONS``.
        input_pdf_path: Source PDF path.
        output_pdf_path: Destination path for the repaired PDF.
        options: Optional keyword arguments forwarded to the repair action.

    Returns:
        Dict[str, Any]: Metadata returned by the selected repair function.
    """
    func = REPAIR_ACTIONS.get(action)
    if func is None:
        raise PDFRepairError(
            f"Unknown repair action {action!r}. " f"Available: {sorted(REPAIR_ACTIONS)}"
        )
    kwargs = dict(options or {})
    return func(input_pdf_path, output_pdf_path, **kwargs)  # type: ignore[operator]


def list_repair_actions() -> List[Dict[str, str]]:
    """Return a JSON-friendly list of available repair actions with help text.

    Returns:
        List[Dict[str, str]]: Available repair action metadata.
    """
    return [
        {"action": key, "description": REPAIR_ACTION_HELP.get(key, "")}
        for key in REPAIR_ACTIONS
    ]


def strip_embedded_fonts(
    input_pdf_path: str,
    output_pdf_path: str,
) -> Dict[str, Any]:
    """Remove embedded font programs from a PDF.

    Args:
        input_pdf_path: Source PDF path.
        output_pdf_path: Destination path for the stripped PDF.

    Returns:
        Dict[str, Any]: Metadata describing how many embedded fonts were removed.
    """
    _assert_pdf(input_pdf_path)
    import pikepdf

    _copy_if_same(input_pdf_path, output_pdf_path)

    fonts_removed = 0
    with pikepdf.open(input_pdf_path) as pdf:
        for page in pdf.pages:
            resources = page.get("/Resources")
            if not resources:
                continue
            font_dict = resources.get("/Font")
            if not font_dict:
                continue
            for _font_name in list(font_dict.keys()):
                font_obj = font_dict[_font_name]
                if not isinstance(font_obj, pikepdf.Object):
                    continue
                try:
                    font_obj = font_obj.resolve() if hasattr(font_obj, "resolve") else font_obj  # type: ignore[operator]
                except Exception:  # nosec B112
                    continue
                descriptor = None
                if hasattr(font_obj, "get"):
                    descriptor = font_obj.get("/FontDescriptor")
                if descriptor is None:
                    continue
                try:
                    descriptor = descriptor.resolve() if hasattr(descriptor, "resolve") else descriptor  # type: ignore[operator]
                except Exception:  # nosec B112
                    continue
                for key in ("/FontFile", "/FontFile2", "/FontFile3"):
                    if hasattr(descriptor, "get") and descriptor.get(key) is not None:
                        del descriptor[key]
                        fonts_removed += 1
        pdf.save(output_pdf_path)

    return {
        "status": "ok",
        "fonts_removed": fonts_removed,
    }
