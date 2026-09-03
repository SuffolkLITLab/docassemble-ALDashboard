import importlib
import json
import os
import re
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

import mako.template
import ruamel.yaml
import textstat
from spellchecker import SpellChecker

try:
    from docassemble.webapp.screenreader import to_text as screenreader_to_text
except Exception:
    def screenreader_to_text(html_text: str) -> str:
        return html_text

try:
    from flask_login import current_user
except Exception:
    current_user = None  # type: ignore

try:
    from docassemble.base.util import path_and_mimetype, log
except Exception:
    path_and_mimetype = None  # type: ignore

    def log(*pargs: Any, **kwargs: Any) -> None:  # type: ignore
        return None


try:
    from docassemble.base.util import user_info
except Exception:
    user_info = None  # type: ignore


def _resolve_current_user_id() -> Optional[int]:
    try:
        if current_user is not None and getattr(
            current_user, "is_authenticated", False
        ):
            uid = getattr(current_user, "id", None)
            if uid is not None:
                return int(uid)
    except Exception:
        pass

    try:
        if user_info is not None:
            uid = getattr(user_info(), "id", None)
            if uid is not None:
                return int(uid)
    except Exception:
        pass

    return None


try:
    from dayamlchecker import (
        RuntimeOptions as _DAYamlRuntimeOptions,
        find_errors_from_string as _dayaml_find_errors_from_string,
    )
except Exception:
    _DAYamlRuntimeOptions = None  # type: ignore
    _dayaml_find_errors_from_string = None  # type: ignore

__all__ = [
    "get_misspelled_words",
    "get_corrections",
    "load_interview",
    "remove_mako",
    "get_all_text",
    "get_user_facing_text",
    "readability_scores",
    "readability_consensus_assessment",
    "lint_interview_content",
    "lint_uploaded_interview",
    "run_deterministic_rules",
    "get_screen_catalog",
    "list_playground_projects",
    "list_playground_yaml_files",
    "lint_multiple_sources",
    "list_lint_modes",
    "normalize_lint_mode",
]


TEXT_SECTIONS = [
    "question",
    "subquestion",
    "under",
    "pre",
    "post",
    "right",
    "note",
    "html",
]
READABILITY_METRICS = [
    ("Flesch Reading Ease", textstat.flesch_reading_ease),
    ("Flesch-Kincaid Grade Level", textstat.flesch_kincaid_grade),
    ("Gunning FOG Scale", textstat.gunning_fog),
    ("SMOG Index", textstat.smog_index),
    ("Automated Readability Index", textstat.automated_readability_index),
    ("Coleman-Liau Index", textstat.coleman_liau_index),
    ("Linsear Write Formula", textstat.linsear_write_formula),
    ("Dale-Chall Readability Score", textstat.dale_chall_readability_score),
    ("Readability Consensus", textstat.text_standard),
]

SEVERITY_ORDER = ["red", "yellow", "green"]
STYLE_GUIDE_URL = "https://assemblyline.suffolklitlab.org/docs/style_guide"
CODING_STYLE_URL = "https://assemblyline.suffolklitlab.org/docs/coding_style"
DEFAULT_LINT_MODE = "full"

DEFINITE_RULE_IDS = {
    "image-missing-alt-text",
    "field-missing-label",
    "blank-choice-label",
    "empty-link-text",
    "table-missing-headers",
    "positive-tabindex",
    "missing-question-id",
    "multiple-mandatory-blocks",
}

DAYAML_LLM_MESSAGE_IDS = {
    "style_tone_and_respect",
    "style_plain_language_rewrite_opportunity",
    "style_llm_configuration_error",
    "style_llm_request_failed",
}


