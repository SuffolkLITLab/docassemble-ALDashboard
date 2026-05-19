import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_IGNORE_ANYWHERE_IN_VAR_NAME = [
    "AL_DEFAULT_COUNTRY",
    "AL_ORGANIZATION_HOMEPAGE",
    "AL_ORGANIZATION_TITLE",
    "al_enable_incomplete_downloads",
    "al_logo",
    "al_menu_items",
    "al_version",
    "al_session_store_default_filename",
    "all_answer_sets",
    "menu_items",
    "._",
    "_attachment",
    "_bundle",
    "court_emails",
    "download_titles",
    "form_approved_for_email_filing",
    "github_user",
    "interview_metadata",
    "interview_short_title",
    "macourts",
    "package_name",
    "package_version_number",
    "preferred_court",
    "signature_fields",
    "speak_text",
    "started_on_phone",
    "user_has_saved_answers",
    "github_repo_name",
    "allow_cron",
    "allowed_courts",
    "_geocoded",
    ".uses_parts",
    ".court_code",
    ".department",
    ".division",
    ".fax",
    ".has_po_box",
    "county_dict",
    "countyinfo",
    "mimetype",
    "_internal",
    "nav",
    "url_args",
    "_class",
    "auto_gather",
    "instanceName",
    "multi_user",
    "file_info",
    "persistent",
    "private",
    "convert_to_pdf_a",
    "convert_to_tagged_pdf",
    "extension",
    "valid_formats",
    "encrypted",
    "has_specific_filename",
    "geolocate_response",
    "norm_long",
    "city_only",
    "geolocated",
    "orig_address",
    "latitude",
    "longitude",
    "norm",
    "geolocate_success",
    "complete_attribute",
]

DEFAULT_IGNORE_IF_IS_KEY = [
    "all_courts",
    "alt_text",
    "ask_number",
    "ask_object_type",
    "object_type",
    "gathered",
    "minimum_number",
]


@dataclass(frozen=True)
class StoryOptions:
    feature_description: str = "Generated docassemble test"
    scenario_description: str = "Generated scenario"
    yaml_file_name: str = "interview.yml"
    question_id: str = "review_screen"
    include_trigger_column: bool = False
    synthesize_target_number: bool = True
    ignore_anywhere_in_var_name: Sequence[str] = tuple(
        DEFAULT_IGNORE_ANYWHERE_IN_VAR_NAME
    )
    ignore_if_is_key: Sequence[str] = tuple(DEFAULT_IGNORE_IF_IS_KEY)


def _format_value(value: Any) -> Any:
    if isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False)[1:-1]
        return re.sub(
            r"(\d\d\d\d)-(\d\d)-(\d\d)T\d\d:\d\d:\d\d-\d\d:\d\d",
            r"\2/\3/\1",
            text,
        )
    return value


def _is_ignored_name(name: str, ignore_anywhere: Sequence[str]) -> bool:
    return any(to_ignore and to_ignore in name for to_ignore in ignore_anywhere)


def _object_name(is_class: bool, name: str, key: str) -> str:
    if not name:
        return key
    if is_class:
        return f"{name}.{key}"
    return f"{name}['{key}']"


def _story_row(
    *,
    name: str,
    value: Any,
    ignore_anywhere: Sequence[str],
    include_trigger_column: bool,
    trigger: str = "",
) -> Optional[str]:
    if _is_ignored_name(name, ignore_anywhere):
        return None

    value = _format_value(value)
    if name.endswith(".filename") and value == "canvas.png":
        name = name[: -len(".filename")]
        value = ""
    if name.endswith(".there_is_another"):
        value = (
            "--- invalid. See docs at "
            "https://suffolklitlab.org/docassemble-AssemblyLine-documentation/docs/"
            "automated_integrated_testing/#there_is_another-loop --- "
        )
    if include_trigger_column:
        return f"| {name} | {value} | {trigger} |"
    return f"| {name} | {value} |"


def _parse_value(name: str, value: Any, options: StoryOptions) -> List[str]:
    if name in options.ignore_if_is_key or _is_ignored_name(
        name, options.ignore_anywhere_in_var_name
    ):
        return []
    if isinstance(value, Mapping):
        return _parse_object(name, value, options)
    if isinstance(value, list):
        return _parse_array(name, value, options)
    if value is None:
        return _single_row(name, "None", options)
    if isinstance(value, bool):
        return _single_row(name, str(value), options)
    if isinstance(value, (str, int, float)):
        return _single_row(name, value, options)
    return _single_row(name, str(value), options)


def _parse_object(
    name: str, value: Mapping[str, Any], options: StoryOptions
) -> List[str]:
    rows: List[str] = []
    class_name = value.get("_class")
    is_class = isinstance(class_name, str) and "DADict" not in class_name
    elements = value.get("elements")

    instance_name = value.get("instanceName")
    if isinstance(instance_name, str) and instance_name and name != instance_name:
        rows.extend(_single_row(name, instance_name, options))

    if isinstance(elements, list) and options.synthesize_target_number:
        rows.extend(_single_row(f"{name}.target_number", len(elements), options))

    if "elements" in value:
        rows.extend(_parse_elements(name, elements, options))

    for key, item in value.items():
        key_text = str(key)
        if key_text == "elements":
            continue
        if (
            options.synthesize_target_number
            and isinstance(elements, list)
            and key_text in {"there_are_any", "there_is_another", "target_number"}
        ):
            continue
        if key_text in options.ignore_if_is_key or _is_ignored_name(
            key_text, options.ignore_anywhere_in_var_name
        ):
            continue
        rows.extend(_parse_value(_object_name(is_class, name, key_text), item, options))
    return rows


