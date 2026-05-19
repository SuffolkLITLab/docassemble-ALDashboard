import ast
import glob
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ruamel.yaml import YAML

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
    "all_reserved_names",
    "all_template_fields",
    "al_interview_languages",
    "al_name_suffixes",
    "al_name_titles",
    "menu_items",
    "._",
    "_attachment",
    "_bundle",
    "available_efile_courts",
    "available_templates",
    "combined_fields",
    "court_emails",
    "document_templates",
    "download_titles",
    "form_approved_for_email_filing",
    "github_user",
    "interview_metadata",
    "interview_short_title",
    "just_bmc_courts",
    "just_district_courts",
    "legalserver_data",
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
    "valid_housing_courts",
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

DEFAULT_IGNORE_IF_TOP_LEVEL_KEY = [
    "DA",
    "DABreadCrumbs",
    "DAGlobal",
    "DAGoogleAPI",
    "DAOAuth",
    "DARedis",
    "DAValidationError",
    "DAWeb",
    "DAWebError",
    "STOP_RENDERING",
    "_attachment_email_address",
    "_attachment_include_editable",
    "_back_one",
    "_checkboxes",
    "_datatypes",
    "_email_attachments",
    "_files",
    "_question_name",
    "_question_number",
    "_save_as",
    "_success",
    "_the_image",
    "_track_location",
    "_tracker",
    "_varnames",
    "action_arguments",
    "background_error_action",
    "chat_partners_available",
    "command",
    "countries_list",
    "device",
    "device_local",
    "dispatch",
    "incoming_email",
    "interface",
    "interview_email",
    "json_response",
    "last_access_days",
    "last_access_delta",
    "last_access_hours",
    "last_access_minutes",
    "last_access_time",
    "location_known",
    "location_returned",
    "logic_explanation",
    "message",
    "multi_user",
    "prevent_going_back",
    "raw",
    "referring_url",
    "returning_user",
    "role",
    "role_event",
    "role_needed",
    "section_links",
    "server_capabilities",
    "session_local",
    "session_tags",
    "start_time",
    "task_not_yet_performed",
    "task_performed",
    "track_location",
    "url_args",
    "user_dict",
    "user_info",
    "user_lat_lon",
    "user_local",
    "user_logged_in",
    "user_privileges",
    "will_send_to_real_court",
]

DEFAULT_IGNORE_IF_IS_KEY = [
    "all_courts",
    "alt_text",
    "ask_number",
    "ask_object_type",
    "object_type",
    "gathered",
    "minimum_number",
    # Imported names used only for Python type annotations can be serialized
    # into docassemble variables, but they are not interview answers.
    "Annotated",
    "Any",
    "Callable",
    "ClassVar",
    "Concatenate",
    "Dict",
    "Fields",
    "Final",
    "ForwardRef",
    "FrozenSet",
    "Generic",
    "Iterable",
    "Iterator",
    "List",
    "Literal",
    "Mapping",
    "MutableMapping",
    "MutableSequence",
    "MutableSet",
    "NewType",
    "NoReturn",
    "NotRequired",
    "Optional",
    "ParamSpec",
    "Protocol",
    "Required",
    "Sequence",
    "Set",
    "Self",
    "Tuple",
    "Type",
    "TypeAlias",
    "TypeGuard",
    "TypeVar",
    "TypedDict",
    "Union",
    "Unpack",
]

