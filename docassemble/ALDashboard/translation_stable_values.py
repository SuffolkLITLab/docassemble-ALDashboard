"""Find the machine-facing values in an interview.

Choices and action buttons both pair text a user reads with a value the
interview itself consumes: a choice stores its value, and an action button
dispatches its action name and arguments. Code elsewhere in the interview
compares against those values, so translating one silently breaks that code.
See issue #287.

This module lives apart from ``translation.py`` so it can be imported (and
tested) without pulling in the docassemble web application.
"""

import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

import docassemble.base.parse

__all__ = [
    "StableValue",
    "collect_stable_values",
    "stable_values_to_preserve",
]


# Keys of a parsed choice entry that hold text a user actually reads. Everything
# else in the entry is either machine-facing or not a TextObject.
CHOICE_DISPLAY_KEYS = ("label", "help", "default", "group", "css class", "color")


class StableValue(NamedTuple):
    """A machine-facing value that must survive translation unchanged."""

    text: str
    interview_name: str
    question_id: str
    language: str


def _choice_entries(question) -> List[dict]:
    """Return every parsed choice entry belonging to a question.

    Multiple choice questions keep their entries on ``field.choices``; fields
    with a ``datatype`` keep them on ``field.selections["values"]``. Choices
    built from code have a ``compute`` key instead and are skipped, because
    their values only exist at runtime.
    """
    entries: List[dict] = []
    for field in getattr(question, "fields", None) or []:
        choices = getattr(field, "choices", None)
        if isinstance(choices, list):
            entries.extend(entry for entry in choices if isinstance(entry, dict))
        selections = getattr(field, "selections", None)
        if isinstance(selections, dict):
            values = selections.get("values")
            if isinstance(values, list):
                entries.extend(entry for entry in values if isinstance(entry, dict))
    return entries


def _text_object_source(candidate) -> Optional[str]:
    """Return the original text of a TextObject, or None for anything else."""
    if isinstance(candidate, docassemble.base.parse.TextObject):
        original = getattr(candidate, "original_text", None)
        if isinstance(original, str):
            return original
    return None


def _question_language(question, interview) -> str:
    """Resolve the source language of a question the way the workbook does."""
    language = question.language
    if language == "*":
        language = question.from_source.get_language()
    if language == "*":
        language = interview.default_language
    return language


def collect_stable_values(
    interview,
) -> Tuple[List[StableValue], Set[str]]:
    """Find the machine-facing values in an interview, plus its display text.

    Choices and action buttons both pair text a user reads with a value the
    interview itself consumes, and code elsewhere compares against that value.
    Most YAML shapes make docassemble mark the value untranslatable, but several
    do not: a ``choices:`` mapping (``Other: other``) and a ``[value, label]``
    sequence both register the stored value as a translatable segment, as do the
    comparison value of ``show if: {variable: ..., is: ...}`` and the ``action``
    name and ``arguments`` of an ``action buttons:`` entry. Handing any of those
    to a translator silently changes what the interview stores or dispatches.
    See issue #287.

    Returns the values worth pinning along with every string used as display
    text, so the caller can leave alone any value that doubles as a label -- a
    bare ``choices: [- Red]`` stores and displays the same string, and freezing
    it would leave the question untranslated.
    """
    values: List[StableValue] = []
    display_texts: Set[str] = set()
    for question in getattr(interview, "all_questions", None) or []:
        try:
            language = _question_language(question, interview)
        except Exception:  # pragma: no cover - one odd question should not stop the run
            continue
        question_id = question.id if hasattr(question, "id") else question.name
        try:
            interview_name = question.from_source.get_name()
        except Exception:  # pragma: no cover - one odd question should not stop the run
            interview_name = ""
        for entry in _choice_entries(question):
            key_text = _text_object_source(entry.get("key"))
            if key_text:
                values.append(
                    StableValue(key_text, interview_name, question_id, language)
                )
            for display_key in CHOICE_DISPLAY_KEYS:
                display_text = _text_object_source(entry.get(display_key))
                if display_text:
                    display_texts.add(display_text)
        for field in getattr(question, "fields", None) or []:
            label_text = _text_object_source(getattr(field, "label", None))
            if label_text:
                display_texts.add(label_text)
            extras = getattr(field, "extras", None)
            if isinstance(extras, dict):
                show_if_text = _text_object_source(extras.get("show_if_val"))
                if show_if_text:
                    values.append(
                        StableValue(show_if_text, interview_name, question_id, language)
                    )
        for button in getattr(question, "action_buttons", None) or []:
            if not isinstance(button, dict):  # pragma: no cover - defensive
                continue
            # The action name and its arguments reach the interview's code
            # through action_argument(); only the label is read by a user. Every
            # other part of the button is already untranslatable.
            action_text = _text_object_source(button.get("action"))
            if action_text:
                values.append(
                    StableValue(action_text, interview_name, question_id, language)
                )
            arguments = button.get("arguments")
            if isinstance(arguments, dict):
                for argument in arguments.values():
                    argument_text = _text_object_source(argument)
                    if argument_text:
                        values.append(
                            StableValue(
                                argument_text, interview_name, question_id, language
                            )
                        )
            button_label = _text_object_source(button.get("label"))
            if button_label:
                display_texts.add(button_label)
        for attribute in ("content", "subcontent", "helptext"):
            content_text = _text_object_source(getattr(question, attribute, None))
            if content_text:
                display_texts.add(content_text)
    return values, display_texts


def stable_values_to_preserve(interview) -> Dict[str, StableValue]:
    """Return the values in an interview that must never be translated.

    A value is left out when it is also used as display text somewhere, because
    the two share a single row in the translation workbook: a bare
    ``choices: [- Red]`` stores and displays the same string, so pinning it
    would leave the question in English rather than protect anything.
    """
    values, display_texts = collect_stable_values(interview)
    to_preserve: Dict[str, StableValue] = {}
    for stable_value in values:
        if stable_value.text in display_texts or stable_value.text in to_preserve:
            continue
        if not re.search(r"[^\s0-9]", stable_value.text):
            # docassemble never offers pure whitespace or numbers for translation.
            continue
        to_preserve[stable_value.text] = stable_value
    return to_preserve