def _stringify(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    return str(item)


def _markdown_to_html(markdown_text: str) -> str:
    """Render markdown when docassemble's optional filter stack is available."""
    for module_name in ("docassemble.base.filter.html", "docassemble.base.filter"):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        converter = getattr(module, "markdown_to_html", None)
        if callable(converter):
            try:
                return _stringify(converter(markdown_text))
            except Exception:
                pass
    return markdown_text


def _shorten(text: Any, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", _stringify(text)).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _anchor_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", _stringify(value).strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "unknown"


def _resolve_source_token(token: str) -> Optional[str]:
    token = _stringify(token).strip()
    if not token:
        return None
    if token.startswith("ref:"):
        if path_and_mimetype is None:
            return None
        try:
            path, _ = path_and_mimetype(token[4:])
            return path
        except Exception:
            return None
    return token


def list_playground_projects() -> List[str]:
    uid = _resolve_current_user_id()
    if uid is None:
        return []
    try:
        from docassemble.webapp.files import SavedFile

        playground = SavedFile(uid, fix=False, section="playground")
        projects = playground.list_of_dirs() or []
        projects = [proj for proj in projects if isinstance(proj, str) and proj]
        if "default" not in projects:
            projects.append("default")
        return sorted(set(projects))
    except Exception as err:
        log(f"interview_linter: unable to list playground projects: {err}")
        return []


def list_playground_yaml_files(project: str = "default") -> List[Dict[str, str]]:
    uid = _resolve_current_user_id()
    if uid is None:
        return []
    try:
        from docassemble.webapp.files import SavedFile
        from docassemble.webapp.backend import directory_for

        area = SavedFile(uid, fix=True, section="playground")
        project_dir = directory_for(area, project or "default")
        if not project_dir or not os.path.isdir(project_dir):
            return []
        output: List[Dict[str, str]] = []
        for filename in sorted(os.listdir(project_dir)):
            full_path = os.path.join(project_dir, filename)
            if os.path.isfile(full_path) and filename.lower().endswith(
                (".yml", ".yaml")
            ):
                output.append({"label": filename, "token": full_path})
        return output
    except Exception as err:
        log(
            f"interview_linter: unable to list playground files for project {project}: {err}"
        )
        return []


def _block_label(doc: dict, fallback: str) -> str:
    return _stringify(doc.get("id")) or _stringify(doc.get("event")) or fallback


def get_misspelled_words(text: str, language: str = "en") -> Set[str]:
    tokens = re.findall(r"\b[\w-]+\b", text)
    filtered_tokens: List[str] = []
    for token in tokens:
        # Ignore invariant-like codes and common acronym/id tokens
        if "_" in token:
            continue
        if token.isupper() and len(token) <= 8:
            continue
        if re.search(r"\d", token):
            continue
        filtered_tokens.append(token)
    if not filtered_tokens:
        return set()

    languages = [lang.strip() for lang in str(language).split(",") if lang.strip()]
    if not languages:
        languages = ["en"]

    unknown_sets: List[Set[str]] = []
    for lang in languages:
        try:
            spell = SpellChecker(language=lang)
            unknown_sets.append(set(spell.unknown(filtered_tokens)))
        except Exception:
            continue

    if not unknown_sets:
        try:
            spell = SpellChecker(language="en")
            return set(spell.unknown(filtered_tokens))
        except Exception:
            return set()

    # Treat as misspelled only if unknown in all selected languages.
    return set.intersection(*unknown_sets)


def get_corrections(
    misspelled: Union[Set[str], List[str]], language: str = "en"
) -> Mapping[str, Set[str]]:
    spell = SpellChecker(language=language)
    corrections_fn = getattr(spell, "corrections", None)
    if callable(corrections_fn):
        return {
            misspelled_word: set(corrections_fn(misspelled_word))
            for misspelled_word in misspelled
        }
    fallback: Dict[str, Set[str]] = {}
    for misspelled_word in misspelled:
        correction = spell.correction(misspelled_word)
        fallback[misspelled_word] = {correction} if correction else set()
    return fallback


def load_interview(content: str) -> List[dict]:
    yaml = ruamel.yaml.YAML(typ="safe")
    content = re.sub(r"\t", "  ", _stringify(content))
    return [doc for doc in yaml.load_all(content) if doc]


def remove_mako(text: str) -> str:
    input_text = _stringify(text)
    if not input_text:
        return ""
    try:
        template = mako.template.Template(input_text)
        markdown_text = template.render()
        html_text = _markdown_to_html(markdown_text)
        return screenreader_to_text(html_text)
    except Exception:
        return input_text


def _extract_choices_text(choices: Any) -> List[str]:
    extracted: List[str] = []
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, str):
                extracted.append(choice)
            elif isinstance(choice, dict):
                extracted.append(_stringify(choice.get("label")))
                extracted.append(_stringify(choice.get("help")))
                for key, val in choice.items():
                    if key not in {"label", "help", "value"}:
                        extracted.append(_stringify(val))
            else:
                extracted.append(_stringify(choice))
    elif isinstance(choices, dict):
        for key, val in choices.items():
            extracted.append(_stringify(key))
            extracted.append(_stringify(val))
    return extracted


def _extract_choice_display_text(choices: Any) -> List[str]:
    """
    Extract only human-facing label text from options, excluding invariant values.
    """
    extracted: List[str] = []
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, str):
                # If someone encoded as "Label: value", keep only the display side.
                if ": " in choice:
                    extracted.append(choice.split(": ", 1)[0])
                else:
                    extracted.append(choice)
            elif isinstance(choice, dict):
                label = _stringify(choice.get("label"))
                if label:
                    extracted.append(label)
                elif len(choice) == 1:
                    extracted.append(_stringify(next(iter(choice.keys()))))
    elif isinstance(choices, dict):
        # Dict form is usually display label -> invariant value
        extracted.extend(_stringify(key) for key in choices.keys())
    return [item for item in extracted if item]


