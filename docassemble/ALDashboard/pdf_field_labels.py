"""Accessible names for PDF form controls: what is wrong with them, and what
the page itself suggests instead.

A control's accessible name (its ``/TU`` tooltip) is what a screen reader
announces before the control's role, so "County" is announced as "County,
edit text". Authoring tools and form authors often write instructions there
instead ("Type name of county", "Click or hit spacebar to check this box"),
which a listener hears in full on every visit and which never says which
choice a checkbox stands for.

Everything here works on plain data (widget boxes and word boxes normalized
to the page, origin top left) so the heuristics can be tested without a PDF.
"""

import re
import shutil
import subprocess  # nosec B404 - fixed executable and arguments only
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

__all__ = [
    "simplify_field_label",
    "field_label_problem",
    "read_page_words",
    "suggest_field_labels",
]

# Phrases that only describe how to operate a control. A screen reader
# already announces the role ("check box", "edit text"), so these carry no
# information about the field at all.
_OPERATION_ONLY = re.compile(
    r"^\s*(?:please\s+)?(?:(?:click|tap|press|hit)(?:\s+(?:or|and)\s+(?:click|tap|press|hit))?"
    r"(?:\s+(?:the\s+)?(?:space\s*bar|spacebar|enter|here))?(?:\s+to\s+(?:check|select|uncheck|"
    r"toggle|mark)(?:\s+(?:this|the))?(?:\s+(?:box|option|item|choice))?)?"
    r"|(?:check|select|mark|tick)(?:\s+(?:this|the|one|all\s+that\s+apply))?(?:\s+(?:box|option|item|choice))?"
    r"|type\s+here|enter\s+text|text\s+field|fill\s+in)\s*[.!:]*\s*$",
    re.IGNORECASE,
)

_PLACEHOLDER = re.compile(
    r"^\s*(?:undefined|null|none|n/?a|field|untitled|text(?:\s*field)?\s*\d*|"
    r"check\s*box\s*\d*|radio(?:\s*button)?\s*\d*|combo\s*box\s*\d*|list\s*box\s*\d*|"
    r"signature\s*\d+|button\s*\d+)\s*$",
    re.IGNORECASE,
)

# A leading imperative tells the person what to do with the control. The
# role announcement already does that; the name should say what the value is.
_LEADING_INSTRUCTION = re.compile(
    r"^\s*(?:please\s+)?(?:type|enter|print|write|fill\s+(?:in|out)|provide|input|insert|"
    r"add|list|give|state|indicate|specify|select|choose|pick|put)"
    r"(?:\s+(?:in|out|down))?(?:\s+(?:the|a|an))?\s+",
    re.IGNORECASE,
)
_LEADING_CHECK_IF = re.compile(
    r"^\s*(?:please\s+)?(?:check|mark|tick|select)(?:\s+(?:this|the|here))?(?:\s+box)?"
    r"\s+(?:if|when|to\s+indicate(?:\s+that)?)\s+",
    re.IGNORECASE,
)
_PRINT_NAME = re.compile(r"^print(?:ed)?\s+(?:your\s+|full\s+)?name$", re.IGNORECASE)
_NAME_OF = re.compile(r"^name\s+of\s+(?:the\s+)?(.+)$", re.IGNORECASE)
_TRAILING_NOISE = re.compile(r"\s+here$|[\s:*_.]+$|^[\s:*•·_-]+", re.IGNORECASE)

# Words that name one answer among several printed next to their boxes.
_OPTION_TEXT_LIMIT = 80


def _tidy(text: str) -> str:
    value = re.sub(r"_{2,}|\.{3,}", " ", str(text or ""))
    value = re.sub(r"\s+", " ", value)
    previous = None
    while previous != value:
        previous = value
        value = _TRAILING_NOISE.sub("", value).strip()
    return value


_ENUMERATOR = re.compile(r"^\(?(?:\d{1,3}|[a-zA-Z]|[ivxIVX]{1,4})[.)]\s+")


