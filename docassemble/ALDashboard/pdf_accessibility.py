from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed executable and arguments only
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, cast

from .standard_font_metrics import is_standard_14, standard_14_widths
from .symbol_fonts import (
    canonical_symbol_family,
    is_symbolic_family,
    propose_character,
)

ACCESSIBILITY_REMEDIATIONS: Dict[str, Dict[str, Any]] = {
    "metadata": {
        "label": "Document metadata",
        "kind": "automatic",
        "description": "Set language, title, author, subject, and display-title preference.",
    },
    "field_tooltips": {
        "label": "Form field names",
        "kind": "assisted",
        "description": "Draft accessible names from nearby text or field names, then review each one.",
    },
    "reading_order": {
        "label": "Form reading order",
        "kind": "assisted",
        "description": "Sort widgets in a chosen reading direction; every item remains manually reorderable.",
    },
    "catalog_flags": {
        "label": "Accessibility flags",
        "kind": "manual",
        "description": "Set MarkInfo.Marked only after the tag tree is meaningful.",
    },
    "draft_structure": {
        "label": "Draft tag structure",
        "kind": "assisted",
        "description": "Tag existing text objects as content blocks, promote conservative heading matches, and preserve widgets as form elements.",
    },
    "fonts": {
        "label": "Embed exact font matches",
        "kind": "assisted",
        "description": "Attach an exact, license-permitted TrueType program without rewriting PDF content; never substitute automatically.",
    },
    "figures": {
        "label": "Figures and alternative text",
        "kind": "manual",
        "description": "Classify each image as meaningful or decorative and review all drafted alternative text.",
    },
    "structure": {
        "label": "Headings, tables, links, and annotations",
        "kind": "manual",
        "description": "Review heuristic candidates and repair semantic structure directly.",
    },
}


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


def draft_field_tooltips_with_ai(
    fields: Iterable[Mapping[str, Any]], *, model: Optional[str] = None
) -> Dict[str, str]:
    """Draft concise field tooltips from caller-supplied names and nearby text.

    This function is never called by inspection or export.  The interactive
    caller must explicitly request it, and its output is always presented as a
    draft for review.
    """
    from docassemble.ALToolbox.llms import chat_completion

    records = []
    allowed_names: set[str] = set()
    for field in fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        allowed_names.add(name)
        records.append(
            {
                "name": name,
                "type": str(field.get("type") or "text"),
                "nearby_text": [
                    str(item)[:240]
                    for item in (field.get("nearby_text") or [])
                    if str(item).strip()
                ][:5],
                "current_tooltip": str(field.get("tooltip") or "")[:240],
            }
        )
    if not records:
        return {}
    response = chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Draft short, plain-language labels for PDF form controls. Use the nearby printed "
                    "label, current label, and field type while preserving the legal meaning. Each "
                    "tooltip must name the information or choice represented by the control: use a "
                    "sentence fragment of at most 45 characters, not an instruction or a full sentence. "
                    "Do not begin with Enter, Type, Input, Provide, Choose, Select, Check, or Please. "
                    "Prefer labels such as 'Date of birth' and 'Mother’s full name', not 'Enter your date "
                    "of birth' or 'Please provide the mother’s full name'. Use simple vocabulary, avoid "
                    "variable names and underscores, and keep a good current label when it is already "
                    "short and clear. Do not rename the opaque PDF field. "
                    "Return JSON with a tooltips array of objects containing exactly name and tooltip."
                ),
            },
            {"role": "user", "content": json.dumps(records, ensure_ascii=False)},
        ],
        json_mode=True,
        temperature=0,
        max_output_tokens=min(32768, max(8192, len(records) * 96)),
    )
    if isinstance(response, str):
        response = json.loads(response)
    rows = response.get("tooltips", []) if isinstance(response, dict) else []
    result: Dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        tooltip = str(row.get("tooltip") or "")
        tooltip = re.sub(r"\s+", " ", tooltip).strip(" .:-")
        tooltip = tooltip[:45].strip()
        if name in allowed_names and tooltip:
            result[name] = tooltip
    return result


