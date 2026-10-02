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
from typing import Any, Dict, Iterable, List, Mapping, Optional

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
_NAME_OF = re.compile(r"^name\s+of\s+(?:the\s+)?(.+)$", re.IGNORECASE)
_TRAILING_NOISE = re.compile(r"\s+here$|[\s:*_.]+$|^[\s:*•·_-]+", re.IGNORECASE)

# Words that name one answer among several printed next to their boxes.
_OPTION_TEXT_LIMIT = 40


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
    stripped = _LEADING_CHECK_IF.sub("", value, count=1)
    if stripped == value:
        stripped = _LEADING_INSTRUCTION.sub("", value, count=1)
    stripped = _tidy(stripped)
    named = _NAME_OF.match(stripped)
    # "name of county" is the county; "name of the person who filed the
    # first case" is still a name, and reads better left as it is.
    if named and len(named.group(1).split()) <= 2:
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
        (word for word in words if _same_line(word, start["box"])),
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


def _phrase_text(phrase: List[Mapping[str, Any]]) -> str:
    return " ".join(str(word["text"]) for word in phrase)


def _option_beside(
    field: Mapping[str, Any], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> Optional[List[Dict[str, Any]]]:
    """The words printed just to the right of a box (or, failing that, just
    to its left): the answer that box stands for."""
    box = field["box"]
    reach = max(box["width"], box["height"]) * 2.5
    right = [
        word for word in words
        if _same_line(word, box)
        and -box["width"] * 0.3 <= word["box"]["x"] - _right(box) <= reach
    ]
    if right:
        start = min(right, key=lambda word: word["box"]["x"])
        return _run(words, start, 1, [s for s in stops if s is not box])
    left = [
        word for word in words
        if _same_line(word, box)
        and -box["width"] * 0.3 <= box["x"] - _right(word["box"]) <= reach
    ]
    if left:
        start = max(left, key=lambda word: _right(word["box"]))
        return _run(words, start, -1, [s for s in stops if s is not box])
    return None


def _question_for(
    first: Mapping[str, Any], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> Optional[List[Dict[str, Any]]]:
    """The words that ask the question a row of boxes answers.

    They sit on the same line, left of the first box. When that text begins
    mid-sentence it wrapped from the line above, so earlier lines are added
    until the sentence starts (at most two).
    """
    box = first["box"]
    left = [
        word for word in words
        if _same_line(word, box) and _right(word["box"]) <= box["x"] + 0.002
    ]
    if not left:
        return None
    start = max(left, key=lambda word: _right(word["box"]))
    phrase = _run(words, start, -1, stops)
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


def _nearby_label(
    field: Mapping[str, Any], words: List[Dict[str, Any]], stops: List[Mapping[str, float]]
) -> Optional[Dict[str, Any]]:
    """The printed words that introduce a text box: the phrase just left of it
    on its line, else the line just above it. A short line just below it
    ("first", "MM/DD/YYYY") is kept as a qualifier."""
    box = field["box"]
    others = [stop for stop in stops if stop is not box]
    left = [
        word for word in words
        if _same_line(word, box)
        and 0 <= box["x"] - _right(word["box"]) <= 0.3
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
            where = "left"
    if label is None:
        above = [
            word for word in words
            if 0 <= box["y"] - _bottom(word["box"]) <= max(box["height"], 0.012) * 1.5
            and word["box"]["x"] < _right(box)
            and _right(word["box"]) > box["x"]
        ]
        if above:
            lowest = max(_middle(word["box"]) for word in above)
            label = sorted(
                (word for word in above if abs(_middle(word["box"]) - lowest) < 0.006),
                key=lambda word: word["box"]["x"],
            )
            where = "above"
    if not label:
        return None
    label_height = min(word["box"]["height"] for word in label)
    # Only small print tucked under the blank qualifies it; ordinary text
    # there is the next line of the form.
    below = [
        word for word in words
        if 0 <= word["box"]["y"] - _bottom(box) <= max(box["height"], 0.012) * 0.9
        and word["box"]["x"] >= box["x"] - 0.01
        and _right(word["box"]) <= _right(box) + 0.01
        and word["box"]["height"] < label_height * 0.95
    ]
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
        options: Dict[int, List[Dict[str, Any]]] = {}
        for index, field in enumerate(choices):
            phrase = _option_beside(field, words, stops)
            text = _tidy(_phrase_text(phrase)) if phrase else ""
            if phrase and 0 < len(text) <= _OPTION_TEXT_LIMIT:
                options[index] = phrase
        rows: List[List[int]] = []
        for index in sorted(options, key=lambda i: (choices[i]["box"]["y"], choices[i]["box"]["x"])):
            box = choices[index]["box"]
            row = next(
                (
                    row for row in rows
                    if abs(_middle(choices[row[0]]["box"]) - _middle(box)) < box["height"] * 0.6
                ),
                None,
            )
            if row is None:
                rows.append([index])
            else:
                row.append(index)
        for row in rows:
            if len(row) < 2:
                continue
            row.sort(key=lambda i: choices[i]["box"]["x"])
            question = _question_for(choices[row[0]], words, stops)
            question_text = (
                _without_enumerator(_tidy(_phrase_text(question))) if question else ""
            )
            for position, index in enumerate(row):
                option_text = _tidy(_phrase_text(options[index]))
                found[choices[index]["name"]] = {
                    "question": question_text,
                    "option": option_text,
                    "position": position,
                    "size": len(row),
                    "questionBox": _union(word["box"] for word in question) if question else None,
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
            option = str(group["option"]).rstrip("*").strip().casefold()
            # Two boxes cannot share a name that leaves out their answer.
            if not problem and option and option not in current.casefold():
                entry["problem"] = "option-missing"
            if entry["problem"]:
                entry["suggested"] = _group_label(
                    dict(group, option=str(group["option"]).rstrip("*").strip())
                )
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