def _without_enumerator(text: str) -> str:
    """Drop a list marker ("d. ", "(2) ", "iv. ") from the start of a question."""
    return _ENUMERATOR.sub("", text, count=1)


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def simplify_field_label(text: Any) -> str:
    """Turn an instruction into the name of the information it asks for.

    ``"Type name of county"`` becomes ``"County"``; ``"Type the Mother's first
    name"`` becomes ``"Mother's first name"``; ``"Check if you are the
    Plaintiff"`` becomes ``"You are the Plaintiff"``. A label that only says
    how to operate the control (``"Click or hit spacebar to check this box"``)
    becomes empty, because nothing in it names the field. Anything else is
    returned tidied but otherwise unchanged.
    """
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    value = _tidy(raw)
    if not value or _OPERATION_ONLY.match(raw) or _OPERATION_ONLY.match(value):
        return ""
    # "Print name" beside a signature line asks for the printed name, which
    # is not the same thing as the signature.
    if _PRINT_NAME.match(value):
        return "Printed name"
    stripped = _LEADING_CHECK_IF.sub("", value, count=1)
    if stripped == value:
        stripped = _LEADING_INSTRUCTION.sub("", value, count=1)
    instructed = stripped != value
    stripped = _tidy(stripped)
    named = _NAME_OF.match(stripped)
    # "Type name of county" asks for the county. A label that simply says
    # "Name of Petitioner" already names the information and is left alone,
    # as is a long "name of the person who ..." phrase.
    if instructed and named and len(named.group(1).split()) <= 2:
        stripped = named.group(1)
    return _capitalize(stripped) if stripped else ""


