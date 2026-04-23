from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, cast


class PDFAccessibilityError(RuntimeError):
    """Raised when accessibility metadata cannot be read or written."""


def default_pdf_field_tooltip(field_name: Any) -> str:
    """Build a readable default tooltip from a PDF field name."""
    raw = str(field_name or "").strip()
    if not raw:
        return "Field"
    normalized = raw.replace("_", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or "Field"


@dataclass
class _PositionedField:
    name: str
    page_index: int
    y: float
    x: float


def build_default_field_order(
    positioned_fields: Iterable[Mapping[str, Any]],
) -> List[str]:
    """Return a stable default reading order for field names.

    Order: page, top-to-bottom, then left-to-right.
    """
    rows: List[_PositionedField] = []
    for field in positioned_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        try:
            page_index = int(field.get("pageIndex", 0))
        except (TypeError, ValueError):
            page_index = 0
        try:
            y = float(field.get("y", 0.0))
        except (TypeError, ValueError):
            y = 0.0
        try:
            x = float(field.get("x", 0.0))
        except (TypeError, ValueError):
            x = 0.0
        rows.append(_PositionedField(name=name, page_index=page_index, y=y, x=x))

    rows.sort(key=lambda row: (row.page_index, row.y, row.x, row.name.casefold()))
    return [row.name for row in rows]


def _named_parent(field_obj: Any) -> Optional[Any]:
    if field_obj is None:
        return None
    if "/T" in field_obj:
        return field_obj
    parent = field_obj.get("/Parent") if hasattr(field_obj, "get") else None
    if parent is None:
        return None
    return _named_parent(parent)


def _safe_pdf_string(value: Any) -> str:
    return str(value) if value is not None else ""


def _widget_tooltip(annot: Any, parent: Any) -> str:
    """Return a custom tooltip stored on a field parent or widget annotation."""
    parent_tooltip = (
        _safe_pdf_string(parent.get("/TU", "")).strip()
        if hasattr(parent, "get")
        else ""
    )
    if parent_tooltip:
        return parent_tooltip
    return (
        _safe_pdf_string(annot.get("/TU", "")).strip() if hasattr(annot, "get") else ""
    )


def _extract_pdf_metadata(pdf: Any) -> Dict[str, str]:
    info = getattr(pdf, "docinfo", None)
    root = getattr(pdf, "Root", None)
    metadata: Dict[str, str] = {
        "title": "",
        "author": "",
        "subject": "",
        "language": "",
    }
    if info is not None:
        metadata["title"] = _safe_pdf_string(info.get("/Title", ""))
        metadata["author"] = _safe_pdf_string(info.get("/Author", ""))
        metadata["subject"] = _safe_pdf_string(info.get("/Subject", ""))
    if root is not None:
        metadata["language"] = _safe_pdf_string(root.get("/Lang", ""))
    return metadata


def _extract_struct_tree_summary(root: Any) -> Dict[str, Any]:
    struct_root = root.get("/StructTreeRoot") if root is not None else None
    if struct_root is None:
        return {
            "present": False,
            "node_count": 0,
            "max_depth": 0,
            "preview": [],
        }

    preview: List[str] = []
    node_count = 0
    max_depth = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal node_count, max_depth
        if node is None:
            return
        node_count += 1
        max_depth = max(max_depth, depth)

        node_type = _safe_pdf_string(node.get("/S", "")) if hasattr(node, "get") else ""
        if node_type and len(preview) < 30:
            preview.append(("  " * depth) + node_type)

        kids = node.get("/K") if hasattr(node, "get") else None
        if kids is None:
            return
        if isinstance(kids, list):
            for kid in kids:
                if hasattr(kid, "get"):
                    walk(kid, depth + 1)
            return
        if hasattr(kids, "get"):
            walk(kids, depth + 1)

    walk(struct_root, 0)
    return {
        "present": True,
        "node_count": node_count,
        "max_depth": max_depth,
        "preview": preview,
    }


def _extract_field_records(pdf: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for page_index, page in enumerate(pdf.pages):
        annots = page.get("/Annots") if hasattr(page, "get") else None
        if not annots:
            continue
        for annot in annots:
            try:
                if annot.get("/Subtype") != "/Widget":
                    continue
                parent = _named_parent(annot)
                if parent is None:
                    continue
                name = _safe_pdf_string(parent.get("/T", "")).strip()
                if not name or name in seen:
                    continue
                tooltip = _widget_tooltip(annot, parent)
                records.append(
                    {
                        "name": name,
                        "tooltip": tooltip or default_pdf_field_tooltip(name),
                        "has_custom_tooltip": bool(tooltip),
                        "pageIndex": page_index,
                    }
                )
                seen.add(name)
            except Exception:
                continue

    acroform = pdf.Root.get("/AcroForm") if hasattr(pdf.Root, "get") else None
    ordered_names: List[str] = []
    if acroform is not None and "/Fields" in acroform:
        for field_ref in acroform["/Fields"]:
            try:
                field_obj = field_ref
                name = _safe_pdf_string(field_obj.get("/T", "")).strip()
                if name and name not in ordered_names:
                    ordered_names.append(name)
            except Exception:
                continue

    if not ordered_names:
        ordered_names = [record["name"] for record in records]

    return records, ordered_names


def _extract_image_assets(pdf: Any) -> List[Dict[str, Any]]:
    assets: List[Dict[str, Any]] = []
    for page_index, page in enumerate(pdf.pages):
        resources = page.get("/Resources") if hasattr(page, "get") else None
        xobject_dict = resources.get("/XObject") if resources is not None else None
        if not xobject_dict:
            continue
        for key, obj in xobject_dict.items():
            try:
                if obj.get("/Subtype") != "/Image":
                    continue
                asset_id = f"p{page_index + 1}:{str(key).lstrip('/')}"
                assets.append(
                    {
                        "assetId": asset_id,
                        "pageIndex": page_index,
                        "name": str(key),
                        "width": int(obj.get("/Width", 0) or 0),
                        "height": int(obj.get("/Height", 0) or 0),
                        "altText": _safe_pdf_string(obj.get("/Alt", "")).strip(),
                    }
                )
            except Exception:
                continue
    return assets


def inspect_pdf_accessibility(pdf_path: str) -> Dict[str, Any]:
    """Read basic accessibility-relevant PDF metadata and structures."""
    try:
        import pikepdf

        with pikepdf.open(pdf_path) as pdf:
            fields, field_order = _extract_field_records(pdf)
            return {
                "metadata": _extract_pdf_metadata(pdf),
                "fields": fields,
                "field_order": field_order,
                "images": _extract_image_assets(pdf),
                "tag_structure": _extract_struct_tree_summary(pdf.Root),
            }
    except Exception as exc:
        raise PDFAccessibilityError(f"Failed to inspect PDF accessibility data: {exc}")


def extract_pdf_field_tooltips(pdf_path: str) -> Dict[str, str]:
    """Return custom PDF field tooltips keyed by field name.

    The PDF tooltip used by assistive technology is stored in the ``/TU`` entry.
    Some generators put it on the field dictionary and others put it on the
    widget annotation, so this checks both and only returns explicit values.
    """
    try:
        import pikepdf

        with pikepdf.open(pdf_path) as pdf:
            fields, _field_order = _extract_field_records(pdf)
            return {
                str(field["name"]): str(field["tooltip"])
                for field in fields
                if field.get("name") and field.get("has_custom_tooltip")
            }
    except Exception as exc:
        raise PDFAccessibilityError(f"Failed to extract PDF field tooltips: {exc}")


def _field_name_to_tooltip(
    field_name: str,
    explicit_tooltips: Mapping[str, str],
    *,
    auto_fill: bool,
) -> Optional[str]:
    explicit = str(explicit_tooltips.get(field_name, "")).strip()
    if explicit:
        return explicit
    if auto_fill:
        return default_pdf_field_tooltip(field_name)
    return None


def apply_pdf_accessibility_settings(
    *,
    input_pdf_path: str,
    output_pdf_path: str,
    field_tooltips: Optional[Mapping[str, str]] = None,
    field_order: Optional[List[str]] = None,
    image_alt_text: Optional[Mapping[str, str]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    auto_fill_missing_tooltips: bool = True,
) -> Dict[str, Any]:
    """Apply basic PDF accessibility metadata in place.

    This updates AcroForm tooltips/order, image alt text, and document metadata.
    """
    if input_pdf_path != output_pdf_path:
        shutil.copyfile(input_pdf_path, output_pdf_path)

    tooltip_map = {str(k): str(v) for k, v in (field_tooltips or {}).items()}
    image_alt_map = {str(k): str(v) for k, v in (image_alt_text or {}).items()}

    try:
        import pikepdf

        tooltip_updates = 0
        reordered_fields = 0
        image_alt_updates = 0
        metadata_updates = 0

        with pikepdf.open(output_pdf_path, allow_overwriting_input=True) as pdf:
            if metadata:
                docinfo = pdf.docinfo
                title = str(metadata.get("title") or "").strip()
                author = str(metadata.get("author") or "").strip()
                subject = str(metadata.get("subject") or "").strip()
                language = str(metadata.get("language") or "").strip()
                if title:
                    docinfo["/Title"] = pikepdf.String(title)
                    metadata_updates += 1
                if author:
                    docinfo["/Author"] = pikepdf.String(author)
                    metadata_updates += 1
                if subject:
                    docinfo["/Subject"] = pikepdf.String(subject)
                    metadata_updates += 1
                if language:
                    pdf.Root["/Lang"] = pikepdf.String(language)
                    metadata_updates += 1

            # Update tooltips by walking widget annotations.
            for page in pdf.pages:
                annots = page.get("/Annots") if hasattr(page, "get") else None
                if not annots:
                    continue
                for annot in cast(Iterable[Any], annots):
                    try:
                        if annot.get("/Subtype") != "/Widget":
                            continue
                        parent = _named_parent(annot)
                        if parent is None:
                            continue
                        field_name = _safe_pdf_string(parent.get("/T", "")).strip()
                        if not field_name:
                            continue
                        tooltip = _field_name_to_tooltip(
                            field_name,
                            tooltip_map,
                            auto_fill=auto_fill_missing_tooltips,
                        )
                        if tooltip:
                            parent["/TU"] = pikepdf.String(tooltip)
                            annot["/TU"] = pikepdf.String(tooltip)
                            tooltip_updates += 1
                    except Exception:
                        continue

            # Reorder AcroForm fields to match caller-supplied order.
            if field_order:
                acroform = (
                    pdf.Root.get("/AcroForm") if hasattr(pdf.Root, "get") else None
                )
                if acroform is not None and "/Fields" in acroform:
                    ordered = [
                        str(name).strip() for name in field_order if str(name).strip()
                    ]
                    if ordered:
                        existing_refs = list(cast(Iterable[Any], acroform["/Fields"]))
                        by_name: Dict[str, Any] = {}
                        fallback_refs: List[Any] = []
                        for ref in existing_refs:
                            try:
                                name = _safe_pdf_string(ref.get("/T", "")).strip()
                                if name and name not in by_name:
                                    by_name[name] = ref
                                else:
                                    fallback_refs.append(ref)
                            except Exception:
                                fallback_refs.append(ref)
                        new_refs: List[Any] = []
                        used_names: set[str] = set()
                        for name in ordered:
                            ref = by_name.get(name)
                            if ref is not None and name not in used_names:
                                new_refs.append(ref)
                                used_names.add(name)
                        for name, ref in by_name.items():
                            if name not in used_names:
                                new_refs.append(ref)
                        new_refs.extend(fallback_refs)
                        acroform["/Fields"] = pikepdf.Array(new_refs)
                        reordered_fields = len(new_refs)

            # Update image alt text when IDs are provided.
            if image_alt_map:
                for page_index, page in enumerate(pdf.pages):
                    resources = page.get("/Resources") if hasattr(page, "get") else None
                    xobject_dict = (
                        resources.get("/XObject") if resources is not None else None
                    )
                    if not xobject_dict:
                        continue
                    for key, obj in xobject_dict.items():
                        try:
                            if obj.get("/Subtype") != "/Image":
                                continue
                            asset_id = f"p{page_index + 1}:{str(key).lstrip('/')}"
                            if asset_id not in image_alt_map:
                                continue
                            alt_value = str(image_alt_map.get(asset_id, "")).strip()
                            if alt_value:
                                obj["/Alt"] = pikepdf.String(alt_value)
                            elif "/Alt" in obj:
                                del obj["/Alt"]
                            image_alt_updates += 1
                        except Exception:
                            continue

            pdf.save(output_pdf_path)

        return {
            "tooltip_updates": tooltip_updates,
            "field_order_count": reordered_fields,
            "image_alt_updates": image_alt_updates,
            "metadata_updates": metadata_updates,
        }
    except Exception as exc:
        raise PDFAccessibilityError(
            f"Failed to apply PDF accessibility settings: {exc}"
        )
