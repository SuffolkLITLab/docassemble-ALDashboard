"""Generate docassemble PDF attachment mappings with AssemblyLine conventions."""

import json
import os
import re
from typing import Any, Iterable, List

_PLURAL_PREFIXES = {
    "user": "users",
    "users": "users",
    "other_party": "other_parties",
    "other_parties": "other_parties",
    "child": "children",
    "children": "children",
    "plaintiff": "plaintiffs",
    "plaintiffs": "plaintiffs",
    "defendant": "defendants",
    "defendants": "defendants",
    "petitioner": "petitioners",
    "petitioners": "petitioners",
    "respondent": "respondents",
    "respondents": "respondents",
    "spouse": "spouses",
    "spouses": "spouses",
    "parent": "parents",
    "parents": "parents",
    "guardian": "guardians",
    "guardians": "guardians",
    "attorney": "attorneys",
    "attorneys": "attorneys",
    "witness": "witnesses",
    "witnesses": "witnesses",
    "decedent": "decedents",
    "decedents": "decedents",
}

_SUFFIXES = {
    "_name": "",
    "_name_full": "",
    "_name_first": ".name.first",
    "_name_middle": ".name.middle",
    "_name_last": ".name.last",
    "_name_suffix": ".name.suffix",
    "_birthdate": ".birthdate.format()",
    "_age": ".age_in_years()",
    "_email": ".email",
    "_phone": ".phone_number",
    "_phone_number": ".phone_number",
    "_mobile": ".mobile_number",
    "_mobile_number": ".mobile_number",
    "_signature": ".signature",
    "_address_block": ".address.block()",
    "_address_address": ".address.address",
    "_address_street": ".address.address",
    "_address_unit": ".address.unit",
    "_address_street2": ".address.unit",
    "_address_city": ".address.city",
    "_address_state": ".address.state",
    "_address_zip": ".address.zip",
    "_address_county": ".address.county",
    "_address_country": ".address.country",
    "_address_on_one_line": ".address.on_one_line()",
    "_mailing_address": ".mailing_address",
    "_mailing_address_block": ".mailing_address.block()",
    "_mailing_address_address": ".mailing_address.address",
    "_mailing_address_street": ".mailing_address.address",
    "_mailing_address_unit": ".mailing_address.unit",
    "_mailing_address_street2": ".mailing_address.unit",
    "_mailing_address_city": ".mailing_address.city",
    "_mailing_address_state": ".mailing_address.state",
    "_mailing_address_zip": ".mailing_address.zip",
}


def assembly_line_expression(field_name: Any) -> str:
    """Map a PDF field name to an ALWeaver-style attachment expression."""
    raw_name = str(field_name or "")
    try:
        from docassemble.ALWeaver.interview_generator import (
            map_raw_to_final_display,
        )

        return str(map_raw_to_final_display(raw_name))
    except ImportError:
        # ALDashboard can be installed without ALWeaver. Keep the common
        # AssemblyLine mappings available in that standalone deployment.
        pass

    normalized = re.sub(r"_{2,}\d+", "", raw_name)
    normalized = re.sub(r"\s+", "_", normalized.strip())
    normalized = re.sub(r"[^A-Za-z0-9_]", "", normalized)
    normalized = re.sub(r"^[0-9]+", "", normalized)
    if not normalized:
        return "None"

    for prefix in sorted(_PLURAL_PREFIXES, key=len, reverse=True):
        match = re.fullmatch(rf"{re.escape(prefix)}(\d*)(.*)", normalized)
        if not match:
            continue
        number_text, suffix = match.groups()
        if suffix and suffix not in _SUFFIXES:
            continue
        index = max(int(number_text or "1") - 1, 0)
        return f"{_PLURAL_PREFIXES[prefix]}[{index}]{_SUFFIXES.get(suffix, '')}"

    return normalized


def _double_quoted_yaml(value: Any) -> str:
    """Return Weaver-style double-quoted YAML text."""
    return json.dumps(str(value or ""), ensure_ascii=False)


def _attachment_filename_parts(pdf_filename: Any) -> tuple[str, str, str]:
    template_filename = os.path.basename(str(pdf_filename or "").strip())
    if not template_filename:
        template_filename = "template.pdf"
    if not template_filename.lower().endswith(".pdf"):
        template_filename += ".pdf"
    output_filename = template_filename[:-4]
    display_name = output_filename.replace("_", " ")
    return display_name, output_filename, template_filename


def generate_attachment_block(
    field_names: Iterable[Any],
    *,
    pdf_filename: Any,
) -> str:
    """Return a complete Weaver-style PDF ``attachment`` YAML block."""
    display_name, output_filename, template_filename = _attachment_filename_parts(
        pdf_filename
    )
    lines: List[str] = [
        "---",
        "attachment:",
        f"  name: {display_name}",
        f"  filename: {output_filename}",
        f"  pdf template file: {template_filename}",
        "  fields:",
    ]
    seen: set[str] = set()
    for raw_name in field_names:
        field_name = str(raw_name or "")
        if not field_name or field_name in seen:
            continue
        seen.add(field_name)
        lines.append(
            f"    - {_double_quoted_yaml(field_name)}: "
            f"${{ {assembly_line_expression(field_name)} }}"
        )
    return "\n".join(lines)
