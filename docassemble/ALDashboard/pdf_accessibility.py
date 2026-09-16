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
        max_output_tokens=min(4000, max(600, len(records) * 60)),
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
    candidates = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
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
        candidates = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
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
            rows = []
            for row_index, row in enumerate(children):
                if _safe_pdf_string(row.get("/S", "")).lstrip("/") != "TR":
                    continue
                row_path = path + [row_index]
                cells = []
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


def _system_truetype_fonts() -> List[Dict[str, Any]]:
    """Inventory embeddable TrueType files known to fontconfig."""
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
        if not path or path in seen or Path(path).suffix.casefold() != ".ttf":
            continue
        seen.add(path)
        try:
            from fontTools.ttLib import TTFont

            font = TTFont(path, lazy=True)
            if "fvar" in font:
                font.close()
                continue
            names: set[str] = set()
            for record in font["name"].names:
                if record.nameID in {1, 2, 4, 6, 16, 17}:
                    try:
                        names.add(record.toUnicode())
                    except Exception:
                        continue
            fs_type = int(font["OS/2"].fsType) if "OS/2" in font else 0
            font.close()
            records.append(
                {
                    "path": path,
                    "names": sorted(names),
                    "canonical_names": {_canonical_font_name(name) for name in names},
                    "embeddable": not bool(fs_type & 0x0002 or fs_type & 0x0200),
                    "fsType": fs_type,
                }
            )
        except Exception:
            continue
    return records


def _font_width_match_score(pdf_font: Any, font_path: str) -> Optional[float]:
    """Compare PDF WinAnsi widths to a candidate TrueType font in 1000-em units."""
    widths = pdf_font.get("/Widths") if hasattr(pdf_font, "get") else None
    if not widths:
        return None
    try:
        first_char = int(pdf_font.get("/FirstChar", 0))
        from fontTools.ttLib import TTFont

        font = TTFont(font_path, lazy=True)
        cmap = font.getBestCmap() or {}
        metrics = font["hmtx"].metrics
        units_per_em = float(font["head"].unitsPerEm)
        differences: List[float] = []
        for offset, pdf_width in enumerate(widths):
            code = first_char + offset
            try:
                character = bytes([code]).decode("cp1252")
            except (UnicodeDecodeError, ValueError):
                continue
            glyph = cmap.get(ord(character))
            if not glyph or glyph not in metrics:
                continue
            candidate_width = metrics[glyph][0] * 1000.0 / units_per_em
            differences.append(abs(float(pdf_width) - candidate_width))
        font.close()
        if len(differences) < 10:
            return None
        differences.sort()
        return differences[len(differences) // 2]
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
    if not tag_summary["present"]:
        for item in semantic_issues:
            item["status"] = "blocked"
            item["remediation"] = "draft_structure"
            item["description"] = (
                "Create or repair the tag tree before this semantic check can run."
            )
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
    issues = [
        _issue(
            "mark-info",
            "6.2.1",
            "Document is marked as tagged",
            0 if _mark_info_marked(root) else 1,
            "catalog_flags",
        ),
        _issue(
            "structure-tree",
            "7.1.11",
            "Logical structure tree exists",
            0 if tag_summary["present"] else 1,
            "draft_structure",
        ),
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
            spec = font_specs.get(text.attrib.get("font", ""), {})
            size = float(spec.get("size", 0) or 0)
            family = str(spec.get("family", ""))
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
                        original_positions = {
                            id(ref): index for index, ref in enumerate(refs)
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

                        page["/Annots"] = pikepdf.Array(
                            sorted(refs, key=annotation_sort_key)
                        )
                        if (
                            set_structure_tab_order
                            and pdf.Root.get("/StructTreeRoot") is not None
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
                        if not hasattr(operand, "get") or operand.get("/MCID") is None:
                            continue
                        try:
                            existing_mcids.append(int(operand.get("/MCID")))
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
                for instruction in instructions:
                    operator = str(instruction.operator)
                    if operator == "BT" and not inside_text:
                        inside_text = True
                        text_block = [instruction]
                        continue
                    if inside_text:
                        text_block.append(instruction)
                        if operator != "ET":
                            continue

                        block_text_parts: List[str] = []
                        for block_instruction in text_block:
                            block_operator = str(block_instruction.operator)
                            operands = list(block_instruction.operands)
                            if block_operator in {"Tj", "'", '"'} and operands:
                                block_text_parts.append(str(operands[-1]))
                            elif block_operator == "TJ" and operands:
                                try:
                                    block_text_parts.extend(
                                        str(item)
                                        for item in operands[0]
                                        if isinstance(item, pikepdf.String)
                                    )
                                except (TypeError, ValueError):
                                    pass
                        block_text = re.sub(
                            r"\s+", " ", "".join(block_text_parts)
                        ).strip()
                        if block_text:
                            mcid = len(mcid_elements)
                            normalized = _normalized_running_text(block_text)
                            tag_name = headings_by_page.get(page_index, {}).get(
                                normalized, "P"
                            )
                            rewritten.append(
                                pikepdf.ContentStreamInstruction(
                                    [
                                        pikepdf.Name(f"/{tag_name}"),
                                        pikepdf.Dictionary({"/MCID": mcid}),
                                    ],
                                    pikepdf.Operator("BDC"),
                                )
                            )
                            rewritten.extend(text_block)
                            rewritten.append(
                                pikepdf.ContentStreamInstruction(
                                    [], pikepdf.Operator("EMC")
                                )
                            )
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
                        else:
                            rewritten.extend(text_block)
                        inside_text = False
                        text_block = []
                        continue
                    rewritten.append(instruction)
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
            pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": True})
            pdf.save(output_pdf_path)
        return {
            "action": "draft_structure",
            "pages_tagged": page_count,
            "text_blocks_tagged": text_block_count,
            "headings_drafted": heading_count,
            "widgets_tagged": widget_count,
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
        embedded: List[Dict[str, str]] = []
        unicode_maps: List[str] = []
        unicode_unresolved: List[Dict[str, str]] = []
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
                    if (
                        _safe_pdf_string(font.get("/Subtype", "")) != "/TrueType"
                        or descriptor is None
                    ):
                        unresolved.append(
                            {
                                "resource": resource,
                                "font": font_name,
                                "reason": "Only exact TrueType matches can be embedded safely.",
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
                                    "reason": "No embeddable installed font matched both the font name and widths.",
                                    "suggested_alternative": suggest_system_font(
                                        font_name
                                    ),
                                    "next_step": "Ask an administrator to install the exact font using the Dashboard font manager.",
                                }
                            )
                        else:
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
                                "reason": "The font is not a simple WinAnsi font, so a trustworthy map cannot be generated from encoding rules alone.",
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