def get_all_text(yaml_parsed: Sequence[dict]) -> List[str]:
    text: List[str] = []
    for doc in yaml_parsed:
        for section in TEXT_SECTIONS:
            text.append(_stringify(doc.get(section)))

        help_section = doc.get("help")
        if isinstance(help_section, dict):
            text.append(_stringify(help_section.get("content")))
            text.append(_stringify(help_section.get("label")))
        else:
            text.append(_stringify(help_section))

        terms_section = doc.get("terms")
        if isinstance(terms_section, dict):
            text.extend(_stringify(definition) for definition in terms_section.values())
        elif isinstance(terms_section, list):
            for term_item in terms_section:
                if isinstance(term_item, dict):
                    text.append(_stringify(term_item.get("definition")))

        if any(doc.get(field_type) for field_type in ["yesno", "noyes"]):
            text.extend(["yes", "no"])
        if any(doc.get(field_type) for field_type in ["yesnomaybe", "noyesmaybe"]):
            text.extend(["yes", "no", "maybe"])

        for field_type in ["choices", "dropdown", "combobox", "buttons"]:
            text.extend(_extract_choices_text(doc.get(field_type)))

        fields_section = doc.get("fields")
        if isinstance(fields_section, dict):
            fields_section = [fields_section]
        if isinstance(fields_section, list):
            for field in fields_section:
                if not isinstance(field, dict) or "code" in field:
                    continue
                text.append(_stringify(field.get("label")))
                text.append(_stringify(field.get("help")))
                text.append(_stringify(field.get("hint")))
                text.append(_stringify(field.get("note")))
                text.append(_stringify(field.get("html")))
                text.extend(_extract_choices_text(field.get("choices")))

    return [item for item in text if item]


def get_user_facing_text(yaml_parsed: Sequence[dict]) -> List[str]:
    """
    Text intended for users. For choices/options, includes only display labels and
    never invariant/internal values.
    """
    text: List[str] = []
    for doc in yaml_parsed:
        for section in TEXT_SECTIONS:
            text.append(_stringify(doc.get(section)))

        help_section = doc.get("help")
        if isinstance(help_section, dict):
            text.append(_stringify(help_section.get("content")))
            text.append(_stringify(help_section.get("label")))
        else:
            text.append(_stringify(help_section))

        terms_section = doc.get("terms")
        if isinstance(terms_section, dict):
            text.extend(_stringify(definition) for definition in terms_section.values())
        elif isinstance(terms_section, list):
            for term_item in terms_section:
                if isinstance(term_item, dict):
                    text.append(_stringify(term_item.get("definition")))

        for field_type in ["choices", "dropdown", "combobox", "buttons"]:
            text.extend(_extract_choice_display_text(doc.get(field_type)))

        fields_section = doc.get("fields")
        if isinstance(fields_section, dict):
            fields_section = [fields_section]
        if isinstance(fields_section, list):
            for field in fields_section:
                if not isinstance(field, dict) or "code" in field:
                    continue
                text.append(_stringify(field.get("label")))
                text.append(_stringify(field.get("help")))
                text.append(_stringify(field.get("hint")))
                text.append(_stringify(field.get("note")))
                text.append(_stringify(field.get("html")))
                if not field.get("label") and field:
                    # Include shorthand field label key, but never variable/value
                    text.append(_stringify(next(iter(field.keys()))))
                text.extend(_extract_choice_display_text(field.get("choices")))

    return [item for item in text if item]