DEFAULT_IGNORE_IF_CLASS_NAME_CONTAINS = [
    "docassemble.AssemblyLine.al_document.DALazyAttribute",
    "docassemble.base.util.DACloudStorage",
    "docassemble.base.util.DAFile",
    "docassemble.base.util.DAFileCollection",
    "docassemble.base.util.DAFileList",
    "docassemble.base.util.DALazyTemplate",
    "docassemble.base.util.DAStaticFile",
    "docassemble.base.util.DAStore",
    "S3Backend",
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
    ignore_if_top_level_key: Sequence[str] = tuple(DEFAULT_IGNORE_IF_TOP_LEVEL_KEY)
    ignore_if_is_key: Sequence[str] = tuple(DEFAULT_IGNORE_IF_IS_KEY)
    ignore_if_class_name_contains: Sequence[str] = tuple(
        DEFAULT_IGNORE_IF_CLASS_NAME_CONTAINS
    )


COMMON_AL_PEOPLE_LISTS = {
    "users",
    "other_parties",
    "children",
    "parents",
    "spouses",
    "guardians",
    "caregivers",
    "attorneys",
    "witnesses",
    "translators",
    "interested_parties",
    "decedents",
    "adoptees",
    "creditors",
    "debt_collectors",
    "chiropractors",
    "defendants",
    "respondents",
    "plaintiffs",
    "petitioners",
}

FIELD_NON_VARIABLE_KEYS = {
    "label",
    "datatype",
    "input type",
    "required",
    "required if",
    "show if",
    "hide if",
    "code",
    "default",
    "help",
    "hint",
    "note",
    "html",
    "under text",
    "maxlength",
    "minlength",
    "min",
    "max",
    "step",
    "validation messages",
    "choice variable",
    "choices",
    "none of the above",
    "address autocomplete",
    "disable others",
    "js show if",
    "js hide if",
    "field",
}


DOC_NON_FIELD_KEYS = {
    "id",
    "question",
    "subquestion",
    "event",
    "mandatory",
    "comment",
    "help",
    "under",
    "progress",
    "section",
    "sections",
    "review",
    "attachment",
    "template",
    "content",
    "table",
    "buttons",
    "continue button field",
    "validation code",
    "decorations",
    "decoration",
    "features",
    "metadata",
    "include",
    "modules",
    "objects",
    "code",
    "language",
    "translations",
    "terms",
    "default screen parts",
    "depends on",
    "sets",
    "only sets",
}


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


def _is_ignored_class(value: Mapping[str, Any], ignore_classes: Sequence[str]) -> bool:
    class_name = value.get("_class")
    return isinstance(class_name, str) and any(
        to_ignore and to_ignore in class_name for to_ignore in ignore_classes
    )


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
    if not name and isinstance(value, Mapping):
        rows: List[str] = []
        for key, item in value.items():
            key_text = str(key)
            if key_text in options.ignore_if_top_level_key:
                continue
            rows.extend(_parse_value(key_text, item, options))
        return rows
    if name in options.ignore_if_is_key or _is_ignored_name(
        name, options.ignore_anywhere_in_var_name
    ):
        return []
    if isinstance(value, Mapping):
        if _is_ignored_class(value, options.ignore_if_class_name_contains):
            return []
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
    all_rows: List[str] = []
    for key, item in variables.items():
        key_text = str(key)
        if key_text in story_options.ignore_if_top_level_key:
            continue
        all_rows.extend(_parse_value(key_text, item, story_options))
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


def build_feature_preview_markdown(feature_text: str) -> str:
    """Format feature text for docassemble's markdown preview as a code block."""
    normalized_text = str(feature_text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(
        "    " if line == "" else f"    {line}"
        for line in normalized_text.split("\n")
    )


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
    feature_text = build_feature_text(rows, story_options)
    return {
        "rows": rows,
        "feature_text": feature_text,
        "preview_markdown": build_feature_preview_markdown(feature_text),
        "row_count": len(rows),
        "yaml_file_name": story_options.yaml_file_name,
        "question_id": story_options.question_id,
        "feature_description": story_options.feature_description,
        "scenario_description": story_options.scenario_description,
    }


def _load_yaml_documents(yaml_text: Any) -> List[Mapping[str, Any]]:
    if isinstance(yaml_text, (bytes, bytearray)):
        yaml_text = bytes(yaml_text).decode("utf-8", errors="replace")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = True
    docs = []
    for doc in yaml.load_all(yaml_text):
        if isinstance(doc, Mapping):
            docs.append(doc)
    return docs


def _repo_root_for_path(path: str) -> str:
    current = os.path.abspath(os.path.dirname(path))
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(os.path.dirname(path))
        current = parent


def _iter_include_strings(include_value: Any) -> List[str]:
    if isinstance(include_value, str):
        return [include_value]
    if isinstance(include_value, list):
        return [item for item in include_value if isinstance(item, str)]
    return []


def _resolve_local_include_path(include_ref: str, source_path: str) -> Optional[str]:
    include_ref = str(include_ref or "").strip()
    if not include_ref:
        return None
    source_dir = os.path.dirname(os.path.abspath(source_path))
    if ":" not in include_ref:
        candidate = os.path.abspath(os.path.join(source_dir, include_ref))
        return candidate if os.path.exists(candidate) else None

    package_name, relative_path = include_ref.split(":", 1)
    relative_path = relative_path.strip()
    if not relative_path:
        return None
    package_path = os.path.join(*package_name.split("."))
    home_dir = os.path.expanduser("~")
    repo_root = _repo_root_for_path(source_path)
    search_patterns = [
        os.path.join(repo_root, package_path, relative_path),
        os.path.join(repo_root, package_path, "data", "questions", relative_path),
        os.path.join(home_dir, "docassemble-*", package_path, relative_path),
        os.path.join(
            home_dir, "docassemble-*", package_path, "data", "questions", relative_path
        ),
    ]
    for pattern in search_patterns:
        for candidate in glob.glob(pattern):
            if os.path.exists(candidate):
                return os.path.abspath(candidate)
    return None


def load_docassemble_yaml_text(
    yaml_text: Any,
    *,
    source_path: Optional[str] = None,
    _seen_paths: Optional[set[str]] = None,
) -> List[Mapping[str, Any]]:
    docs = _load_yaml_documents(yaml_text)
    if not source_path or not os.path.exists(source_path):
        return docs

    seen_paths = _seen_paths if _seen_paths is not None else set()
    normalized_source_path = os.path.abspath(source_path)
    if normalized_source_path in seen_paths:
        return docs
    seen_paths.add(normalized_source_path)

    expanded_docs: List[Mapping[str, Any]] = []
    for doc in docs:
        expanded_docs.append(doc)
        for include_ref in _iter_include_strings(doc.get("include")):
            include_path = _resolve_local_include_path(include_ref, normalized_source_path)
            if not include_path or include_path in seen_paths:
                continue
            try:
                with open(include_path, "r", encoding="utf-8") as include_file:
                    expanded_docs.extend(
                        load_docassemble_yaml_text(
                            include_file.read(),
                            source_path=include_path,
                            _seen_paths=seen_paths,
                        )
                    )
            except OSError:
                continue
    return expanded_docs


def _clean_yaml_filename(filename: str, fallback: str = "interview.yml") -> str:
    filename = str(filename or "").strip()
    if not filename:
        return fallback
    if ":" in filename:
        return filename.rsplit(":", 1)[-1].split("/")[-1] or fallback
    return os.path.basename(filename) or fallback


def _looks_like_variable_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or "${" in text or "\n" in text:
        return False
    if text.startswith(("if ", "for ", "while ", "return ")):
        return False
    return bool(
        re.match(
            r"^[A-Za-z_][\w]*(?:\[[^\]\n]+\]|\.[A-Za-z_][\w]*|\[['\"][^'\"]+['\"]\])*$",
            text,
        )
    )


def _default_value_for_field(field_info: Mapping[str, Any]) -> Any:
    if "default" in field_info and field_info.get("default") not in (None, ""):
        value = field_info.get("default")
        if isinstance(value, bool):
            return value
        if isinstance(value, (str, int, float)):
            normalized_value = _normalize_yaml_default_value(value)
            if normalized_value is not None:
                return normalized_value
    datatype = str(field_info.get("datatype") or "").strip().lower()
    input_type = str(field_info.get("input type") or "").strip().lower()
    if datatype in {"yesno", "yesnowide", "truefalse", "boolean"}:
        return True
    if datatype in {"integer", "number", "float", "currency", "range"}:
        return 1
    if datatype == "date":
        return "01/02/2026"
    if input_type in {"checkboxes", "checkbox"}:
        return True
    if input_type in {"email"}:
        return "user@example.com"
    return "Sample answer"


def _field_labels(field_info: Mapping[str, Any]) -> List[str]:
    labels: List[str] = []
    for key, value in field_info.items():
        key_text = str(key).strip()
        if key_text in FIELD_NON_VARIABLE_KEYS or key_text == "field":
            continue
        if isinstance(value, str) and _looks_like_variable_name(value):
            labels.append(key_text)
    return labels


def _example_value_from_field_info(field_info: Mapping[str, Any]) -> Optional[str]:
    for key in ("under text", "hint", "help", "note"):
        value = field_info.get(key)
        if not isinstance(value, str):
            continue
        match = re.search(r"(?:example|e\.g\.)\s*:\s*([A-Za-z0-9 .'-]+)", value, re.I)
        if match:
            return match.group(1).strip().strip(".")
    return None


def _default_value_for_yaml_field(variable: str, field_info: Mapping[str, Any]) -> Any:
    base_value = _default_value_for_field(field_info)
    if base_value != "Sample answer":
        return base_value

    example_value = _example_value_from_field_info(field_info)
    if example_value:
        return example_value

    labels = " ".join(_field_labels(field_info)).lower()
    variable_name = variable.lower()
    if "year" in labels or variable_name.endswith("_year"):
        return "2023"

    min_length = field_info.get("minlength")
    if isinstance(min_length, int) and min_length > 0:
        fill = "1" if "vin" in labels or variable_name == "vin" else "A"
        return fill * min_length

    return _default_value_for_variable_name(variable)


def _normalize_index_placeholders(name: str) -> str:
    return re.sub(r"\[[A-Za-z_][A-Za-z0-9_]*\]", "[0]", name)


def _normalize_yaml_default_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    expression_match = re.fullmatch(r"\$\{\s*(.*?)\s*\}", normalized)
    expression = expression_match.group(1).strip() if expression_match else normalized
    if expression in {"today()", "today"}:
        return "today"
    if expression_match:
        return None
    return value


def _add_unique_row(
    rows: List[str], name: str, value: Any, options: StoryOptions
) -> None:
    normalized_name = _normalize_index_placeholders(name)
    if not _looks_like_variable_name(normalized_name):
        return
    row_items = _single_row(normalized_name, value, options)
    for row in row_items:
        if row not in rows:
            rows.append(row)


def _default_value_for_variable_name(name: str) -> Any:
    if name.endswith(".signature"):
        return "/placeholder_signature.png"
    if name.endswith(".name.title"):
        return "Mr."
    if name.endswith(".name.first"):
        return "Jane"
    if name.endswith(".name.last"):
        return "Smith"
    if name.endswith(".name.suffix"):
        return "Jr."
    if name.endswith(".address.address"):
        return "123 Main St"
    if name.endswith(".address.city"):
        return "Boston"
    if name.endswith(".address.state"):
        return "MA"
    if name.endswith(".address.zip"):
        return "02108"
    if name.endswith(".address.county"):
        return "Suffolk"
    if name.endswith(".address.country"):
        return "US"
    if name.endswith(".address.has_no_address"):
        return False
    if name.endswith(".address.impounded"):
        return False
    if name.endswith(".email"):
        return "user@example.com"
    if name.endswith(".phone_number"):
        return "6175551212"
    if name.endswith(".gender"):
        return "female"
    if name.endswith(".language"):
        return "en"
    if name.endswith(".language_other"):
        return "Spanish"
    if name.endswith(".person_type"):
        return "ALIndividual"
    return "Sample answer"


def _should_skip_heuristic_variable(name: str) -> bool:
    normalized_name = _normalize_index_placeholders(name)
    lower_name = normalized_name.lower()
    return (
        lower_name.startswith("al_")
        or normalized_name.startswith("AL_")
        or lower_name.startswith("share_interview_")
        or lower_name.startswith("tell_a_friend_")
        or lower_name.startswith("trial_court_address.")
        or lower_name.startswith("appeals_court_address.")
        or lower_name.endswith(".there_is_another")
    )


def _add_related_variable_rows(rows: List[str], name: str, options: StoryOptions) -> None:
    normalized_name = _normalize_index_placeholders(name)
    if normalized_name.endswith(".name.first"):
        _add_unique_row(
            rows,
            normalized_name[: -len(".first")] + ".last",
            _default_value_for_variable_name(normalized_name[: -len(".first")] + ".last"),
            options,
        )
    elif normalized_name.endswith(".address.address"):
        base = normalized_name[: -len(".address")]
        for suffix in (".city", ".state", ".zip"):
            field_name = base + suffix
            _add_unique_row(rows, field_name, _default_value_for_variable_name(field_name), options)
    elif normalized_name.endswith(".target_number"):
        list_name = normalized_name[: -len(".target_number")]
        _add_people_list_rows(rows, list_name, options)


def _add_inferred_row(
    rows: List[str],
    name: str,
    value: Any,
    options: StoryOptions,
    *,
    with_related: bool = True,
    overwrite_existing_variable: bool = False,
) -> None:
    normalized_name = _normalize_index_placeholders(name)
    if _should_skip_heuristic_variable(normalized_name):
        return
    if not overwrite_existing_variable:
        prefix = f"| {normalized_name} |"
        if any(row.startswith(prefix) for row in rows):
            return
    resolved_value = value
    if resolved_value in (None, "", "Sample answer"):
        resolved_value = _default_value_for_variable_name(normalized_name)
    _add_unique_row(rows, normalized_name, resolved_value, options)
    if with_related:
        _add_related_variable_rows(rows, normalized_name, options)


def _choice_entries(field_info: Mapping[str, Any]) -> List[Any]:
    choices = field_info.get("choices")
    if isinstance(choices, list):
        entries: List[Any] = []
        for choice in choices:
            if isinstance(choice, Mapping):
                if "value" in choice:
                    entries.append(choice.get("value"))
                elif len(choice) == 1:
                    entries.append(next(iter(choice.values())))
            else:
                entries.append(choice)
        return [item for item in entries if item not in (None, "")]
    return []


def _first_choice_value(field_info: Mapping[str, Any]) -> Optional[Any]:
    for choice in _choice_entries(field_info):
        if isinstance(choice, (str, int, float, bool)):
            return _normalize_yaml_default_value(choice)
    buttons = field_info.get("buttons")
    if isinstance(buttons, list):
        for button in buttons:
            if isinstance(button, Mapping) and button.get("value") is not None:
                value = button.get("value")
                if isinstance(value, (str, int, float, bool)):
                    return value
    return None


def _field_rows(variable: str, field_info: Mapping[str, Any]) -> List[tuple[str, Any]]:
    normalized_variable = _normalize_index_placeholders(variable)
    datatype = str(field_info.get("datatype") or "").strip().lower()
    input_type = str(field_info.get("input type") or "").strip().lower()
    if datatype == "checkboxes" or input_type == "checkboxes":
        checkbox_rows: List[tuple[str, Any]] = []
        choices = _choice_entries(field_info)
        for index, choice in enumerate(choices):
            if isinstance(choice, (str, int, float, bool)):
                checkbox_rows.append(
                    (f"{normalized_variable}['{choice}']", True if index == 0 else False)
                )
        if checkbox_rows:
            return checkbox_rows
    first_choice = _first_choice_value(field_info)
    if first_choice is not None and datatype not in {"yesno", "yesnowide", "truefalse", "boolean"}:
        return [(normalized_variable, first_choice)]
    return [
        (normalized_variable, _default_value_for_yaml_field(normalized_variable, field_info))
    ]


def _field_variable_and_info(field: Any) -> tuple[Optional[str], Dict[str, Any]]:
    if isinstance(field, str):
        return (field.strip(), {})
    if not isinstance(field, Mapping):
        return (None, {})
    field_info = dict(field)
    explicit = field.get("field")
    if _looks_like_variable_name(explicit):
        return (str(explicit).strip(), field_info)
    for key, value in field.items():
        key_text = str(key).strip()
        if key_text in FIELD_NON_VARIABLE_KEYS:
            continue
        if _looks_like_variable_name(value):
            return (str(value).strip(), field_info)
    return (None, field_info)


def _declared_al_people_lists(docs: Sequence[Mapping[str, Any]]) -> set[str]:
    people_lists = set(COMMON_AL_PEOPLE_LISTS)
    people_lists.update(_assemblyline_people_lists_from_checkout())
    for doc in docs:
        objects = doc.get("objects")
        if not isinstance(objects, list):
            continue
        for item in objects:
            if not isinstance(item, Mapping):
                continue
            for name, class_info in item.items():
                if "ALPeopleList" in str(class_info) and _looks_like_variable_name(
                    name
                ):
                    people_lists.add(_normalize_index_placeholders(str(name).strip()))
    return people_lists


def _assemblyline_people_lists_from_checkout() -> set[str]:
    baseline_path = os.path.expanduser(
        "~/docassemble-AssemblyLine/docassemble/AssemblyLine/data/questions/ql_baseline.yml"
    )
    if not os.path.exists(baseline_path):
        return set()
    try:
        with open(baseline_path, "r", encoding="utf-8") as baseline_file:
            docs = load_docassemble_yaml_text(baseline_file.read())
    except Exception:
        return set()
    people_lists: set[str] = set()
    for doc in docs:
        objects = doc.get("objects")
        if not isinstance(objects, list):
            continue
        for item in objects:
            if not isinstance(item, Mapping):
                continue
            for name, class_info in item.items():
                if "ALPeopleList" in str(class_info) and _looks_like_variable_name(
                    name
                ):
                    people_lists.add(_normalize_index_placeholders(str(name).strip()))
    return people_lists


def _code_blocks_from_doc(doc: Mapping[str, Any]) -> List[str]:
    blocks: List[str] = []
    for key in (
        "code",
        "mandatory",
        "initial",
        "validation code",
        "reconsider",
        "depends on",
    ):
        value = doc.get(key)
        if isinstance(value, str):
            blocks.append(value)
    fields = doc.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, Mapping):
                code = field.get("code")
                if isinstance(code, str):
                    blocks.append(code)
    return blocks


def _add_people_list_rows(
    rows: List[str], list_name: str, options: StoryOptions, *, target_number: int = 1
) -> None:
    normalized_list_name = _normalize_index_placeholders(list_name)
    _add_unique_row(rows, f"{normalized_list_name}.target_number", target_number, options)
    _add_unique_row(rows, f"{normalized_list_name}[0].name.first", "Jane", options)
    _add_unique_row(rows, f"{normalized_list_name}[0].name.last", "Smith", options)


def _doc_field_variable_and_info(doc: Mapping[str, Any]) -> tuple[Optional[str], Dict[str, Any]]:
    explicit = doc.get("field")
    if not _looks_like_variable_name(explicit):
        return (None, {})
    field_info = {
        str(key): value for key, value in doc.items() if str(key) not in DOC_NON_FIELD_KEYS
    }
    return (str(explicit).strip(), field_info)


def _set_variables_from_doc(doc: Mapping[str, Any]) -> List[str]:
    variables: List[str] = []
    has_prompt = isinstance(doc.get("question"), str) or isinstance(doc.get("subquestion"), str)
    if not has_prompt:
        return variables
    for key in ("sets", "only sets"):
        raw_value = doc.get(key)
        if isinstance(raw_value, list):
            for item in raw_value:
                if _looks_like_variable_name(item):
                    variables.append(str(item).strip())
        elif _looks_like_variable_name(raw_value):
            variables.append(str(raw_value).strip())
    return variables


def _resolve_code_reference(text: str, aliases: Mapping[str, str]) -> str:
    cleaned = text.strip()
    for alias, replacement in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if cleaned == alias or cleaned.startswith(alias + "."):
            cleaned = replacement + cleaned[len(alias) :]
            break
    return _normalize_index_placeholders(cleaned)


def _parse_simple_python_value(expression: str) -> Optional[Any]:
    expression = expression.strip()
    if not expression:
        return None
    try:
        value = ast.literal_eval(expression)
    except (ValueError, SyntaxError):
        if expression in {"True", "False", "None"}:
            return {"True": True, "False": False, "None": None}[expression]
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list) and value:
        first_value = value[0]
        if isinstance(first_value, (str, int, float, bool)):
            return first_value
    return None


