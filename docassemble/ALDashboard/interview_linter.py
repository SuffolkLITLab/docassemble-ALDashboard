import importlib.resources
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

import mako.runtime
import mako.template
import ruamel.yaml
import textstat
from spellchecker import SpellChecker

import docassemble.base.filter
import docassemble.webapp.screenreader

try:
    from docassemble.ALToolbox.llms import chat_completion
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
]


TEXT_SECTIONS = ["question", "subquestion", "under", "pre", "post", "right", "note", "html"]
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
            if os.path.isfile(full_path) and filename.lower().endswith((".yml", ".yaml")):
                output.append({"label": filename, "token": full_path})
        return output
    except Exception as err:
        log(f"interview_linter: unable to list playground files for project {project}: {err}")
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
                values.append((f"fields[{idx}].{field_key}", _stringify(field.get(field_key))))
            # first key is often label shorthand, keep it available for checks
            if field:
                first_key = next(iter(field.keys()))
                values.append((f"fields[{idx}].first_key", _stringify(first_key)))
    return [(k, v) for k, v in values if v]


def get_misspelled_words(text: str, language: str = "en") -> Set[str]:
    spell = SpellChecker(language=language)
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
    return spell.unknown(filtered_tokens)


def get_corrections(
    misspelled: Union[Set[str], List[str]], language: str = "en"
) -> Mapping[str, Set[str]]:
    spell = SpellChecker(language=language)
    return {misspelled_word: spell.corrections(misspelled_word) for misspelled_word in misspelled}


def load_interview(content: str) -> List[dict]:
    yaml = ruamel.yaml.YAML(typ="safe")
    content = re.sub(r"\t", "  ", _stringify(content))
    return [doc for doc in yaml.load_all(content) if doc]


def remove_mako(text: str) -> str:
    input_text = _stringify(text)
    if not input_text:
        return ""
    original_undefined = mako.runtime.UNDEFINED
    mako.runtime.UNDEFINED = DAEmpty()
    try:
        template = mako.template.Template(input_text)
        markdown_text = template.render()
        html_text = docassemble.base.filter.markdown_to_html(markdown_text)
        return docassemble.webapp.screenreader.to_text(html_text)
    except Exception:
        return input_text
    finally:
        mako.runtime.UNDEFINED = original_undefined


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
    contractions = ("can't", "won't", "don't", "wouldn't", "shouldn't", "couldn't", "y'all", "you've")
    idioms = ("get the hang of", "sit tight", "up in the air", "on the ball", "rule of thumb")
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
        if not screen_text:
            continue
        catalog.append(
            {
                "screen_id": screen_id,
                "anchor": f"screen-{_anchor_slug(screen_id)}",
                "text": screen_text,
            }
        )
    return catalog


def _attach_screen_links_and_evidence(
    findings: List[Dict[str, Any]], screen_catalog: Sequence[Dict[str, str]]
) -> List[Dict[str, Any]]:
    by_id = {item["screen_id"]: item for item in screen_catalog if item.get("screen_id")}
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


def readability_scores(paragraph: str) -> Dict[str, Union[float, str]]:
    scores: Dict[str, Union[float, str]] = {}
    for name, metric in READABILITY_METRICS:
        try:
            scores[name] = metric(paragraph)
        except Exception:
            scores[name] = "N/A"
    return scores