def get_screen_catalog(yaml_parsed: Sequence[dict]) -> List[Dict[str, str]]:
    """
    Build a screen catalog with stable ids and anchor links for report navigation.
    """
    yaml_writer = ruamel.yaml.YAML()
    yaml_writer.default_flow_style = False
    catalog: List[Dict[str, str]] = []
    for idx, doc in enumerate(yaml_parsed):
        if not isinstance(doc, dict):
            continue
        screen_id = _block_label(doc, f"block-{idx}")
        parts: List[str] = []
        for key in ["question", "subquestion", "under", "help", "note", "html"]:
            value = doc.get(key)
            if isinstance(value, dict):
                parts.append(_stringify(value.get("content")))
                parts.append(_stringify(value.get("label")))
            else:
                parts.append(_stringify(value))
        screen_text = "\n\n".join(part for part in parts if part).strip()
        stream = ruamel.yaml.compat.StringIO()
        try:
            yaml_writer.dump(doc, stream)
            yaml_text = stream.getvalue().strip()
        except Exception:
            yaml_text = _stringify(doc).strip()
        catalog.append(
            {
                "screen_id": screen_id,
                "anchor": f"screen-{_anchor_slug(screen_id)}",
                "text": screen_text,
                "yaml_text": yaml_text,
            }
        )
    return catalog


def _attach_screen_links_and_evidence(
    findings: List[Dict[str, Any]], screen_catalog: Sequence[Dict[str, str]]
) -> List[Dict[str, Any]]:
    by_id = {
        item["screen_id"]: item for item in screen_catalog if item.get("screen_id")
    }
    for finding in findings:
        screen_id = _stringify(finding.get("screen_id")).strip()
        if not screen_id:
            continue
        screen = by_id.get(screen_id)
        if not screen:
            continue
        finding["screen_link"] = f"#{screen['anchor']}"
        if not finding.get("problematic_text"):
            finding["problematic_text"] = _shorten(screen.get("text", ""))
    return findings


def _build_screen_payload(
    screen_catalog: Sequence[Dict[str, str]],
    max_screens: int = 40,
    max_chars_per_screen: int = 800,
) -> str:
    trimmed: List[Dict[str, str]] = []
    for screen in list(screen_catalog)[:max_screens]:
        trimmed.append(
            {
                "screen_id": _stringify(screen.get("screen_id")),
                "text": _shorten(
                    remove_mako(_stringify(screen.get("text"))), max_chars_per_screen
                ),
            }
        )
    return json.dumps(trimmed, ensure_ascii=False)


def readability_scores(paragraph: str) -> Dict[str, Union[float, str]]:
    scores: Dict[str, Union[float, str]] = {}
    for name, metric in READABILITY_METRICS:
        try:
            scores[name] = metric(paragraph)
        except Exception:
            scores[name] = "N/A"
    return scores


def readability_consensus_assessment(
    paragraph: str,
) -> Dict[str, Optional[Union[str, int]]]:
    """
    Return readability consensus plus severity guidance:
    - yellow when consensus grade is > 7
    - red when consensus grade is > 10
    """
    try:
        consensus = textstat.text_standard(paragraph)
    except Exception:
        consensus = "N/A"

    grades = [
        int(num)
        for num in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", _stringify(consensus))
    ]
    max_grade = max(grades) if grades else None

    severity: Optional[str] = None
    warning: Optional[str] = None
    if max_grade is not None:
        if max_grade > 10:
            severity = "red"
            warning = "Readability consensus is above 10th grade."
        elif max_grade > 7:
            severity = "yellow"
            warning = "Readability consensus is above 7th grade."

    return {
        "consensus": _stringify(consensus),
        "max_grade": max_grade,
        "severity": severity,
        "warning": warning,
    }


def _dayaml_runtime_options(
    *, include_style: bool = False, include_style_llm: bool = False
) -> Any:
    if _DAYamlRuntimeOptions is None:
        return None
    return _DAYamlRuntimeOptions(
        accessibility_error_on_widgets=frozenset({"combobox"}),
        style_enabled=include_style or include_style_llm,
        style_include_llm=include_style_llm,
    )