def _parse_simple_python_expression(expression: str) -> Optional[Any]:
    try:
        value = ast.literal_eval(expression)
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _parse_method_call_arguments(arguments_text: str) -> tuple[List[Any], Dict[str, Any]]:
    if not arguments_text.strip():
        return ([], {})
    try:
        parsed = ast.parse(f"_f({arguments_text})", mode="eval")
    except SyntaxError:
        return ([], {})
    if not isinstance(parsed.body, ast.Call):
        return ([], {})
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}
    for arg in parsed.body.args:
        value = _parse_simple_python_expression(ast.unparse(arg))
        if value is not None:
            args.append(value)
    for keyword in parsed.body.keywords:
        if keyword.arg is None:
            continue
        value = _parse_simple_python_expression(ast.unparse(keyword.value))
        if value is not None:
            kwargs[keyword.arg] = value
    return (args, kwargs)


def _al_fields_method_rows(
    object_name: str, method_name: str, arguments_text: str
) -> List[tuple[str, Any]]:
    args, kwargs = _parse_method_call_arguments(arguments_text)
    normalized_object_name = _normalize_index_placeholders(object_name)
    if method_name == "name_fields":
        person_or_business = kwargs.get("person_or_business")
        if person_or_business is None and args:
            person_or_business = args[0]
        show_suffix = kwargs.get("show_suffix", True)
        show_title = kwargs.get("show_title", False)
        if person_or_business == "business":
            return [(f"{normalized_object_name}.name.first", "Acme LLC")]
        rows: List[tuple[str, Any]] = []
        if person_or_business not in {"person", None}:
            rows.append((f"{normalized_object_name}.person_type", "ALIndividual"))
        if show_title:
            rows.append((f"{normalized_object_name}.name.title", "Mr."))
        rows.extend(
            [
                (f"{normalized_object_name}.name.first", "Jane"),
                (f"{normalized_object_name}.name.middle", "Sample answer"),
                (f"{normalized_object_name}.name.last", "Smith"),
            ]
        )
        if show_suffix:
            rows.append((f"{normalized_object_name}.name.suffix", "Jr."))
        return rows
    if method_name == "address_fields":
        address_root = (
            normalized_object_name
            if normalized_object_name.endswith(".address")
            else f"{normalized_object_name}.address"
        )
        rows: List[tuple[str, Any]] = []
        if kwargs.get("allow_no_address") is True:
            rows.append((f"{address_root}.has_no_address", False))
        rows.extend(
            [
                (f"{address_root}.address", "123 Main St"),
                (f"{address_root}.unit", "Sample answer"),
                (f"{address_root}.city", "Boston"),
                (f"{address_root}.state", "MA"),
                (f"{address_root}.zip", "02108"),
            ]
        )
        if kwargs.get("show_county") is True:
            rows.append((f"{address_root}.county", "Suffolk"))
        if kwargs.get("show_country") is True:
            rows.append((f"{address_root}.country", "US"))
        if kwargs.get("ask_if_impounded") is True:
            rows.append((f"{address_root}.impounded", False))
        return rows
    if method_name == "gender_fields":
        return [(f"{normalized_object_name}.gender", "female")]
    if method_name == "language_fields":
        return [(f"{normalized_object_name}.language", "en")]
    if method_name == "pronoun_fields":
        return [(f"{normalized_object_name}.pronouns['he/him/his']", True)]
    if method_name == "contact_fields":
        return []
    return []


