import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

import mako.runtime
import mako.template
import ruamel.yaml
import textstat
from spellchecker import SpellChecker

import docassemble.base.filter
import docassemble.webapp.screenreader

try:
    from docassemble.base.util import DAEmpty
except Exception:
    DAEmpty = str  # type: ignore

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
    "readability_scores",
    "lint_interview_content",
    "lint_uploaded_interview",
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


def _stringify(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    return str(item)


def get_misspelled_words(text: str, language: str = "en") -> Set[str]:
    spell = SpellChecker(language=language)
    return spell.unknown(re.findall(r"\w+", text))


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
    big_words = {"obtain": "get", "receive": "get", "whether": "if", "such as": "like", "provide": "give", "assist": "help"}
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
            seen.add(("Avoid using \"please\"", f"{base_docs_url}/respect#please"))
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


def readability_scores(paragraph: str) -> Dict[str, Union[float, str]]:
    scores: Dict[str, Union[float, str]] = {}
    for name, metric in READABILITY_METRICS:
        try:
            scores[name] = metric(paragraph)
        except Exception:
            scores[name] = "N/A"
    return scores


def lint_interview_content(content: str, language: str = "en") -> Dict[str, Any]:
    yaml_parsed = load_interview(content)
    interview_texts = get_all_text(yaml_parsed)
    interview_texts_no_mako = [remove_mako(text) for text in interview_texts]
    headings = {key: remove_mako(text) for key, text in get_all_headings(yaml_parsed).items()}
    paragraph = " ".join(text for text in interview_texts_no_mako if text).strip()
    style_warnings = [{"message": message, "url": url} for message, url in text_violations(interview_texts_no_mako)]
    return {
        "interview_scores": readability_scores(paragraph),
        "misspelled": sorted(get_misspelled_words(paragraph, language=language)),
        "headings_warnings": headings_violations(headings),
        "style_warnings": style_warnings,
        "interview_texts": interview_texts,
    }


def lint_uploaded_interview(path: str, language: str = "en") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as yaml_file:
        return lint_interview_content(yaml_file.read(), language=language)