def readability_consensus_assessment(paragraph: str) -> Dict[str, Optional[Union[str, int]]]:
    """
    Return readability consensus plus severity guidance:
    - yellow when consensus grade is > 7
    - red when consensus grade is > 10
    """
    try:
        consensus = textstat.text_standard(paragraph)
    except Exception:
        consensus = "N/A"

    grades = [int(num) for num in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", _stringify(consensus))]
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


def _check_missing_id(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_multiple_mandatory(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_yesno_shortcuts(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_combobox_usage(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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
                if isinstance(field, dict) and _stringify(field.get("datatype")).lower() == "combobox":
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


def _check_subquestion_h1(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_skipped_heading_levels(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_choices_without_values(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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
                if isinstance(field, dict) and has_unstable_choices(field.get("choices")):
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


def _check_language_en_flag(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_hardcoded_strings_in_code(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
    findings: List[LintIssue] = []
    quoted = re.compile(r"(['\"])([^'\"]{20,})\1")
    for idx, doc in enumerate(docs):
        code = _stringify(doc.get("code"))
        if not code:
            continue
        for _, content in quoted.findall(code):
            normalized = content.strip()
            if " " in normalized and not normalized.startswith("http") and not re.match(r"^[A-Za-z0-9_./:-]+$", normalized):
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


def _check_long_sentences(_: Sequence[dict], interview_texts: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_compound_questions(_: Sequence[dict], interview_texts: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_overlong_labels(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_too_many_fields(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
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


def _check_wall_of_text(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        subq = _stringify(doc.get("subquestion"))
        word_count = len(re.findall(r"\b\w+\b", remove_mako(subq)))
        has_structure = bool(re.search(r"(?m)^\s*[-*]\s+", subq) or re.search(r"(?m)^\s*#{2,6}\s+", subq))
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


def _check_missing_help_on_complex_screens(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
    findings: List[LintIssue] = []
    for idx, doc in enumerate(docs):
        fields = doc.get("fields")
        if not isinstance(fields, list) or len(fields) < 5:
            continue
        has_help = bool(doc.get("help"))
        for field in fields:
            if isinstance(field, dict) and (field.get("help") or field.get("hint") or field.get("note")):
                has_help = True
                break
        if not has_help:
            sample_labels: List[str] = []
            for field in fields:
                if isinstance(field, dict):
                    sample_labels.append(_stringify(field.get("label")) or _stringify(next(iter(field.keys()))))
            findings.append(
                LintIssue(
                    rule_id="complex-screen-missing-help",
                    severity="green",
                    message="Complex screen has no inline help/hint text.",
                    url=f"{STYLE_GUIDE_URL}/question_style_organize_fields/",
                    screen_id=_block_label(doc, f"block-{idx}"),
                    problematic_text=_shorten(", ".join(label for label in sample_labels if label) or f"{len(fields)} fields"),
                )
            )
    return findings


def _check_image_alt_text(docs: Sequence[dict], _: Sequence[str], __: str) -> List[LintIssue]:
    findings: List[LintIssue] = []
    md_image_re = re.compile(r"!\[(.*?)\]\((.*?)\)")
    file_tag_re = re.compile(r"\[FILE\s+([^,\]]+)(?:\s*,\s*([^,\]]+))?(?:\s*,\s*([^\]]+))?\]")
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
                            problematic_text=_shorten(f"[FILE {file_target}, {width_value}]"),
                        )
                    )
            for img_tag in img_tag_re.findall(text):
                alt_match = re.search(r"\balt\s*=\s*([\"\'])(.*?)\1", img_tag, re.IGNORECASE)
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


RULES: List[LintRule] = [
    LintRule("missing-question-id", "red", f"{CODING_STYLE_URL}/yaml_structure/", _check_missing_id),
    LintRule("multiple-mandatory-blocks", "red", f"{CODING_STYLE_URL}/yaml_structure/", _check_multiple_mandatory),
    LintRule("avoid-yesno-shortcuts", "red", f"{CODING_STYLE_URL}/accessibility/", _check_yesno_shortcuts),
    LintRule("avoid-combobox", "red", f"{CODING_STYLE_URL}/accessibility/", _check_combobox_usage),
    LintRule("subquestion-h1", "red", f"{STYLE_GUIDE_URL}/formatting/", _check_subquestion_h1),
    LintRule("skipped-heading-level", "red", f"{STYLE_GUIDE_URL}/formatting/", _check_skipped_heading_levels),
    LintRule("choices-without-stable-values", "red", f"{CODING_STYLE_URL}/yaml_interface/", _check_choices_without_values),
    LintRule("remove-language-en", "red", f"{CODING_STYLE_URL}/yaml_translation/", _check_language_en_flag),
    LintRule("hardcoded-user-text-in-code", "red", f"{CODING_STYLE_URL}/yaml_translation/", _check_hardcoded_strings_in_code),
    LintRule("image-missing-alt-text", "red", "https://docassemble.org/docs/markup.html#inserting%20images", _check_image_alt_text),
    LintRule("long-sentences", "yellow", f"{STYLE_GUIDE_URL}/readability/", _check_long_sentences),
    LintRule("compound-questions", "yellow", f"{STYLE_GUIDE_URL}/question_overview/", _check_compound_questions),
    LintRule("overlong-question-label", "yellow", f"{STYLE_GUIDE_URL}/question_style_organize_fields/", _check_overlong_labels),
    LintRule("too-many-fields-on-screen", "yellow", f"{STYLE_GUIDE_URL}/question_style_organize_fields/", _check_too_many_fields),
    LintRule("wall-of-text", "yellow", f"{STYLE_GUIDE_URL}/formatting/", _check_wall_of_text),
    LintRule("complex-screen-missing-help", "green", f"{STYLE_GUIDE_URL}/question_style_organize_fields/", _check_missing_help_on_complex_screens),
]


def run_deterministic_rules(docs: Sequence[dict], interview_texts: Sequence[str], raw_content: str) -> List[Dict[str, Any]]:
    findings: List[LintIssue] = []
    for rule in RULES:
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
            }
        )
    return deduped


def findings_by_severity(findings: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped = {severity: [] for severity in SEVERITY_ORDER}
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
            log(f"interview_linter: failed resolving {file_ref} with path_and_mimetype: {err}")

    # Fallback for local/dev execution.
    try:
        prompt_path = importlib.resources.files(f"{package_name}.data.sources").joinpath(
            "interview_linter_prompts.yml"
        )
        return yaml.load(prompt_path.read_text(encoding="utf-8")) or {}
    except Exception as err:
        log(f"interview_linter: could not load prompt templates from package {package_name}: {err}")
        return {}


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
        llm_rules = [rule for rule in llm_rules if _stringify(rule.get("rule_id")) in enabled]

    combined_text = "\n\n".join(remove_mako(text) for text in interview_texts if text)
    if not combined_text.strip():
        return []
    if screen_catalog is None:
        screen_catalog = get_screen_catalog(docs)
    screen_payload = json.dumps(
        [{"screen_id": s.get("screen_id"), "text": remove_mako(_stringify(s.get("text")))} for s in screen_catalog],
        ensure_ascii=False,
    )

    findings: List[Dict[str, Any]] = []
    for rule in llm_rules:
        system_prompt = _stringify(rule.get("system_prompt"))
        user_template = _stringify(rule.get("user_prompt"))
        user_prompt = user_template.replace("{interview_text}", combined_text[:12000])
        user_prompt = user_prompt.replace("{screens_json}", screen_payload[:20000])

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
                    "rule_id": _stringify(item.get("rule_id")) or _stringify(rule.get("rule_id")),
                    "severity": _stringify(item.get("severity")).lower() or _stringify(rule.get("default_severity", "yellow")),
                    "message": _stringify(item.get("message")) or "LLM identified a potential issue.",
                    "url": _stringify(rule.get("url")),
                    "screen_id": _stringify(item.get("screen_id")) or None,
                    "problematic_text": _stringify(item.get("problematic_text")) or None,
                    "source": "llm",
                }
            )
    return findings


def lint_interview_content(content: str, language: str = "en", include_llm: bool = False) -> Dict[str, Any]:
    yaml_parsed = load_interview(content)
    interview_texts = get_all_text(yaml_parsed)
    user_facing_texts = get_user_facing_text(yaml_parsed)
    screen_catalog = get_screen_catalog(yaml_parsed)
    interview_texts_no_mako = [remove_mako(text) for text in user_facing_texts]
    headings = {key: remove_mako(text) for key, text in get_all_headings(yaml_parsed).items()}
    paragraph = " ".join(text for text in interview_texts_no_mako if text).strip()
    style_warnings = [
        {"message": message, "url": url}
        for message, url in text_violations(interview_texts_no_mako)
    ]

    findings = run_deterministic_rules(yaml_parsed, interview_texts, content)
    if include_llm:
        findings.extend(run_llm_rules(yaml_parsed, interview_texts, screen_catalog=screen_catalog))
    findings = _attach_screen_links_and_evidence(findings, screen_catalog)

    readability = readability_consensus_assessment(paragraph)

    return {
        "interview_scores": {"Readability Consensus": readability["consensus"]},
        "readability": readability,
        "misspelled": sorted(get_misspelled_words(paragraph, language=language)),
        "headings_warnings": headings_violations(headings),
        "style_warnings": style_warnings,
        "interview_texts": interview_texts,
        "screen_catalog": screen_catalog,
        "findings": findings,
        "findings_by_severity": findings_by_severity(findings),
    }


def lint_multiple_sources(
    sources: Sequence[Dict[str, str]], language: str = "en", include_llm: bool = False
) -> List[Dict[str, Any]]:
    """
    Lint multiple source files. Each source item should contain:
    - name: display name
    - token: either absolute path or "ref:<package>:data/questions/file.yml"
    """
    reports: List[Dict[str, Any]] = []
    for source in sources:
        name = _stringify(source.get("name")) or _stringify(source.get("token")) or "unknown"
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
                result = lint_interview_content(fp.read(), language=language, include_llm=include_llm)
            reports.append({"name": name, "token": token, "error": None, "result": result})
        except Exception as err:
            reports.append({"name": name, "token": token, "error": str(err), "result": None})
    return reports


def lint_uploaded_interview(path: str, language: str = "en", include_llm: bool = False) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as yaml_file:
        return lint_interview_content(yaml_file.read(), language=language, include_llm=include_llm)