def _gathered_people_list_name(reference: str, people_lists: set[str]) -> Optional[str]:
    normalized_reference = _normalize_index_placeholders(reference)
    if normalized_reference in people_lists:
        return normalized_reference
    root_name = normalized_reference.split(".", 1)[0]
    if root_name in people_lists:
        return root_name
    return None


def _add_code_block_rows(
    rows: List[str], code_block: str, people_lists: set[str], options: StoryOptions
) -> None:
    aliases: Dict[str, str] = {}
    for raw_line in code_block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        helper_match = re.match(
            r"([A-Za-z_][\w]*(?:\[[^\]]+\])?(?:\.[A-Za-z_][\w]*)*)\.(name_fields|address_fields|gender_fields|pronoun_fields|language_fields|contact_fields)\((.*)\)\s*$",
            line,
        )
        if helper_match:
            resolved_name = _resolve_code_reference(helper_match.group(1), aliases)
            for row_name, row_value in _al_fields_method_rows(
                resolved_name, helper_match.group(2), helper_match.group(3)
            ):
                _add_inferred_row(
                    rows,
                    row_name,
                    row_value,
                    options,
                    with_related=False,
                    overwrite_existing_variable=True,
                )
            continue

        loop_match = re.match(
            r"for\s+([A-Za-z_][\w]*)\s+in\s+([A-Za-z_][\w]*(?:\[[^\]]+\])?(?:\.[A-Za-z_][\w]*)*)\s*:",
            line,
        )
        if loop_match:
            aliases[loop_match.group(1)] = _resolve_code_reference(
                loop_match.group(2), aliases
            )
            continue

        for match in re.finditer(
            r"\b([A-Za-z_][\w]*(?:\[[^\]]+\])?(?:\.[A-Za-z_][\w]*)*)\.gather\s*\(",
            line,
        ):
            gathered_name = _gathered_people_list_name(
                _resolve_code_reference(match.group(1), aliases), people_lists
            )
            if gathered_name:
                _add_people_list_rows(rows, gathered_name, options)

        if not line.startswith(("if ", "elif ", "while ")):
            assignment_parts = [part.strip() for part in line.split("=")]
            if len(assignment_parts) >= 2:
                assigned_value = _parse_simple_python_value(assignment_parts[-1])
                if assigned_value is not None:
                    for left_side in assignment_parts[:-1]:
                        resolved_name = _resolve_code_reference(left_side, aliases)
                        if _looks_like_variable_name(resolved_name):
                            _add_inferred_row(
                                rows,
                                resolved_name,
                                assigned_value,
                                options,
                            )
                    continue

            resolved_name = _resolve_code_reference(line, aliases)
            if _looks_like_variable_name(resolved_name):
                _add_inferred_row(
                    rows,
                    resolved_name,
                    _default_value_for_variable_name(resolved_name),
                    options,
                )
                continue

        condition_match = re.match(
            r"(?:if|elif)\s+([A-Za-z_][\w]*(?:\[[^\]]+\])?(?:\.[A-Za-z_][\w]*)*)\s*==\s*(.+?)\s*:\s*$",
            line,
        )
        if condition_match:
            resolved_name = _resolve_code_reference(condition_match.group(1), aliases)
            value = _parse_simple_python_value(condition_match.group(2))
            if value is not None:
                _add_inferred_row(rows, resolved_name, value, options)
            continue

        membership_match = re.match(
            r"(?:if|elif)\s+([A-Za-z_][\w]*(?:\[[^\]]+\])?(?:\.[A-Za-z_][\w]*)*)\s+in\s+(.+?)\s*:\s*$",
            line,
        )
        if membership_match:
            resolved_name = _resolve_code_reference(membership_match.group(1), aliases)
            value = _parse_simple_python_value(membership_match.group(2))
            if value is not None:
                _add_inferred_row(rows, resolved_name, value, options)


