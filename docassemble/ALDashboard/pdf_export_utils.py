from typing import Any, Callable, Dict, Iterable, List, Optional


def _parse_bool(value: Any, *, default: bool = False) -> bool:
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


def build_pdf_export_fields_per_page(
    fields_data: Iterable[Dict[str, Any]],
    *,
    page_count: int,
    form_field_cls: Any,
    field_type_enum: Any,
    color_parser: Optional[Callable[[str], Any]] = None,
) -> List[List[Any]]:
    """Convert browser field definitions into FormFyxer/ReportLab field objects."""
    fields_per_page: List[List[Any]] = [[] for _ in range(page_count)]

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
            except Exception:
                pass

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
            field_name=str(field_data.get("name", "field")),
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