def draft_heading_levels_with_ai(
    candidates: Iterable[Mapping[str, Any]], *, model: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Classify supplied heading candidates after an explicit AI request."""
    from docassemble.ALToolbox.llms import chat_completion

    records = []
    allowed_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidateId") or "").strip()
        if not candidate_id:
            continue
        allowed_ids.add(candidate_id)
        records.append(
            {
                "candidateId": candidate_id,
                "page": int(candidate.get("pageIndex", 0)) + 1,
                "text": str(candidate.get("text") or "")[:300],
                "fontSize": candidate.get("fontSize"),
                "heuristicTag": str(candidate.get("suggestedTag") or "H2"),
            }
        )
    if not records:
        return []
    response = chat_completion(
        model=model,
        json_mode=True,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify possible PDF headings. Use document semantics, not font size alone. "
                    "For every candidate return candidateId, isHeading, suggestedTag (H1-H6), "
                    "and a short reason. Preserve candidateId exactly. Form labels, instructions, "
                    "question markers, and running headers are usually not headings. Return JSON "
                    "with a decisions array. This is a review draft, not a final accessibility decision."
                ),
            },
            {"role": "user", "content": json.dumps({"candidates": records})},
        ],
    )
    rows = response.get("decisions", []) if isinstance(response, dict) else []
    decisions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidateId") or "")
        tag = str(row.get("suggestedTag") or "H2").upper()
        if candidate_id not in allowed_ids or not re.fullmatch(r"H[1-6]", tag):
            continue
        decisions.append(
            {
                "candidateId": candidate_id,
                "isHeading": bool(row.get("isHeading")),
                "suggestedTag": tag,
                "reason": str(row.get("reason") or "")[:240],
            }
        )
    return decisions


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
    import pikepdf

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
        if isinstance(kids, (list, pikepdf.Array)):
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


def _structure_children(node: Any) -> List[Any]:
    """Return child structure elements, excluding MCIDs and object references."""
    import pikepdf

    kids = node.get("/K") if hasattr(node, "get") else None
    if kids is None:
        return []
    candidates = list(kids) if isinstance(kids, (list, pikepdf.Array)) else [kids]
    return [
        kid for kid in candidates if hasattr(kid, "get") and kid.get("/S") is not None
    ]


def _structure_scope(node: Any) -> str:
    import pikepdf

    attributes = node.get("/A") if hasattr(node, "get") else None
    if attributes is None:
        return ""
    candidates = (
        list(attributes)
        if isinstance(attributes, (list, pikepdf.Array))
        else [attributes]
    )
    for item in candidates:
        if hasattr(item, "get") and item.get("/Scope") is not None:
            return _safe_pdf_string(item.get("/Scope", "")).lstrip("/")
    return ""


def _structure_form_field_name(node: Any) -> str:
    """Return the field name referenced by a Form structure element."""
    import pikepdf

    kids = node.get("/K") if hasattr(node, "get") else None
    candidates: List[Any] = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
    for kid in candidates:
        if not hasattr(kid, "get") or kid.get("/Type") != "/OBJR":
            continue
        field = _named_parent(kid.get("/Obj"))
        if field is not None:
            return _safe_pdf_string(field.get("/T", "")).strip()
    return ""


def _reorder_structure_form_elements(root: Any, ordered: List[str]) -> int:
    """Sort sibling Form elements while preserving every non-Form position."""
    import pikepdf

    struct_root = root.get("/StructTreeRoot") if root is not None else None
    if struct_root is None:
        return 0
    order_index = {name: index for index, name in enumerate(ordered)}
    moved = 0

    def walk(node: Any) -> None:
        nonlocal moved
        kids = node.get("/K") if hasattr(node, "get") else None
        if isinstance(kids, pikepdf.Array):
            values = list(kids)
            slots = [
                index
                for index, child in enumerate(values)
                if hasattr(child, "get")
                and _safe_pdf_string(child.get("/S", "")) == "/Form"
            ]
            forms = [values[index] for index in slots]
            original_positions = {id(form): index for index, form in enumerate(forms)}
            sorted_forms = sorted(
                forms,
                key=lambda form: (
                    order_index.get(_structure_form_field_name(form), len(order_index)),
                    original_positions[id(form)],
                ),
            )
            moved += sum(
                1 for before, after in zip(forms, sorted_forms) if before is not after
            )
            for slot, form in zip(slots, sorted_forms):
                kids[slot] = form
        for child in _structure_children(node):
            walk(child)

    walk(struct_root)
    return moved


def _structure_editor_data(pdf: Any) -> Dict[str, Any]:
    """Return stable tree paths and annotation coordinates for manual repairs."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return {"tables": [], "figures": [], "annotations": []}
    page_indexes = {
        str(getattr(page.obj, "objgen", "")): index
        for index, page in enumerate(pdf.pages)
    }
    tables: List[Dict[str, Any]] = []
    figures: List[Dict[str, Any]] = []
    tagged_annotation_roles: Dict[str, set[str]] = {}

    def page_index_for(node: Any) -> Optional[int]:
        page = node.get("/Pg") if hasattr(node, "get") else None
        return page_indexes.get(str(getattr(page, "objgen", "")))

    def collect_objr(node: Any) -> None:
        import pikepdf

        parent_role = _safe_pdf_string(node.get("/S", "")).lstrip("/")
        kids = node.get("/K") if hasattr(node, "get") else None
        candidates: List[Any] = (
            list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        )
        for kid in candidates:
            if hasattr(kid, "get") and kid.get("/Type") == "/OBJR":
                object_id = str(getattr(kid.get("/Obj"), "objgen", ""))
                tagged_annotation_roles.setdefault(object_id, set()).add(parent_role)

    def walk(node: Any, path: List[int]) -> None:
        role = _safe_pdf_string(node.get("/S", "")).lstrip("/")
        children = _structure_children(node)
        collect_objr(node)
        path_text = "/".join(str(index) for index in path)
        if role == "Figure":
            alt_text = _safe_pdf_string(node.get("/Alt", ""))
            figures.append(
                {
                    "path": path_text,
                    "pageIndex": page_index_for(node),
                    "altText": alt_text,
                    "actualText": _safe_pdf_string(node.get("/ActualText", "")),
                    "issueIds": [] if alt_text.strip() else ["figure-structure-alt"],
                }
            )
        if role == "Table":
            rows: List[Dict[str, Any]] = []
            for row_index, row in enumerate(children):
                if _safe_pdf_string(row.get("/S", "")).lstrip("/") != "TR":
                    continue
                row_path = path + [row_index]
                cells: List[Dict[str, Any]] = []
                for cell_index, cell in enumerate(_structure_children(row)):
                    cells.append(
                        {
                            "path": "/".join(
                                str(index) for index in row_path + [cell_index]
                            ),
                            "role": _safe_pdf_string(cell.get("/S", "")).lstrip("/"),
                            "scope": _structure_scope(cell),
                        }
                    )
                rows.append(
                    {
                        "path": "/".join(str(index) for index in row_path),
                        "cells": cells,
                        "validCellCount": sum(
                            1 for cell in cells if cell["role"] in {"TH", "TD"}
                        ),
                    }
                )
            tables.append(
                {
                    "path": path_text,
                    "pageIndex": page_index_for(node),
                    "rows": rows,
                    "targetColumns": max(
                        (row["validCellCount"] for row in rows), default=0
                    ),
                    "issueIds": [
                        issue_id
                        for issue_id, applies in (
                            (
                                "table-row-children",
                                any(
                                    cell["role"] not in {"TH", "TD"}
                                    for row in rows
                                    for cell in row["cells"]
                                ),
                            ),
                            (
                                "table-columns",
                                len({row["validCellCount"] for row in rows}) > 1,
                            ),
                            (
                                "table-header-scope",
                                any(
                                    cell["role"] == "TH" and not cell["scope"]
                                    for row in rows
                                    for cell in row["cells"]
                                ),
                            ),
                        )
                        if applies
                    ],
                }
            )
        for index, child in enumerate(children):
            walk(child, path + [index])

    for index, child in enumerate(_structure_children(struct_root)):
        walk(child, [index])

    annotations = []
    for page_index, page in enumerate(pdf.pages):
        for index, annot in enumerate(cast(Iterable[Any], page.get("/Annots") or [])):
            subtype = _safe_pdf_string(annot.get("/Subtype", "")).lstrip("/")
            if subtype == "Widget":
                continue
            object_id = str(getattr(annot, "objgen", ""))
            expected_role = "Link" if subtype == "Link" else "Annot"
            actual_roles = sorted(tagged_annotation_roles.get(object_id, set()))
            correctly_tagged = expected_role in actual_roles
            annotations.append(
                {
                    "pageIndex": page_index,
                    "index": index,
                    "subtype": subtype or "Annotation",
                    "contents": _safe_pdf_string(annot.get("/Contents", "")),
                    "tagged": correctly_tagged,
                    "existingRoles": actual_roles,
                    "suggestedRole": expected_role,
                    "issueIds": (
                        []
                        if correctly_tagged
                        else [
                            "link-tags" if subtype == "Link" else "annotation-tags"
                        ]
                    ),
                }
            )
    return {"tables": tables, "figures": figures, "annotations": annotations}


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


def _pdf_object_identity(obj: Any, fallback: Any) -> str:
    """Use an indirect object number, but never collapse direct objects at (0, 0)."""
    objgen = getattr(obj, "objgen", None)
    if objgen is not None and tuple(objgen) != (0, 0):
        return str(objgen)
    return str(fallback)


def _walk_resource_xobjects(
    resources: Any, *, prefix: str = "", seen: Optional[set[str]] = None
) -> Iterable[Tuple[str, Any]]:
    """Yield nested image/Form XObjects with stable resource paths."""
    if seen is None:
        seen = set()
    xobjects = resources.get("/XObject") if resources is not None else None
    if not xobjects:
        return
    for key, obj in xobjects.items():
        name = str(key).lstrip("/")
        path = f"{prefix}/{name}" if prefix else name
        identity = _pdf_object_identity(obj, path)
        yield path, obj
        if (
            _safe_pdf_string(obj.get("/Subtype", "")) == "/Form"
            and identity not in seen
        ):
            seen.add(identity)
            yield from _walk_resource_xobjects(
                obj.get("/Resources"), prefix=path, seen=seen
            )


def _extract_image_assets(pdf: Any) -> List[Dict[str, Any]]:
    assets: List[Dict[str, Any]] = []
    for page_index, page in enumerate(pdf.pages):
        resources = page.get("/Resources") if hasattr(page, "get") else None
        for resource_path, obj in _walk_resource_xobjects(resources):
            try:
                if obj.get("/Subtype") != "/Image":
                    continue
                asset_id = f"p{page_index + 1}:{resource_path}"
                assets.append(
                    {
                        "assetId": asset_id,
                        "pageIndex": page_index,
                        "name": resource_path,
                        "width": int(obj.get("/Width", 0) or 0),
                        "height": int(obj.get("/Height", 0) or 0),
                        "altText": _safe_pdf_string(obj.get("/Alt", "")).strip(),
                    }
                )
            except Exception:
                continue
    return assets


def _font_descriptor(font: Any) -> Optional[Any]:
    """Return a simple or descendant font descriptor, when present."""
    descriptor = font.get("/FontDescriptor") if hasattr(font, "get") else None
    if descriptor is not None:
        return descriptor
    descendants = font.get("/DescendantFonts") if hasattr(font, "get") else None
    if descendants:
        try:
            descendant = descendants[0]
            return descendant.get("/FontDescriptor")
        except Exception:
            return None
    return None


def _extract_font_records(pdf: Any) -> List[Dict[str, Any]]:
    """Inventory fonts once per indirect object, including PDF/UA essentials."""
    records: List[Dict[str, Any]] = []
    for resource, font in _iter_pdf_fonts(pdf):
        try:
            descriptor = _font_descriptor(font)
            embedded = bool(
                descriptor
                and any(
                    key in descriptor
                    for key in ("/FontFile", "/FontFile2", "/FontFile3")
                )
            )
            page_match = re.match(r"p(\d+)", resource)
            records.append(
                {
                    "resource": resource,
                    "name": _safe_pdf_string(font.get("/BaseFont", resource)).lstrip(
                        "/"
                    ),
                    "subtype": _safe_pdf_string(font.get("/Subtype", "")).lstrip("/"),
                    "pageIndex": int(page_match.group(1)) - 1 if page_match else None,
                    "embedded": embedded,
                    "hasToUnicode": "/ToUnicode" in font,
                }
            )
        except Exception:
            continue
    return records


def _iter_pdf_fonts(pdf: Any) -> Iterable[Tuple[str, Any]]:
    """Yield each distinct page font object once with its resource path."""
    seen: set[str] = set()
    resource_sets: List[Tuple[str, Any]] = []
    for page_index, page in enumerate(pdf.pages):
        page_prefix = f"p{page_index + 1}"
        resources = page.get("/Resources") if hasattr(page, "get") else None
        resource_sets.append((page_prefix, resources))
        resource_sets.extend(
            (f"{page_prefix}:{path}", obj.get("/Resources"))
            for path, obj in _walk_resource_xobjects(resources)
            if _safe_pdf_string(obj.get("/Subtype", "")) == "/Form"
        )
        for annot_index, annot in enumerate(
            cast(Iterable[Any], page.get("/Annots") or [])
        ):
            appearances = annot.get("/AP") if hasattr(annot, "get") else None
            normal = appearances.get("/N") if appearances is not None else None
            if normal is None:
                continue
            streams = (
                [normal]
                if hasattr(normal, "read_bytes")
                else list(normal.values()) if hasattr(normal, "values") else []
            )
            for state_index, stream in enumerate(streams):
                resource_sets.append(
                    (
                        f"{page_prefix}:annot{annot_index}:ap{state_index}",
                        stream.get("/Resources"),
                    )
                )
    for resource_path, resource_dict in resource_sets:
        fonts = resource_dict.get("/Font") if resource_dict is not None else None
        if not fonts:
            continue
        for resource_name, font in fonts.items():
            identity = _pdf_object_identity(
                font, (resource_path, str(resource_name))
            )
            if identity in seen:
                continue
            seen.add(identity)
            yield f"{resource_path}/{str(resource_name).lstrip('/')}", font


def _canonical_font_name(value: Any) -> str:
    name = re.sub(r"^[A-Z]{6}\+", "", str(value or "").lstrip("/"))
    canonical = re.sub(r"[^a-z0-9]+", "", name.casefold())
    canonical = canonical.replace("postscript", "").replace("newromanps", "newroman")
    canonical = re.sub(r"(?:std|mt)$", "", canonical)
    return canonical


def _font_program_format(font: Any) -> str:
    """Classify an opened font by the kind of program it carries."""
    if "glyf" in font:
        return "truetype"
    if "CFF " in font:
        try:
            top = font["CFF "].cff.topDictIndex[0]
        except Exception:
            return ""
        # A CID-keyed CFF needs a composite font dictionary, which this tool
        # does not build, so only plain CFF is offered for embedding.
        return "" if hasattr(top, "ROS") else "cff"
    return ""


def _system_embeddable_fonts() -> List[Dict[str, Any]]:
    """Inventory embeddable font files known to fontconfig.

    Covers TrueType and plain CFF/OpenType, recording which kind each one is
    so the caller can pick the right /FontFile entry for it.
    """
    executable = shutil.which("fc-list")
    if not executable:
        return []
    try:
        result = subprocess.run(  # nosec B603
            [executable, "-f", "%{file}\n"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in result.stdout.splitlines():
        path = raw_path.strip()
        if not path or path in seen:
            continue
        if Path(path).suffix.casefold() not in {".ttf", ".otf"}:
            continue
        seen.add(path)
        try:
            from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

            font = TTFont(path, lazy=True)
            try:
                if "fvar" in font:
                    continue
                program_format = _font_program_format(font)
                if not program_format:
                    continue
                names: set[str] = set()
                postscript_name = ""
                for record in font["name"].names:
                    if record.nameID not in {1, 2, 4, 6, 16, 17}:
                        continue
                    try:
                        value = record.toUnicode()
                    except Exception:
                        continue
                    names.add(value)
                    if record.nameID == 6 and not postscript_name:
                        postscript_name = value
                fs_type = int(font["OS/2"].fsType) if "OS/2" in font else 0
                os2 = font["OS/2"] if "OS/2" in font else None
                weight = int(getattr(os2, "usWeightClass", 400) or 400)
                italic_angle = float(getattr(font["post"], "italicAngle", 0.0))
            finally:
                font.close()
            records.append(
                {
                    "path": path,
                    "names": sorted(names),
                    "postscript_name": postscript_name or Path(path).stem,
                    "canonical_names": {_canonical_font_name(name) for name in names},
                    "format": program_format,
                    "bold": weight >= 600,
                    "italic": abs(italic_angle) > 0.5,
                    "embeddable": not bool(fs_type & 0x0002 or fs_type & 0x0200),
                    "fsType": fs_type,
                }
            )
        except Exception:
            continue
    return records


def _system_truetype_fonts() -> List[Dict[str, Any]]:
    """Inventory only the fonts that can fill a /FontFile2 entry."""
    return [
        record
        for record in _system_embeddable_fonts()
        if record.get("format") == "truetype"
    ]


def _expected_font_widths(pdf_font: Any) -> Tuple[Dict[int, float], str]:
    """Collect the widths a replacement font must reproduce, by code point.

    Most fonts state their own widths. A standard 14 font never does, because
    conforming viewers already know its metrics, so fall back to the published
    table for the face it names. Without one of those two references there is
    nothing to verify against and no font may be embedded.
    """
    expected: Dict[int, float] = {}
    widths = pdf_font.get("/Widths") if hasattr(pdf_font, "get") else None
    if widths:
        first_char = int(pdf_font.get("/FirstChar", 0))
        for offset, pdf_width in enumerate(widths):
            code = first_char + offset
            try:
                character = bytes([code]).decode("cp1252")
            except (UnicodeDecodeError, ValueError):
                continue
            expected[ord(character)] = float(pdf_width)
        return expected, "pdf-widths"
    canonical = _canonical_font_name(pdf_font.get("/BaseFont", ""))
    published = standard_14_widths(canonical)
    if published:
        return {code: float(width) for code, width in published.items()}, "standard-14"
    return {}, ""


def _font_width_match_score(pdf_font: Any, font_path: str) -> Optional[float]:
    """Compare the widths a font must reproduce against a candidate file."""
    expected, _source = _expected_font_widths(pdf_font)
    if not expected:
        return None
    try:
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

        font = TTFont(font_path, lazy=True)
        try:
            cmap = font.getBestCmap() or {}
            metrics = font["hmtx"].metrics
            units_per_em = float(font["head"].unitsPerEm)
            differences: List[float] = []
            for code_point, expected_width in expected.items():
                glyph = cmap.get(code_point)
                if not glyph or glyph not in metrics:
                    continue
                candidate_width = metrics[glyph][0] * 1000.0 / units_per_em
                differences.append(abs(expected_width - candidate_width))
        finally:
            font.close()
        if len(differences) < 10:
            return None
        differences.sort()
        # A median would report a perfect match for a bold face, because bold
        # and regular share widths for digits, punctuation and accents and
        # differ only on letters -- enough glyphs to agree that the middle
        # value stays at zero. The 90th percentile catches that minority.
        index = min(int(len(differences) * 0.9), len(differences) - 1)
        return differences[index]
    except Exception:
        return None


def find_exact_system_font(
    pdf_font: Any, inventory: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Find an embeddable font with matching name and existing PDF metrics."""
    expected = _canonical_font_name(pdf_font.get("/BaseFont", ""))
    descriptor = _font_descriptor(pdf_font)
    if descriptor is not None:
        expected_names = {
            expected,
            _canonical_font_name(descriptor.get("/FontName", "")),
        }
    else:
        expected_names = {expected}
    expected_names.discard("")
    candidates = [
        record
        for record in inventory
        if record["embeddable"]
        and expected_names.intersection(record["canonical_names"])
    ]
    scored = []
    for record in candidates:
        score = _font_width_match_score(pdf_font, record["path"])
        if score is not None and score <= 2.0:
            scored.append((score, record))
    return min(scored, key=lambda item: item[0])[1] if scored else None


def _simple_font_code_points(pdf_font: Any) -> Dict[int, int]:
    """Map each single-byte character code to the code point it draws.

    Starts from WinAnsi, which is what a form field's text is encoded as, then
    applies any /Differences the font declares. Codes that resolve to nothing
    are simply absent, and the caller gives them a missing width.
    """
    from fontTools.agl import toUnicode  # type: ignore[import-untyped]

    mapping: Dict[int, int] = {}
    for code in range(0x20, 0x100):
        try:
            mapping[code] = ord(bytes([code]).decode("cp1252"))
        except (UnicodeDecodeError, ValueError):
            continue
    encoding = pdf_font.get("/Encoding") if hasattr(pdf_font, "get") else None
    differences = (
        encoding.get("/Differences")
        if encoding is not None and hasattr(encoding, "get")
        else None
    ) or []
    current = 0
    for item in differences:
        if isinstance(item, (int, float)):
            current = int(item)
            continue
        text = toUnicode(_safe_pdf_string(item).lstrip("/"))
        if text:
            mapping[current] = ord(text[0])
        else:
            mapping.pop(current, None)
        current += 1
    return mapping


def _descriptor_for_program(
    pdf: Any,
    font_name: str,
    font_path: str,
    symbolic: bool,
    file_key: str = "/FontFile2",
    file_subtype: str = "",
) -> Any:
    """Build the /FontDescriptor a standard 14 font never carried.

    Every value is read from the font program being embedded rather than
    guessed, apart from /StemV, which no TrueType table records and which
    viewers use only as a hint.
    """
    import pikepdf
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    font = TTFont(font_path, lazy=True)
    try:
        units = float(font["head"].unitsPerEm) or 1000.0
        scale = 1000.0 / units

        def scaled(value: Any) -> int:
            return int(round(float(value) * scale))

        head = font["head"]
        os2 = font["OS/2"] if "OS/2" in font else None
        hhea = font["hhea"]
        ascent = scaled(getattr(os2, "sTypoAscender", None) or hhea.ascent)
        descent = scaled(getattr(os2, "sTypoDescender", None) or hhea.descent)
        cap_height = scaled(getattr(os2, "sCapHeight", None) or ascent / scale)
        weight = int(getattr(os2, "usWeightClass", 400) or 400)
        italic_angle = float(getattr(font["post"], "italicAngle", 0.0))
        descriptor = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name(f"/{font_name}"),
                "/Flags": 4 if symbolic else 32,
                "/FontBBox": [
                    scaled(head.xMin),
                    scaled(head.yMin),
                    scaled(head.xMax),
                    scaled(head.yMax),
                ],
                "/ItalicAngle": italic_angle,
                "/Ascent": ascent,
                "/Descent": descent,
                "/CapHeight": cap_height,
                # No TrueType table records stem width; viewers treat it as a
                # hint, so derive a conventional value from the weight class.
                "/StemV": 160 if weight >= 600 else 80,
            }
        )
    finally:
        font.close()
    program, resolved_key, resolved_subtype = _embeddable_program(font_path)
    if program is None:
        raise PDFAccessibilityError(f"{font_name} could not be read for embedding.")
    file_key = resolved_key or file_key
    file_subtype = resolved_subtype or file_subtype
    stream = pdf.make_stream(program)
    if file_key == "/FontFile2":
        # /Length1 is the uncompressed TrueType program length; a CFF stream
        # carries its subtype instead.
        stream["/Length1"] = len(program)
    elif file_subtype:
        stream["/Subtype"] = pikepdf.Name(file_subtype)
    descriptor[file_key] = stream
    return pdf.make_indirect(descriptor)


def _embed_into_standard_14_font(
    pdf: Any, pdf_font: Any, font_name: str, font_path: str
) -> bool:
    """Give a standard 14 font dictionary a real, self-contained font program.

    A standard 14 entry names a face and stops there, trusting the viewer to
    own a copy. PDF/UA does not allow that, and there is no /FontDescriptor to
    attach a program to, so the dictionary has to be completed: published
    widths are written out, a descriptor is built from the program, and the
    subtype becomes /TrueType to match what is now embedded. Widths come from
    the same table the viewer was already using, so nothing reflows.
    """
    import pikepdf

    canonical = _canonical_font_name(pdf_font.get("/BaseFont", font_name))
    published = standard_14_widths(canonical)
    if published is None:
        return False
    code_points = _simple_font_code_points(pdf_font)
    codes = sorted(code_points)
    if not codes:
        return False
    first_char, last_char = codes[0], codes[-1]
    widths = [
        published.get(code_points.get(code, -1), 0)
        for code in range(first_char, last_char + 1)
    ]
    symbolic = canonical in {"symbol", "zapfdingbats"}
    descriptor = _descriptor_for_program(pdf, font_name, font_path, symbolic)
    pdf_font["/FontDescriptor"] = descriptor
    pdf_font["/Subtype"] = pikepdf.Name("/TrueType")
    pdf_font["/FirstChar"] = first_char
    pdf_font["/LastChar"] = last_char
    pdf_font["/Widths"] = pikepdf.Array(widths)
    encoding = pdf_font.get("/Encoding")
    if isinstance(encoding, pikepdf.Dictionary) and "/BaseEncoding" not in encoding:
        # The widths just written are WinAnsi-based, so say so explicitly
        # rather than leaving the base to the viewer's built-in guess.
        encoding["/BaseEncoding"] = pikepdf.Name("/WinAnsiEncoding")
    elif encoding is None:
        pdf_font["/Encoding"] = pikepdf.Name("/WinAnsiEncoding")
    return True


def suggest_system_font(font_name: str) -> Optional[Dict[str, str]]:
    """Return fontconfig's closest installed alternative without applying it."""
    executable = shutil.which("fc-match")
    if not executable:
        return None
    try:
        result = subprocess.run(  # nosec B603
            [
                executable,
                "-f",
                "%{file}|%{postscriptname}|%{family}|%{style}\n",
                font_name,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        path, postscript_name, family, style = result.stdout.splitlines()[0].split(
            "|", 3
        )
        return {
            "path": path,
            "postscript_name": postscript_name,
            "family": family,
            "style": style,
        }
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def _embedding_unresolved_reason(
    font_name: str, canonical: str, inventory: List[Dict[str, Any]]
) -> str:
    """Say which gate an installed font failed, not just that it failed.

    "No match" covers four very different situations, and the reviewer can only
    act on the right one: install the font, replace a restricted copy, convert
    an OpenType file, or accept that the installed copy is a different face.
    """
    named = [
        record
        for record in inventory
        if canonical in set(record.get("canonical_names") or ())
    ]
    if not named:
        return (
            f"No installed font is named {font_name}. Install the exact font "
            "with the Dashboard font manager, then run this check again."
        )
    blocked = [record for record in named if not record.get("embeddable")]
    if len(blocked) == len(named):
        return (
            f"{font_name} is installed, but its license flags forbid "
            "embedding. Install a copy that permits embedding."
        )
    return (
        f"{font_name} is installed and embeddable, but its widths do not match "
        "the metrics this PDF expects, so embedding it would reflow the text. "
        "The installed copy is a different face with the same name."
    )


def _winansi_to_unicode_cmap(pdf_font: Any) -> Optional[bytes]:
    """Build a one-byte ToUnicode map for a simple WinAnsi font."""
    encoding = pdf_font.get("/Encoding") if hasattr(pdf_font, "get") else None
    if str(encoding) != "/WinAnsiEncoding":
        return None
    pairs: List[Tuple[int, str]] = []
    for code in range(256):
        try:
            character = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            continue
        if character and (ord(character) >= 32 or character in "\t\r\n"):
            pairs.append((code, character.encode("utf-16-be").hex().upper()))
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /ALDashboard-WinAnsi def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
    ]
    for start in range(0, len(pairs), 100):
        batch = pairs[start : start + 100]
        lines.append(f"{len(batch)} beginbfchar")
        lines.extend(f"<{code:02X}> <{unicode_hex}>" for code, unicode_hex in batch)
        lines.append("endbfchar")
    lines.extend(
        ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    )
    return ("\n".join(lines) + "\n").encode("ascii")

def _font_program_bytes(pdf_font: Any) -> Tuple[Optional[bytes], str]:
    """Return the embedded font program and which FontFile key supplied it."""
    descriptor = _font_descriptor(pdf_font)
    if descriptor is None:
        return None, ""
    for key in ("/FontFile2", "/FontFile3", "/FontFile"):
        stream = descriptor.get(key)
        if stream is None:
            continue
        try:
            return bytes(stream.read_bytes()), key.lstrip("/")
        except Exception:
            continue
    return None, ""


def _load_glyph_source(program: Optional[bytes]) -> Optional[Any]:
    """Open an embedded font program with fontTools, or give up quietly."""
    if not program:
        return None
    try:
        import io

        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

        return TTFont(io.BytesIO(program), lazy=True, fontNumber=0)
    except Exception:
        return None


def _glyph_outline(font_source: Any, glyph_id: int) -> Optional[Dict[str, Any]]:
    """Extract one glyph outline as an SVG path so a human can look at it.

    The outline is read from the program embedded in the PDF, so the reviewer
    sees the glyph the document actually draws rather than a lookalike from an
    installed font.
    """
    if font_source is None:
        return None
    try:
        from fontTools.pens.svgPathPen import (  # type: ignore[import-untyped]
            SVGPathPen,
        )

        order = font_source.getGlyphOrder()
        if glyph_id < 0 or glyph_id >= len(order):
            return None
        glyph_set = font_source.getGlyphSet()
        name = order[glyph_id]
        pen = SVGPathPen(glyph_set)
        glyph_set[name].draw(pen)
        path = pen.getCommands()
        if not path:
            return None
        from fontTools.pens.boundsPen import (  # type: ignore[import-untyped]
            BoundsPen,
        )

        bounds_pen = BoundsPen(glyph_set)
        glyph_set[name].draw(bounds_pen)
        units_per_em = int(getattr(font_source["head"], "unitsPerEm", 1000) or 1000)
        bounds = bounds_pen.bounds or (0, 0, units_per_em, units_per_em)
        return {
            "path": path,
            "unitsPerEm": units_per_em,
            "glyphName": str(name),
            "bbox": [float(value) for value in bounds],
        }
    except Exception:
        return None


def _embedded_char_code(font_source: Any, glyph_id: int) -> Optional[int]:
    """Recover a glyph's own character code from the embedded program's cmap.

    This is the most trustworthy source available, because it needs nothing
    outside the PDF. Subset programs frequently drop the cmap table, in which
    case there is nothing to recover and the caller falls back to an installed
    font.
    """
    if font_source is None:
        return None
    try:
        if "cmap" not in font_source:
            return None
        order = font_source.getGlyphOrder()
        if glyph_id < 0 or glyph_id >= len(order):
            return None
        name = order[glyph_id]
        for table in font_source["cmap"].tables:
            for code, glyph_name in table.cmap.items():
                if glyph_name == name:
                    return int(code) & 0xFF
    except Exception:
        return None
    return None


def _installed_symbol_codes(font_name: str, glyph_count: int) -> Dict[int, int]:
    """Map glyph ids to character codes using a matching installed font.

    Subsetters normally preserve glyph numbering, so a subset that dropped its
    cmap can often be read against the shipping font. Agreement on glyph count
    is the only evidence that the alignment holds, so this is recorded as a
    weaker source than the embedded cmap, and the reviewer still confirms every
    character. Returns an empty map when no installed font matches.
    """
    family = canonical_symbol_family(font_name)
    if not family or glyph_count <= 0:
        return {}
    for candidate in _system_truetype_fonts():
        if family not in set(candidate.get("canonical_names") or ()):
            continue
        try:
            from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

            installed = TTFont(candidate["path"], lazy=True, fontNumber=0)
            try:
                if int(installed["maxp"].numGlyphs) != glyph_count:
                    continue
                order = installed.getGlyphOrder()
                positions = {name: gid for gid, name in enumerate(order)}
                codes: Dict[int, int] = {}
                for table in installed["cmap"].tables:
                    for code, glyph_name in table.cmap.items():
                        gid = positions.get(glyph_name)
                        if gid is not None:
                            codes.setdefault(gid, int(code) & 0xFF)
                if codes:
                    return codes
            finally:
                installed.close()
        except Exception:
            continue
    return {}


def _resource_font_names(resources: Any) -> Dict[str, Any]:
    """Map the /Font resource names visible in one resource dictionary."""
    fonts = resources.get("/Font") if resources is not None else None
    if not fonts:
        return {}
    return {str(name): font for name, font in fonts.items()}


def _encoding_label(pdf_font: Any) -> str:
    """Describe a font's /Encoding without dumping an inline dictionary."""
    import pikepdf

    encoding = pdf_font.get("/Encoding") if hasattr(pdf_font, "get") else None
    if encoding is None:
        return ""
    if isinstance(encoding, pikepdf.Name):
        return _safe_pdf_string(encoding).lstrip("/")
    base = encoding.get("/BaseEncoding") if hasattr(encoding, "get") else None
    label = _safe_pdf_string(base).lstrip("/") if base is not None else "custom"
    has_differences = hasattr(encoding, "get") and encoding.get("/Differences")
    return f"{label} with /Differences" if has_differences else label


def _is_two_byte_font(pdf_font: Any) -> bool:
    """Report whether show-text strings for this font use two-byte codes."""
    if not hasattr(pdf_font, "get"):
        return False
    if _safe_pdf_string(pdf_font.get("/Subtype", "")).lstrip("/") != "Type0":
        return False
    encoding = _safe_pdf_string(pdf_font.get("/Encoding", "")).lstrip("/")
    # Identity-H/V and the standard CJK CMaps are all two-byte for our purposes;
    # a mixed-width CMap would need its codespace ranges parsed, so treat an
    # embedded CMap stream as unknown and leave it alone.
    return encoding.endswith(("-H", "-V"))


def _codes_from_operands(operands: Any, two_byte: bool) -> List[int]:
    """Decode the character codes shown by one text-showing operator."""
    import pikepdf

    raw: List[bytes] = []
    for operand in operands:
        if isinstance(operand, pikepdf.String):
            raw.append(bytes(operand))
        elif isinstance(operand, pikepdf.Array):
            raw.extend(
                bytes(item) for item in operand if isinstance(item, pikepdf.String)
            )
    codes: List[int] = []
    for chunk in raw:
        if two_byte:
            codes.extend(
                int.from_bytes(chunk[index : index + 2], "big")
                for index in range(0, len(chunk) - 1, 2)
            )
        else:
            codes.extend(chunk)
    return codes


def _font_code_usage(pdf: Any) -> Dict[str, Dict[str, Any]]:
    """Count the codes each page font actually shows, keyed by resource path.

    Only codes that appear in page content matter for review: a subset font can
    carry hundreds of glyphs while the document draws two of them.
    """
    import pikepdf

    usage: Dict[str, Dict[str, Any]] = {}
    for page_index, page in enumerate(pdf.pages):
        resources = page.get("/Resources") if hasattr(page, "get") else None
        containers: List[Tuple[str, Any, Any]] = [
            (f"p{page_index + 1}", resources, page)
        ]
        for path, obj in _walk_resource_xobjects(resources):
            if _safe_pdf_string(obj.get("/Subtype", "")) == "/Form":
                containers.append(
                    (f"p{page_index + 1}:{path}", obj.get("/Resources"), obj)
                )
        for annot_index, annot in enumerate(
            cast(Iterable[Any], page.get("/Annots") or [])
        ):
            appearances = annot.get("/AP") if hasattr(annot, "get") else None
            normal = appearances.get("/N") if appearances is not None else None
            if normal is None:
                continue
            streams = (
                [normal]
                if hasattr(normal, "read_bytes")
                else list(normal.values()) if hasattr(normal, "values") else []
            )
            for state_index, stream in enumerate(streams):
                containers.append(
                    (
                        f"p{page_index + 1}:annot{annot_index}:ap{state_index}",
                        stream.get("/Resources"),
                        stream,
                    )
                )
        for prefix, container_resources, container in containers:
            names = _resource_font_names(container_resources)
            if not names:
                continue
            try:
                instructions = list(pikepdf.parse_content_stream(container))
            except Exception:
                continue
            current = ""
            for instruction in instructions:
                operator = str(instruction.operator)
                if operator == "Tf" and instruction.operands:
                    current = str(instruction.operands[0])
                    continue
                if operator not in {"Tj", "TJ", "'", '"'} or current not in names:
                    continue
                font = names[current]
                resource_path = f"{prefix}/{current.lstrip('/')}"
                identity = _pdf_object_identity(font, resource_path)
                record = usage.setdefault(
                    identity,
                    {
                        "resource": resource_path,
                        "counts": {},
                        "pages": set(),
                        "runs": 0,
                    },
                )
                record["pages"].add(page_index)
                record["runs"] += 1
                for code in _codes_from_operands(
                    instruction.operands, _is_two_byte_font(font)
                ):
                    record["counts"][code] = record["counts"].get(code, 0) + 1
    return usage


def _glyph_review_entry(
    font_source: Any,
    font_name: str,
    code: int,
    count: int,
    installed_codes: Mapping[int, int],
) -> Dict[str, Any]:
    """Describe one unmapped code: what it draws and what we think it means."""
    outline = _glyph_outline(font_source, code)
    char_code = _embedded_char_code(font_source, code)
    code_source = "embedded-cmap" if char_code is not None else ""
    if char_code is None:
        char_code = installed_codes.get(code)
        code_source = "installed-font" if char_code is not None else ""
    proposal = (
        propose_character(font_name, char_code) if char_code is not None else None
    )
    return {
        "code": code,
        "codeLabel": f"{code} (0x{code:02X})",
        "count": count,
        "outline": outline,
        "charCode": char_code,
        "charCodeHex": f"0x{char_code:02X}" if char_code is not None else "",
        "charCodeSource": code_source,
        "proposal": (
            {
                "character": proposal.character,
                "codepoint": proposal.codepoint_label,
                "unicodeName": proposal.unicode_name,
                "evidence": proposal.evidence,
            }
            if proposal is not None
            else None
        ),
    }


GLYPH_REVIEW_LIMIT = 512


def _acroform_appearance_fonts(pdf: Any) -> Dict[str, int]:
    """Identify fonts that form fields will draw with once they are filled.

    A widget's appearance stream for an empty field shows an empty string, so
    the font looks unused while the form is blank. It is not: the viewer
    regenerates that stream from the field's /DA the moment someone types, and
    a missing font resource breaks filling. Returns each such font's object
    identity mapped to the number of fields that name it.
    """
    counts: Dict[str, int] = {}
    root = pdf.Root.get("/AcroForm") if hasattr(pdf.Root, "get") else None
    if root is None:
        return counts
    resources = root.get("/DR") if hasattr(root, "get") else None
    fonts = resources.get("/Font") if resources is not None else None
    if not fonts:
        return counts
    identities = {
        str(name).lstrip("/"): _pdf_object_identity(font, f"AcroForm/DR/{name}")
        for name, font in fonts.items()
    }

    def default_appearance_name(source: Any) -> str:
        appearance = source.get("/DA") if hasattr(source, "get") else None
        if appearance is None:
            return ""
        match = re.search(rb"/([^\s/]+)\s+[\d.]+\s+Tf", bytes(appearance))
        return match.group(1).decode("latin-1") if match else ""

    named = [default_appearance_name(root)]
    for page in pdf.pages:
        for annot in cast(Iterable[Any], page.get("/Annots") or []):
            if not hasattr(annot, "get"):
                continue
            named.append(default_appearance_name(annot))
            parent = annot.get("/Parent")
            if parent is not None:
                named.append(default_appearance_name(parent))
    for name in named:
        identity = identities.get(name)
        if identity:
            counts[identity] = counts.get(identity, 0) + 1
    return counts


def collect_symbolic_font_review(input_pdf_path: str) -> Dict[str, Any]:
    """Describe every font lacking a Unicode map so a human can decide about it.

    For each such font this reports the codes the document actually draws, the
    outline of each one taken from the embedded program, and a curated
    character proposal where one is established. Nothing here changes the file;
    the caller reviews the result and sends decisions back to
    :func:`apply_unicode_map_decisions`.
    """
    try:
        import pikepdf

        with pikepdf.open(input_pdf_path) as pdf:
            usage = _font_code_usage(pdf)
            form_fonts = _acroform_appearance_fonts(pdf)
            fonts: List[Dict[str, Any]] = []
            for resource, font in _iter_pdf_fonts(pdf):
                if "/ToUnicode" in font:
                    continue
                font_name = _safe_pdf_string(font.get("/BaseFont", resource)).lstrip(
                    "/"
                )
                identity = _pdf_object_identity(font, resource)
                record = usage.get(identity) or {}
                counts: Dict[int, int] = record.get("counts") or {}
                program, program_kind = _font_program_bytes(font)
                font_source = _load_glyph_source(program)
                glyph_count = 0
                if font_source is not None:
                    try:
                        glyph_count = int(font_source["maxp"].numGlyphs)
                    except Exception:
                        glyph_count = 0
                needs_installed = font_source is not None and "cmap" not in font_source
                installed_codes = (
                    _installed_symbol_codes(font_name, glyph_count)
                    if needs_installed
                    else {}
                )
                ordered = sorted(counts.items(), key=lambda item: item[0])
                glyphs = [
                    _glyph_review_entry(
                        font_source, font_name, code, count, installed_codes
                    )
                    for code, count in ordered[:GLYPH_REVIEW_LIMIT]
                ]
                proposed = sum(1 for glyph in glyphs if glyph["proposal"] is not None)
                fonts.append(
                    {
                        "resource": resource,
                        "font": font_name,
                        "family": canonical_symbol_family(font_name),
                        "subtype": _safe_pdf_string(font.get("/Subtype", "")).lstrip(
                            "/"
                        ),
                        "encoding": _encoding_label(font),
                        "symbolic": is_symbolic_family(font_name),
                        "embedded": bool(program),
                        "programKind": program_kind,
                        "hasOutlines": any(
                            glyph["outline"] is not None for glyph in glyphs
                        ),
                        "pages": sorted(record.get("pages") or []),
                        "runs": int(record.get("runs") or 0),
                        "formFieldCount": form_fonts.get(identity, 0),
                        "glyphCount": len(counts),
                        "truncated": len(counts) > GLYPH_REVIEW_LIMIT,
                        "proposedCount": proposed,
                        "glyphs": glyphs,
                        "winAnsiEligible": _winansi_to_unicode_cmap(font) is not None,
                    }
                )
        return {
            "action": "unicode_review",
            "fonts": fonts,
            "review_required": bool(fonts),
        }
    except PDFAccessibilityError:
        raise
    except Exception as exc:
        raise PDFAccessibilityError(f"Font review failed: {exc}") from exc


def _confirmed_unicode_cmap(
    mappings: Mapping[int, str], two_byte: bool
) -> Optional[bytes]:
    """Build a ToUnicode CMap from codes a human explicitly confirmed."""
    pairs = sorted(
        (code, text)
        for code, text in mappings.items()
        if text and 0 <= code <= (0xFFFF if two_byte else 0xFF)
    )
    if not pairs:
        return None
    width = 4 if two_byte else 2
    low = "0" * width
    high = "F" * width
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /ALDashboard-Reviewed def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        f"<{low}> <{high}>",
        "endcodespacerange",
    ]
    for start in range(0, len(pairs), 100):
        batch = pairs[start : start + 100]
        lines.append(f"{len(batch)} beginbfchar")
        lines.extend(
            f"<{code:0{width}X}> <{text.encode('utf-16-be').hex().upper()}>"
            for code, text in batch
        )
        lines.append("endbfchar")
    lines.extend(
        ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def _mark_font_runs_as_artifact(pdf: Any, targets: Mapping[str, str]) -> Dict[str, Any]:
    """Wrap every run drawn in a target font with /Artifact BMC ... EMC.

    Decorative glyphs -- a checkbox drawn in a dingbat font, a rule, a bullet --
    carry no text meaning, and an artifact is the accessibility-correct home for
    them: assistive technology skips them entirely, so no Unicode map is needed.
    Runs already inside a tagged /MCID sequence are left untouched and reported,
    because retagging them as artifacts would orphan entries in an existing
    structure tree.
    """
    import pikepdf

    wrapped: Dict[str, int] = {}
    skipped_tagged: Dict[str, int] = {}
    for page_index, page in enumerate(pdf.pages):
        resources = page.get("/Resources") if hasattr(page, "get") else None
        page_prefix = f"p{page_index + 1}"
        containers: List[Tuple[str, Any, Any]] = [(page_prefix, resources, page)]
        containers.extend(
            (f"{page_prefix}:{path}", obj.get("/Resources"), obj)
            for path, obj in _walk_resource_xobjects(resources)
            if _safe_pdf_string(obj.get("/Subtype", "")) == "/Form"
        )
        for prefix, container_resources, container in containers:
            names = _resource_font_names(container_resources)
            if not names:
                continue
            identities = {
                name: _pdf_object_identity(font, f"{prefix}/{name.lstrip('/')}")
                for name, font in names.items()
            }
            if not any(identity in targets for identity in identities.values()):
                continue
            try:
                instructions = list(pikepdf.parse_content_stream(container))
            except Exception:
                continue
            rewritten: List[Any] = []
            current = ""
            tagged_depth = 0
            changed = False
            for instruction in instructions:
                operator = str(instruction.operator)
                operands = list(instruction.operands)
                if operator == "Tf" and operands:
                    current = str(operands[0])
                elif operator == "BDC":
                    tagged_depth += 1
                elif operator == "EMC" and tagged_depth:
                    tagged_depth -= 1
                identity = identities.get(current, "")
                resource_key = targets.get(identity, "")
                if operator in {"Tj", "TJ", "'", '"'} and resource_key:
                    if tagged_depth:
                        skipped_tagged[resource_key] = (
                            skipped_tagged.get(resource_key, 0) + 1
                        )
                    else:
                        rewritten.append(
                            pikepdf.ContentStreamInstruction(
                                [pikepdf.Name("/Artifact")], pikepdf.Operator("BMC")
                            )
                        )
                        rewritten.append(instruction)
                        rewritten.append(
                            pikepdf.ContentStreamInstruction(
                                [], pikepdf.Operator("EMC")
                            )
                        )
                        wrapped[resource_key] = wrapped.get(resource_key, 0) + 1
                        changed = True
                        continue
                rewritten.append(instruction)
            if not changed:
                continue
            data = pikepdf.unparse_content_stream(rewritten)
            if container is page:
                page["/Contents"] = pdf.make_stream(data)
            else:
                container.write(data)
    return {"wrapped": wrapped, "skipped_tagged": skipped_tagged}


def apply_unicode_map_decisions(
    input_pdf_path: str,
    output_pdf_path: str,
    decisions: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Apply reviewed Unicode mappings and artifact marks, font by font.

    Every character written here was confirmed by a person against the glyph
    outline taken from the PDF. Nothing is inferred at this stage: a code with
    no confirmed character is left unmapped rather than guessed.
    """
    if input_pdf_path != output_pdf_path:
        shutil.copyfile(input_pdf_path, output_pdf_path)
    decision_list = list(decisions)
    if not decision_list or len(decision_list) > 100:
        raise PDFAccessibilityError("Provide between 1 and 100 font decisions.")
    try:
        import pikepdf

        mapped: List[Dict[str, Any]] = []
        artifacts: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        with pikepdf.open(output_pdf_path, allow_overwriting_input=True) as pdf:
            fonts_by_resource = {
                resource: font for resource, font in _iter_pdf_fonts(pdf)
            }
            artifact_targets: Dict[str, str] = {}
            for decision in decision_list:
                resource = str(decision.get("resource") or "")
                action = str(decision.get("action") or "")
                font = fonts_by_resource.get(resource)
                if font is None:
                    raise PDFAccessibilityError(
                        f"Font resource {resource or '(missing)'} is not in this PDF."
                    )
                if action == "artifact":
                    artifact_targets[_pdf_object_identity(font, resource)] = resource
                elif action == "map":
                    raw = decision.get("mappings") or {}
                    if not isinstance(raw, Mapping):
                        raise PDFAccessibilityError(
                            "Unicode mappings must be a code-to-character object."
                        )
                    mappings: Dict[int, str] = {}
                    for code, text in raw.items():
                        try:
                            code_int = int(code)
                        except (TypeError, ValueError) as exc:
                            raise PDFAccessibilityError(
                                f"Glyph code {code!r} is not a number."
                            ) from exc
                        value = str(text or "")
                        if len(value) > 16:
                            raise PDFAccessibilityError(
                                "Each glyph maps to at most 16 characters."
                            )
                        if value:
                            mappings[code_int] = value
                    cmap = _confirmed_unicode_cmap(mappings, _is_two_byte_font(font))
                    if cmap is None:
                        skipped.append(
                            {
                                "resource": resource,
                                "reason": "No characters were confirmed for this font.",
                            }
                        )
                        continue
                    font["/ToUnicode"] = pdf.make_stream(cmap)
                    mapped.append(
                        {
                            "resource": resource,
                            "font": _safe_pdf_string(
                                font.get("/BaseFont", resource)
                            ).lstrip("/"),
                            "characters": len(mappings),
                        }
                    )
                elif action == "skip":
                    skipped.append(
                        {"resource": resource, "reason": "Left unchanged by reviewer."}
                    )
                else:
                    raise PDFAccessibilityError(
                        "Font decision action must be map, artifact, or skip."
                    )
            artifact_result = (
                _mark_font_runs_as_artifact(pdf, artifact_targets)
                if artifact_targets
                else {"wrapped": {}, "skipped_tagged": {}}
            )
            for resource in artifact_targets.values():
                artifacts.append(
                    {
                        "resource": resource,
                        "runs_marked": artifact_result["wrapped"].get(resource, 0),
                        "runs_left_tagged": artifact_result["skipped_tagged"].get(
                            resource, 0
                        ),
                    }
                )
            pdf.save(output_pdf_path)
        return {
            "action": "unicode_map",
            "fonts_mapped": mapped,
            "fonts_artifacted": artifacts,
            "fonts_skipped": skipped,
            "review_required": bool(mapped or artifacts),
            "warning": (
                "Only characters confirmed against the rendered glyph were written. "
                "Validate the result with veraPDF and check extracted text."
            ),
        }
    except PDFAccessibilityError:
        raise
    except Exception as exc:
        raise PDFAccessibilityError(f"Unicode map update failed: {exc}") from exc


def _unicode_unresolved_reason(pdf_font: Any, font_name: str) -> str:
    """Explain why a deterministic Unicode map is impossible for this font.

    The old wording named the code path ("not a simple WinAnsi font") rather
    than the obstacle, which sent people looking for a missing font install
    when the real problem is that the glyphs have no Unicode meaning to find.
    """
    subtype = _safe_pdf_string(pdf_font.get("/Subtype", "")).lstrip("/")
    encoding = _encoding_label(pdf_font)
    if is_symbolic_family(font_name):
        return (
            f"{font_name} is a symbol font: its glyphs live in the private use "
            "area and carry no Unicode meaning, so no map can be derived from "
            "the font or the PDF. Confirm each glyph by sight, or mark the "
            "font as decorative."
        )
    if subtype == "Type0":
        return (
            f"{font_name} is a composite ({encoding or 'CID'}) font, so its "
            "codes are glyph ids rather than characters. Nothing in the file "
            "records what they mean."
        )
    return (
        f"{font_name} uses the {encoding or 'built-in'} encoding, which this "
        "deterministic tool cannot map without guessing."
    )


SUBSTITUTION_WIDTH_TOLERANCE = 2.0


def _embeddable_program(font_path: str) -> Tuple[Optional[bytes], str, str]:
    """Read a font file as the program bytes a PDF font dictionary can hold.

    TrueType programs go into /FontFile2 whole. A plain CFF is carried as the
    bare CFF table in /FontFile3 with subtype /Type1C, which is understood far
    more widely than wrapping the entire OpenType file.
    """
    try:
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

        font = TTFont(font_path, lazy=True)
        try:
            program_format = _font_program_format(font)
        finally:
            font.close()
        if program_format == "truetype":
            return Path(font_path).read_bytes(), "/FontFile2", ""
        if program_format == "cff":
            import io

            from fontTools.ttLib.sfnt import SFNTReader  # type: ignore[import-untyped]

            data = Path(font_path).read_bytes()
            reader = SFNTReader(io.BytesIO(data))
            entry = reader.tables["CFF "]
            return (
                data[entry.offset : entry.offset + entry.length],
                "/FontFile3",
                "/Type1C",
            )
    except Exception:
        return None, "", ""
    return None, "", ""


def _font_style_flags(pdf_font: Any) -> Tuple[bool, bool]:
    """Infer whether a PDF font is meant to be bold and/or italic."""
    name = _safe_pdf_string(pdf_font.get("/BaseFont", "")).lstrip("/").casefold()
    descriptor = _font_descriptor(pdf_font)
    flags = int(descriptor.get("/Flags", 0)) if descriptor is not None else 0
    angle = float(descriptor.get("/ItalicAngle", 0)) if descriptor is not None else 0.0
    bold = "bold" in name or bool(flags & (1 << 18))
    italic = (
        "italic" in name
        or "oblique" in name
        or bool(flags & (1 << 6))
        or abs(angle) > 0.5
    )
    return bold, italic


def find_metric_compatible_fonts(
    pdf_font: Any, inventory: List[Dict[str, Any]], limit: int = 5
) -> List[Dict[str, Any]]:
    """Rank installed fonts that reproduce the widths this font expects.

    Unlike :func:`find_exact_system_font` this deliberately ignores the font
    name: the point of a substitution is to use a different face whose metrics
    line up, so that text keeps its exact position and line breaks. The caller
    still has to choose one.
    """
    expected, source = _expected_font_widths(pdf_font)
    if not expected:
        return []
    # An oblique face has the same widths as its upright, so metric matching
    # alone cannot tell them apart. Compare the intended style separately.
    wanted = _font_style_flags(pdf_font)
    scored: List[Dict[str, Any]] = []
    for record in inventory:
        if not record.get("embeddable"):
            continue
        score = _font_width_match_score(pdf_font, record["path"])
        if score is None or score > SUBSTITUTION_WIDTH_TOLERANCE:
            continue
        style = (bool(record.get("bold")), bool(record.get("italic")))
        scored.append(
            {
                "path": record["path"],
                "postscript_name": record.get("postscript_name") or "",
                "family": (record.get("names") or [""])[0],
                "format": record.get("format") or "",
                "bold": style[0],
                "italic": style[1],
                "style_matches": style == wanted,
                "width_delta": round(float(score), 3),
                "reference": source,
            }
        )
    scored.sort(
        key=lambda item: (
            not item["style_matches"],
            item["width_delta"],
            item["postscript_name"],
        )
    )
    return scored[:limit]


def _substitute_font_program(
    pdf: Any, pdf_font: Any, font_name: str, candidate: Mapping[str, Any]
) -> bool:
    """Point a font dictionary at a different, metric-compatible program.

    The resource name is left alone so every /DA string and content stream
    that names this font keeps working. /BaseFont becomes the substitute's
    real PostScript name, because claiming to be a font that is not embedded
    here is what caused the original problem.
    """
    import pikepdf

    program, file_key, file_subtype = _embeddable_program(str(candidate["path"]))
    if not program:
        return False
    canonical = _canonical_font_name(pdf_font.get("/BaseFont", font_name))
    published = standard_14_widths(canonical)
    if "/Widths" not in pdf_font and published is not None:
        code_points = _simple_font_code_points(pdf_font)
        codes = sorted(code_points)
        if codes:
            first_char, last_char = codes[0], codes[-1]
            pdf_font["/FirstChar"] = first_char
            pdf_font["/LastChar"] = last_char
            pdf_font["/Widths"] = pikepdf.Array(
                [
                    published.get(code_points.get(code, -1), 0)
                    for code in range(first_char, last_char + 1)
                ]
            )
    postscript_name = str(candidate.get("postscript_name") or "").strip() or font_name
    symbolic = canonical in {"symbol", "zapfdingbats"}
    descriptor = _descriptor_for_program(
        pdf, postscript_name, str(candidate["path"]), symbolic, file_key, file_subtype
    )
    pdf_font["/FontDescriptor"] = descriptor
    pdf_font["/BaseFont"] = pikepdf.Name(f"/{postscript_name}")
    if file_key == "/FontFile2":
        pdf_font["/Subtype"] = pikepdf.Name("/TrueType")
    encoding = pdf_font.get("/Encoding")
    if isinstance(encoding, pikepdf.Dictionary) and "/BaseEncoding" not in encoding:
        encoding["/BaseEncoding"] = pikepdf.Name("/WinAnsiEncoding")
    elif encoding is None:
        pdf_font["/Encoding"] = pikepdf.Name("/WinAnsiEncoding")
    return True


def collect_font_substitution_options(input_pdf_path: str) -> Dict[str, Any]:
    """List metric-compatible replacements for each font lacking a program.

    Nothing is changed. Substitution swaps in a different typeface, so the
    choice belongs to a person who can look at the result.
    """
    try:
        import pikepdf

        with pikepdf.open(input_pdf_path) as pdf:
            inventory = _system_embeddable_fonts()
            form_fonts = _acroform_appearance_fonts(pdf)
            fonts: List[Dict[str, Any]] = []
            for resource, font in _iter_pdf_fonts(pdf):
                descriptor = _font_descriptor(font)
                if descriptor is not None and any(
                    key in descriptor
                    for key in ("/FontFile", "/FontFile2", "/FontFile3")
                ):
                    continue
                font_name = _safe_pdf_string(font.get("/BaseFont", resource)).lstrip(
                    "/"
                )
                canonical = _canonical_font_name(font_name)
                exact = find_exact_system_font(font, inventory)
                candidates = find_metric_compatible_fonts(font, inventory)
                _expected, reference = _expected_font_widths(font)
                fonts.append(
                    {
                        "resource": resource,
                        "font": font_name,
                        "subtype": _safe_pdf_string(font.get("/Subtype", "")).lstrip(
                            "/"
                        ),
                        "standard14": is_standard_14(canonical),
                        "widthReference": reference,
                        "formFieldCount": form_fonts.get(
                            _pdf_object_identity(font, resource), 0
                        ),
                        "exactMatch": (
                            {
                                "path": exact["path"],
                                "postscript_name": exact.get("postscript_name") or "",
                            }
                            if exact
                            else None
                        ),
                        "candidates": candidates,
                    }
                )
        return {
            "action": "substitution_options",
            "fonts": fonts,
            "review_required": bool(fonts),
        }
    except PDFAccessibilityError:
        raise
    except Exception as exc:
        raise PDFAccessibilityError(f"Substitution lookup failed: {exc}") from exc


def substitute_fonts(
    input_pdf_path: str,
    output_pdf_path: str,
    decisions: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Replace chosen fonts with metric-compatible embedded substitutes."""
    if input_pdf_path != output_pdf_path:
        shutil.copyfile(input_pdf_path, output_pdf_path)
    decision_list = list(decisions)
    if not decision_list or len(decision_list) > 100:
        raise PDFAccessibilityError("Provide between 1 and 100 font decisions.")
    try:
        import pikepdf

        substituted: List[Dict[str, Any]] = []
        with pikepdf.open(output_pdf_path, allow_overwriting_input=True) as pdf:
            inventory = {
                record["path"]: record for record in _system_embeddable_fonts()
            }
            fonts_by_resource = {
                resource: font for resource, font in _iter_pdf_fonts(pdf)
            }
            for decision in decision_list:
                resource = str(decision.get("resource") or "")
                path = str(decision.get("path") or "")
                font = fonts_by_resource.get(resource)
                if font is None:
                    raise PDFAccessibilityError(
                        f"Font resource {resource or '(missing)'} is not in this PDF."
                    )
                record = inventory.get(path)
                if record is None:
                    raise PDFAccessibilityError(
                        "Choose a substitute from the offered list."
                    )
                if not record.get("embeddable"):
                    raise PDFAccessibilityError(
                        f"{record.get('postscript_name') or path} may not be embedded."
                    )
                score = _font_width_match_score(font, path)
                if score is None or score > SUBSTITUTION_WIDTH_TOLERANCE:
                    raise PDFAccessibilityError(
                        "That substitute's widths do not match this font's metrics."
                    )
                font_name = _safe_pdf_string(font.get("/BaseFont", resource)).lstrip(
                    "/"
                )
                if not _substitute_font_program(pdf, font, font_name, record):
                    raise PDFAccessibilityError(
                        f"{record.get('postscript_name') or path} could not be embedded."
                    )
                substituted.append(
                    {
                        "resource": resource,
                        "replaced": font_name,
                        "substitute": record.get("postscript_name") or path,
                        "source": path,
                        "width_delta": round(float(score), 3),
                    }
                )
            pdf.save(output_pdf_path)
        return {
            "action": "substitute_fonts",
            "fonts_substituted": substituted,
            "review_required": bool(substituted),
            "warning": (
                "A different typeface is now embedded. Widths match, so text keeps "
                "its position and line breaks, but letterforms differ. Compare the "
                "result visually and validate with veraPDF."
            ),
        }
    except PDFAccessibilityError:
        raise
    except Exception as exc:
        raise PDFAccessibilityError(f"Font substitution failed: {exc}") from exc


def _extract_annotation_records(pdf: Any) -> Tuple[List[Dict[str, Any]], List[int]]:
    records: List[Dict[str, Any]] = []
    pages_without_tabs: List[int] = []
    for page_index, page in enumerate(pdf.pages):
        annots = page.get("/Annots") if hasattr(page, "get") else None
        if not annots:
            continue
        has_widget = False
        for index, annot in enumerate(cast(Iterable[Any], annots)):
            try:
                subtype = _safe_pdf_string(annot.get("/Subtype", ""))
                has_widget = has_widget or subtype == "/Widget"
                if subtype == "/Widget":
                    continue
                contents = _safe_pdf_string(annot.get("/Contents", "")).strip()
                records.append(
                    {
                        "pageIndex": page_index,
                        "index": index,
                        "subtype": subtype.lstrip("/") or "Annotation",
                        "contents": contents,
                        "hasDescription": bool(contents),
                    }
                )
            except Exception:
                continue
        if has_widget and _safe_pdf_string(page.get("/Tabs", "")) != "/S":
            pages_without_tabs.append(page_index)
    return records, pages_without_tabs


def _page_has_marked_content(page: Any) -> bool:
    """Return whether a page or nested Form stream contains marked content."""
    streams: List[Any] = []
    contents = page.get("/Contents") if hasattr(page, "get") else None
    if (
        contents is not None
        and not hasattr(contents, "read_bytes")
        and hasattr(contents, "__iter__")
    ):
        streams.extend(contents)
    elif contents is not None:
        streams.append(contents)
    resources = page.get("/Resources") if hasattr(page, "get") else None
    streams.extend(
        obj
        for _path, obj in _walk_resource_xobjects(resources)
        if _safe_pdf_string(obj.get("/Subtype", "")) == "/Form"
    )
    for stream in streams:
        try:
            data = bytes(stream.read_bytes())
            if re.search(
                rb"(?:/Artifact\s+BMC|/\w+\s*<<[^>]*?/MCID\s+\d+[^>]*?>>\s*BDC)",
                data,
                re.DOTALL,
            ):
                return True
        except Exception:
            continue
    return False


def _viewer_pref_display_title(root: Any) -> bool:
    prefs = root.get("/ViewerPreferences") if root is not None else None
    return bool(prefs and prefs.get("/DisplayDocTitle", False))


def _mark_info_marked(root: Any) -> bool:
    mark_info = root.get("/MarkInfo") if root is not None else None
    return bool(mark_info and mark_info.get("/Marked", False))


def _issue(
    issue_id: str,
    rule: str,
    title: str,
    count: int,
    remediation: str,
    *,
    severity: str = "fail",
    description: str = "",
) -> Dict[str, Any]:
    return {
        "id": issue_id,
        "rule": rule,
        "title": title,
        "count": int(count),
        "severity": severity,
        "status": "fail" if count else "pass",
        "remediation": remediation,
        "description": description,
    }


def build_accessibility_report(
    pdf: Any, structure_editor: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """Build a fast, explainable preflight report without claiming conformance.

    This intentionally checks document structures directly.  It is a workshop
    work queue, not a replacement for veraPDF or assistive-technology testing.
    """
    root = pdf.Root
    metadata = _extract_pdf_metadata(pdf)
    fields, _order = _extract_field_records(pdf)
    images = _extract_image_assets(pdf)
    fonts = _extract_font_records(pdf)
    annotations, pages_without_tabs = _extract_annotation_records(pdf)
    tag_summary = _extract_struct_tree_summary(root)
    editor = dict(structure_editor or _structure_editor_data(pdf))
    editor_tables = list(editor.get("tables") or [])
    editor_figures = list(editor.get("figures") or [])
    editor_annotations = list(editor.get("annotations") or [])

    def editor_issue_count(issue_id: str, records: Iterable[Mapping[str, Any]]) -> int:
        return sum(1 for record in records if issue_id in (record.get("issueIds") or []))

    table_target_counts = {
        issue_id: editor_issue_count(issue_id, editor_tables)
        for issue_id in (
            "table-row-children",
            "table-columns",
            "table-header-scope",
        )
    }
    missing_tooltips = [field for field in fields if not field["has_custom_tooltip"]]
    unembedded = [font for font in fonts if not font["embedded"]]
    no_unicode = [font for font in fonts if not font["hasToUnicode"]]
    undescribed_annots = [item for item in annotations if not item["hasDescription"]]
    pages_without_marked_content = [
        page_index
        for page_index, page in enumerate(pdf.pages)
        if not _page_has_marked_content(page)
    ]
    content_tag_issue_count = (
        len(pdf.pages)
        if not tag_summary["present"]
        else len(pages_without_marked_content)
    )

    figure_issue = _issue(
        "figure-alt",
        "7.3.1",
        "Figures are classified and have reviewed alternative text",
        len(images),
        "figures",
        severity="warning",
        description="Image-object /Alt values are drafts; PDF/UA requires alternative text on linked Figure structure elements.",
    )
    figure_issue["status"] = "review" if images else "pass"
    semantic_issues = [
        _issue(
            "table-row-children",
            "7.2.10",
            "Table rows contain only header or data cells",
            sum(
                1
                for table in editor_tables
                for row in table.get("rows", [])
                for cell in row.get("cells", [])
                if cell.get("role") not in {"TH", "TD"}
            ),
            "structure",
        ),
        _issue(
            "table-columns",
            "7.2.42/7.2.43",
            "Table rows have consistent column counts",
            table_target_counts["table-columns"],
            "structure",
        ),
        _issue(
            "table-header-scope",
            "7.5.1",
            "Table headers declare row or column scope",
            sum(
                1
                for table in editor_tables
                for row in table.get("rows", [])
                for cell in row.get("cells", [])
                if cell.get("role") == "TH" and not cell.get("scope")
            ),
            "structure",
        ),
        _issue(
            "figure-structure-alt",
            "7.3.1",
            "Figure tags contain alternative text",
            editor_issue_count("figure-structure-alt", editor_figures),
            "figures",
        ),
        _issue(
            "link-tags",
            "7.18.5.1",
            "Link annotations are represented by Link tags",
            editor_issue_count("link-tags", editor_annotations),
            "structure",
        ),
        _issue(
            "annotation-tags",
            "7.18.1.1",
            "Non-link annotations are represented by Annot tags",
            editor_issue_count("annotation-tags", editor_annotations),
            "structure",
        ),
    ]
    for item in semantic_issues:
        issue_id = str(item["id"])
        if issue_id.startswith("table-"):
            item["editorTargetCount"] = table_target_counts.get(issue_id, 0)
        elif issue_id == "figure-structure-alt":
            item["editorTargetCount"] = editor_issue_count(issue_id, editor_figures)
        else:
            item["editorTargetCount"] = editor_issue_count(
                issue_id, editor_annotations
            )
    if tag_summary["present"]:
        has_link_annotations = any(
            item.get("subtype") == "Link" for item in editor_annotations
        )
        has_other_annotations = any(
            item.get("subtype") != "Link" for item in editor_annotations
        )
        semantic_issues = [
            item
            for item in semantic_issues
            if (
                (str(item["id"]).startswith("table-") and bool(editor_tables))
                or (item["id"] == "figure-structure-alt" and bool(editor_figures))
                or (item["id"] == "link-tags" and has_link_annotations)
                or (item["id"] == "annotation-tags" and has_other_annotations)
            )
        ]
    else:
        semantic_issues = []

    structure_tree_present = bool(tag_summary["present"])
    structure_tree_issue = _issue(
        "structure-tree",
        "7.1.11",
        (
            "Logical structure tree exists"
            if structure_tree_present
            else "No semantic structures to inspect"
        ),
        0 if structure_tree_present else 1,
        "draft_structure",
        description=(
            "Create or import a logical tag tree before inspecting tables, figures, links, and annotations."
            if not structure_tree_present
            else ""
        ),
    )
    issues = [
        _issue(
            "mark-info",
            "6.2.1",
            "Document is marked as tagged",
            0 if _mark_info_marked(root) else 1,
            "catalog_flags",
        ),
        structure_tree_issue,
        _issue(
            "content-tags",
            "7.1.3",
            "Page content has semantic tags or artifact markers",
            content_tag_issue_count,
            "draft_structure",
            description="This quick check finds pages with no marked content. veraPDF is still needed to find individual untagged objects.",
        ),
        _issue(
            "field-names",
            "7.18.1.3",
            "Form fields have accessible names",
            len(missing_tooltips),
            "field_tooltips",
        ),
        _issue(
            "tab-order",
            "7.18.3.1",
            "Widget pages use structure tab order",
            len(pages_without_tabs),
            "reading_order",
            severity="warning",
        ),
        _issue(
            "font-embedding",
            "7.21.4.1.1",
            "Fonts are embedded",
            len(unembedded),
            "fonts",
        ),
        _issue(
            "unicode-maps",
            "7.21.7.1",
            "Fonts have Unicode mappings",
            len(no_unicode),
            "fonts",
        ),
        figure_issue,
        _issue(
            "annotation-description",
            "7.18.1.2",
            "Non-widget annotations have descriptions",
            len(undescribed_annots),
            "structure",
        ),
        _issue(
            "document-language",
            "7.2.33/7.2.34",
            "Natural language is declared",
            0 if metadata["language"] else 1,
            "metadata",
            severity="warning",
        ),
        _issue(
            "document-title",
            "7.1.9",
            "Document title is present",
            0 if metadata["title"] else 1,
            "metadata",
            severity="warning",
        ),
        _issue(
            "display-title",
            "7.1.10",
            "Viewer displays document title",
            0 if _viewer_pref_display_title(root) else 1,
            "metadata",
            severity="warning",
        ),
        *semantic_issues,
    ]
    failing = sum(1 for item in issues if item["status"] != "pass")
    return {
        "summary": {
            "failed_checks": failing,
            "passed_checks": len(issues) - failing,
            "total_checks": len(issues),
            "disclaimer": "Workshop preflight only; validate the exported PDF with veraPDF and a screen reader.",
        },
        "issues": issues,
        "remediations": ACCESSIBILITY_REMEDIATIONS,
        "fonts": fonts,
        "annotations": annotations,
        "pages_without_structure_tabs": pages_without_tabs,
    }


def _xml_number(element: ET.Element, name: str) -> float:
    try:
        return float(element.attrib.get(name, 0))
    except (TypeError, ValueError):
        return 0.0


def _dominant_text_size(values: Iterable[Tuple[float, int]]) -> float:
    """Return the half-point size used by the most visible characters."""
    totals: Dict[float, int] = {}
    for value, weight in values:
        if value <= 0 or weight <= 0:
            continue
        bucket = round(value * 2) / 2
        totals[bucket] = totals.get(bucket, 0) + weight
    if not totals:
        return 0.0
    return max(totals, key=lambda size: (totals[size], -size))


def _join_visual_fragments(fragments: List[Dict[str, Any]]) -> str:
    output = ""
    previous_right: Optional[float] = None
    previous_size = 0.0
    for fragment in sorted(fragments, key=lambda item: item["left"]):
        value = str(fragment["text"]).strip()
        if not value:
            continue
        gap = fragment["left"] - previous_right if previous_right is not None else 0
        needs_space = bool(output) and (
            gap > max(min(previous_size, fragment["size"]) * 0.12, 1.0)
            or (not output.endswith((" ", "-", "/")) and value[0] not in ",.;:!?)]}")
        )
        if needs_space:
            output += " "
        output += value
        previous_right = max(
            previous_right or 0,
            fragment["left"] + fragment["width"],
        )
        previous_size = fragment["size"]
    return re.sub(r"\s+", " ", output).strip()


def _is_heading_marker(value: str) -> bool:
    compact = value.strip()
    if not re.search(r"[\w]", compact, flags=re.UNICODE):
        return True
    alphanumeric = re.sub(r"[^\w]", "", compact, flags=re.UNICODE)
    if len(alphanumeric) <= 1:
        return True
    if re.fullmatch(r"(?i)(?:[ivxlcdm]+|[a-z]|\d{1,3})[.)]?", compact):
        return True
    if re.fullmatch(r"[a-z]{1,4}", compact):
        return True
    return False


def _normalized_running_text(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _heading_candidates_from_xml(root: ET.Element) -> List[Dict[str, Any]]:
    """Infer conservative heading candidates from pdftohtml's visual XML."""
    font_specs: Dict[str, Dict[str, Any]] = {}
    for spec in root.findall(".//fontspec"):
        size = _xml_number(spec, "size")
        font_specs[spec.attrib.get("id", "")] = {
            "size": size,
            "family": spec.attrib.get("family", ""),
        }

    lines: List[Dict[str, Any]] = []
    for page_index, page in enumerate(root.findall(".//page")):
        page_width = _xml_number(page, "width")
        page_height = _xml_number(page, "height")
        fragments: List[Dict[str, Any]] = []
        for text in page.findall(".//text"):
            font_spec = font_specs.get(text.attrib.get("font", ""), {})
            size = float(font_spec.get("size", 0) or 0)
            family = str(font_spec.get("family", ""))
            value = "".join(text.itertext()).strip()
            if not value or size <= 0:
                continue
            bold_markup = any(
                child.tag.casefold().split("}")[-1] in {"b", "strong"}
                for child in text.iter()
                if child is not text
            )
            fragments.append(
                {
                    "text": value,
                    "top": _xml_number(text, "top"),
                    "left": _xml_number(text, "left"),
                    "width": _xml_number(text, "width"),
                    "height": _xml_number(text, "height"),
                    "size": size,
                    "family": family,
                    "bold": bold_markup or "bold" in family.casefold(),
                }
            )

        grouped: List[List[Dict[str, Any]]] = []
        for fragment in sorted(fragments, key=lambda item: (item["top"], item["left"])):
            matching: Optional[List[Dict[str, Any]]] = None
            for group in reversed(grouped[-4:]):
                anchor = group[0]
                tolerance = max(2.5, min(anchor["size"], fragment["size"]) * 0.22)
                group_right = max(item["left"] + item["width"] for item in group)
                maximum_gap = max(12.0, min(anchor["size"], fragment["size"]) * 1.25)
                if (
                    abs(anchor["top"] - fragment["top"]) <= tolerance
                    and fragment["left"] <= group_right + maximum_gap
                ):
                    matching = group
                    break
            if matching is None:
                grouped.append([fragment])
            else:
                matching.append(fragment)

        for group in grouped:
            value = _join_visual_fragments(group)
            if not value:
                continue
            character_total = sum(max(len(item["text"].strip()), 1) for item in group)
            size = (
                sum(item["size"] * max(len(item["text"].strip()), 1) for item in group)
                / character_total
            )
            bold_characters = sum(
                len(item["text"].strip()) for item in group if item["bold"]
            )
            left = min(item["left"] for item in group)
            right = max(item["left"] + item["width"] for item in group)
            lines.append(
                {
                    "pageIndex": page_index,
                    "text": value,
                    "fontSize": round(size, 1),
                    "fontFamily": next(
                        (item["family"] for item in group if item["family"]), ""
                    ),
                    "boldRatio": bold_characters / character_total,
                    "top": min(item["top"] for item in group),
                    "left": left,
                    "right": right,
                    "height": max(item["height"] for item in group),
                    "pageWidth": page_width,
                    "pageHeight": page_height,
                    "centered": bool(page_width)
                    and abs(((left + right) / 2) - (page_width / 2))
                    <= page_width * 0.12,
                    "weight": max(len(re.sub(r"\s+", "", value)), 1),
                }
            )

    if not lines:
        return []
    body_size = _dominant_text_size(
        (line["fontSize"], line["weight"])
        for line in lines
        if len(line["text"]) >= 2 and not _is_heading_marker(line["text"])
    )
    if body_size <= 0:
        return []

    running_occurrences: Dict[str, List[Dict[str, Any]]] = {}
    for line in lines:
        height = line["pageHeight"]
        near_edge = bool(height) and (
            line["top"] <= height * 0.18 or line["top"] >= height * 0.88
        )
        normalized = _normalized_running_text(line["text"])
        if near_edge and normalized:
            running_occurrences.setdefault(normalized, []).append(line)

    candidates: List[Dict[str, Any]] = []
    for line in lines:
        value = line["text"]
        size = line["fontSize"]
        normalized = _normalized_running_text(value)
        repeated = running_occurrences.get(normalized, [])
        repeated_pages = {item["pageIndex"] for item in repeated}
        is_running = len(repeated_pages) > 1
        looks_like_form_code = bool(
            re.fullmatch(
                r"(?i)(?:form\s+)?[a-z]{1,8}[\s–—-]*\d[\w.–—-]*", value.strip()
            )
        )
        if (
            len(value) > 180
            or _is_heading_marker(value)
            or looks_like_form_code
            or (is_running and line is not repeated[0])
        ):
            continue
        # A repeated running title may be a genuine document title on page one.
        # Keep only its first occurrence and require enough words to distinguish
        # it from a field label or form identifier.
        if is_running and len(re.findall(r"\w+", value, flags=re.UNICODE)) < 4:
            continue
        size_ratio = size / body_size
        bold = line["boldRatio"] >= 0.5
        prominent = size_ratio >= 1.15
        bold_prominent = bold and size_ratio >= 1.0
        if not (prominent or bold_prominent):
            continue
        reasons = [f"{size_ratio:.1f}× the estimated body-text size"]
        if bold:
            reasons.append("mostly bold")
        if line["centered"]:
            reasons.append("visually centered")
        if is_running:
            reasons.append("first occurrence of a repeated title")
        line["reason"] = "; ".join(reasons)
        line["candidateId"] = (
            f"p{line['pageIndex'] + 1}:{line['top']:.1f}:{line['left']:.1f}"
        )
        line["box"] = {
            "x": line["left"] / line["pageWidth"] if line["pageWidth"] else 0,
            "y": line["top"] / line["pageHeight"] if line["pageHeight"] else 0,
            "width": (
                (line["right"] - line["left"]) / line["pageWidth"]
                if line["pageWidth"]
                else 0
            ),
            "height": (
                line["height"] / line["pageHeight"] if line["pageHeight"] else 0
            ),
        }
        candidates.append(line)

    # Levels are relative to the document's surviving heading-size clusters,
    # not fixed multipliers. This avoids turning oversized question markers into H1.
    size_clusters = sorted({item["fontSize"] for item in candidates}, reverse=True)
    for candidate in candidates:
        level = min(size_clusters.index(candidate["fontSize"]) + 1, 6)
        candidate["suggestedTag"] = f"H{level}"
        candidate["confidence"] = (
            "high"
            if candidate["fontSize"] / body_size >= 1.45
            or (candidate["boldRatio"] >= 0.5 and candidate["centered"])
            else (
                "medium"
                if candidate["fontSize"] / body_size >= 1.2
                or candidate["boldRatio"] >= 0.5
                else "low"
            )
        )
        for internal_key in (
            "boldRatio",
            "top",
            "left",
            "right",
            "height",
            "pageWidth",
            "pageHeight",
            "centered",
            "weight",
        ):
            candidate.pop(internal_key, None)
    return candidates[:250]


def suggest_heading_candidates(pdf_path: str) -> List[Dict[str, Any]]:
    """Return conservative, review-only heading candidates from Poppler XML."""
    executable = shutil.which("pdftohtml")
    if not executable:
        return []
    with tempfile.TemporaryDirectory(prefix="pdf-a11y-headings-") as tmp_dir:
        xml_path = os.path.join(tmp_dir, "document.xml")
        command = [executable, "-q", "-xml", "-hidden", "-i", pdf_path, xml_path]
        try:
            subprocess.run(
                command, check=True, timeout=60, capture_output=True
            )  # nosec B603
            tree = ET.parse(xml_path)
        except (OSError, subprocess.SubprocessError, ET.ParseError):
            return []
    return _heading_candidates_from_xml(tree.getroot())


def inspect_pdf_accessibility(pdf_path: str) -> Dict[str, Any]:
    """Read basic accessibility-relevant PDF metadata and structures."""
    try:
        import pikepdf

        with pikepdf.open(pdf_path) as pdf:
            fields, field_order = _extract_field_records(pdf)
            structure_editor = _structure_editor_data(pdf)
            return {
                "metadata": _extract_pdf_metadata(pdf),
                "fields": fields,
                "field_order": field_order,
                "images": _extract_image_assets(pdf),
                "tag_structure": _extract_struct_tree_summary(pdf.Root),
                "structure_editor": structure_editor,
                "report": build_accessibility_report(pdf, structure_editor),
                "heading_candidates": suggest_heading_candidates(pdf_path),
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
    set_display_doc_title: bool = True,
    set_structure_tab_order: bool = False,
    mark_as_tagged: Optional[bool] = None,
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
        tab_order_updates = 0
        structure_order_updates = 0

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
                document_title = title or _safe_pdf_string(
                    docinfo.get("/Title", "")
                ).strip()
                if set_display_doc_title and document_title:
                    viewer_preferences = pdf.Root.get("/ViewerPreferences")
                    if not isinstance(viewer_preferences, pikepdf.Dictionary):
                        viewer_preferences = pikepdf.Dictionary()
                        pdf.Root["/ViewerPreferences"] = viewer_preferences
                    viewer_preferences["/DisplayDocTitle"] = True
                    metadata_updates += 1

            if mark_as_tagged is not None:
                mark_info = pdf.Root.get("/MarkInfo")
                if not isinstance(mark_info, pikepdf.Dictionary):
                    mark_info = pikepdf.Dictionary()
                    pdf.Root["/MarkInfo"] = mark_info
                mark_info["/Marked"] = bool(mark_as_tagged)

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
                ordered = [
                    str(name).strip() for name in field_order if str(name).strip()
                ]
                acroform = (
                    pdf.Root.get("/AcroForm") if hasattr(pdf.Root, "get") else None
                )
                if acroform is not None and "/Fields" in acroform:
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

                    # Widget keyboard order lives in each page's /Annots array.
                    # /Tabs /S tells readers to use the structure order; setting
                    # this is explicit because it is only meaningful with tags.
                    order_index = {name: index for index, name in enumerate(ordered)}
                    for page in pdf.pages:
                        annots = page.get("/Annots")
                        if not annots:
                            continue
                        refs = list(cast(Iterable[Any], annots))
                        widget_slots = [
                            index
                            for index, ref in enumerate(refs)
                            if _safe_pdf_string(ref.get("/Subtype", "")) == "/Widget"
                        ]
                        widgets = [refs[index] for index in widget_slots]
                        original_positions = {
                            id(ref): index for index, ref in enumerate(widgets)
                        }

                        def annotation_sort_key(ref: Any) -> Tuple[int, int]:
                            parent = _named_parent(ref)
                            name = (
                                _safe_pdf_string(parent.get("/T", "")).strip()
                                if parent is not None
                                else ""
                            )
                            return (
                                order_index.get(name, len(order_index)),
                                original_positions[id(ref)],
                            )

                        sorted_widgets = sorted(widgets, key=annotation_sort_key)
                        for slot, widget in zip(widget_slots, sorted_widgets):
                            refs[slot] = widget
                        if any(
                            before is not after
                            for before, after in zip(widgets, sorted_widgets)
                        ):
                            page["/Annots"] = pikepdf.Array(refs)
                        if (
                            set_structure_tab_order
                            and pdf.Root.get("/StructTreeRoot") is not None
                            and _safe_pdf_string(page.get("/Tabs", "")) != "/S"
                        ):
                            page["/Tabs"] = pikepdf.Name("/S")
                            tab_order_updates += 1
                    if ordered:
                        structure_order_updates = _reorder_structure_form_elements(
                            pdf.Root, ordered
                        )

            # Update image alt text when IDs are provided.
            if image_alt_map:
                for page_index, page in enumerate(pdf.pages):
                    resources = page.get("/Resources") if hasattr(page, "get") else None
                    for resource_path, obj in _walk_resource_xobjects(resources):
                        try:
                            if obj.get("/Subtype") != "/Image":
                                continue
                            asset_id = f"p{page_index + 1}:{resource_path}"
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
            "tab_order_updates": tab_order_updates,
            "structure_order_updates": structure_order_updates,
        }
    except Exception as exc:
        raise PDFAccessibilityError(
            f"Failed to apply PDF accessibility settings: {exc}"
        )


def create_draft_structure_tree(
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    overwrite: bool = False,
    heading_decisions: Optional[Iterable[Mapping[str, Any]]] = None,
    mark_as_tagged: bool = False,
) -> Dict[str, Any]:
    """Create a content-block tag-tree draft without redrawing page content.

    Existing PDF text objects become individual paragraph elements. Exact
    matches from the conservative heading analysis are promoted to headings,
    and widgets become Form elements. Graphics remain unclassified because
    deciding whether they are figures or artifacts requires human review.
    """
    if input_pdf_path != output_pdf_path:
        shutil.copyfile(input_pdf_path, output_pdf_path)
    try:
        import pikepdf

        heading_candidates = suggest_heading_candidates(output_pdf_path)
        decisions_by_id = {
            str(item.get("candidateId") or ""): item
            for item in (heading_decisions or [])
            if str(item.get("status") or "") == "approved"
        }
        headings_by_page: Dict[int, Dict[str, str]] = {}
        for candidate in heading_candidates:
            if heading_decisions is not None:
                decision = decisions_by_id.get(str(candidate.get("candidateId") or ""))
                if decision is None:
                    continue
                selected_tag = str(decision.get("tag") or "H2").upper()
                if not re.fullmatch(r"H[1-6]", selected_tag):
                    continue
            else:
                selected_tag = str(candidate.get("suggestedTag") or "H2")
            normalized = _normalized_running_text(str(candidate.get("text") or ""))
            if normalized:
                headings_by_page.setdefault(int(candidate["pageIndex"]), {})[
                    normalized
                ] = selected_tag

        with pikepdf.open(output_pdf_path, allow_overwriting_input=True) as pdf:
            page_count = len(pdf.pages)
            if pdf.Root.get("/StructTreeRoot") is not None and not overwrite:
                raise PDFAccessibilityError(
                    "A structure tree already exists; refusing to replace it without overwrite=true."
                )
            struct_root = pdf.make_indirect(
                pikepdf.Dictionary({"/Type": pikepdf.Name("/StructTreeRoot")})
            )
            document = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/StructElem"),
                        "/S": pikepdf.Name("/Document"),
                        "/P": struct_root,
                    }
                )
            )
            document_children: List[Any] = []
            parent_tree_entries: Dict[int, Any] = {}
            next_struct_parent = page_count
            widget_count = 0
            text_block_count = 0
            heading_count = 0
            for page_index, page in enumerate(pdf.pages):
                page["/StructParents"] = page_index
                page_part = pdf.make_indirect(
                    pikepdf.Dictionary(
                        {
                            "/Type": pikepdf.Name("/StructElem"),
                            "/S": pikepdf.Name("/Part"),
                            "/P": document,
                            "/Pg": page.obj,
                        }
                    )
                )
                document_children.append(page_part)
                page_children: List[Any] = []

                instructions = list(pikepdf.parse_content_stream(page))
                existing_mcids: List[int] = []
                for instruction in instructions:
                    if str(instruction.operator) != "BDC":
                        continue
                    for operand in instruction.operands:
                        raw_mcid = (
                            operand.get("/MCID") if hasattr(operand, "get") else None
                        )
                        if raw_mcid is None:
                            continue
                        try:
                            existing_mcids.append(int(raw_mcid))
                        except (TypeError, ValueError):
                            continue
                first_new_mcid = max(existing_mcids, default=-1) + 1
                # ParentTree arrays are indexed by MCID. Preserve slots used by
                # existing marked content so newly drafted elements cannot
                # collide with them, even when the old tree is absent/broken.
                mcid_elements: List[Any] = [None] * first_new_mcid
                rewritten: List[Any] = []
                text_block: List[Any] = []
                inside_text = False
                text_block_is_artifact = False
                artifact_stack: List[bool] = []

                def starts_artifact(instruction: Any) -> bool:
                    if str(instruction.operator) not in {"BMC", "BDC"}:
                        return False
                    operands = list(instruction.operands)
                    return bool(
                        operands
                        and _safe_pdf_string(operands[0]).lstrip("/") == "Artifact"
                    )

                def shown_text(instruction: Any) -> str:
                    operator = str(instruction.operator)
                    operands = list(instruction.operands)
                    if operator in {"Tj", "'", '"'} and operands:
                        return str(operands[-1])
                    if operator == "TJ" and operands:
                        try:
                            return "".join(
                                str(item)
                                for item in operands[0]
                                if isinstance(item, pikepdf.String)
                            )
                        except (TypeError, ValueError):
                            return ""
                    return ""

                for instruction in instructions:
                    operator = str(instruction.operator)
                    if operator == "BT" and not inside_text:
                        inside_text = True
                        text_block_is_artifact = any(artifact_stack)
                        text_block = [instruction]
                        continue
                    if inside_text:
                        text_block.append(instruction)
                        if operator != "ET":
                            continue

                        if text_block_is_artifact or any(
                            starts_artifact(item) for item in text_block
                        ):
                            rewritten.extend(text_block)
                        else:
                            raw_groups: List[List[int]] = []
                            current_group: List[int] = []
                            for block_index, block_instruction in enumerate(
                                text_block[1:-1], start=1
                            ):
                                block_operator = str(block_instruction.operator)
                                if block_operator in {"Td", "TD", "Tm", "T*"}:
                                    if current_group:
                                        raw_groups.append(current_group)
                                        current_group = []
                                if block_operator in {"'", '"'} and current_group:
                                    raw_groups.append(current_group)
                                    current_group = []
                                if block_operator in {"Tj", "TJ", "'", '"'}:
                                    if shown_text(block_instruction).strip():
                                        current_group.append(block_index)
                            if current_group:
                                raw_groups.append(current_group)

                            groups: List[Tuple[List[int], str]] = []
                            group_index = 0
                            page_headings = headings_by_page.get(page_index, {})
                            while group_index < len(raw_groups):
                                matched: Optional[Tuple[List[int], str]] = None
                                max_span = min(4, len(raw_groups) - group_index)
                                for span in range(max_span, 0, -1):
                                    selected = raw_groups[
                                        group_index : group_index + span
                                    ]
                                    spaced_text = " ".join(
                                        " ".join(
                                            shown_text(text_block[index])
                                            for index in group
                                        )
                                        for group in selected
                                    )
                                    compact_text = " ".join(
                                        "".join(
                                            shown_text(text_block[index])
                                            for index in group
                                        )
                                        for group in selected
                                    )
                                    tag_name = page_headings.get(
                                        _normalized_running_text(spaced_text)
                                    ) or page_headings.get(
                                        _normalized_running_text(compact_text)
                                    )
                                    if tag_name:
                                        matched = (
                                            [
                                                index
                                                for group in selected
                                                for index in group
                                            ],
                                            tag_name,
                                        )
                                        group_index += span
                                        break
                                if matched is None:
                                    matched = (raw_groups[group_index], "P")
                                    group_index += 1
                                groups.append(matched)

                            starts: Dict[int, Tuple[int, str]] = {}
                            ends: Dict[int, int] = {}
                            for group, tag_name in groups:
                                mcid = len(mcid_elements)
                                starts[group[0]] = (mcid, tag_name)
                                ends[group[-1]] = mcid
                                element = pdf.make_indirect(
                                    pikepdf.Dictionary(
                                        {
                                            "/Type": pikepdf.Name("/StructElem"),
                                            "/S": pikepdf.Name(f"/{tag_name}"),
                                            "/P": page_part,
                                            "/Pg": page.obj,
                                            "/K": mcid,
                                        }
                                    )
                                )
                                page_children.append(element)
                                mcid_elements.append(element)
                                text_block_count += 1
                                if tag_name.startswith("H"):
                                    heading_count += 1

                            for block_index, block_instruction in enumerate(text_block):
                                if block_index in starts:
                                    mcid, tag_name = starts[block_index]
                                    rewritten.append(
                                        pikepdf.ContentStreamInstruction(
                                            [
                                                pikepdf.Name(f"/{tag_name}"),
                                                pikepdf.Dictionary({"/MCID": mcid}),
                                            ],
                                            pikepdf.Operator("BDC"),
                                        )
                                    )
                                rewritten.append(block_instruction)
                                if block_index in ends:
                                    rewritten.append(
                                        pikepdf.ContentStreamInstruction(
                                            [], pikepdf.Operator("EMC")
                                        )
                                    )
                        for block_instruction in text_block:
                            block_operator = str(block_instruction.operator)
                            if block_operator in {"BMC", "BDC"}:
                                artifact_stack.append(
                                    starts_artifact(block_instruction)
                                )
                            elif block_operator == "EMC" and artifact_stack:
                                artifact_stack.pop()
                        inside_text = False
                        text_block_is_artifact = False
                        text_block = []
                        continue
                    rewritten.append(instruction)
                    if operator in {"BMC", "BDC"}:
                        artifact_stack.append(starts_artifact(instruction))
                    elif operator == "EMC" and artifact_stack:
                        artifact_stack.pop()
                if text_block:
                    rewritten.extend(text_block)
                if rewritten:
                    page["/Contents"] = pdf.make_stream(
                        pikepdf.unparse_content_stream(rewritten)
                    )
                parent_tree_entries[page_index] = pikepdf.Array(mcid_elements)

                annots = page.get("/Annots")
                page_has_widgets = False
                for annot in cast(Iterable[Any], annots or []):
                    if not hasattr(annot, "get"):
                        continue
                    if _safe_pdf_string(annot.get("/Subtype", "")) != "/Widget":
                        continue
                    page_has_widgets = True
                    annot["/StructParent"] = next_struct_parent
                    object_reference = pikepdf.Dictionary(
                        {
                            "/Type": pikepdf.Name("/OBJR"),
                            "/Obj": annot,
                            "/Pg": page.obj,
                        }
                    )
                    form_element = pdf.make_indirect(
                        pikepdf.Dictionary(
                            {
                                "/Type": pikepdf.Name("/StructElem"),
                                "/S": pikepdf.Name("/Form"),
                                "/P": page_part,
                                "/Pg": page.obj,
                                "/K": object_reference,
                            }
                        )
                    )
                    page_children.append(form_element)
                    parent_tree_entries[next_struct_parent] = form_element
                    next_struct_parent += 1
                    widget_count += 1
                if page_has_widgets:
                    page["/Tabs"] = pikepdf.Name("/S")
                page_part["/K"] = pikepdf.Array(page_children)

            parent_tree_numbers: List[Any] = []
            for key in sorted(parent_tree_entries):
                parent_tree_numbers.extend([key, parent_tree_entries[key]])
            document["/K"] = pikepdf.Array(document_children)
            struct_root["/K"] = document
            struct_root["/ParentTree"] = pdf.make_indirect(
                pikepdf.Dictionary({"/Nums": pikepdf.Array(parent_tree_numbers)})
            )
            struct_root["/ParentTreeNextKey"] = next_struct_parent
            pdf.Root["/StructTreeRoot"] = struct_root
            mark_info = pdf.Root.get("/MarkInfo")
            if not isinstance(mark_info, pikepdf.Dictionary):
                mark_info = pikepdf.Dictionary()
                pdf.Root["/MarkInfo"] = mark_info
            mark_info["/Marked"] = bool(mark_as_tagged)
            pdf.save(output_pdf_path)
        return {
            "action": "draft_structure",
            "pages_tagged": page_count,
            "text_blocks_tagged": text_block_count,
            "headings_drafted": heading_count,
            "widgets_tagged": widget_count,
            "marked_as_tagged": bool(mark_as_tagged),
            "review_required": True,
            "warning": "Content-block tags are a draft. Review reading order, heading levels, paragraph grouping, field placement, lists, tables, figures, links, and artifacts manually.",
        }
    except PDFAccessibilityError:
        raise
    except Exception as exc:
        raise PDFAccessibilityError(f"Failed to create draft tag structure: {exc}")


def _structure_node_at_path(struct_root: Any, path: str) -> Any:
    node = struct_root
    try:
        indexes = [int(value) for value in str(path).split("/") if value != ""]
    except ValueError as exc:
        raise PDFAccessibilityError("Invalid structure-tree path.") from exc
    if not indexes:
        raise PDFAccessibilityError("A structure-tree path is required.")
    for index in indexes:
        children = _structure_children(node)
        if index < 0 or index >= len(children):
            raise PDFAccessibilityError(
                "The structure tree changed; refresh and retry."
            )
        node = children[index]
    return node


def _append_structure_child(parent: Any, child: Any) -> None:
    import pikepdf

    kids = parent.get("/K") if hasattr(parent, "get") else None
    if kids is None:
        parent["/K"] = pikepdf.Array([child])
    elif isinstance(kids, pikepdf.Array):
        kids.append(child)
    else:
        parent["/K"] = pikepdf.Array([kids, child])


def _number_tree_entries(node: Any) -> Dict[int, Any]:
    import pikepdf

    result: Dict[int, Any] = {}
    numbers = node.get("/Nums") if hasattr(node, "get") else None
    if isinstance(numbers, pikepdf.Array):
        values = list(numbers)
        for index in range(0, len(values) - 1, 2):
            try:
                result[int(values[index])] = values[index + 1]
            except (TypeError, ValueError):
                continue
    kids = node.get("/Kids") if hasattr(node, "get") else None
    for kid in cast(Iterable[Any], kids or []):
        result.update(_number_tree_entries(kid))
    return result


def _document_structure_parent(struct_root: Any) -> Any:
    children = _structure_children(struct_root)
    for child in children:
        if _safe_pdf_string(child.get("/S", "")).lstrip("/") == "Document":
            return child
    return struct_root


def apply_manual_structure_repairs(
    input_pdf_path: str,
    output_pdf_path: str,
    operations: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Apply explicit, path-addressed semantic edits selected by a human."""
    if input_pdf_path != output_pdf_path:
        shutil.copyfile(input_pdf_path, output_pdf_path)
    operation_list = list(operations)
    if not operation_list or len(operation_list) > 200:
        raise PDFAccessibilityError("Provide between 1 and 200 structure edits.")
    try:
        import pikepdf

        with pikepdf.open(output_pdf_path, allow_overwriting_input=True) as pdf:
            struct_root = pdf.Root.get("/StructTreeRoot")
            if struct_root is None:
                raise PDFAccessibilityError(
                    "Create a draft structure tree before editing semantic tags."
                )
            counts = {
                "roles_changed": 0,
                "scopes_changed": 0,
                "figure_alts_changed": 0,
                "rows_padded": 0,
                "cells_added": 0,
                "annotations_tagged": 0,
                "annotation_descriptions_changed": 0,
            }
            parent_tree = struct_root.get("/ParentTree")
            number_entries = (
                _number_tree_entries(parent_tree) if parent_tree is not None else {}
            )
            next_key = max(
                int(struct_root.get("/ParentTreeNextKey", 0)),
                max(number_entries, default=-1) + 1,
            )
            for operation in operation_list:
                action = str(operation.get("action") or "")
                if action in {"set_role", "set_scope", "set_figure_alt", "pad_table"}:
                    node = _structure_node_at_path(
                        struct_root, str(operation.get("path") or "")
                    )
                if action == "set_role":
                    role = str(operation.get("role") or "")
                    if role not in {"TH", "TD"}:
                        raise PDFAccessibilityError(
                            "Table child roles must be TH or TD."
                        )
                    node["/S"] = pikepdf.Name(f"/{role}")
                    counts["roles_changed"] += 1
                elif action == "set_scope":
                    scope = str(operation.get("scope") or "")
                    if scope not in {"Row", "Column", "Both"}:
                        raise PDFAccessibilityError(
                            "Header scope must be Row, Column, or Both."
                        )
                    attributes = node.get("/A")
                    candidates = (
                        list(attributes)
                        if isinstance(attributes, pikepdf.Array)
                        else [attributes] if attributes is not None else []
                    )
                    table_attribute = next(
                        (
                            item
                            for item in candidates
                            if hasattr(item, "get")
                            and _safe_pdf_string(item.get("/O", "")) == "/Table"
                        ),
                        None,
                    )
                    if table_attribute is None:
                        table_attribute = pikepdf.Dictionary(
                            {"/O": pikepdf.Name("/Table")}
                        )
                        candidates.append(table_attribute)
                    table_attribute["/Scope"] = pikepdf.Name(f"/{scope}")
                    node["/A"] = (
                        candidates[0]
                        if len(candidates) == 1
                        else pikepdf.Array(candidates)
                    )
                    counts["scopes_changed"] += 1
                elif action == "set_figure_alt":
                    if _safe_pdf_string(node.get("/S", "")) != "/Figure":
                        raise PDFAccessibilityError(
                            "Alternative text can only be set on a Figure tag."
                        )
                    alt_text = str(operation.get("altText") or "").strip()
                    if alt_text:
                        node["/Alt"] = pikepdf.String(alt_text[:1000])
                    elif "/Alt" in node:
                        del node["/Alt"]
                    counts["figure_alts_changed"] += 1
                elif action == "pad_table":
                    if _safe_pdf_string(node.get("/S", "")) != "/Table":
                        raise PDFAccessibilityError("The selected node is not a table.")
                    rows = [
                        child
                        for child in _structure_children(node)
                        if _safe_pdf_string(child.get("/S", "")) == "/TR"
                    ]
                    target = max(
                        (
                            sum(
                                1
                                for cell in _structure_children(row)
                                if _safe_pdf_string(cell.get("/S", ""))
                                in {"/TH", "/TD"}
                            )
                            for row in rows
                        ),
                        default=0,
                    )
                    for row in rows:
                        current = sum(
                            1
                            for cell in _structure_children(row)
                            if _safe_pdf_string(cell.get("/S", "")) in {"/TH", "/TD"}
                        )
                        added_to_row = 0
                        while current < target:
                            cell = pdf.make_indirect(
                                pikepdf.Dictionary(
                                    {
                                        "/Type": pikepdf.Name("/StructElem"),
                                        "/S": pikepdf.Name("/TD"),
                                        "/P": row,
                                    }
                                )
                            )
                            if row.get("/Pg") is not None:
                                cell["/Pg"] = row.get("/Pg")
                            _append_structure_child(row, cell)
                            current += 1
                            added_to_row += 1
                            counts["cells_added"] += 1
                        if added_to_row:
                            counts["rows_padded"] += 1
                elif action in {"tag_annotation", "set_annotation_contents"}:
                    try:
                        page_index = int(operation.get("pageIndex", -1))
                        annot_index = int(operation.get("index", -1))
                        annot = pdf.pages[page_index]["/Annots"][annot_index]
                    except (IndexError, KeyError, TypeError, ValueError) as exc:
                        raise PDFAccessibilityError(
                            "The annotation list changed; refresh and retry."
                        ) from exc
                    if action == "set_annotation_contents":
                        contents = str(operation.get("contents") or "").strip()
                        if contents:
                            annot["/Contents"] = pikepdf.String(contents[:1000])
                        elif "/Contents" in annot:
                            del annot["/Contents"]
                        counts["annotation_descriptions_changed"] += 1
                    else:
                        role = str(operation.get("role") or "")
                        subtype = _safe_pdf_string(annot.get("/Subtype", ""))
                        expected_role = "Link" if subtype == "/Link" else "Annot"
                        if role != expected_role:
                            raise PDFAccessibilityError(
                                f"{subtype.lstrip('/') or 'Annotation'} requires a {expected_role} tag."
                            )
                        existing_key = annot.get("/StructParent")
                        if (
                            existing_key is not None
                            and int(existing_key) in number_entries
                        ):
                            continue
                        key = (
                            int(existing_key) if existing_key is not None else next_key
                        )
                        if key in number_entries:
                            key = next_key
                        next_key = max(next_key, key + 1)
                        parent = _document_structure_parent(struct_root)
                        objr = pikepdf.Dictionary(
                            {
                                "/Type": pikepdf.Name("/OBJR"),
                                "/Obj": annot,
                                "/Pg": pdf.pages[page_index].obj,
                            }
                        )
                        element = pdf.make_indirect(
                            pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/StructElem"),
                                    "/S": pikepdf.Name(f"/{role}"),
                                    "/P": parent,
                                    "/Pg": pdf.pages[page_index].obj,
                                    "/K": objr,
                                }
                            )
                        )
                        _append_structure_child(parent, element)
                        annot["/StructParent"] = key
                        number_entries[key] = element
                        counts["annotations_tagged"] += 1
                else:
                    raise PDFAccessibilityError(
                        f"Unsupported structure edit: {action or 'missing action'}."
                    )
            flattened: List[Any] = []
            for key in sorted(number_entries):
                flattened.extend([key, number_entries[key]])
            struct_root["/ParentTree"] = pdf.make_indirect(
                pikepdf.Dictionary({"/Nums": pikepdf.Array(flattened)})
            )
            struct_root["/ParentTreeNextKey"] = next_key
            pdf.save(output_pdf_path)
        return {
            "action": "structure",
            **counts,
            "review_required": True,
            "warning": "Manual structure edits were applied. Review the resulting tree and validate with veraPDF and assistive technology.",
        }
    except PDFAccessibilityError:
        raise
    except Exception as exc:
        raise PDFAccessibilityError(f"Failed to apply structure edits: {exc}") from exc


def embed_fonts_and_rebuild_unicode(
    input_pdf_path: str,
    output_pdf_path: str,
    *,
    embed_exact_fonts: bool = True,
    add_unicode_maps: bool = True,
) -> Dict[str, Any]:
    """Embed exact local TrueType matches and add deterministic WinAnsi maps.

    Unlike a PDF re-distiller, this changes only font dictionaries and streams.
    It never substitutes a font or rewrites page content.
    """
    if input_pdf_path != output_pdf_path:
        shutil.copyfile(input_pdf_path, output_pdf_path)
    try:
        import pikepdf

        with pikepdf.open(input_pdf_path) as source_pdf:
            before = _extract_font_records(source_pdf)
        needs_embedding_lookup = embed_exact_fonts and any(
            not bool(record.get("embedded")) for record in before
        )
        inventory = _system_truetype_fonts() if needs_embedding_lookup else []
        embedded: List[Dict[str, Any]] = []
        unicode_maps: List[str] = []
        unicode_unresolved: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        with pikepdf.open(output_pdf_path, allow_overwriting_input=True) as pdf:
            for resource, font in _iter_pdf_fonts(pdf):
                font_name = _safe_pdf_string(font.get("/BaseFont", resource)).lstrip(
                    "/"
                )
                descriptor = _font_descriptor(font)
                is_embedded = bool(
                    descriptor
                    and any(
                        key in descriptor
                        for key in ("/FontFile", "/FontFile2", "/FontFile3")
                    )
                )
                if not is_embedded and embed_exact_fonts:
                    subtype = _safe_pdf_string(font.get("/Subtype", "")).lstrip("/")
                    canonical = _canonical_font_name(font.get("/BaseFont", font_name))
                    # A standard 14 font has no descriptor to hold a program and
                    # no widths to verify against, but its metrics are published,
                    # so it can still be completed into a self-contained font.
                    completable = descriptor is None and is_standard_14(canonical)
                    if subtype not in {"TrueType", "Type1"} or (
                        descriptor is None and not completable
                    ):
                        unresolved.append(
                            {
                                "resource": resource,
                                "font": font_name,
                                "reason": f"A {subtype or 'unknown'} font without a descriptor cannot be completed safely.",
                                "suggested_alternative": suggest_system_font(font_name),
                                "next_step": "Ask an administrator to install the exact font using the Dashboard font manager.",
                            }
                        )
                    else:
                        match = find_exact_system_font(font, inventory)
                        if match is None:
                            unresolved.append(
                                {
                                    "resource": resource,
                                    "font": font_name,
                                    "reason": _embedding_unresolved_reason(
                                        font_name, canonical, inventory
                                    ),
                                    "suggested_alternative": suggest_system_font(
                                        font_name
                                    ),
                                    "next_step": "Ask an administrator to install the exact font using the Dashboard font manager.",
                                }
                            )
                        elif completable:
                            if _embed_into_standard_14_font(
                                pdf, font, font_name, match["path"]
                            ):
                                embedded.append(
                                    {
                                        "resource": resource,
                                        "font": font_name,
                                        "source": match["path"],
                                        "completed_standard_14": True,
                                    }
                                )
                            else:
                                unresolved.append(
                                    {
                                        "resource": resource,
                                        "font": font_name,
                                        "reason": "The standard 14 font dictionary could not be completed.",
                                        "suggested_alternative": None,
                                        "next_step": "Report this PDF; the font dictionary is unusual.",
                                    }
                                )
                        elif descriptor is not None:
                            font_bytes = Path(match["path"]).read_bytes()
                            stream = pdf.make_stream(font_bytes)
                            stream["/Length1"] = len(font_bytes)
                            descriptor["/FontFile2"] = stream
                            embedded.append(
                                {
                                    "resource": resource,
                                    "font": font_name,
                                    "source": match["path"],
                                }
                            )
                if add_unicode_maps and "/ToUnicode" not in font:
                    cmap = _winansi_to_unicode_cmap(font)
                    if cmap is not None:
                        font["/ToUnicode"] = pdf.make_stream(cmap)
                        unicode_maps.append(resource)
                    else:
                        unicode_unresolved.append(
                            {
                                "resource": resource,
                                "font": font_name,
                                "reason": _unicode_unresolved_reason(font, font_name),
                                "next_step": (
                                    "Review the glyphs and confirm what they mean."
                                    if is_symbolic_family(font_name)
                                    else "Review this font's extracted text by hand."
                                ),
                                "reviewable": True,
                            }
                        )
            pdf.save(output_pdf_path)
        with pikepdf.open(output_pdf_path) as repaired_pdf:
            after = _extract_font_records(repaired_pdf)
        return {
            "action": "fonts",
            "before": before,
            "after": after,
            "fonts_embedded": embedded,
            "unicode_maps_added": unicode_maps,
            "unicode_unresolved": unicode_unresolved,
            "unresolved": unresolved,
            "requested": {
                "embed_exact_fonts": embed_exact_fonts,
                "add_unicode_maps": add_unicode_maps,
            },
            "review_required": bool(
                embedded or unresolved or unicode_maps or unicode_unresolved
            ),
            "warning": (
                "Only exact, license-permitted TrueType matches were embedded; page content, fields, annotations, and tags were not rewritten. "
                "Review unresolved fonts and validate the result with veraPDF."
            ),
        }
    except PDFAccessibilityError:
        raise
    except Exception as exc:
        raise PDFAccessibilityError(f"Font repair failed: {exc}") from exc