def _parse_elements(name: str, value: Any, options: StoryOptions) -> List[str]:
    rows: List[str] = []
    if isinstance(value, Mapping):
        were_checkboxes = False
        any_true = False
        for key, item in value.items():
            if isinstance(item, bool):
                were_checkboxes = True
                any_true = any_true or item
                rows.extend(
                    _single_row(_object_name(False, name, str(key)), str(item), options)
                )
            else:
                rows.extend(
                    _parse_value(_object_name(False, name, str(key)), item, options)
                )
        if were_checkboxes and not any_true:
            rows.extend(_single_row(_object_name(False, name, "None"), "True", options))
        return rows
    return _parse_value(name, value, options)


def _parse_array(name: str, value: Sequence[Any], options: StoryOptions) -> List[str]:
    rows: List[str] = []
    for index, item in enumerate(value):
        rows.extend(_parse_value(f"{name}[{index}]", item, options))
    return rows


def _single_row(name: str, value: Any, options: StoryOptions) -> List[str]:
    if isinstance(value, str) and value in {"True", "False"}:
        formatted_value: Any = value
    elif isinstance(value, str):
        formatted_value = value
    elif isinstance(value, bool):
        formatted_value = str(value)
    else:
        formatted_value = value
    if isinstance(formatted_value, str) and formatted_value.lower() in {
        "true",
        "false",
    }:
        formatted_value = formatted_value[:1].upper() + formatted_value[1:].lower()
    row = _story_row(
        name=name,
        value=formatted_value,
        ignore_anywhere=options.ignore_anywhere_in_var_name,
        include_trigger_column=options.include_trigger_column,
    )
    return [row] if row else []


def rows_from_variables(
    variables: Mapping[str, Any], *, options: Optional[StoryOptions] = None
) -> List[str]:
    story_options = options or StoryOptions()
    all_rows = _parse_value("", variables, story_options)
    rows: List[str] = []
    for row in all_rows:
        if isinstance(row, str) and row not in rows:
            rows.append(row)
    return rows


def default_yaml_file_name(
    data: Mapping[str, Any], fallback: str = "interview.yml"
) -> str:
    interview_path = data.get("i")
    if isinstance(interview_path, str) and interview_path.strip():
        return interview_path.rsplit(":", 1)[-1]
    return fallback


def load_docassemble_json_text(json_text: str) -> Dict[str, Any]:
    """Load docassemble's variables JSON from pasted textarea text.

    Some docassemble textarea submissions can contain literal control
    characters inside string values where the original exported JSON had
    escaped sequences such as ``\r\n``. Python's default JSON parser rejects
    that, while the story generator should still accept the pasted export.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        try:
            data = json.loads(json_text, strict=False)
        except json.JSONDecodeError:
            data = json.loads(
                _escape_likely_unescaped_inner_quotes(json_text),
                strict=False,
            )
    if not isinstance(data, dict):
        raise ValueError("Docassemble JSON must be a JSON object.")
    return data


def _is_escaped(text: str, index: int) -> bool:
    slash_count = 0
    pos = index - 1
    while pos >= 0 and text[pos] == "\\":
        slash_count += 1
        pos -= 1
    return slash_count % 2 == 1


def _next_non_space(text: str, start: int) -> tuple[int, str]:
    pos = start
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    if pos >= len(text):
        return pos, ""
    return pos, text[pos]


def _escape_likely_unescaped_inner_quotes(json_text: str) -> str:
    """Escape quotes that look like text inside a JSON string value.

    This is intentionally a fallback for pasted docassemble exports. It leaves
    normal JSON alone and handles textarea-shaped input where escaped quotes in
    large descriptive strings have become bare quotes.
    """
    output: List[str] = []
    in_string = False
    for index, char in enumerate(json_text):
        if char != '"' or _is_escaped(json_text, index):
            output.append(char)
            continue

        if not in_string:
            in_string = True
            output.append(char)
            continue

        _next_index, next_char = _next_non_space(json_text, index + 1)
        closes_string = next_char in {"", ":", "}", "]"}
        if next_char == ",":
            _after_comma_index, after_comma_char = _next_non_space(
                json_text, _next_index + 1
            )
            closes_string = after_comma_char in {'"', "}", "]"}

        if closes_string:
            in_string = False
            output.append(char)
        else:
            output.append('\\"')
    return "".join(output)


def build_feature_text(rows: Sequence[str], options: StoryOptions) -> str:
    lines = [
        f"Feature: {options.feature_description or options.scenario_description}",
        "",
        f"Scenario: {options.scenario_description}",
        f'  Given I start the interview at "{options.yaml_file_name}"',
        f'  And the user gets to "{options.question_id}" with this data:',
    ]
    if options.include_trigger_column:
        lines.append("    | var | value | trigger |")
    else:
        lines.append("    | var | value |")
    lines.extend(f"    {row}" for row in rows)
    return "\n".join(lines)


def story_from_docassemble_json(
    data: Mapping[str, Any], *, options: Optional[StoryOptions] = None
) -> Dict[str, Any]:
    story_options = options or StoryOptions(yaml_file_name=default_yaml_file_name(data))
    variables = data.get("variables", data)
    if not isinstance(variables, Mapping):
        raise ValueError(
            "Docassemble JSON must be an object or contain variables object."
        )
    rows = rows_from_variables(variables, options=story_options)
    return {
        "rows": rows,
        "feature_text": build_feature_text(rows, story_options),
        "row_count": len(rows),
        "yaml_file_name": story_options.yaml_file_name,
        "question_id": story_options.question_id,
        "feature_description": story_options.feature_description,
        "scenario_description": story_options.scenario_description,
    }