def _collect_dayamlchecker_findings(
    content: str,
    *,
    lint_mode: str = "default",
    include_style: bool = False,
    include_style_llm: bool = False,
) -> List[Any]:
    if _dayaml_find_errors_from_string is None:
        return []
    try:
        findings = _dayaml_find_errors_from_string(
            _stringify(content),
            lint_mode=lint_mode,
            runtime_options=_dayaml_runtime_options(
                include_style=include_style, include_style_llm=include_style_llm
            ),
        )
        return findings if isinstance(findings, list) else []
    except Exception as err:
        log(f"interview_linter: dayamlchecker failed: {err}")
        return []


def _run_dayamlchecker(content: str) -> List[str]:
    yaml_message_ids = {
        "yaml_duplicate_key",
        "yaml_parse_error",
        "yaml_string_required",
        "mako_syntax_error",
        "mako_compile_error",
        "field_value_mako_syntax_error",
        "field_value_mako_compile_error",
    }
    return [
        _stringify(getattr(finding, "message", finding)).strip()
        for finding in _collect_dayamlchecker_findings(content)
        if _stringify(getattr(finding, "message_id", "")) in yaml_message_ids
        and _stringify(getattr(finding, "message", finding)).strip()
    ]



RULE_IDS_BY_MODE: Dict[str, List[str]] = {
    "full": [],
    "wcag-basic": [],
}

LINT_MODE_ALIASES: Dict[str, str] = {
    "all": "full",
    "default": "full",
    "full": "full",
    "accessibility": "wcag-basic",
    "wcag": "wcag-basic",
    "wcag-basic": "wcag-basic",
}


def list_lint_modes() -> List[str]:
    return sorted(RULE_IDS_BY_MODE.keys())


def normalize_lint_mode(
    lint_mode: str = DEFAULT_LINT_MODE, strict: bool = False
) -> str:
    normalized = _stringify(lint_mode).strip().lower().replace("_", "-")
    if not normalized:
        return DEFAULT_LINT_MODE
    mode = LINT_MODE_ALIASES.get(normalized)
    if mode:
        return mode
    if strict:
        raise ValueError(
            "Unsupported lint_mode "
            f"`{lint_mode}`. Valid options: {', '.join(list_lint_modes())}."
        )
    return DEFAULT_LINT_MODE



def _finding_confidence(rule_id: str, source: str = "deterministic") -> str:
    if source == "llm":
        return "needs-review"
    return "definite" if rule_id in DEFINITE_RULE_IDS else "needs-review"


def _dayaml_severity_color(severity: Any) -> str:
    value = _stringify(severity).lower()
    if value.endswith("error"):
        return "red"
    if value.endswith("warning"):
        return "yellow"
    return "green"


def _dayaml_finding_class(finding: Any) -> str:
    return _stringify(getattr(finding, "finding_class", "")).lower()


def _line_screen_lookup(
    docs: Sequence[dict], raw_content: str
) -> List[Tuple[int, int, str]]:
    starts = [1]
    for match in re.finditer(r"(?m)^---\s*$", raw_content):
        line = raw_content.count("\n", 0, match.start()) + 1
        if line != 1:
            starts.append(line)
    starts = starts[: len(docs)] or [1]
    total_lines = raw_content.count("\n") + 1
    ranges: List[Tuple[int, int, str]] = []
    for idx, doc in enumerate(docs):
        start = starts[idx] if idx < len(starts) else 1
        end = (starts[idx + 1] - 1) if idx + 1 < len(starts) else total_lines
        ranges.append((start, end, _block_label(doc, f"block-{idx}")))
    return ranges


def _screen_id_for_line(
    line_number: Optional[int], line_lookup: Sequence[Tuple[int, int, str]]
) -> Optional[str]:
    if line_number is None:
        return None
    for start, end, screen_id in line_lookup:
        if start <= line_number <= end:
            return screen_id
    return None


def _dayaml_problematic_text(finding: Any) -> Optional[str]:
    context = getattr(finding, "context", {}) or {}
    for key in (
        "snippet",
        "text",
        "matched_text",
        "field_name",
        "labels",
        "roots",
        "detail",
        "fields",
    ):
        value = context.get(key) if isinstance(context, Mapping) else None
        if value:
            return _shorten(value)
    return None