def rows_from_yaml_heuristics(
    yaml_text: str,
    *,
    options: Optional[StoryOptions] = None,
    source_path: Optional[str] = None,
) -> List[str]:
    story_options = options or StoryOptions()
    primary_docs = _load_yaml_documents(yaml_text)
    docs = load_docassemble_yaml_text(yaml_text, source_path=source_path)
    people_lists = _declared_al_people_lists(docs)
    rows: List[str] = []

    for doc in docs:
        fields = doc.get("fields")
        if isinstance(fields, list):
            for field in fields:
                variable, field_info = _field_variable_and_info(field)
                if variable:
                    for row_name, row_value in _field_rows(variable, field_info):
                        _add_inferred_row(rows, row_name, row_value, story_options)

        single_field_variable, single_field_info = _doc_field_variable_and_info(doc)
        if single_field_variable:
            for row_name, row_value in _field_rows(single_field_variable, single_field_info):
                _add_inferred_row(
                    rows,
                    row_name,
                    row_value,
                    story_options,
                    overwrite_existing_variable=True,
                )

        for set_variable in _set_variables_from_doc(doc):
            _add_inferred_row(
                rows,
                set_variable,
                _default_value_for_variable_name(set_variable),
                story_options,
                overwrite_existing_variable=True,
            )

    for doc in primary_docs:
        continue_button_field = doc.get("continue button field")
        if _looks_like_variable_name(continue_button_field):
            _add_inferred_row(
                rows,
                str(continue_button_field),
                True,
                story_options,
                overwrite_existing_variable=True,
            )

        for code_block in _code_blocks_from_doc(doc):
            _add_code_block_rows(rows, code_block, people_lists, story_options)

    return rows


