import re
from typing import Any, Callable, Dict, Iterable, List, Optional


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


def _dedupe_pdf_field_name(raw_name: Any, used_names: set[str]) -> str:
    base_name = str(raw_name or "").strip() or "field"
    candidate = base_name
    suffix = 1
    while candidate in used_names:
        candidate = f"{base_name}__{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _looks_like_single_line_auto_size_field(field_name: Any) -> bool:
    return bool(
        re.search(
            r"(name|address|street|city|state|zip|postal|phone|phone_number|email|cell)",
            str(field_name or ""),
            flags=re.IGNORECASE,
        )
    )


def _field_value(field: Any, key: str, default: Any = None) -> Any:
    if isinstance(field, dict):
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
) -> List[Dict[str, Any]]:
    """Convert detected FormFyxer fields into normalized export definitions."""
    normalized_fields: List[Dict[str, Any]] = []
    used_names: set[str] = set()

    for page_idx, page_fields in enumerate(detected_fields_per_page):
        if page_idx < 0 or page_idx >= page_count:
            continue
        for field in page_fields or []:
            field_name = _dedupe_pdf_field_name(_field_value(field, "name", "field"), used_names)
            field_type_str = _normalize_detected_field_type(_field_value(field, "type", "text"))
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
    used_names: set[str] = set()

    for field_data in fields_data:
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

        form_field = form_field_cls(
            field_name=_dedupe_pdf_field_name(
                field_data.get("name", "field"), used_names
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