def _dayaml_rule_id(finding: Any) -> str:
    message_id = _stringify(getattr(finding, "message_id", "")).strip()
    if message_id:
        return message_id.replace("_", "-")
    code = _stringify(getattr(finding, "code", "")).strip()
    if code:
        return code.lower()
    return "dayamlchecker-finding"


def _dayaml_reference_url(finding: Any) -> str:
    context = getattr(finding, "context", {}) or {}
    if isinstance(context, Mapping):
        url = _stringify(context.get("url")).strip()
        if url:
            return url
    finding_class = _dayaml_finding_class(finding)
    if finding_class == "accessibility":
        return f"{CODING_STYLE_URL}/accessibility/"
    if finding_class == "style":
        return STYLE_GUIDE_URL
    return "https://assemblyline.suffolklitlab.org/docs/authoring/yaml/"


def _dayaml_finding_to_dashboard(
    finding: Any,
    *,
    line_lookup: Sequence[Tuple[int, int, str]],
) -> Optional[Dict[str, Any]]:
    context = getattr(finding, "context", {}) or {}
    screen_id = (
        _stringify(context.get("screen_id")).strip()
        if isinstance(context, Mapping)
        else ""
    ) or _screen_id_for_line(getattr(finding, "line_number", None), line_lookup)
    rule_id = _dayaml_rule_id(finding)
    message_id = _stringify(getattr(finding, "message_id", ""))
    source = "llm" if message_id in DAYAML_LLM_MESSAGE_IDS else "dayamlchecker"
    return {
        "rule_id": rule_id,
        "message_id": message_id or None,
        "severity": _dayaml_severity_color(getattr(finding, "severity", "")),
        "message": _stringify(getattr(finding, "message", "")),
        "summary": _stringify(getattr(finding, "summary", "")) or None,
        "url": _dayaml_reference_url(finding),
        "screen_id": screen_id,
        "problematic_text": _dayaml_problematic_text(finding),
        "source": source,
        "confidence": _finding_confidence(rule_id, source=source),
        "line_number": getattr(finding, "line_number", None),
        "code": _stringify(getattr(finding, "code", "")) or None,
        "finding_class": _dayaml_finding_class(finding) or None,
    }


def run_deterministic_rules(
    docs: Sequence[dict],
    interview_texts: Sequence[str],
    raw_content: str,
    lint_mode: str = DEFAULT_LINT_MODE,
    include_llm: bool = False,
) -> List[Dict[str, Any]]:
    resolved_lint_mode = normalize_lint_mode(lint_mode)
    dayaml_findings: List[Any] = []
    if resolved_lint_mode == "wcag-basic":
        dayaml_findings.extend(
            _collect_dayamlchecker_findings(raw_content, lint_mode="accessibility")
        )
    else:
        dayaml_findings.extend(
            _collect_dayamlchecker_findings(
                raw_content,
                lint_mode="default",
                include_style=True,
                include_style_llm=include_llm,
            )
        )
        dayaml_findings.extend(
            _collect_dayamlchecker_findings(raw_content, lint_mode="accessibility")
        )

    line_lookup = _line_screen_lookup(docs, raw_content)
    deduped: List[Dict[str, Any]] = []
    unique: Set[Tuple[str, str, str, str, Optional[str], Optional[str]]] = set()
    for finding in dayaml_findings:
        finding_class = _dayaml_finding_class(finding)
        if resolved_lint_mode == "wcag-basic" and finding_class != "accessibility":
            continue
        if resolved_lint_mode == "full" and finding_class not in {
            "accessibility",
            "style",
            "general",
        }:
            continue
        rendered = _dayaml_finding_to_dashboard(finding, line_lookup=line_lookup)
        if not rendered:
            continue
        key = (
            _stringify(rendered.get("rule_id")),
            _stringify(rendered.get("severity")),
            _stringify(rendered.get("message")),
            _stringify(rendered.get("url")),
            _stringify(rendered.get("screen_id")) or None,
            _stringify(rendered.get("problematic_text")) or None,
        )
        if key in unique:
            continue
        unique.add(key)
        deduped.append(rendered)
    return deduped