def detect_yaml_ending_screen(
    yaml_text: str,
    fallback: str = "review_screen",
    *,
    source_path: Optional[str] = None,
) -> str:
    docs = load_docassemble_yaml_text(yaml_text, source_path=source_path)
    final_candidate: Optional[str] = None
    for doc in docs:
        candidate: Optional[str] = None
        screen_id = doc.get("id")
        if isinstance(screen_id, str) and screen_id.strip():
            candidate = screen_id.strip()
        event = doc.get("event")
        if isinstance(event, str) and event.strip():
            normalized = event.strip()
            if normalized not in {"restart", "exit", "logout"}:
                candidate = candidate or normalized
        continue_button_field = doc.get("continue button field")
        if isinstance(continue_button_field, str) and continue_button_field.strip():
            candidate = candidate or continue_button_field.strip()
        if candidate:
            final_candidate = candidate
    return final_candidate or fallback


def story_from_docassemble_yaml(
    yaml_text: str,
    *,
    filename: str = "interview.yml",
    options: Optional[StoryOptions] = None,
) -> Dict[str, Any]:
    yaml_file_name = _clean_yaml_filename(filename)
    if options is None:
        story_options = StoryOptions(
            yaml_file_name=yaml_file_name,
            question_id=detect_yaml_ending_screen(yaml_text, source_path=filename),
        )
    else:
        story_options = options
    rows = rows_from_yaml_heuristics(
        yaml_text,
        options=story_options,
        source_path=filename if os.path.exists(filename) else None,
    )
    feature_text = build_feature_text(rows, story_options)
    return {
        "rows": rows,
        "feature_text": feature_text,
        "preview_markdown": build_feature_preview_markdown(feature_text),
        "row_count": len(rows),
        "yaml_file_name": story_options.yaml_file_name,
        "question_id": story_options.question_id,
        "feature_description": story_options.feature_description,
        "scenario_description": story_options.scenario_description,
        "source_type": "yaml",
    }
