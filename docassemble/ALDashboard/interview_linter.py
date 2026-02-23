import importlib.resources
import json
import os
import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

import mako.runtime
import mako.template
import ruamel.yaml
import textstat  # type: ignore[import-untyped]
from spellchecker import SpellChecker

import docassemble.base.filter
import docassemble.webapp.screenreader

ChatCompletionFn = Callable[..., Union[List[Any], Dict[str, Any], str]]
chat_completion: Optional[ChatCompletionFn]
try:
    from docassemble.ALToolbox.llms import (
        chat_completion as _chat_completion,  # type: ignore[attr-defined]
    )

    chat_completion = _chat_completion
except Exception:
    chat_completion = None

try:
    from docassemble.base.util import DAEmpty
except Exception:
    DAEmpty = str  # type: ignore

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

try:
    from dayamlchecker.yaml_structure import find_errors as _dayaml_find_errors  # type: ignore[import-untyped]
except Exception:
    _dayaml_find_errors = None  # type: ignore

__all__ = [
    "get_misspelled_words",
    "get_corrections",
    "load_interview",
    "remove_mako",
    "get_all_headings",
    "get_heading_width",
    "headings_violations",
    "text_violations",
    "get_all_text",
    "get_user_facing_text",
    "readability_scores",
    "readability_consensus_assessment",
    "lint_interview_content",
    "lint_uploaded_interview",
    "run_deterministic_rules",
    "run_llm_rules",
    "load_llm_prompt_templates",
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
AUTHORING_GUIDE_URL = (
    "https://assemblyline.suffolklitlab.org/docs/authoring/generated_yaml/"
)
PLAIN_LANGUAGE_GUIDE_URL = (
    "https://www.plainlanguage.gov/guidelines/words/use-simple-words-phrases/"
)
METADATA_GUIDE_URL = f"{AUTHORING_GUIDE_URL}#interview-metadata-and-metadata-for-publishing-on-courtformsonline"
WCAG_LABELS_INSTRUCTIONS_URL = (
    "https://www.w3.org/WAI/WCAG21/Understanding/labels-or-instructions.html"
)
WCAG_LINK_PURPOSE_URL = (
    "https://www.w3.org/WAI/WCAG21/Understanding/link-purpose-in-context.html"
)
DEFAULT_LINT_MODE = "full"

FIELD_NON_LABEL_KEYS = {
    "label",
    "field",
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
    "js disable if",
    "list collect",
    "list collect allow delete",
    "rows",
    "columns",
    "grid",
    "table",
}

GENERIC_LINK_TEXT = {
    "click here",
    "go here",
    "here",
    "learn more",
    "link",
    "more",
    "read more",
    "read this",
    "this link",
}

NON_DESCRIPTIVE_FIELD_LABELS = {
    "answer",
    "click here",
    "n/a",
    "na",
    "option",
    "option 1",
    "select",
    "value",
}

AMBIGUOUS_BUTTON_TEXT = {
    "go",
    "ok",
    "submit",
}

COLOR_WORDS = {
    "red",
    "green",
    "yellow",
    "blue",
    "orange",
    "purple",
    "pink",
    "black",
    "white",
    "gray",
    "grey",
}

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


@dataclass(frozen=True)
class LintIssue:
    rule_id: str
    severity: str
    message: str
    url: str
    screen_id: Optional[str] = None
    problematic_text: Optional[str] = None


@dataclass(frozen=True)
class LintRule:
    rule_id: str
    severity: str
    url: str
    check: Callable[[Sequence[dict], Sequence[str], str], List[LintIssue]]


def _stringify(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    return str(item)


def _shorten(text: Any, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", _stringify(text)).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _normalize_human_text(value: Any) -> str:
    plain_text = remove_mako(_stringify(value))
    plain_text = re.sub(r"<[^>]+>", " ", plain_text)
    plain_text = re.sub(r"\s+", " ", plain_text).strip().lower()
    return re.sub(r"[^\w\s]", "", plain_text)


def _looks_like_emoji_or_punctuation_only(value: str) -> bool:
    stripped = _stringify(value).strip()
    if not stripped:
        return False
    # If no alphanumeric characters remain, this is likely punctuation/symbol-only text.
    return not bool(re.search(r"[A-Za-z0-9]", stripped))


def _is_no_label_marker(value: Any) -> bool:
    normalized = _normalize_human_text(value)
    return normalized in {"no label", "nolabel"}


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
    if user_info is None:
        return []
    try:
        from docassemble.webapp.files import SavedFile

        uid = user_info().id
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
    if user_info is None:
        return []
    try:
        from docassemble.webapp.files import SavedFile
        from docassemble.webapp.backend import directory_for

        uid = user_info().id
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


def _iter_doc_texts(doc: dict) -> List[Tuple[str, str]]:
    values: List[Tuple[str, str]] = []
    for key in ["question", "subquestion", "under", "help", "note", "html"]:
        val = doc.get(key)
        if isinstance(val, dict):
            values.append((f"{key}.content", _stringify(val.get("content"))))
            values.append((f"{key}.label", _stringify(val.get("label"))))
        else:
            values.append((key, _stringify(val)))

    fields = doc.get("fields")
    if isinstance(fields, dict):
        fields = [fields]
    if isinstance(fields, list):
        for idx, field in enumerate(fields):
            if not isinstance(field, dict):
                continue
            for field_key in ["label", "help", "hint", "note", "html"]:
                values.append(
                    (f"fields[{idx}].{field_key}", _stringify(field.get(field_key)))
                )
            # first key is often label shorthand, keep it available for checks
            if field:
                first_key = next(iter(field.keys()))
                values.append((f"fields[{idx}].first_key", _stringify(first_key)))
    return [(k, v) for k, v in values if v]


def _coerce_fields(doc: dict) -> List[dict]:
    fields = doc.get("fields")
    if isinstance(fields, dict):
        fields = [fields]
    if not isinstance(fields, list):
        return []
    return [field for field in fields if isinstance(field, dict)]


def _extract_field_variable(field: dict) -> str:
    explicit = _stringify(field.get("field")).strip()
    if explicit:
        return explicit
    for key, value in field.items():
        if key in FIELD_NON_LABEL_KEYS:
            continue
        return _stringify(value).strip()
    return ""


def _extract_field_label(field: dict) -> str:
    explicit = _stringify(field.get("label")).strip()
    if explicit and not _is_no_label_marker(explicit):
        return explicit
    for key in field.keys():
        key_text = _stringify(key).strip()
        if _is_no_label_marker(key_text):
            return ""
        if key_text and key_text not in FIELD_NON_LABEL_KEYS:
            return key_text
    return ""


def _iter_choice_labels(choices: Any) -> List[str]:
    labels: List[str] = []
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, str):
                if ": " in choice:
                    labels.append(choice.split(": ", 1)[0])
                else:
                    labels.append(choice)
            elif isinstance(choice, dict):
                label = _stringify(choice.get("label"))
                if label:
                    labels.append(label)
                elif len(choice) == 1:
                    labels.append(_stringify(next(iter(choice.keys()))))
    elif isinstance(choices, dict):
        labels.extend(_stringify(key) for key in choices.keys())
    return labels


def _is_truthy_yaml_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _stringify(value).strip().lower()
    return normalized in {"true", "yes", "1", "on"}


def _find_metadata(docs: Sequence[dict]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    for doc in docs:
        block = doc.get("metadata")
        if isinstance(block, dict):
            metadata.update(block)
    return metadata


def _iter_include_values(docs: Sequence[dict]) -> List[str]:
    include_values: List[str] = []
    for doc in docs:
        include = doc.get("include")
        if isinstance(include, str):
            include_values.append(include)
        elif isinstance(include, list):
            include_values.extend(
                _stringify(item) for item in include if _stringify(item).strip()
            )
    return include_values


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
        html_text = docassemble.base.filter.markdown_to_html(markdown_text)
        return docassemble.webapp.screenreader.to_text(html_text)
    except Exception:
        return input_text


def get_all_headings(yaml_parsed: Sequence[dict]) -> Dict[str, str]:
    headings: Dict[str, str] = {}
    for doc in yaml_parsed:
        question = _stringify(doc.get("question"))
        if question:
            key = _stringify(doc.get("id")) or f"question: {question}"
            headings[key] = question
    return headings


def get_heading_width(heading_text: str) -> int:
    if not heading_text:
        return 0
    char_widths = {
        "a": 13,
        "b": 14,
        "f": 11,
        "i": 4,
        "j": 7,
        "l": 4,
        "m": 23,
        " ": 9,
        "A": 19,
        "B": 15,
        "F": 13,
        "G": 17,
        "M": 22,
    }
    total_width = 0
    for char in heading_text:
        if char in char_widths:
            total_width += char_widths.get(char, 0)
        elif char.isupper():
            total_width += 18
        elif char.islower():
            total_width += 15
        else:
            total_width += 10
    return total_width


def headings_violations(headings: Mapping[str, str]) -> List[str]:
    violations = []
    stages = [540 * 2, 381 * 2, 290 * 2]
    for key, heading in headings.items():
        heading_width = get_heading_width(heading)
        longer_than_count = sum(heading_width > stage for stage in stages)
        if longer_than_count >= 3:
            violations.append(
                f'Screen `{key}` has a heading that will be multiple lines. You should shorten it: "{heading}"'
            )
    return violations


def text_violations(interview_texts: Sequence[str]) -> List[Tuple[str, str]]:
    base_docs_url = "https://suffolklitlab.org/docassemble-AssemblyLine-documentation/docs/style_guide"
    contractions = (
        "can't",
        "won't",
        "don't",
        "wouldn't",
        "shouldn't",
        "couldn't",
        "y'all",
        "you've",
    )
    idioms = (
        "get the hang of",
        "sit tight",
        "up in the air",
        "on the ball",
        "rule of thumb",
    )
    big_words = {
        "obtain": "get",
        "receive": "get",
        "whether": "if",
        "such as": "like",
        "provide": "give",
        "assist": "help",
    }
    warnings: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for text in interview_texts:
        lower_text = _stringify(text).lower()
        if "/" in lower_text:
            seen.add(
                (
                    'Write out "or" rather than using "/" to separate related concepts.',
                    f"{base_docs_url}/readability#target-reading-level",
                )
            )
        if "please" in lower_text:
            seen.add(('Avoid using "please"', f"{base_docs_url}/respect#please"))
        for contraction in contractions:
            if contraction in lower_text:
                seen.add(
                    (
                        f'Avoid contractions like "{contraction}"',
                        f"{base_docs_url}/readability#avoid-contractions",
                    )
                )
        for idiom in idioms:
            if idiom in lower_text:
                seen.add(
                    (
                        f"Avoid idioms, such as {idiom}",
                        f"{base_docs_url}/readability#avoid-idioms",
                    )
                )
        for big_word, little_word in big_words.items():
            if big_word in lower_text:
                seen.add(
                    (
                        f"Use simple words, such as {little_word}, instead of {big_word}",
                        f"{base_docs_url}/readability#simple-words",
                    )
                )
    warnings.extend(sorted(seen))
    return warnings


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


def _run_dayamlchecker(content: str) -> List[str]:
    if _dayaml_find_errors is None:
        return []
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False, encoding="utf-8"
        ) as temp_file:
            temp_file.write(_stringify(content))
            temp_path = temp_file.name
        errors = _dayaml_find_errors(temp_path)
        if not isinstance(errors, list):
            return []
        return [
            _stringify(error).strip() for error in errors if _stringify(error).strip()
        ]
    except Exception as err:
        log(f"interview_linter: dayamlchecker failed: {err}")
        return []
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def _check_missing_id(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        if doc.get("question") and not doc.get("id"):
            findings.append(
                LintIssue(
                    rule_id="missing-question-id",
                    severity="red",
                    message="Question block is missing an `id`.",
                    url=f"{CODING_STYLE_URL}/yaml_structure/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(doc.get("question")),
                )
            )
    return findings


def _check_multiple_mandatory(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    mandatory_docs = [doc for doc in docs if doc.get("mandatory") is True]
    if len(mandatory_docs) <= 1:
        return []
    labels = [_block_label(doc, "unknown") for doc in mandatory_docs]
    return [
        LintIssue(
            rule_id="multiple-mandatory-blocks",
            severity="red",
            message="Interview has more than one `mandatory: True` block.",
            url=f"{CODING_STYLE_URL}/yaml_structure/",
            problematic_text=", ".join(labels),
        )
    ]


def _check_yesno_shortcuts(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        for key in ["yesno", "noyes", "yesnomaybe", "noyesmaybe"]:
            if key in doc:
                findings.append(
                    LintIssue(
                        rule_id="avoid-yesno-shortcuts",
                        severity="red",
                        message=f"Screen uses `{key}` question shorthand; prefer `fields` with explicit datatypes.",
                        url=f"{CODING_STYLE_URL}/accessibility/",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=f"{key}: {_shorten(doc.get(key))}",
                    )
                )
    return findings


def _check_combobox_usage(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        if "combobox" in doc:
            findings.append(
                LintIssue(
                    rule_id="avoid-combobox",
                    severity="red",
                    message="Screen uses `combobox`; avoid it for accessibility/usability reasons.",
                    url=f"{CODING_STYLE_URL}/accessibility/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(doc.get("combobox")),
                )
            )
        fields = doc.get("fields")
        if isinstance(fields, dict):
            fields = [fields]
        if isinstance(fields, list):
            for field in fields:
                if (
                    isinstance(field, dict)
                    and _stringify(field.get("datatype")).lower() == "combobox"
                ):
                    findings.append(
                        LintIssue(
                            rule_id="avoid-combobox",
                            severity="red",
                            message="Field uses `datatype: combobox`; avoid it for accessibility/usability reasons.",
                            url=f"{CODING_STYLE_URL}/accessibility/",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten(field),
                        )
                    )
    return findings


def _check_subquestion_h1(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        subq = _stringify(doc.get("subquestion"))
        bad_h1 = re.search(r"(?m)^\s*#\s+.*$", subq)
        if bad_h1:
            findings.append(
                LintIssue(
                    rule_id="subquestion-h1",
                    severity="red",
                    message="Subquestion contains an H1 heading (`# ...`); use H2+ inside body content.",
                    url=f"{STYLE_GUIDE_URL}/formatting/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(bad_h1.group(0)),
                )
            )
    return findings


def _check_skipped_heading_levels(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    heading_re = re.compile(r"(?m)^\s*(#{1,6})\s+")
    for idx, doc in enumerate(docs):
        for section_key in ["question", "subquestion", "help", "under"]:
            text = doc.get(section_key)
            if isinstance(text, dict):
                text = text.get("content", "")
            text = _stringify(text)
            levels = [len(match.group(1)) for match in heading_re.finditer(text)]
            for i in range(1, len(levels)):
                if levels[i] > levels[i - 1] + 1:
                    findings.append(
                        LintIssue(
                            rule_id="skipped-heading-level",
                            severity="red",
                            message="Heading levels are skipped (for example H2 to H4).",
                            url=f"{STYLE_GUIDE_URL}/formatting/",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=f"levels {levels[i - 1]} -> {levels[i]} in `{section_key}`",
                        )
                    )
                    break
    return findings


def _check_choices_without_values(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []

    def has_unstable_choices(choices: Any) -> bool:
        if isinstance(choices, list):
            for item in choices:
                if isinstance(item, str):
                    # String choices should provide invariant values, e.g. "Label: value".
                    if ": " not in item:
                        return True
                if isinstance(item, dict):
                    if len(item) == 1 and "label" not in item and "value" not in item:
                        # Shorthand dict form {"Label": "value"} is acceptable.
                        continue
                    if "label" in item and "value" not in item:
                        return True
            return False
        return False

    for idx, doc in enumerate(docs):
        for key in ["choices", "dropdown", "buttons"]:
            if has_unstable_choices(doc.get(key)):
                findings.append(
                    LintIssue(
                        rule_id="choices-without-stable-values",
                        severity="red",
                        message=f"`{key}` includes labels without explicit values.",
                        url=f"{CODING_STYLE_URL}/yaml_interface/",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(doc.get(key)),
                    )
                )
        fields = doc.get("fields")
        if isinstance(fields, dict):
            fields = [fields]
        if isinstance(fields, list):
            for field in fields:
                if isinstance(field, dict) and has_unstable_choices(
                    field.get("choices")
                ):
                    findings.append(
                        LintIssue(
                            rule_id="choices-without-stable-values",
                            severity="red",
                            message="Field `choices` includes labels without explicit values.",
                            url=f"{CODING_STYLE_URL}/yaml_interface/",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten(field.get("choices")),
                        )
                    )
    return findings


def _check_language_en_flag(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        if _stringify(doc.get("language")).strip().lower() == "en":
            findings.append(
                LintIssue(
                    rule_id="remove-language-en",
                    severity="red",
                    message="Block sets `language: en`; remove default-language declarations.",
                    url=f"{CODING_STYLE_URL}/yaml_translation/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=f"language: {_shorten(doc.get('language'))}",
                )
            )
    return findings


def _check_hardcoded_strings_in_code(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    quoted = re.compile(r"(['\"])([^'\"]{20,})\1")
    for idx, doc in enumerate(docs):
        code = _stringify(doc.get("code"))
        if not code:
            continue
        for _, content in quoted.findall(code):
            normalized = content.strip()
            if (
                " " in normalized
                and not normalized.startswith("http")
                and not re.match(r"^[A-Za-z0-9_./:-]+$", normalized)
            ):
                findings.append(
                    LintIssue(
                        rule_id="hardcoded-user-text-in-code",
                        severity="red",
                        message="Code block appears to contain hardcoded user-facing text.",
                        url=f"{CODING_STYLE_URL}/yaml_translation/",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(normalized),
                    )
                )
                break
    return findings


def _check_long_sentences(
    _: Sequence[dict], interview_texts: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    sentence_re = re.compile(r"[^.!?]+[.!?]")
    for text in interview_texts:
        plain = remove_mako(text)
        for sentence in sentence_re.findall(plain):
            if len(re.findall(r"\b\w+\b", sentence)) > 20:
                findings.append(
                    LintIssue(
                        rule_id="long-sentences",
                        severity="yellow",
                        message="Sentence is longer than 20 words.",
                        url=f"{STYLE_GUIDE_URL}/readability/",
                        problematic_text=_shorten(sentence),
                    )
                )
                break
    return findings


def _check_compound_questions(
    _: Sequence[dict], interview_texts: Sequence[str], __: str
) -> List[LintIssue]:
    for text in interview_texts:
        plain = remove_mako(text).lower()
        if "and/or" in plain or re.search(r"\b(or|and)\b", plain) and "?" in plain:
            return [
                LintIssue(
                    rule_id="compound-questions",
                    severity="yellow",
                    message="Potential compound question detected; split into simpler prompts where possible.",
                    url=f"{STYLE_GUIDE_URL}/question_overview/",
                    problematic_text=_shorten(plain),
                )
            ]
    return []


def _check_overlong_labels(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        question = _stringify(doc.get("question"))
        if len(question) > 120:
            findings.append(
                LintIssue(
                    rule_id="overlong-question-label",
                    severity="yellow",
                    message="Question heading is very long.",
                    url=f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(question),
                )
            )
        fields = doc.get("fields")
        if isinstance(fields, dict):
            fields = [fields]
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                field_label = _stringify(field.get("label"))
                if not field_label and field:
                    field_label = _stringify(next(iter(field.keys())))
                if len(field_label) > 90:
                    findings.append(
                        LintIssue(
                            rule_id="overlong-field-label",
                            severity="yellow",
                            message="Field label is very long.",
                            url=f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten(field_label),
                        )
                    )
                    break
    return findings


def _check_too_many_fields(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        fields = doc.get("fields")
        if isinstance(fields, list) and len(fields) > 6:
            findings.append(
                LintIssue(
                    rule_id="too-many-fields-on-screen",
                    severity="yellow",
                    message="Screen has more than 6 fields; consider splitting into smaller screens.",
                    url=f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=f"{len(fields)} fields",
                )
            )
    return findings


def _check_wall_of_text(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        subq = _stringify(doc.get("subquestion"))
        word_count = len(re.findall(r"\b\w+\b", remove_mako(subq)))
        has_structure = bool(
            re.search(r"(?m)^\s*[-*]\s+", subq) or re.search(r"(?m)^\s*#{2,6}\s+", subq)
        )
        if word_count > 120 and not has_structure:
            findings.append(
                LintIssue(
                    rule_id="wall-of-text",
                    severity="yellow",
                    message="Subquestion has a large unstructured block of text.",
                    url=f"{STYLE_GUIDE_URL}/formatting/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(subq),
                )
            )
    return findings


def _check_missing_help_on_complex_screens(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        fields = doc.get("fields")
        if not isinstance(fields, list) or len(fields) < 5:
            continue
        has_help = bool(doc.get("help"))
        for field in fields:
            if isinstance(field, dict) and (
                field.get("help") or field.get("hint") or field.get("note")
            ):
                has_help = True
                break
        if not has_help:
            sample_labels: List[str] = []
            for field in fields:
                if isinstance(field, dict):
                    sample_labels.append(
                        _stringify(field.get("label"))
                        or _stringify(next(iter(field.keys())))
                    )
            findings.append(
                LintIssue(
                    rule_id="complex-screen-missing-help",
                    severity="green",
                    message="Complex screen has no inline help/hint text.",
                    url=f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(
                        ", ".join(label for label in sample_labels if label)
                        or f"{len(fields)} fields"
                    ),
                )
            )
    return findings


def _check_image_alt_text(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    md_image_re = re.compile(r"!\[(.*?)\]\((.*?)\)")
    file_tag_re = re.compile(
        r"\[FILE\s+([^,\]]+)(?:\s*,\s*([^,\]]+))?(?:\s*,\s*([^\]]+))?\]"
    )
    img_tag_re = re.compile(r"<img\b[^>]*>", re.IGNORECASE)

    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for alt_text, image_target in md_image_re.findall(text):
                if not alt_text.strip():
                    findings.append(
                        LintIssue(
                            rule_id="image-missing-alt-text",
                            severity="red",
                            message=f"Image in `{location}` is missing markdown alt text.",
                            url="https://docassemble.org/docs/markup.html#inserting%20images",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten(f"![{alt_text}]({image_target})"),
                        )
                    )
            for file_target, width_value, alt_text in file_tag_re.findall(text):
                if not _stringify(alt_text).strip():
                    findings.append(
                        LintIssue(
                            rule_id="image-missing-alt-text",
                            severity="red",
                            message=f"[FILE ...] image in `{location}` is missing alt text argument.",
                            url="https://docassemble.org/docs/markup.html#inserting%20images",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten(
                                f"[FILE {file_target}, {width_value}]"
                            ),
                        )
                    )
            for img_tag in img_tag_re.findall(text):
                alt_match = re.search(
                    r"\balt\s*=\s*([\"\'])(.*?)\1", img_tag, re.IGNORECASE
                )
                if not alt_match or not alt_match.group(2).strip():
                    findings.append(
                        LintIssue(
                            rule_id="image-missing-alt-text",
                            severity="red",
                            message=f"HTML image in `{location}` is missing `alt` text.",
                            url="https://docassemble.org/docs/markup.html#inserting%20images",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten(img_tag),
                        )
                    )
    return findings


def _check_missing_field_labels(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        for field in _coerce_fields(doc):
            if "code" in field:
                continue
            variable_name = _extract_field_variable(field)
            if not variable_name:
                continue

            label_text = _extract_field_label(field)
            if label_text:
                continue

            findings.append(
                LintIssue(
                    rule_id="field-missing-label",
                    severity="red",
                    message=(
                        "Field appears to collect user input but has no visible label."
                    ),
                    url=WCAG_LABELS_INSTRUCTIONS_URL,
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=f"field variable `{variable_name}`",
                )
            )
    return findings


def _check_non_descriptive_field_labels(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        for field in _coerce_fields(doc):
            label_text = _extract_field_label(field)
            if not label_text:
                continue
            normalized = _normalize_human_text(label_text)
            if (
                normalized in NON_DESCRIPTIVE_FIELD_LABELS
                or _looks_like_emoji_or_punctuation_only(label_text)
            ):
                findings.append(
                    LintIssue(
                        rule_id="non-descriptive-field-label",
                        severity="yellow",
                        message="Field label may be too vague for assistive technology users.",
                        url=WCAG_LABELS_INSTRUCTIONS_URL,
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(label_text),
                    )
                )
    return findings


def _check_choice_label_quality(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []

    def inspect_labels(
        labels: Sequence[str], doc: dict, idx: int, location: str
    ) -> None:
        for label in labels:
            clean = _stringify(label).strip()
            if not clean:
                findings.append(
                    LintIssue(
                        rule_id="blank-choice-label",
                        severity="red",
                        message=f"Choice in `{location}` has an empty label.",
                        url=WCAG_LABELS_INSTRUCTIONS_URL,
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(label),
                    )
                )
                continue

            normalized = _normalize_human_text(clean)
            if (
                normalized in NON_DESCRIPTIVE_FIELD_LABELS
                or _looks_like_emoji_or_punctuation_only(clean)
            ):
                findings.append(
                    LintIssue(
                        rule_id="non-descriptive-choice-label",
                        severity="yellow",
                        message=f"Choice label in `{location}` may be too vague.",
                        url=WCAG_LABELS_INSTRUCTIONS_URL,
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(clean),
                    )
                )

    for idx, doc in enumerate(docs):
        for key in ["choices", "dropdown", "combobox", "buttons"]:
            inspect_labels(_iter_choice_labels(doc.get(key)), doc, idx, key)

        for field_idx, field in enumerate(_coerce_fields(doc)):
            inspect_labels(
                _iter_choice_labels(field.get("choices")),
                doc,
                idx,
                f"fields[{field_idx}].choices",
            )
    return findings


def _check_duplicate_field_labels(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    ignorable_labels = {"no label", "note", "html"}
    for idx, doc in enumerate(docs):
        by_label: Dict[str, Set[str]] = {}
        by_label_conditionals: Dict[str, bool] = {}
        for field in _coerce_fields(doc):
            label_text = _extract_field_label(field)
            variable = _extract_field_variable(field)
            if not label_text or not variable:
                continue
            normalized = _normalize_human_text(label_text)
            if not normalized or normalized in ignorable_labels:
                continue
            by_label.setdefault(normalized, set()).add(variable)
            by_label_conditionals[normalized] = by_label_conditionals.get(
                normalized, False
            ) or bool(field.get("show if") or field.get("js show if"))

        duplicates = []
        for label, variables in by_label.items():
            if len(variables) <= 1:
                continue
            # Conditional duplicates are often intentional (for alternate paths).
            if by_label_conditionals.get(label):
                continue
            duplicates.append(label)
        if duplicates:
            findings.append(
                LintIssue(
                    rule_id="duplicate-field-label",
                    severity="yellow",
                    message="Multiple fields on this screen share the same label text.",
                    url=WCAG_LABELS_INSTRUCTIONS_URL,
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(", ".join(sorted(duplicates))),
                )
            )
    return findings


def _check_empty_screen_title(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        question_text = remove_mako(_stringify(doc.get("question"))).strip()
        if question_text:
            continue

        has_fields = len(_coerce_fields(doc)) > 0
        supplemental = " ".join(
            remove_mako(_stringify(doc.get(key)))
            for key in ["subquestion", "under", "help", "note", "html"]
        ).strip()
        if not has_fields and len(supplemental) < 60:
            continue

        findings.append(
            LintIssue(
                rule_id="missing-screen-title",
                severity="yellow",
                message="Screen appears to have content but no meaningful `question` title.",
                url=f"{STYLE_GUIDE_URL}/question_overview/",
                screen_id=_block_label(doc, f"block-{idx}"),
                problematic_text=_shorten(supplemental or "question is blank"),
            )
        )
    return findings


def _check_color_only_instructions(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    color_pattern = "|".join(sorted(COLOR_WORDS))
    semantic_pattern = (
        r"required|important|correct|wrong|invalid|valid|error|highlighted"
    )
    nearby_pattern = re.compile(
        rf"\b(?:{color_pattern})\b[^.?!\n]{{0,40}}\b(?:{semantic_pattern})\b"
        rf"|\b(?:{semantic_pattern})\b[^.?!\n]{{0,40}}\b(?:{color_pattern})\b",
        re.IGNORECASE,
    )
    symbol_pattern = re.compile(
        r"(✅|❌|✔|✖|⚠)\s*(means|indicates|shows)\b", re.IGNORECASE
    )

    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            plain = remove_mako(text)
            match = nearby_pattern.search(plain) or symbol_pattern.search(plain)
            if not match:
                continue
            findings.append(
                LintIssue(
                    rule_id="color-only-instructions",
                    severity="yellow",
                    message=(
                        f"Text in `{location}` may rely on color/symbols alone to convey meaning."
                    ),
                    url="https://www.w3.org/WAI/WCAG21/Understanding/use-of-color.html",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(match.group(0)),
                )
            )
    return findings


def _check_inline_color_styling(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    style_color_re = re.compile(r"\bstyle\s*=\s*([\"\']).*?color\s*:", re.IGNORECASE)
    font_color_re = re.compile(r"<font\b[^>]*\bcolor\s*=", re.IGNORECASE)
    text_class_re = re.compile(
        r"\bclass\s*=\s*([\"\']).*?\btext-(danger|warning|success|info)\b",
        re.IGNORECASE,
    )

    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            match = (
                style_color_re.search(text)
                or font_color_re.search(text)
                or text_class_re.search(text)
            )
            if not match:
                continue
            findings.append(
                LintIssue(
                    rule_id="inline-color-styling",
                    severity="yellow",
                    message=(
                        f"Text in `{location}` uses inline or semantic color classes; verify contrast and non-color cues."
                    ),
                    url="https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(match.group(0)),
                )
            )
    return findings


def _extract_links_from_text(text: str) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    markdown_link_re = re.compile(r"(?<!!)\[(.*?)\]\((.*?)\)")
    html_link_re = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
    for link_text, target in markdown_link_re.findall(text):
        links.append(
            {
                "kind": "markdown",
                "text": _stringify(link_text),
                "target": _stringify(target),
                "attrs": "",
                "aria_label": "",
                "title": "",
            }
        )
    for attrs, inner in html_link_re.findall(text):
        href_match = re.search(r"\bhref\s*=\s*([\"\'])(.*?)\1", attrs, re.IGNORECASE)
        aria_label_match = re.search(
            r"\baria-label\s*=\s*([\"\'])(.*?)\1", attrs, re.IGNORECASE
        )
        title_match = re.search(r"\btitle\s*=\s*([\"\'])(.*?)\1", attrs, re.IGNORECASE)
        links.append(
            {
                "kind": "html",
                "text": _stringify(inner),
                "target": _stringify(href_match.group(2) if href_match else ""),
                "attrs": _stringify(attrs),
                "aria_label": _stringify(
                    aria_label_match.group(2) if aria_label_match else ""
                ),
                "title": _stringify(title_match.group(2) if title_match else ""),
            }
        )
    return links


def _normalize_link_text(label_text: str) -> str:
    return _normalize_human_text(label_text)


def _check_generic_link_text(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for link in _extract_links_from_text(text):
                link_text = _stringify(link.get("text"))
                target = _stringify(link.get("target"))
                normalized = _normalize_link_text(link_text)
                text_stripped = link_text.strip().lower()
                target_stripped = target.strip().lower()

                looks_like_url = bool(re.match(r"^(https?://|www\.)", text_stripped))
                same_as_target_url = (
                    bool(target_stripped)
                    and _normalize_link_text(link_text) == _normalize_link_text(target)
                    and bool(re.match(r"^(https?://|www\.)", target_stripped))
                )
                if (
                    normalized in GENERIC_LINK_TEXT
                    or looks_like_url
                    or same_as_target_url
                ):
                    findings.append(
                        LintIssue(
                            rule_id="non-descriptive-link-text",
                            severity="yellow",
                            message=(
                                f"Link text in `{location}` is not descriptive enough."
                            ),
                            url=WCAG_LINK_PURPOSE_URL,
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten(link_text or target),
                        )
                    )
    return findings


def _check_empty_link_text(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for link in _extract_links_from_text(text):
                link_text = _stringify(link.get("text")).strip()
                aria_label = _stringify(link.get("aria_label")).strip()
                title = _stringify(link.get("title")).strip()
                normalized = _normalize_link_text(link_text)
                if normalized:
                    continue
                if link.get("kind") == "html" and (aria_label or title):
                    continue
                findings.append(
                    LintIssue(
                        rule_id="empty-link-text",
                        severity="red",
                        message=f"Link in `{location}` has no accessible text.",
                        url=WCAG_LINK_PURPOSE_URL,
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(link.get("target")),
                    )
                )
    return findings


def _check_ambiguous_link_destinations(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        destinations_by_text: Dict[str, Set[str]] = {}
        display_by_text: Dict[str, str] = {}
        for _, text in _iter_doc_texts(doc):
            for link in _extract_links_from_text(text):
                link_text = _stringify(link.get("text")).strip()
                target = _stringify(link.get("target")).strip()
                normalized = _normalize_link_text(link_text)
                if not normalized or not target:
                    continue
                # Skip template-evaluated links where static destination is unknown.
                if "${" in target or "% if" in target or "% for" in target:
                    continue
                canonical_target = re.sub(
                    r"^https?://", "", target, flags=re.IGNORECASE
                )
                canonical_target = canonical_target.rstrip("/")
                destinations_by_text.setdefault(normalized, set()).add(canonical_target)
                display_by_text.setdefault(normalized, link_text)

        for normalized, destinations in destinations_by_text.items():
            if len(destinations) <= 1:
                continue
            findings.append(
                LintIssue(
                    rule_id="ambiguous-link-destinations",
                    severity="yellow",
                    message=(
                        "Same link text points to multiple destinations on one screen."
                    ),
                    url=WCAG_LINK_PURPOSE_URL,
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(
                        f"{display_by_text.get(normalized, normalized)} -> {', '.join(sorted(destinations))}"
                    ),
                )
            )
    return findings


def _check_new_tab_links_without_warning(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    new_tab_re = re.compile(r"\btarget\s*=\s*([\"\'])_blank\1", re.IGNORECASE)
    warning_re = re.compile(r"new (tab|window)|opens in", re.IGNORECASE)
    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for link in _extract_links_from_text(text):
                if link.get("kind") != "html":
                    continue
                attrs = _stringify(link.get("attrs"))
                if not new_tab_re.search(attrs):
                    continue
                warning_text = " ".join(
                    [
                        _stringify(link.get("text")),
                        _stringify(link.get("aria_label")),
                        _stringify(link.get("title")),
                    ]
                )
                if warning_re.search(warning_text):
                    continue
                findings.append(
                    LintIssue(
                        rule_id="opens-new-tab-without-warning",
                        severity="yellow",
                        message=(
                            f"Link in `{location}` opens a new tab/window without warning text."
                        ),
                        url=WCAG_LINK_PURPOSE_URL,
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(link.get("target")),
                    )
                )
    return findings


def _check_svg_accessible_names(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    svg_re = re.compile(r"<svg\b([^>]*)>(.*?)</svg>", re.IGNORECASE | re.DOTALL)
    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for attrs, body in svg_re.findall(text):
                has_aria = bool(
                    re.search(
                        r"\baria-label\b|\baria-labelledby\b", attrs, re.IGNORECASE
                    )
                )
                has_title = bool(
                    re.search(
                        r"<title\b[^>]*>.*?</title>", body, re.IGNORECASE | re.DOTALL
                    )
                )
                if has_aria or has_title:
                    continue
                findings.append(
                    LintIssue(
                        rule_id="svg-missing-accessible-name",
                        severity="yellow",
                        message=f"Inline SVG in `{location}` is missing a title/ARIA label.",
                        url="https://www.w3.org/WAI/WCAG21/Understanding/non-text-content.html",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten("<svg ...>"),
                    )
                )
    return findings


def _check_tables_accessibility(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    table_re = re.compile(r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL)
    row_re = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
    cell_re = re.compile(r"<t[dh]\b", re.IGNORECASE)
    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for table_body in table_re.findall(text):
                has_th = bool(re.search(r"<th\b", table_body, re.IGNORECASE))
                has_caption = bool(re.search(r"<caption\b", table_body, re.IGNORECASE))
                rows = row_re.findall(table_body)
                row_count = len(rows)
                max_cells = max((len(cell_re.findall(row)) for row in rows), default=0)

                if row_count >= 2 and max_cells >= 2 and not has_th:
                    findings.append(
                        LintIssue(
                            rule_id="table-missing-headers",
                            severity="red",
                            message=f"Table in `{location}` appears to be data but has no `<th>` headers.",
                            url="https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships.html",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten("<table ...>"),
                        )
                    )
                elif not has_th and not has_caption:
                    findings.append(
                        LintIssue(
                            rule_id="layout-table-needs-review",
                            severity="yellow",
                            message=(
                                f"Table in `{location}` has no headers/caption; confirm it is not layout-only."
                            ),
                            url="https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships.html",
                            screen_id=_block_label(doc, f"block-{idx}"),
                            problematic_text=_shorten("<table ...>"),
                        )
                    )
    return findings


def _check_positive_tabindex(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    tabindex_re = re.compile(
        r"\btabindex\s*=\s*([\"\'])?\s*([1-9][0-9]*)\s*([\"\'])?",
        re.IGNORECASE,
    )
    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for match in tabindex_re.finditer(text):
                findings.append(
                    LintIssue(
                        rule_id="positive-tabindex",
                        severity="red",
                        message=f"HTML in `{location}` uses `tabindex` greater than 0.",
                        url="https://www.w3.org/WAI/WCAG21/Understanding/focus-order.html",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(match.group(0)),
                    )
                )
    return findings


def _check_clickable_non_controls(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    tag_re = re.compile(r"<(div|span|p|li)\b([^>]*)>", re.IGNORECASE)
    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            for tag_name, attrs in tag_re.findall(text):
                attrs_lower = _stringify(attrs).lower()
                if "onclick" not in attrs_lower:
                    continue
                has_role = bool(
                    re.search(r"\brole\s*=\s*['\"](button|link)['\"]", attrs_lower)
                )
                has_keyboard = any(
                    key in attrs_lower
                    for key in ["onkeydown", "onkeypress", "onkeyup", "tabindex"]
                )
                if has_role and has_keyboard:
                    continue
                findings.append(
                    LintIssue(
                        rule_id="clickable-non-control-html",
                        severity="yellow",
                        message=(
                            f"`<{tag_name}>` in `{location}` uses `onclick` without clear keyboard semantics."
                        ),
                        url="https://www.w3.org/WAI/WCAG21/Understanding/keyboard.html",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(f"<{tag_name}{attrs}>"),
                    )
                )
    return findings


def _check_required_fields_indicated(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        question_text = _stringify(doc.get("question"))
        for field in _coerce_fields(doc):
            if not _is_truthy_yaml_value(field.get("required")):
                continue
            label_text = _extract_field_label(field)
            support_text = " ".join(
                [
                    label_text,
                    _stringify(field.get("help")),
                    _stringify(field.get("hint")),
                    _stringify(field.get("note")),
                    question_text,
                ]
            )
            normalized = _normalize_human_text(support_text)
            if "required" in normalized or "*" in _stringify(label_text):
                continue
            findings.append(
                LintIssue(
                    rule_id="required-field-not-indicated",
                    severity="yellow",
                    message=(
                        "Required field may not clearly indicate that it is required."
                    ),
                    url=WCAG_LABELS_INSTRUCTIONS_URL,
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(
                        label_text or _extract_field_variable(field)
                    ),
                )
            )
    return findings


def _collect_validation_messages(field: dict) -> List[str]:
    messages: List[str] = []
    val = field.get("validation messages")
    if isinstance(val, dict):
        messages.extend(_stringify(item) for item in val.values())
    elif isinstance(val, list):
        messages.extend(_stringify(item) for item in val)
    else:
        messages.append(_stringify(val))
    return [msg for msg in messages if _stringify(msg).strip()]


def _check_validation_guidance(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    constraint_keys = {
        "validation code",
        "pattern",
        "regex",
    }
    for idx, doc in enumerate(docs):
        for field in _coerce_fields(doc):
            present_constraints = {key for key in constraint_keys if key in field}
            has_constraints = bool(present_constraints)
            validation_messages = _collect_validation_messages(field)
            has_constraints = has_constraints or bool(validation_messages)
            if not has_constraints:
                continue

            has_guidance = bool(
                _stringify(field.get("help")).strip()
                or _stringify(field.get("hint")).strip()
                or _stringify(field.get("note")).strip()
                or validation_messages
            )
            if has_guidance:
                continue

            findings.append(
                LintIssue(
                    rule_id="validation-without-guidance",
                    severity="yellow",
                    message=(
                        "Field has validation constraints but no hint/help or validation message."
                    ),
                    url="https://www.w3.org/WAI/WCAG21/Understanding/error-identification.html",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(
                        _extract_field_label(field) or _extract_field_variable(field)
                    ),
                )
            )
    return findings


def _check_generic_validation_messages(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    generic_messages = {
        "error",
        "invalid",
        "invalid input",
        "invalid value",
        "not valid",
    }
    for idx, doc in enumerate(docs):
        for field in _coerce_fields(doc):
            for message in _collect_validation_messages(field):
                normalized = _normalize_human_text(message)
                if normalized not in generic_messages:
                    continue
                findings.append(
                    LintIssue(
                        rule_id="generic-validation-message",
                        severity="yellow",
                        message="Validation message may be too generic to help users recover.",
                        url="https://www.w3.org/WAI/WCAG21/Understanding/error-suggestion.html",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(message),
                    )
                )
    return findings


def _check_ambiguous_button_text(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        labels = _iter_choice_labels(doc.get("buttons"))
        labels.append(_stringify(doc.get("continue button label")))
        for field in _coerce_fields(doc):
            datatype = _stringify(field.get("datatype")).strip().lower()
            if datatype in {"button", "buttons"}:
                labels.extend(_iter_choice_labels(field.get("choices")))

        for label in labels:
            normalized = _normalize_human_text(label)
            if normalized not in AMBIGUOUS_BUTTON_TEXT:
                continue
            findings.append(
                LintIssue(
                    rule_id="ambiguous-button-text",
                    severity="yellow",
                    message="Button text may be too vague out of context.",
                    url=WCAG_LINK_PURPOSE_URL,
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(label),
                )
            )
    return findings


def _check_metadata_fields(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    metadata = _find_metadata(docs)
    required_fields = [
        "title",
        "short title",
        "description",
        "LIST_topics",
        "jurisdiction",
        "landing_page_url",
    ]
    missing = [field for field in required_fields if not metadata.get(field)]
    findings: List[LintIssue] = []
    if missing:
        findings.append(
            LintIssue(
                rule_id="missing-metadata-fields",
                severity="red",
                message="Metadata block is missing key CourtFormsOnline/AssemblyLine fields.",
                url=METADATA_GUIDE_URL,
                problematic_text=", ".join(missing),
            )
        )
    return findings


def _check_placeholder_language(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    placeholder_patterns = [
        re.compile(r"\bplaceholder\b", re.IGNORECASE),
        re.compile(r"\blorem ipsum\b", re.IGNORECASE),
        re.compile(r"\btodo\b", re.IGNORECASE),
        re.compile(r"\btbd\b", re.IGNORECASE),
        re.compile(r"\bto be determined\b", re.IGNORECASE),
        re.compile(r"\bcoming soon\b", re.IGNORECASE),
        re.compile(r"\[insert[^\]]*\]", re.IGNORECASE),
        re.compile(r"\byour text here\b", re.IGNORECASE),
    ]

    for idx, doc in enumerate(docs):
        for location, text in _iter_doc_texts(doc):
            plain = remove_mako(text)
            for pattern in placeholder_patterns:
                match = pattern.search(plain)
                if not match:
                    continue
                findings.append(
                    LintIssue(
                        rule_id="placeholder-language",
                        severity="yellow",
                        message=f"Possible placeholder language found in `{location}`.",
                        url=f"{STYLE_GUIDE_URL}/question_overview/",
                        screen_id=_block_label(doc, f"block-{idx}"),
                        problematic_text=_shorten(match.group(0)),
                    )
                )
                break
    return findings


def _check_exit_criteria_and_screen(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    metadata = _find_metadata(docs)
    screening_signal = bool(_stringify(metadata.get("can_I_use_this_form")).strip())
    for doc in docs:
        combined = " ".join(
            [
                _stringify(doc.get("question")),
                _stringify(doc.get("subquestion")),
                _stringify(doc.get("id")),
                _stringify(doc.get("event")),
            ]
        ).lower()
        if any(
            marker in combined
            for marker in [
                "can i use",
                "eligible",
                "qualify",
                "right form",
                "wrong form",
            ]
        ):
            screening_signal = True
            break

    if not screening_signal:
        return []

    exit_signal = False
    for doc in docs:
        combined = " ".join(
            [
                _stringify(doc.get("question")),
                _stringify(doc.get("subquestion")),
                _stringify(doc.get("under")),
                _stringify(doc.get("id")),
                _stringify(doc.get("event")),
            ]
        ).lower()
        if any(
            marker in combined
            for marker in [
                "not eligible",
                "may not be able",
                "cannot help",
                "can't help",
                "wrong form",
                "stop here",
                "exit",
            ]
        ):
            exit_signal = True
            break

    if exit_signal:
        return []
    return [
        LintIssue(
            rule_id="missing-exit-criteria-screen",
            severity="green",
            message="Interview appears to screen for eligibility but no clear ineligible/exit screen was detected.",
            url=f"{STYLE_GUIDE_URL}/question_overview/",
            problematic_text="Add clear criteria and a screen for users this tool cannot help.",
        )
    ]


def _check_theme_usage(
    docs: Sequence[dict], _: Sequence[str], raw_content: str
) -> List[LintIssue]:
    include_values = [item.lower() for item in _iter_include_values(docs)]
    has_theme_include = any(
        "docassemble.massaccess" in item
        or "docassemble.litlabtheme" in item
        or "theme" in item
        for item in include_values
    )
    has_css_reference = bool(
        re.search(r"docassemble\.[A-Za-z0-9_]+:data/static/[^\s\"']+\.css", raw_content)
        or re.search(r"(?m)^\s*css\s*:\s*$", raw_content)
    )
    if has_theme_include or has_css_reference:
        return []
    return [
        LintIssue(
            rule_id="missing-custom-theme",
            severity="yellow",
            message="No explicit custom theme/CSS dependency detected (for example MassAccess or LITLabTheme).",
            url="https://assemblyline.suffolklitlab.org/docs/customizing-look-and-feel/",
        )
    ]


def _extract_decision_variables(docs: Sequence[dict]) -> Set[str]:
    decision_datatypes = {
        "yesno",
        "noyes",
        "yesnoradio",
        "noyesradio",
        "yesnowide",
        "radio",
        "dropdown",
    }
    decision_vars: Set[str] = set()
    for doc in docs:
        for field in _coerce_fields(doc):
            datatype = _stringify(field.get("datatype")).lower().strip()
            if datatype in decision_datatypes or field.get("choices"):
                var = _extract_field_variable(field)
                if var:
                    decision_vars.add(var)
    return decision_vars


def _check_review_screen_editability(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    review_docs: List[dict] = []
    for doc in docs:
        text = " ".join(
            [
                _stringify(doc.get("id")),
                _stringify(doc.get("event")),
                _stringify(doc.get("question")),
            ]
        ).lower()
        if "review" in text or "review" in doc:
            review_docs.append(doc)

    if not review_docs:
        return []

    editable_vars: Set[str] = set()
    for review_doc in review_docs:
        review_entries = review_doc.get("review")
        if isinstance(review_entries, dict):
            review_entries = [review_entries]
        if not isinstance(review_entries, list):
            continue
        for item in review_entries:
            if not isinstance(item, dict):
                continue
            edit_target = _stringify(item.get("Edit")).strip()
            if edit_target:
                editable_vars.add(edit_target)

    if not editable_vars:
        return [
            LintIssue(
                rule_id="review-screen-missing-edit-links",
                severity="yellow",
                message="Review screen detected but no editable `Edit:` links were found.",
                url="https://assemblyline.suffolklitlab.org/docs/authoring/review_screen/",
            )
        ]

    decision_vars = _extract_decision_variables(
        [doc for doc in docs if doc not in review_docs]
    )
    if not decision_vars:
        return []

    for decision_var in decision_vars:
        if decision_var in editable_vars:
            return []

    return [
        LintIssue(
            rule_id="review-screen-missing-key-choice-edits",
            severity="green",
            message="Review screen exists but does not appear to allow editing decision/key choice fields.",
            url="https://assemblyline.suffolklitlab.org/docs/authoring/review_screen/",
            problematic_text=_shorten(", ".join(sorted(list(decision_vars))[:5])),
        )
    ]


def _extract_variable_references(docs: Sequence[dict]) -> Set[str]:
    refs: Set[str] = set()
    for doc in docs:
        for key in ["yesno", "noyes", "yesnomaybe", "noyesmaybe"]:
            value = _stringify(doc.get(key)).strip()
            if value:
                refs.add(value)
        for field in _coerce_fields(doc):
            value = _extract_field_variable(field)
            if value:
                refs.add(value)
    return refs


def _check_variable_conventions(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    refs = _extract_variable_references(docs)
    valid_root_re = re.compile(r"^[a-z][a-z0-9_]*$")
    name_part_vars = {
        "first_name",
        "middle_name",
        "last_name",
        "name_first",
        "name_middle",
        "name_last",
        "name_suffix",
    }
    address_part_vars = {
        "address",
        "address_line_1",
        "address_line_2",
        "street",
        "street_address",
        "city",
        "state",
        "zip",
        "postal_code",
        "county",
        "country",
    }

    bad_roots: Set[str] = set()
    for ref in refs:
        root = re.split(r"[.\[]", ref, maxsplit=1)[0].strip()
        if not root:
            continue
        if not valid_root_re.match(root):
            bad_roots.add(root)

    if bad_roots:
        findings.append(
            LintIssue(
                rule_id="variable-root-not-snake-case",
                severity="yellow",
                message="Variable names should use snake_case roots.",
                url=f"{CODING_STYLE_URL}/yaml_interface/",
                problematic_text=", ".join(sorted(bad_roots)),
            )
        )

    uses_person_objects = False
    for doc in docs:
        objects = doc.get("objects")
        if isinstance(objects, list):
            for item in objects:
                if "ALIndividual" in _stringify(item) or "ALPeopleList" in _stringify(
                    item
                ):
                    uses_person_objects = True
                    break
        if uses_person_objects:
            break
    if not uses_person_objects:
        uses_person_objects = any(
            ".name." in ref or ".address." in ref
            for ref in refs
            if isinstance(ref, str)
        )

    simple_refs = {ref for ref in refs if "." not in ref and "[" not in ref}
    standalone_name_parts = simple_refs.intersection(name_part_vars)
    standalone_address_parts = simple_refs.intersection(address_part_vars)
    if not uses_person_objects and (
        len(standalone_name_parts) >= 2 or len(standalone_address_parts) >= 3
    ):
        findings.append(
            LintIssue(
                rule_id="prefer-person-objects",
                severity="green",
                message="Interview appears to use disconnected name/address variables; prefer ALIndividual/ALPeopleList patterns.",
                url=f"{CODING_STYLE_URL}/yaml_interface/",
                problematic_text=_shorten(
                    ", ".join(sorted(standalone_name_parts | standalone_address_parts))
                ),
            )
        )
    return findings


def _check_plain_language_replacements(
    docs: Sequence[dict], interview_texts: Sequence[str], raw_content: str
) -> List[LintIssue]:
    return _check_plain_language_replacements_impl(docs, interview_texts, raw_content)


RULES: List[LintRule] = [
    LintRule(
        "missing-metadata-fields",
        "red",
        METADATA_GUIDE_URL,
        _check_metadata_fields,
    ),
    LintRule(
        "missing-question-id",
        "red",
        f"{CODING_STYLE_URL}/yaml_structure/",
        _check_missing_id,
    ),
    LintRule(
        "multiple-mandatory-blocks",
        "red",
        f"{CODING_STYLE_URL}/yaml_structure/",
        _check_multiple_mandatory,
    ),
    LintRule(
        "avoid-yesno-shortcuts",
        "red",
        f"{CODING_STYLE_URL}/accessibility/",
        _check_yesno_shortcuts,
    ),
    LintRule(
        "avoid-combobox",
        "red",
        f"{CODING_STYLE_URL}/accessibility/",
        _check_combobox_usage,
    ),
    LintRule(
        "subquestion-h1", "red", f"{STYLE_GUIDE_URL}/formatting/", _check_subquestion_h1
    ),
    LintRule(
        "skipped-heading-level",
        "red",
        f"{STYLE_GUIDE_URL}/formatting/",
        _check_skipped_heading_levels,
    ),
    LintRule(
        "choices-without-stable-values",
        "red",
        f"{CODING_STYLE_URL}/yaml_interface/",
        _check_choices_without_values,
    ),
    LintRule(
        "remove-language-en",
        "red",
        f"{CODING_STYLE_URL}/yaml_translation/",
        _check_language_en_flag,
    ),
    LintRule(
        "hardcoded-user-text-in-code",
        "red",
        f"{CODING_STYLE_URL}/yaml_translation/",
        _check_hardcoded_strings_in_code,
    ),
    LintRule(
        "image-missing-alt-text",
        "red",
        "https://docassemble.org/docs/markup.html#inserting%20images",
        _check_image_alt_text,
    ),
    LintRule(
        "field-missing-label",
        "red",
        WCAG_LABELS_INSTRUCTIONS_URL,
        _check_missing_field_labels,
    ),
    LintRule(
        "non-descriptive-field-label",
        "yellow",
        WCAG_LABELS_INSTRUCTIONS_URL,
        _check_non_descriptive_field_labels,
    ),
    LintRule(
        "blank-choice-label",
        "red",
        WCAG_LABELS_INSTRUCTIONS_URL,
        _check_choice_label_quality,
    ),
    LintRule(
        "duplicate-field-label",
        "yellow",
        WCAG_LABELS_INSTRUCTIONS_URL,
        _check_duplicate_field_labels,
    ),
    LintRule(
        "missing-screen-title",
        "yellow",
        f"{STYLE_GUIDE_URL}/question_overview/",
        _check_empty_screen_title,
    ),
    LintRule(
        "color-only-instructions",
        "yellow",
        "https://www.w3.org/WAI/WCAG21/Understanding/use-of-color.html",
        _check_color_only_instructions,
    ),
    LintRule(
        "inline-color-styling",
        "yellow",
        "https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html",
        _check_inline_color_styling,
    ),
    LintRule(
        "non-descriptive-link-text",
        "yellow",
        WCAG_LINK_PURPOSE_URL,
        _check_generic_link_text,
    ),
    LintRule(
        "empty-link-text",
        "red",
        WCAG_LINK_PURPOSE_URL,
        _check_empty_link_text,
    ),
    LintRule(
        "ambiguous-link-destinations",
        "yellow",
        WCAG_LINK_PURPOSE_URL,
        _check_ambiguous_link_destinations,
    ),
    LintRule(
        "opens-new-tab-without-warning",
        "yellow",
        WCAG_LINK_PURPOSE_URL,
        _check_new_tab_links_without_warning,
    ),
    LintRule(
        "svg-missing-accessible-name",
        "yellow",
        "https://www.w3.org/WAI/WCAG21/Understanding/non-text-content.html",
        _check_svg_accessible_names,
    ),
    LintRule(
        "table-missing-headers",
        "red",
        "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships.html",
        _check_tables_accessibility,
    ),
    LintRule(
        "positive-tabindex",
        "red",
        "https://www.w3.org/WAI/WCAG21/Understanding/focus-order.html",
        _check_positive_tabindex,
    ),
    LintRule(
        "clickable-non-control-html",
        "yellow",
        "https://www.w3.org/WAI/WCAG21/Understanding/keyboard.html",
        _check_clickable_non_controls,
    ),
    LintRule(
        "required-field-not-indicated",
        "yellow",
        WCAG_LABELS_INSTRUCTIONS_URL,
        _check_required_fields_indicated,
    ),
    LintRule(
        "validation-without-guidance",
        "yellow",
        "https://www.w3.org/WAI/WCAG21/Understanding/error-identification.html",
        _check_validation_guidance,
    ),
    LintRule(
        "generic-validation-message",
        "yellow",
        "https://www.w3.org/WAI/WCAG21/Understanding/error-suggestion.html",
        _check_generic_validation_messages,
    ),
    LintRule(
        "ambiguous-button-text",
        "yellow",
        WCAG_LINK_PURPOSE_URL,
        _check_ambiguous_button_text,
    ),
    LintRule(
        "placeholder-language",
        "yellow",
        f"{STYLE_GUIDE_URL}/question_overview/",
        _check_placeholder_language,
    ),
    LintRule(
        "plain-language-replacements",
        "yellow",
        PLAIN_LANGUAGE_GUIDE_URL,
        _check_plain_language_replacements,
    ),
    LintRule(
        "missing-custom-theme",
        "yellow",
        "https://assemblyline.suffolklitlab.org/docs/customizing-look-and-feel/",
        _check_theme_usage,
    ),
    LintRule(
        "variable-root-not-snake-case",
        "yellow",
        f"{CODING_STYLE_URL}/yaml_interface/",
        _check_variable_conventions,
    ),
    LintRule(
        "long-sentences",
        "yellow",
        f"{STYLE_GUIDE_URL}/readability/",
        _check_long_sentences,
    ),
    LintRule(
        "compound-questions",
        "yellow",
        f"{STYLE_GUIDE_URL}/question_overview/",
        _check_compound_questions,
    ),
    LintRule(
        "overlong-question-label",
        "yellow",
        f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
        _check_overlong_labels,
    ),
    LintRule(
        "too-many-fields-on-screen",
        "yellow",
        f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
        _check_too_many_fields,
    ),
    LintRule(
        "wall-of-text", "yellow", f"{STYLE_GUIDE_URL}/formatting/", _check_wall_of_text
    ),
    LintRule(
        "complex-screen-missing-help",
        "green",
        f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
        _check_missing_help_on_complex_screens,
    ),
    LintRule(
        "missing-exit-criteria-screen",
        "green",
        f"{STYLE_GUIDE_URL}/question_overview/",
        _check_exit_criteria_and_screen,
    ),
    LintRule(
        "review-screen-missing-edit-links",
        "yellow",
        "https://assemblyline.suffolklitlab.org/docs/authoring/review_screen/",
        _check_review_screen_editability,
    ),
]


RULE_IDS_BY_MODE: Dict[str, List[str]] = {
    "full": [rule.rule_id for rule in RULES],
    "wcag-basic": [
        "avoid-yesno-shortcuts",
        "avoid-combobox",
        "subquestion-h1",
        "skipped-heading-level",
        "image-missing-alt-text",
        "field-missing-label",
        "non-descriptive-field-label",
        "blank-choice-label",
        "duplicate-field-label",
        "missing-screen-title",
        "color-only-instructions",
        "inline-color-styling",
        "non-descriptive-link-text",
        "empty-link-text",
        "ambiguous-link-destinations",
        "opens-new-tab-without-warning",
        "svg-missing-accessible-name",
        "table-missing-headers",
        "positive-tabindex",
        "clickable-non-control-html",
        "required-field-not-indicated",
        "validation-without-guidance",
        "generic-validation-message",
        "ambiguous-button-text",
    ],
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


def _rules_for_mode(lint_mode: str) -> List[LintRule]:
    mode = normalize_lint_mode(lint_mode)
    enabled_rule_ids = set(
        RULE_IDS_BY_MODE.get(mode, RULE_IDS_BY_MODE[DEFAULT_LINT_MODE])
    )
    return [rule for rule in RULES if rule.rule_id in enabled_rule_ids]


def _finding_confidence(rule_id: str, source: str = "deterministic") -> str:
    if source == "llm":
        return "needs-review"
    return "definite" if rule_id in DEFINITE_RULE_IDS else "needs-review"


def run_deterministic_rules(
    docs: Sequence[dict],
    interview_texts: Sequence[str],
    raw_content: str,
    lint_mode: str = DEFAULT_LINT_MODE,
) -> List[Dict[str, Any]]:
    findings: List[LintIssue] = []
    for rule in _rules_for_mode(lint_mode):
        findings.extend(rule.check(docs, interview_texts, raw_content))

    unique: Set[Tuple[str, str, str, str, Optional[str], Optional[str]]] = set()
    deduped: List[Dict[str, Any]] = []
    for finding in findings:
        key = (
            finding.rule_id,
            finding.severity,
            finding.message,
            finding.url,
            finding.screen_id,
            finding.problematic_text,
        )
        if key in unique:
            continue
        unique.add(key)
        deduped.append(
            {
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "message": finding.message,
                "url": finding.url,
                "screen_id": finding.screen_id,
                "problematic_text": finding.problematic_text,
                "source": "deterministic",
                "confidence": _finding_confidence(finding.rule_id),
            }
        )
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


def load_llm_prompt_templates() -> Dict[str, Any]:
    yaml = ruamel.yaml.YAML(typ="safe")
    package_name = _stringify(__package__) or "docassemble.ALDashboard"
    rel_path = "data/sources/interview_linter_prompts.yml"
    file_ref = f"{package_name}:{rel_path}"

    # Prefer docassemble resolver so this works with installed, renamed, or playground packages.
    if path_and_mimetype is not None:
        try:
            prompt_path, _ = path_and_mimetype(file_ref)
            if prompt_path and os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as fp:
                    return yaml.load(fp.read()) or {}
        except Exception as err:
            log(
                f"interview_linter: failed resolving {file_ref} with path_and_mimetype: {err}"
            )

    # Fallback for local/dev execution.
    try:
        prompt_path = importlib.resources.files(package_name).joinpath(
            "data", "sources", "interview_linter_prompts.yml"
        )
        return yaml.load(prompt_path.read_text(encoding="utf-8")) or {}
    except Exception as err:
        log(
            f"interview_linter: could not load prompt templates from package {package_name}: {err}"
        )
        return {}


@lru_cache(maxsize=1)
def load_plain_language_replacements() -> Dict[str, str]:
    yaml = ruamel.yaml.YAML(typ="safe")
    package_name = _stringify(__package__) or "docassemble.ALDashboard"
    rel_path = "data/sources/plain_language_replacements.yml"
    file_ref = f"{package_name}:{rel_path}"

    loaded: Any = None
    if path_and_mimetype is not None:
        try:
            terms_path, _ = path_and_mimetype(file_ref)
            if terms_path and os.path.exists(terms_path):
                with open(terms_path, "r", encoding="utf-8") as fp:
                    loaded = yaml.load(fp.read()) or {}
        except Exception as err:
            log(
                f"interview_linter: failed resolving {file_ref} with path_and_mimetype: {err}"
            )

    if loaded is None:
        try:
            terms_path = importlib.resources.files(package_name).joinpath(
                "data", "sources", "plain_language_replacements.yml"
            )
            loaded = yaml.load(terms_path.read_text(encoding="utf-8")) or {}
        except Exception as err:
            log(
                "interview_linter: could not load plain-language replacement list "
                f"from package {package_name}: {err}"
            )
            loaded = {}

    if not isinstance(loaded, dict):
        return {}

    normalized: Dict[str, str] = {}
    for key, value in loaded.items():
        term = _stringify(key).strip().lower().replace("’", "'")
        replacement = _stringify(value).strip()
        if term and replacement:
            normalized[term] = replacement
    return normalized


@lru_cache(maxsize=1)
def _compiled_plain_language_patterns() -> List[Tuple[str, str, Any]]:
    compiled: List[Tuple[str, str, Any]] = []
    replacements = load_plain_language_replacements()
    sorted_terms = sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    )
    for term, replacement in sorted_terms:
        if not re.search(r"[a-z0-9]", term):
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        compiled.append((term, replacement, pattern))
    return compiled


def _find_plain_language_suggestions(
    text: str, max_matches: int = 8
) -> List[Tuple[str, str]]:
    plain = remove_mako(text)
    if not plain:
        return []

    occupied: List[Tuple[int, int]] = []
    seen_terms: Set[str] = set()
    matches: List[Tuple[int, str, str]] = []
    for _, replacement, pattern in _compiled_plain_language_patterns():
        if len(matches) >= max_matches:
            break
        for found in pattern.finditer(plain):
            span = (found.start(), found.end())
            overlaps = any(
                not (span[1] <= used_start or span[0] >= used_end)
                for used_start, used_end in occupied
            )
            if overlaps:
                continue
            seen_key = found.group(0).strip().lower()
            if seen_key in seen_terms:
                continue
            seen_terms.add(seen_key)
            occupied.append(span)
            matches.append((found.start(), found.group(0), replacement))
            break
    matches.sort(key=lambda item: item[0])
    return [(match_text, replacement) for _, match_text, replacement in matches]


def _format_plain_language_replacement(value: str) -> str:
    formatted = _stringify(value).strip()
    if formatted.startswith("[") and formatted.endswith("]"):
        formatted = formatted[1:-1].strip()
    return formatted


def _check_plain_language_replacements_impl(
    docs: Sequence[dict], _: Sequence[str], __: str
) -> List[LintIssue]:
    findings: List[LintIssue] = []
    seen: Set[Tuple[str, str, str]] = set()

    for idx, doc in enumerate(docs):
        screen_id = _block_label(doc, f"block-{idx}")
        for location, text in _iter_doc_texts(doc):
            suggestions = _find_plain_language_suggestions(text)
            for matched_text, replacement in suggestions:
                formatted_replacement = _format_plain_language_replacement(replacement)
                key = (screen_id, location, matched_text.strip().lower())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    LintIssue(
                        rule_id="plain-language-replacements",
                        severity="yellow",
                        message=(
                            "Complex wording found; consider a simpler phrase "
                            f"(`{matched_text}` -> `{formatted_replacement}`)."
                        ),
                        url=PLAIN_LANGUAGE_GUIDE_URL,
                        screen_id=screen_id,
                        problematic_text=_shorten(
                            f"{location}: {matched_text} -> {formatted_replacement}"
                        ),
                    )
                )
    return findings


def _safe_parse_llm_json(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        if isinstance(raw.get("findings"), list):
            return [item for item in raw["findings"] if isinstance(item, dict)]
        return [raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return _safe_parse_llm_json(parsed)
        except Exception:
            return []
    return []


def run_llm_rules(
    docs: Sequence[dict],
    interview_texts: Sequence[str],
    screen_catalog: Optional[Sequence[Dict[str, str]]] = None,
    model: str = "gpt-4o-mini",
    enabled_rules: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    if chat_completion is None:
        return []
    prompts = load_llm_prompt_templates()
    llm_rules = prompts.get("llm_rules", [])
    if not isinstance(llm_rules, list):
        return []

    if enabled_rules:
        enabled = set(enabled_rules)
        llm_rules = [
            rule for rule in llm_rules if _stringify(rule.get("rule_id")) in enabled
        ]

    combined_text = "\n\n".join(remove_mako(text) for text in interview_texts if text)
    if not combined_text.strip():
        return []
    if screen_catalog is None:
        screen_catalog = get_screen_catalog(docs)
    screen_payload = _build_screen_payload(screen_catalog)

    findings: List[Dict[str, Any]] = []
    for rule in llm_rules:
        system_prompt = _stringify(rule.get("system_prompt"))
        user_template = _stringify(rule.get("user_prompt"))
        user_prompt = user_template.replace("{interview_text}", combined_text[:12000])
        user_prompt = user_prompt.replace("{screens_json}", screen_payload)

        try:
            response = chat_completion(
                system_message=system_prompt,
                user_message=user_prompt,
                model=model,
                json_mode=True,
                temperature=0,
            )
        except Exception:
            continue

        parsed = _safe_parse_llm_json(response)
        for item in parsed:
            findings.append(
                {
                    "rule_id": _stringify(item.get("rule_id"))
                    or _stringify(rule.get("rule_id")),
                    "severity": _stringify(item.get("severity")).lower()
                    or _stringify(rule.get("default_severity", "yellow")),
                    "message": _stringify(item.get("message"))
                    or "LLM identified a potential issue.",
                    "url": _stringify(rule.get("url")),
                    "screen_id": _stringify(item.get("screen_id")) or None,
                    "problematic_text": _stringify(item.get("problematic_text"))
                    or None,
                    "source": "llm",
                    "confidence": _finding_confidence(
                        _stringify(item.get("rule_id"))
                        or _stringify(rule.get("rule_id")),
                        source="llm",
                    ),
                }
            )
    return findings


def lint_interview_content(
    content: str,
    language: str = "en",
    include_llm: bool = False,
    lint_mode: str = DEFAULT_LINT_MODE,
) -> Dict[str, Any]:
    resolved_lint_mode = normalize_lint_mode(lint_mode)
    yaml_errors = _run_dayamlchecker(content)
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

    yaml_parsed = load_interview(content)
    interview_texts = get_all_text(yaml_parsed)
    user_facing_texts = get_user_facing_text(yaml_parsed)
    screen_catalog = get_screen_catalog(yaml_parsed)
    interview_texts_no_mako = [remove_mako(text) for text in user_facing_texts]
    headings = {
        key: remove_mako(text) for key, text in get_all_headings(yaml_parsed).items()
    }
    paragraph = " ".join(text for text in interview_texts_no_mako if text).strip()
    style_warnings = [
        {"message": message, "url": url}
        for message, url in text_violations(interview_texts_no_mako)
    ]

    findings = run_deterministic_rules(
        yaml_parsed, interview_texts, content, lint_mode=resolved_lint_mode
    )
    if include_llm:
        findings.extend(
            run_llm_rules(yaml_parsed, interview_texts, screen_catalog=screen_catalog)
        )
    findings = _attach_screen_links_and_evidence(findings, screen_catalog)

    readability = readability_consensus_assessment(paragraph)

    return {
        "interview_scores": {"Readability Consensus": readability["consensus"]},
        "readability": readability,
        "yaml_errors": [],
        "misspelled": sorted(get_misspelled_words(paragraph, language=language)),
        "headings_warnings": headings_violations(headings),
        "style_warnings": style_warnings,
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