def findings_by_severity(
    findings: Sequence[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {
        severity: [] for severity in SEVERITY_ORDER
    }
    for finding in findings:
        sev = _stringify(finding.get("severity")).lower()
        if sev not in grouped:
            grouped[sev] = []
        grouped[sev].append(finding)
    return grouped


def lint_interview_content(
    content: str,
    language: str = "en",
    include_llm: bool = False,
    lint_mode: str = DEFAULT_LINT_MODE,
) -> Dict[str, Any]:
    resolved_lint_mode = normalize_lint_mode(lint_mode)
    yaml_errors = _run_dayamlchecker(content)
    yaml_parsed: List[dict]
    if not yaml_errors:
        try:
            yaml_parsed = load_interview(content)
        except Exception as err:
            yaml_errors = [_stringify(err).strip() or "YAML validation failed."]
    if yaml_errors:
        findings = [
            {
                "rule_id": "yaml-parse-errors",
                "severity": "red",
                "message": "YAML validation failed. Fix these errors before style checks.",
                "url": "https://assemblyline.suffolklitlab.org/docs/authoring/yaml/",
                "screen_id": None,
                "problematic_text": _shorten(error, limit=400),
                "source": "yaml",
                "confidence": "definite",
            }
            for error in yaml_errors
        ]
        return {
            "interview_scores": {"Readability Consensus": "N/A"},
            "readability": {
                "consensus": "N/A",
                "max_grade": None,
                "severity": None,
                "warning": None,
            },
            "yaml_errors": yaml_errors,
            "misspelled": [],
            "headings_warnings": [],
            "style_warnings": [],
            "lint_mode": resolved_lint_mode,
            "interview_texts": [],
            "screen_catalog": [],
            "findings": findings,
            "findings_by_severity": findings_by_severity(findings),
        }

    interview_texts = get_all_text(yaml_parsed)
    user_facing_texts = get_user_facing_text(yaml_parsed)
    screen_catalog = get_screen_catalog(yaml_parsed)
    interview_texts_no_mako = [remove_mako(text) for text in user_facing_texts]
    paragraph = " ".join(text for text in interview_texts_no_mako if text).strip()

    findings = run_deterministic_rules(
        yaml_parsed,
        interview_texts,
        content,
        lint_mode=resolved_lint_mode,
        include_llm=include_llm,
    )
    findings = _attach_screen_links_and_evidence(findings, screen_catalog)

    readability = readability_consensus_assessment(paragraph)

    return {
        "interview_scores": {"Readability Consensus": readability["consensus"]},
        "readability": readability,
        "yaml_errors": [],
        "misspelled": sorted(get_misspelled_words(paragraph, language=language)),
        "headings_warnings": [],
        "style_warnings": [],
        "lint_mode": resolved_lint_mode,
        "interview_texts": interview_texts,
        "screen_catalog": screen_catalog,
        "findings": findings,
        "findings_by_severity": findings_by_severity(findings),
    }


def lint_multiple_sources(
    sources: Sequence[Dict[str, str]],
    language: str = "en",
    include_llm: bool = False,
    lint_mode: str = DEFAULT_LINT_MODE,
) -> List[Dict[str, Any]]:
    """
    Lint multiple source files. Each source item should contain:
    - name: display name
    - token: either absolute path or "ref:<package>:data/questions/file.yml"
    """
    reports: List[Dict[str, Any]] = []
    resolved_lint_mode = normalize_lint_mode(lint_mode)
    for source in sources:
        name = (
            _stringify(source.get("name"))
            or _stringify(source.get("token"))
            or "unknown"
        )
        token = _stringify(source.get("token"))
        path = _resolve_source_token(token)
        if not path or not os.path.exists(path):
            reports.append(
                {
                    "name": name,
                    "token": token,
                    "error": f"Could not resolve file path for {token}",
                    "result": None,
                }
            )
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                result = lint_interview_content(
                    fp.read(),
                    language=language,
                    include_llm=include_llm,
                    lint_mode=resolved_lint_mode,
                )
            reports.append(
                {"name": name, "token": token, "error": None, "result": result}
            )
        except Exception as err:
            reports.append(
                {"name": name, "token": token, "error": str(err), "result": None}
            )
    return reports


def lint_uploaded_interview(
    path: str,
    language: str = "en",
    include_llm: bool = False,
    lint_mode: str = DEFAULT_LINT_MODE,
) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as yaml_file:
        return lint_interview_content(
            yaml_file.read(),
            language=language,
            include_llm=include_llm,
            lint_mode=lint_mode,
        )