def _squash(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def field_label_problem(tooltip: Any, field_name: Any) -> str:
    """Say what is wrong with an accessible name, or ``""`` if nothing is.

    ``missing``: there is none. ``placeholder``: an authoring tool's default
    ("Text12", "undefined") or a phrase about operating the control.
    ``derived``: a respelling of the internal field name. ``instruction``: it
    tells the person what to do instead of naming the information.
    """
    value = re.sub(r"\s+", " ", str(tooltip or "")).strip()
    if not value:
        return "missing"
    if _PLACEHOLDER.match(value) or _OPERATION_ONLY.match(value):
        return "placeholder"
    # "Previous city 1" as both name and tooltip is fine; "users1_name"
    # respelled as "users1 name" is an identifier read aloud.
    name = str(field_name or "").strip()
    if _squash(value) == _squash(name) and not re.search(r"\s", name):
        return "derived"
    # "court_division_bmc" is some field's identifier, whichever field.
    if re.fullmatch(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+", value):
        return "derived"
    if _squash(simplify_field_label(value)) != _squash(_tidy(value)):
        return "instruction"
    return ""


# ---------------------------------------------------------------------------
# Words on the page
# ---------------------------------------------------------------------------


def read_page_words(pdf_path: str) -> Dict[int, List[Dict[str, Any]]]:
    """Return every word's box per page, normalized to the page, top left.

    Uses Poppler's ``pdftotext -bbox``. Layout lines merge a question with
    the answers printed beside its boxes ("Who do the children live with now?
    Mother Father Other"); words keep them apart.
    """
    executable = shutil.which("pdftotext")
    if not executable:
        return {}
    try:
        completed = subprocess.run(  # nosec B603
            [executable, "-q", "-bbox", pdf_path, "-"],
            check=True,
            timeout=60,
            capture_output=True,
        )
        root = ET.fromstring(completed.stdout)
    except (OSError, subprocess.SubprocessError, ET.ParseError):
        return {}
    pages: Dict[int, List[Dict[str, Any]]] = {}
    for page_index, page in enumerate(root.iter("{http://www.w3.org/1999/xhtml}page")):
        try:
            width = float(page.attrib["width"]) or 1.0
            height = float(page.attrib["height"]) or 1.0
        except (KeyError, ValueError):
            continue
        words: List[Dict[str, Any]] = []
        for word in page.iter("{http://www.w3.org/1999/xhtml}word"):
            text = (word.text or "").strip()
            if not text:
                continue
            try:
                left = float(word.attrib["xMin"]) / width
                top = float(word.attrib["yMin"]) / height
                right = float(word.attrib["xMax"]) / width
                bottom = float(word.attrib["yMax"]) / height
            except (KeyError, ValueError):
                continue
            # Some forms draw their text twice (a fake bold, or a flattened
            # copy under the live one); one copy is enough.
            if any(
                other["text"] == text
                and abs(other["box"]["x"] - left) < 0.004
                and abs(other["box"]["y"] - top) < 0.004
                for other in words[-40:]
            ):
                continue
            words.append(
                {
                    "text": text,
                    "box": {
                        "x": left,
                        "y": top,
                        "width": right - left,
                        "height": bottom - top,
                    },
                }
            )
        pages[page_index] = words
    return pages


def _right(box: Mapping[str, float]) -> float:
    return box["x"] + box["width"]


def _bottom(box: Mapping[str, float]) -> float:
    return box["y"] + box["height"]


def _middle(box: Mapping[str, float]) -> float:
    return box["y"] + box["height"] / 2


def _union(boxes: Iterable[Mapping[str, float]]) -> Optional[Dict[str, float]]:
    items = list(boxes)
    if not items:
        return None
    left = min(box["x"] for box in items)
    top = min(box["y"] for box in items)
    right = max(_right(box) for box in items)
    bottom = max(_bottom(box) for box in items)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _same_line(word: Mapping[str, Any], box: Mapping[str, float]) -> bool:
    """A word sits on the line of a control when its middle falls inside it,
    allowing for boxes drawn a little above or below the text baseline."""
    middle = _middle(word["box"])
    slack = max(box["height"], word["box"]["height"]) * 0.35
    return box["y"] - slack <= middle <= _bottom(box) + slack


def _run(
    words: List[Dict[str, Any]],
    start: Dict[str, Any],
    direction: int,
    stops: List[Mapping[str, float]],
) -> List[Dict[str, Any]]:
    """Extend from ``start`` along its line while the words stay close.

    A gap wider than about a word space, a change of type size, or another
    control in between ends the phrase: "Other" beside a box is one answer
    even when "(name and relationship):" for the next blank follows it.
    """
    line = sorted(
        (
            word for word in words
            if abs(_middle(word["box"]) - _middle(start["box"])) < start["box"]["height"] * 0.5
        ),
        key=lambda word: word["box"]["x"],
    )
    index = next(i for i, word in enumerate(line) if word is start)
    phrase = [start]
    height = start["box"]["height"]
    while True:
        index += direction
        if index < 0 or index >= len(line):
            break
        previous = phrase[-1]["box"]
        candidate = line[index]["box"]
        gap = (
            candidate["x"] - _right(previous)
            if direction > 0
            else previous["x"] - _right(candidate)
        )
        if gap > height * 1.1 or not _same_size(line[index], start):
            break
        low, high = (
            (_right(previous), candidate["x"])
            if direction > 0
            else (_right(candidate), previous["x"])
        )
        if any(_between(stop, low, high, start["box"]) for stop in stops):
            break
        phrase.append(line[index])
    return phrase if direction > 0 else list(reversed(phrase))


def _same_size(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    a = first["box"]["height"]
    b = second["box"]["height"]
    return abs(a - b) <= max(a, b) * 0.08


def _between(
    stop: Mapping[str, float], low: float, high: float, line: Mapping[str, float]
) -> bool:
    """Whether a control on this line sits in the horizontal gap low..high."""
    on_line = abs(_middle(stop) - _middle(line)) < max(stop["height"], line["height"]) * 0.5
    return on_line and low - 0.002 <= stop["x"] <= high + 0.002


def _phrase_text(phrase: Iterable[Mapping[str, Any]]) -> str:
    return " ".join(str(word["text"]) for word in phrase)


def _covers(box: Mapping[str, float], word: Mapping[str, float]) -> bool:
    width = min(_right(box), _right(word)) - max(box["x"], word["x"])
    height = min(_bottom(box), _bottom(word)) - max(box["y"], word["y"])
    if width <= 0 or height <= 0:
        return False
    smaller = min(word["width"] * word["height"], box["width"] * box["height"])
    return width * height >= 0.25 * smaller


def _option_on_side(
    field: Mapping[str, Any],
    words: List[Dict[str, Any]],
    stops: List[Mapping[str, float]],
    side: int,
) -> Optional[Tuple[List[Dict[str, Any]], float]]:
    """The words printed right beside a box on one side (``1`` right, ``-1``
    left), and how far they sit from it."""
    box = field["box"]
    # A box drawn over printed words ("week/month (circle one)") marks one
    # of those words; it has no answer beside it to read. A single character
    # under a box is usually the box itself, drawn as a symbol-font glyph.
    if any(
        _covers(box, word["box"]) and _letters(str(word["text"])) >= 2
        for word in words
    ):
        return None
    reach = max(box["width"], box["height"]) * 2.5
    others = [stop for stop in stops if stop is not box]
    if side > 0:
        near = [
            (word["box"]["x"] - _right(box), word) for word in words
            if _same_line(word, box)
            and -box["width"] * 0.3 <= word["box"]["x"] - _right(box) <= reach
        ]
    else:
        near = [
            (box["x"] - _right(word["box"]), word) for word in words
            if _same_line(word, box)
            and -box["width"] * 0.3 <= box["x"] - _right(word["box"]) <= reach
        ]
    if not near:
        return None
    gap, start = min(near, key=lambda item: item[0])
    # "Check one: [ ] yes": text ending in a colon or question mark just
    # left of a box asks the question; it is not that box's answer.
    if side < 0 and str(start["text"]).endswith(("?", ":")):
        return None
    phrase = _run(words, start, side, others)
    # The question often runs straight into its first answer ("Own home?
    # Yes"); an answer starts after the question's final "?" or ":".
    for index in range(len(phrase) - 1, -1, -1):
        if str(phrase[index]["text"]).endswith(("?", ":")) and index < len(phrase) - 1:
            phrase = phrase[index + 1 :]
            break
    return phrase, max(gap, 0.0)


def _option_beside(
    field: Mapping[str, Any], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> Optional[List[Dict[str, Any]]]:
    """The words a lone box stands for: whichever side hugs it closer."""
    found = [
        item for item in (
            _option_on_side(field, words, stops, 1),
            _option_on_side(field, words, stops, -1),
        )
        if item is not None
    ]
    if not found:
        return None
    return min(found, key=lambda item: item[1])[0]


def _clean_option(text: str) -> str:
    value = _tidy(text).rstrip("*").strip(" ,;")
    value = re.sub(r"^(?:or|and)\s+|\s+(?:or|and)$", "", value, flags=re.IGNORECASE)
    return value.strip(" ,;")


def _option_core(text: str) -> str:
    """An answer without its parenthetical directions: "Yes (go to #10)" is
    the answer "Yes"."""
    return _squash(re.sub(r"\([^)]*\)?", " ", _clean_option(text)))


def _clean_question(text: str) -> str:
    value = _without_enumerator(_tidy(text)).strip(" ,;")
    # A comma or a stray letter left of the boxes is not a question.
    return value if len(re.findall(r"[^\W\d_]", value)) >= 3 else ""


def _question_for(
    first: Mapping[str, Any], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> Optional[List[Dict[str, Any]]]:
    """The words that ask the question a row of boxes answers.

    They sit on the same line, left of the first box, or on the line above
    when the answers have a line of their own.
    """
    box = first["box"]
    left = [
        word for word in words
        if _same_line(word, box) and _right(word["box"]) <= box["x"] + 0.002
    ]
    if not left:
        above = _question_above(box, words, stops)
        return _with_wrapped_start(above, words, stops) if above else None
    start = max(left, key=lambda word: _right(word["box"]))
    return _with_wrapped_start(_run(words, start, -1, stops), words, stops)


def _with_wrapped_start(
    phrase: List[Dict[str, Any]], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> List[Dict[str, Any]]:
    """Text that begins mid-sentence wrapped from the line above; add earlier
    lines until the sentence starts (at most two)."""
    for _ in range(2):
        opening = _phrase_text(phrase)
        if _ENUMERATOR.match(opening) or re.match(r"^[\W\d]*[A-Z0-9]", opening):
            break
        head = phrase[0]["box"]
        # The previous line of the same sentence: same type size, starting
        # near where this text starts. It may run on past the boxes.
        above = [
            word for word in words
            if 0 < head["y"] - _bottom(word["box"]) <= head["height"] * 1.2
            and _same_size(word, phrase[0])
            and word["box"]["x"] >= head["x"] - 0.02
        ]
        if not above:
            break
        lowest = max(_middle(word["box"]) for word in above)
        line_start = min(
            (word for word in above if abs(_middle(word["box"]) - lowest) < head["height"] * 0.5),
            key=lambda word: word["box"]["x"],
        )
        phrase = _run(words, line_start, 1, stops) + phrase
    return phrase


def _question_above(
    box: Mapping[str, float], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> Optional[List[Dict[str, Any]]]:
    """A row of answers on a line of its own is asked on the line above. Only
    a line that reads as a question (ending "?" or ":") is taken, so a
    heading or the previous answer's text is never borrowed."""
    above = [
        word for word in words
        if 0 < box["y"] - _bottom(word["box"]) <= box["height"] * 1.5
        and word["box"]["x"] <= box["x"] + 0.02
    ]
    if not above:
        return None
    lowest = max(_middle(word["box"]) for word in above)
    start = min(
        (word for word in above if abs(_middle(word["box"]) - lowest) < box["height"] * 0.5),
        key=lambda word: word["box"]["x"],
    )
    phrase = _run(words, start, 1, stops)
    return phrase if _phrase_text(phrase).rstrip().endswith(("?", ":")) else None


def _letters(text: str) -> int:
    return len(re.findall(r"[^\W\d_]", text))


_NOT_A_LABEL = {"or", "and", "if", "of", "the", "to", "a", "an", "per"}


def _is_label(text: str) -> bool:
    """Words that could name a blank: not just a "$", leader dots, or a
    connective left over from the sentence around it."""
    tidy = _tidy(text)
    return _letters(tidy) >= 2 and tidy.casefold() not in _NOT_A_LABEL


def _nearby_label(
    field: Mapping[str, Any], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> Optional[Dict[str, Any]]:
    """The printed words that introduce a text box.

    In order: the phrase just left of it on its line (looking past a lone
    "$" or "(" to the words before it), the line just above it (a column
    heading), or, when neither exists, the small print just below it
    ("first", "State"). Small print below is otherwise kept as a qualifier.
    """
    box = field["box"]
    others = [stop for stop in stops if stop is not box]
    left = [
        word for word in words
        if _same_line(word, box)
        and -0.01 <= box["x"] - _right(word["box"]) <= 0.3
    ]
    label: Optional[List[Dict[str, Any]]] = None
    where = ""
    if left:
        # A tall blank can overlap two lines of text; its label is the line
        # level with it, not one that only grazes its top edge.
        nearest = min(abs(_middle(word["box"]) - _middle(box)) for word in left)
        level = [
            word for word in left
            if abs(_middle(word["box"]) - _middle(box)) - nearest < 0.004
        ]
        start = max(level, key=lambda word: _right(word["box"]))
        # Text left of another blank on the same line labels that blank.
        if not any(
            _between(stop, _right(start["box"]), box["x"], box) for stop in others
        ):
            label = _run(words, start, -1, others)
            # "Income from assets   $ [______]": the "$" is a unit, and the
            # label is the phrase before it.
            for _ in range(4):
                if _is_label(_phrase_text(label)):
                    break
                head = label[0]["box"]
                # Beside a tall blank the label may sit a little above or
                # below the "$"; any line level with the blank will do.
                before = [
                    word for word in words
                    if _same_line(word, box)
                    and 0 <= head["x"] - _right(word["box"]) <= 0.5
                ]
                if not before:
                    break
                closest = min(abs(_middle(word["box"]) - _middle(box)) for word in before)
                previous = max(
                    (
                        word for word in before
                        if abs(_middle(word["box"]) - _middle(box)) - closest < 0.004
                    ),
                    key=lambda word: _right(word["box"]),
                )
                if any(_between(stop, _right(previous["box"]), head["x"], head) for stop in others):
                    break
                label = _run(words, previous, -1, others)
            if not _is_label(_phrase_text(label)):
                label = None
            else:
                where = "left"
                # "Dental and/or vision insurance: I pay $ [____]": a short
                # phrase is the end of a sentence whose subject precedes it.
                head = label[0]["box"]
                before = [
                    word for word in words
                    if abs(_middle(word["box"]) - _middle(head)) < head["height"] * 0.5
                    and 0 <= head["x"] - _right(word["box"]) <= 0.2
                ]
                if len(label) <= 2 and before:
                    previous = max(before, key=lambda word: _right(word["box"]))
                    if str(previous["text"]).endswith(":") and not any(
                        _between(stop, _right(previous["box"]), head["x"], head) for stop in others
                    ):
                        label = _run(words, previous, -1, others) + label
    reach = max(box["height"] * 1.5, 0.035)
    if label is None:
        # A "$" from the row above is not a column heading.
        above = [
            word for word in words
            if 0 <= box["y"] - _bottom(word["box"]) <= reach
            and word["box"]["x"] < _right(box)
            and _right(word["box"]) > box["x"]
            and _letters(str(word["text"])) >= 1
        ]
        if above:
            lowest = max(_middle(word["box"]) for word in above)
            label = sorted(
                (word for word in above if abs(_middle(word["box"]) - lowest) < 0.006),
                key=lambda word: word["box"]["x"],
            )
            where = "above"
    under = [
        word for word in words
        if 0 <= word["box"]["y"] - _bottom(box) <= max(box["height"], 0.012) * 0.9
        and word["box"]["x"] >= box["x"] - 0.01
        and _right(word["box"]) <= _right(box) + 0.01
    ]
    if not label:
        if not under or not _is_label(_phrase_text(under)):
            return None
        under.sort(key=lambda word: word["box"]["x"])
        return {
            "text": _without_enumerator(_tidy(_phrase_text(under))),
            "required": False,
            "box": _union(word["box"] for word in under),
            "qualifierBox": None,
            "where": "below",
        }
    label_height = min(word["box"]["height"] for word in label)
    # Only small print tucked under the blank qualifies it; ordinary text
    # there is the next line of the form.
    below = [word for word in under if word["box"]["height"] < label_height * 0.95]
    qualifier = _tidy(" ".join(word["text"] for word in sorted(below, key=lambda w: w["box"]["x"])))
    text = _without_enumerator(_tidy(_phrase_text(label)))
    if qualifier and len(qualifier) <= 30 and qualifier.casefold() not in text.casefold():
        text = f"{text} ({qualifier.strip('()')})" if text else qualifier
    return {
        "text": text,
        "required": "*" in _phrase_text(label),
        "box": _union(word["box"] for word in label),
        "qualifierBox": _union(word["box"] for word in below) if qualifier else None,
        "where": where,
    }


def _is_choice(field: Mapping[str, Any]) -> bool:
    return field.get("type") in {"checkbox", "radio"}


def _option_groups(
    fields: List[Dict[str, Any]], words_by_page: Mapping[int, List[Dict[str, Any]]]
) -> Dict[str, Dict[str, Any]]:
    """Find rows of checkboxes that answer one question.

    Two or more boxes on one line, each with a short answer printed beside
    it, form a group; the text left of the first box is the question.
    """
    found: Dict[str, Dict[str, Any]] = {}
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for field in fields:
        if _is_choice(field) and field.get("box"):
            by_page.setdefault(int(field["pageIndex"]), []).append(field)
    for page_index, choices in by_page.items():
        words = words_by_page.get(page_index) or []
        stops = [field["box"] for field in fields if field.get("box") and int(field["pageIndex"]) == page_index]
        sides: Dict[int, Dict[int, Tuple[List[Dict[str, Any]], float]]] = {}
        for index, field in enumerate(choices):
            for side in (1, -1):
                found_side = _option_on_side(field, words, stops, side)
                if found_side is None:
                    continue
                if _clean_option(_phrase_text(found_side[0])):
                    sides.setdefault(index, {})[side] = found_side
        rows: List[List[int]] = []
        for index in sorted(sides, key=lambda i: (choices[i]["box"]["y"], choices[i]["box"]["x"])):
            box = choices[index]["box"]
            matching_row = next(
                (
                    row for row in rows
                    if abs(_middle(choices[row[0]]["box"]) - _middle(box)) < box["height"] * 0.6
                ),
                None,
            )
            if matching_row is None:
                rows.append([index])
            else:
                matching_row.append(index)
        for row in rows:
            if len(row) < 2:
                continue
            row.sort(key=lambda i: choices[i]["box"]["x"])
            # Answers sit on one side of their boxes throughout a row: "[ ] Yes
            # [ ] No" or "Yes [ ] No [ ]". Take the side most boxes have, then
            # the one whose words hug the boxes closer; a box without an
            # answer on that side is not part of the row.
            side = min(
                (1, -1),
                key=lambda option_side: (
                    -sum(1 for i in row if option_side in sides[i]),
                    sum(sides[i][option_side][1] for i in row if option_side in sides[i]),
                ),
            )
            # An answer is short; a long run beside a box is a sentence the
            # box sits in, not one of a row of answers.
            row = [
                index for index in row
                if side in sides[index]
                and len(_clean_option(_phrase_text(sides[index][side][0]))) <= _OPTION_TEXT_LIMIT
            ]
            if len(row) < 2:
                continue
            options = {index: sides[index][side][0] for index in row}
            first_box = choices[row[0]]["box"]
            if side < 0:
                start = _union(word["box"] for word in options[row[0]]) or first_box
                first_box = dict(first_box, x=start["x"])
            question = _question_for({"box": first_box}, words, stops)
            question_text = _clean_question(_phrase_text(question)) if question else ""
            for position, index in enumerate(row):
                found[choices[index]["name"]] = {
                    "question": question_text,
                    "option": _clean_option(_phrase_text(options[index])),
                    "position": position,
                    "size": len(row),
                    "questionBox": (
                        _union(word["box"] for word in question)
                        if question and question_text
                        else None
                    ),
                    "optionBox": _union(word["box"] for word in options[index]),
                    "members": [choices[i]["name"] for i in row],
                }
    return found


def _group_label(group: Mapping[str, Any]) -> str:
    question = str(group.get("question") or "")
    option = str(group.get("option") or "")
    if not question:
        return option
    if question.endswith(("?", ":")):
        return f"{question} {option}"
    return f"{question}: {option}"


def suggest_field_labels(
    fields: Iterable[Mapping[str, Any]],
    words_by_page: Mapping[int, List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Review every control's accessible name and suggest a better one.

    ``fields`` holds one entry per field: ``name``, ``type`` (``text``,
    ``checkbox``, ``radio``, ``combo``, ``list``, ``signature``, ``button``),
    ``pageIndex``, ``box`` (normalized, top left) and its current
    ``tooltip``. Returns, per field name: ``problem`` (see
    :func:`field_label_problem`, plus ``option-missing`` for a box whose name
    never says which answer it is), ``suggested``, ``source``
    (``existing``, ``nearby`` or ``option-group``), and the boxes of the
    printed words the suggestion came from.

    Suggestions are drafts. Internal field names are never touched.
    """
    field_list = [dict(field) for field in fields]
    groups = _option_groups(field_list, words_by_page)
    result: Dict[str, Dict[str, Any]] = {}
    for field in field_list:
        name = str(field.get("name") or "")
        if not name:
            continue
        current = re.sub(r"\s+", " ", str(field.get("tooltip") or "")).strip()
        problem = field_label_problem(current, name)
        cleaned = simplify_field_label(current) if problem in {"", "instruction"} else ""
        entry: Dict[str, Any] = {
            "problem": problem,
            "suggested": cleaned,
            "source": "existing" if cleaned else "",
            "required": False,
        }
        group = groups.get(name)
        if group is not None:
            entry["group"] = {
                key: group[key] for key in ("question", "option", "position", "size", "members")
            }
            entry["labelBox"] = group["questionBox"]
            entry["optionBox"] = group["optionBox"]
            core = _option_core(group["option"])
            # Two boxes cannot share a name that leaves out their answer.
            if not problem and core and core not in _squash(current):
                entry["problem"] = "option-missing"
            if entry["problem"]:
                question = group["question"]
                # No question printed beside the row: the form's own name for
                # the box may still say what is being asked.
                if not question and cleaned and _squash(cleaned) != core:
                    question = cleaned
                entry["suggested"] = _group_label(dict(group, question=question))
                entry["source"] = "option-group"
        elif field.get("box"):
            words = words_by_page.get(int(field.get("pageIndex") or 0)) or []
            stops = [
                other["box"] for other in field_list
                if other.get("box") and other.get("pageIndex") == field.get("pageIndex")
            ]
            nearby = (
                _option_beside(field, words, stops) if _is_choice(field) else None
            )
            if nearby:
                label: Optional[Dict[str, Any]] = {
                    "text": _tidy(_phrase_text(nearby)),
                    "box": _union(word["box"] for word in nearby),
                    "required": False,
                    "where": "beside",
                }
            else:
                label = _nearby_label(field, words, stops)
            if label:
                entry["labelBox"] = label["box"]
                entry["qualifierBox"] = label.get("qualifierBox")
                entry["nearby"] = simplify_field_label(label["text"]) or label["text"]
                entry["required"] = bool(label.get("required"))
                if not entry["suggested"]:
                    entry["suggested"] = entry["nearby"]
                    entry["source"] = "nearby"
        result[name] = entry
    return result


def field_type_from_flags(field_type: str, flags: int) -> str:
    """Name a control the way a screen reader announces its role."""
    if field_type == "/Btn":
        if flags & (1 << 16):
            return "button"
        if flags & (1 << 15):
            return "radio"
        return "checkbox"
    if field_type == "/Ch":
        return "combo" if flags & (1 << 17) else "list"
    if field_type == "/Sig":
        return "signature"
    return "text"


def normalized_rect(rect: Iterable[Any], mediabox: Iterable[Any]) -> Optional[Dict[str, float]]:
    try:
        x1, y1, x2, y2 = (float(value) for value in rect)
        left, bottom, right, top = (float(value) for value in mediabox)
    except (TypeError, ValueError):
        return None
    width = (right - left) or 1.0
    height = (top - bottom) or 1.0
    return {
        "x": (min(x1, x2) - left) / width,
        "y": (top - max(y1, y2)) / height,
        "width": abs(x2 - x1) / width,
        "height": abs(y2 - y1) / height,
    }
