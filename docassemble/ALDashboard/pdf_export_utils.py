import re
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce common serialized boolean values used by the PDF labeler UI.

    Args:
        value: The raw value to interpret.
        default: The fallback value when ``value`` is ``None``.

    Returns:
        bool: The parsed boolean value.

    Raises:
        ValueError: If ``value`` cannot be interpreted as a boolean.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
    raise ValueError(f"Could not parse boolean value {value!r}.")


def _pdf_field_name(raw_name: Any) -> str:
    """Return the submitted field name without normalizing valid characters."""
    if raw_name is None or raw_name == "":
        return "field"
    return str(raw_name)


def deduplicate_pdf_field_names(
    raw_names: Iterable[Any],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Make only exactly repeated PDF field names unique.

    This is the PDF labeler's deduplication contract:

    * A duplicate is a complete, exact string match. Similar names are unrelated.
    * Every unique submitted name stays byte-for-byte unchanged. In particular,
      ``name__1`` and ``name__27`` are valid names, not suffixes to renumber.
    * Keep the first occurrence of a repeated name. Rename only later occurrences
      by appending ``__N`` for an available positive integer.
    * Reserve all original names before generating names. Therefore
      ``["name", "name", "name__1"]`` becomes
      ``["name", "name__2", "name__1"]``; the unique ``name__1`` is untouched.

    Empty or missing names use the existing ``field`` fallback because PDF field
    objects require a usable name.
    """
    names = [_pdf_field_name(name) for name in raw_names]
    original_names = set(names)
    used_names: set[str] = set()
    suffixes: Counter[str] = Counter()
    deduplicated: List[str] = []
    renames: List[Dict[str, Any]] = []

    for index, original_name in enumerate(names):
        if original_name not in used_names:
            candidate = original_name
        else:
            suffix = suffixes[original_name] + 1
            candidate = f"{original_name}__{suffix}"
            while candidate in original_names or candidate in used_names:
                suffix += 1
                candidate = f"{original_name}__{suffix}"
            suffixes[original_name] = suffix
            renames.append(
                {
                    "index": index,
                    "old_name": original_name,
                    "new_name": candidate,
                }
            )
        used_names.add(candidate)
        deduplicated.append(candidate)

    return deduplicated, renames


def build_pdf_preview_fill_data(
    pdf_field_tuples: Iterable[Tuple[Any, ...]],
    *,
    signature_image_path: Optional[str] = None,
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, Dict[str, str]]]]:
    """Build the placeholder values used by ALWeaver's flattened PDF previews.

    This mirrors ``ALWeaver.interview_generator.reflect_fields`` while returning
    the ``data_strings`` and ``images`` collections accepted by docassemble's
    ``fill_template`` helper.
    """
    data_strings: List[Tuple[str, str]] = []
    images: List[Tuple[str, Dict[str, str]]] = []
    mapped_field_names: set[str] = set()

    for field in pdf_field_tuples:
        field_name = str(field[0])
        field_type = str(field[4])
        if field_type in {"/Btn", "/'Btn'"}:
            export_value = str(field[5] if len(field) >= 6 and field[5] else "")
            if export_value.lower() in {"yes", "on", "true", ""}:
                data_strings.append((field_name, "Yes"))
            elif field_name not in mapped_field_names:
                data_strings.append((field_name, export_value))
            mapped_field_names.add(field_name)
        elif field_type in {"/Sig", "/'Sig'"} and signature_image_path:
            images.append((field_name, {"fullpath": signature_image_path}))
            mapped_field_names.add(field_name)
        else:
            data_strings.append((field_name, field_name))
            mapped_field_names.add(field_name)

    return data_strings, images


def _looks_like_single_line_auto_size_field(field_name: Any) -> bool:
    return bool(
        re.search(
            r"(name|address|street|city|state|zip|postal|phone|phone_number|email|cell)",
            str(field_name or ""),
            flags=re.IGNORECASE,
        )
    )


def deduplicate_fields_data(
    fields_data: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return a copy of fields_data with only exact duplicate names suffixed.

    Applies the same ``__N`` suffix strategy used by
    :func:`build_pdf_export_fields_per_page` so that side-channel metadata
    (checkbox export values, background settings, tooltips, field order) can be
    built from the already-deduplicated names.

    Args:
        fields_data: Browser field definitions as submitted by the labeler UI.

    Returns:
        List[Dict[str, Any]]: New list with each field's ``name`` key uniquified.
    """
    names, _renames = deduplicate_pdf_field_names(
        field.get("name", "field") for field in fields_data
    )
    result: List[Dict[str, Any]] = []
    for field, deduped_name in zip(fields_data, names):
        raw_name = _pdf_field_name(field.get("name", "field"))
        if deduped_name != raw_name:
            field = {**field, "name": deduped_name}
        result.append(field)
    return result


def deduplicate_fields_data_with_renames(
    fields_data: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return deduplicated fields and an occurrence-level rename log."""
    names, renames = deduplicate_pdf_field_names(
        field.get("name", "field") for field in fields_data
    )
    result: List[Dict[str, Any]] = []
    for field, deduplicated_name in zip(fields_data, names):
        original_name = _pdf_field_name(field.get("name", "field"))
        result.append(
            {**field, "name": deduplicated_name}
            if deduplicated_name != original_name
            else field
        )
    return result, renames


def _field_value(field: Any, key: str, default: Any = None) -> Any:
    if isinstance(field, dict):
        if key == "name":
            return field.get("name", field.get("var_name", default))
        return field.get(key, default)
    if key == "name":
        return getattr(field, "name", getattr(field, "var_name", default))
    if key == "type":
        return getattr(field, "type", default)
    if key == "fontSize":
        return getattr(field, "font_size", default)
    if key == "font":
        return getattr(field, "font", default)
    if key in {"width", "height"}:
        configs = getattr(field, "configs", None)
        if isinstance(configs, dict) and key in configs:
            return configs[key]
    return getattr(field, key, default)


def _normalize_detected_field_type(raw_type: Any) -> str:
    normalized = str(raw_type or "text").lower()
    normalized = normalized.replace("fieldtype.", "")
    if normalized in {"area", "textarea"}:
        return "multiline"
    if normalized in {"check_box", "checkbox"}:
        return "checkbox"
    if normalized in {"choice", "dropdown", "combo"}:
        return "dropdown"
    if normalized in {"list_box", "listbox"}:
        return "listbox"
    if normalized in {"radio", "signature", "text", "multiline"}:
        return normalized
    return "text"


def build_normalized_pdf_field_definitions(
    detected_fields_per_page: Iterable[Iterable[Any]],
    *,
    page_count: int,
    normalize_font: bool = True,
    font_name: str = "Helvetica",
    normalize_font_size: bool = True,
    font_size_pt: int = 10,
    normalize_checkbox_style: bool = True,
    checkbox_style: str = "cross",
    checkbox_export_value: str = "Yes",
    uniform_checkbox_size: bool = True,
    checkbox_size_pt: int = 12,
    auto_size_name_address: bool = True,
    fixed_text_height_pt: int = 14,
    deduplicate_field_names: bool = False,
) -> List[Dict[str, Any]]:
    """Convert detected FormFyxer fields into normalized export definitions."""
    normalized_fields: List[Dict[str, Any]] = []
    detected_pages = [
        list(page_fields or []) for page_fields in detected_fields_per_page
    ]
    all_raw_names = [
        _pdf_field_name(_field_value(field, "name", "field"))
        for page_fields in detected_pages
        for field in page_fields
    ]
    deduplicated_names, _renames = deduplicate_pdf_field_names(all_raw_names)
    name_index = 0

    for page_idx, page_fields in enumerate(detected_pages):
        if page_idx < 0 or page_idx >= page_count:
            continue
        for field in page_fields or []:
            raw_field_name = _pdf_field_name(_field_value(field, "name", "field"))
            field_name = (
                deduplicated_names[name_index]
                if deduplicate_field_names
                else raw_field_name
            )
            name_index += 1
            field_type_str = _normalize_detected_field_type(
                _field_value(field, "type", "text")
            )
            raw_font_size = _field_value(field, "fontSize", 12)
            auto_size = raw_font_size == 0
            nf: Dict[str, Any] = {
                "name": field_name,
                "type": field_type_str,
                "pageIndex": page_idx,
                "x": float(_field_value(field, "x", 0) or 0),
                "y": float(_field_value(field, "y", 0) or 0),
                "width": float(_field_value(field, "width", 100) or 100),
                "height": float(_field_value(field, "height", 20) or 20),
                "font": (
                    font_name
                    if normalize_font
                    else str(_field_value(field, "font", "Helvetica") or "Helvetica")
                ),
                "fontSize": (
                    font_size_pt if normalize_font_size else int(raw_font_size or 12)
                ),
                "autoSize": False if normalize_font_size else auto_size,
            }

            if field_type_str == "checkbox" and normalize_checkbox_style:
                nf["checkboxStyle"] = checkbox_style
                nf["checkboxExportValue"] = checkbox_export_value
            if field_type_str == "checkbox" and uniform_checkbox_size:
                nf["width"] = checkbox_size_pt
                nf["height"] = checkbox_size_pt
            if (
                not normalize_font_size
                and auto_size_name_address
                and field_type_str == "text"
                and _looks_like_single_line_auto_size_field(field_name)
            ):
                nf["autoSize"] = True
                nf["height"] = fixed_text_height_pt

            normalized_fields.append(nf)

    return normalized_fields


def build_pdf_export_fields_per_page(
    fields_data: Iterable[Dict[str, Any]],
    *,
    page_count: int,
    form_field_cls: Any,
    field_type_enum: Any,
    color_parser: Optional[Callable[[str], Any]] = None,
    deduplicate_field_names: bool = False,
) -> List[List[Any]]:
    """Convert browser field definitions into FormFyxer/ReportLab field objects.

    Args:
        fields_data: Browser field definitions to convert.
        page_count: Number of pages in the target PDF.
        form_field_cls: FormFyxer field class to instantiate.
        field_type_enum: Enum-like container for FormFyxer field type constants.
        color_parser: Optional parser for CSS-like color strings.

    Returns:
        List[List[Any]]: Form field objects grouped by page index.
    """
    fields_per_page: List[List[Any]] = [[] for _ in range(page_count)]
    fields_list = list(fields_data)
    deduplicated_names, _renames = deduplicate_pdf_field_names(
        field_data.get("name", "field") for field_data in fields_list
    )

    for field_index, field_data in enumerate(fields_list):
        page_idx = int(field_data.get("pageIndex", 0))
        if page_idx < 0 or page_idx >= page_count:
            continue

        field_type_str = str(field_data.get("type", "text")).lower()
        if field_type_str in ("text", "multiline"):
            field_type = (
                field_type_enum.TEXT
                if field_type_str == "text"
                else field_type_enum.AREA
            )
        elif field_type_str == "checkbox":
            field_type = field_type_enum.CHECK_BOX
        elif field_type_str == "signature":
            field_type = field_type_enum.SIGNATURE
        elif field_type_str == "radio":
            field_type = field_type_enum.RADIO
        elif field_type_str in ("dropdown", "choice"):
            field_type = field_type_enum.CHOICE
        elif field_type_str == "listbox":
            field_type = field_type_enum.LIST_BOX
        else:
            field_type = field_type_enum.TEXT

        width = float(field_data.get("width", 100))
        height = float(field_data.get("height", 20))
        x_position = float(field_data.get("x", 0))
        y_position = float(field_data.get("y", 0))
        font_name = str(field_data.get("font") or "Helvetica").strip() or "Helvetica"
        auto_size = _parse_bool(field_data.get("autoSize"), default=False)
        allow_scroll = _parse_bool(field_data.get("allowScroll"), default=True)
        font_size_raw = field_data.get("fontSize")
        font_size = 0 if auto_size else int(font_size_raw or 12)
        field_configs: Dict[str, Any] = {}
        field_flag_parts: List[str] = []

        if field_type in (
            field_type_enum.TEXT,
            field_type_enum.AREA,
            field_type_enum.SIGNATURE,
            field_type_enum.CHOICE,
            field_type_enum.LIST_BOX,
        ):
            field_configs["width"] = width
            field_configs["height"] = height

        if field_type in (
            field_type_enum.TEXT,
            field_type_enum.AREA,
            field_type_enum.SIGNATURE,
        ):
            field_configs["fontName"] = font_name
        elif field_type in (field_type_enum.CHOICE, field_type_enum.LIST_BOX):
            field_configs["fontName"] = font_name
            field_configs["fontSize"] = font_size or 12
        elif field_type in (field_type_enum.CHECK_BOX, field_type_enum.RADIO):
            widget_size = max(1.0, min(width, height))
            x_position += max(0.0, (width - widget_size) / 2.0)
            y_position += max(0.0, (height - widget_size) / 2.0)
            field_configs["size"] = widget_size

        if field_type == field_type_enum.AREA:
            field_flag_parts.append("multiline")
        if field_type == field_type_enum.CHOICE:
            field_flag_parts.append("combo")
        if (
            field_type
            in (
                field_type_enum.TEXT,
                field_type_enum.AREA,
                field_type_enum.SIGNATURE,
            )
            and not allow_scroll
        ):
            field_flag_parts.append("doNotScroll")
        if field_flag_parts:
            field_configs["fieldFlags"] = " ".join(field_flag_parts)

        checkbox_style = str(field_data.get("checkboxStyle") or "").strip()
        if checkbox_style and field_type in (
            field_type_enum.CHECK_BOX,
            field_type_enum.RADIO,
        ):
            field_configs["buttonStyle"] = checkbox_style

        background_color = field_data.get("backgroundColor")
        if (
            color_parser
            and isinstance(background_color, str)
            and background_color.strip()
        ):
            try:
                field_configs["fillColor"] = color_parser(background_color.strip())
            except (ValueError, TypeError):
                pass

        # Default to no border.  reportlab uses borderWidth=1 with a near-black
        # borderColor when these keys are absent, producing a solid black outline
        # around every field.  Setting borderWidth=0 suppresses that outline.
        if "borderWidth" not in field_configs:
            field_configs["borderWidth"] = 0

        if field_type in (field_type_enum.CHOICE, field_type_enum.LIST_BOX):
            raw_options = field_data.get("options")
            if isinstance(raw_options, list):
                options = [str(option) for option in raw_options if str(option).strip()]
            else:
                options = []
            if options:
                field_configs["options"] = options
        elif field_type == field_type_enum.RADIO:
            raw_options = field_data.get("options")
            if isinstance(raw_options, list):
                options = [str(option) for option in raw_options if str(option).strip()]
            else:
                options = []
            field_configs["value"] = (
                options[0] if options else str(field_data.get("name", "field"))
            )
            field_configs["selected"] = False

        raw_field_name = _pdf_field_name(field_data.get("name", "field"))
        form_field = form_field_cls(
            field_name=(
                deduplicated_names[field_index]
                if deduplicate_field_names
                else raw_field_name
            ),
            type_name=field_type,
            x=int(round(x_position)),
            y=int(round(y_position)),
            font_size=font_size,
            configs=field_configs,
        )
        # FormFyxer injects broad defaults per type; export should use the exact config
        # determined here instead of carrying those defaults into incompatible widgets.
        form_field.configs = field_configs
        fields_per_page[page_idx].append(form_field)

    return fields_per_page
