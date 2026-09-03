"""Jurisdiction profiles that describe the shape of a court form.

A court form is mostly boilerplate. The caption, the running footer with the
form number and "Page 1 of 2", the signature block and the certificate of
service are fixed by the court; only the middle of the document changes from
form to form. Every jurisdiction writes that boilerplate differently -- compare
Michigan's boxed SCAO caption against Vermont's two-column unit/docket line --
so a drafting tool that hard-codes one court's layout is only useful in that
court (SuffolkLITLab/docassemble-ALDashboard#272,
SuffolkLITLab/docassemble-ALWeaver#498).

This module keeps that boilerplate out of the code. A profile is a YAML file
naming the styles the court wants and the blocks that make up each fixed
section, and a section can instead be a Word-authored ``.docx`` fragment when a
layout is easier to draw than to describe. Both are editable without touching
Python:

* ``data/sources/court_form_profiles/<id>.yml`` -- the declarative default.
* ``data/templates/court_forms/<id>/<section>.docx`` -- an optional override,
  spliced in verbatim, for people who would rather edit a caption in Word.

Fonts, sizes and spacing live in the profile's ``styles:`` map and are written
into the generated document as real Word paragraph styles. A court that wants
Arial instead of Times gets a one-line edit rather than a search for every
``Pt(12)`` in the generator.
"""

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO

try:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt
except ImportError:  # pragma: no cover - python-docx is a hard dependency at runtime
    docx = None  # type: ignore[assignment]

__all__ = [
    "CourtFormProfile",
    "PageSpec",
    "StyleSpec",
    "DEFAULT_PROFILE_ID",
    "SECTION_NAMES",
    "apply_profile_styles",
    "find_docx_fragment",
    "list_court_form_profiles",
    "load_court_form_profile",
    "packaged_profile_directory",
    "packaged_fragment_directory",
    "splice_docx_fragment",
]

DEFAULT_PROFILE_ID = "generic"

# Sections a profile may define. Each may come from YAML blocks or from a
# same-named .docx fragment.
SECTION_NAMES: Sequence[str] = (
    "caption",
    "header",
    "footer",
    "letterhead",
    "signature",
    "certificate_of_service",
    "jurat",
)

_ALIGNMENTS = {
    "left": "LEFT",
    "center": "CENTER",
    "centre": "CENTER",
    "right": "RIGHT",
    "justify": "JUSTIFY",
    "both": "JUSTIFY",
}


def _alignment(value: Any):
    """Translate a profile's ``align:`` string into a python-docx constant."""
    if value is None or docx is None:
        return None
    key = str(value).strip().lower()
    name = _ALIGNMENTS.get(key)
    if not name:
        return None
    return getattr(WD_ALIGN_PARAGRAPH, name)


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class StyleSpec:
    """One Word style the profile wants defined in the generated document."""

    name: str
    style_type: str = "paragraph"
    based_on: Optional[str] = None
    font: Optional[str] = None
    size: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    all_caps: Optional[bool] = None
    color: Optional[str] = None
    align: Optional[str] = None
    line_spacing: Optional[float] = None
    space_before: Optional[float] = None
    space_after: Optional[float] = None
    left_indent: Optional[float] = None
    first_line_indent: Optional[float] = None
    keep_with_next: Optional[bool] = None

    @classmethod
    def from_mapping(cls, name: str, raw: Mapping[str, Any]) -> "StyleSpec":
        def flag(key: str) -> Optional[bool]:
            if key not in raw or raw.get(key) is None:
                return None
            return bool(raw.get(key))

        return cls(
            name=name,
            style_type=str(raw.get("type") or "paragraph").strip().lower(),
            based_on=(str(raw["based_on"]).strip() if raw.get("based_on") else None),
            font=(str(raw["font"]).strip() if raw.get("font") else None),
            size=_as_float(raw.get("size")),
            bold=flag("bold"),
            italic=flag("italic"),
            underline=flag("underline"),
            all_caps=flag("all_caps"),
            color=(str(raw["color"]).strip() if raw.get("color") else None),
            align=(str(raw["align"]).strip() if raw.get("align") else None),
            line_spacing=_as_float(raw.get("line_spacing")),
            space_before=_as_float(raw.get("space_before")),
            space_after=_as_float(raw.get("space_after")),
            left_indent=_as_float(raw.get("left_indent")),
            first_line_indent=_as_float(raw.get("first_line_indent")),
            keep_with_next=flag("keep_with_next"),
        )


@dataclass
class PageSpec:
    """Page setup the court expects."""

    top_margin: float = 1.0
    bottom_margin: float = 1.0
    left_margin: float = 1.0
    right_margin: float = 1.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PageSpec":
        raw_margins = raw.get("margins")
        margins: Mapping[str, Any] = (
            raw_margins if isinstance(raw_margins, Mapping) else {}
        )
        defaults = cls()

        def margin(key: str, fallback: float) -> float:
            value = _as_float(margins.get(key))
            return value if value is not None else fallback

        return cls(
            top_margin=margin("top", defaults.top_margin),
            bottom_margin=margin("bottom", defaults.bottom_margin),
            left_margin=margin("left", defaults.left_margin),
            right_margin=margin("right", defaults.right_margin),
        )


@dataclass
class CourtFormProfile:
    """Everything the generator needs to draw one court's boilerplate."""

    id: str
    name: str = ""
    jurisdiction: str = ""
    description: str = ""
    page: PageSpec = field(default_factory=PageSpec)
    styles: Dict[str, StyleSpec] = field(default_factory=dict)
    sections: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)
    body: Dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    # Directories searched, ahead of the packaged one, for .docx section
    # overrides. Carried on the profile so a caller that named its own
    # directory once does not have to name it again for every section.
    fragment_dirs: List[str] = field(default_factory=list)

    def style_name(self, role: str, fallback: str = "Normal") -> str:
        """The style to use for a role, falling back when a profile omits it.

        A role may be named either way round -- ``"text"`` or ``"text_style"``
        -- because the profile writes ``body: {text_style: ...}`` while callers
        think in roles.
        """
        key = role if role.endswith("_style") else f"{role}_style"
        candidate = self.body.get(key) if self.body else None
        if candidate and str(candidate) in self.styles:
            return str(candidate)
        if role in self.styles:
            return role
        return fallback

    def label(self, key: str, fallback: str = "") -> str:
        return str(self.labels.get(key, fallback) or fallback)

    def section_blocks(self, section: str) -> List[Dict[str, Any]]:
        blocks = self.sections.get(section)
        return list(blocks) if isinstance(blocks, list) else []


def packaged_profile_directory() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data",
        "sources",
        "court_form_profiles",
    )


def packaged_fragment_directory() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data",
        "templates",
        "court_forms",
    )


def _configured_profile_directories() -> List[str]:
    """Extra profile directories named in the docassemble configuration.

    Servers that keep their own court's profile outside this package can set
    ``court form profiles`` in the configuration rather than forking the
    package. Missing configuration is normal outside docassemble.
    """
    try:
        from .pdf_field_labeler import get_config  # type: ignore

        configured = get_config("court form profiles")
    except Exception:
        return []
    if not configured:
        return []
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, (list, tuple)):
        return []
    return [str(item) for item in configured if str(item or "").strip()]


def _profile_search_directories(
    extra_dirs: Optional[Iterable[str]] = None,
) -> List[str]:
    """Where to look for profiles, most specific first."""
    directories: List[str] = []
    for candidate in list(extra_dirs or []) + _configured_profile_directories():
        text = str(candidate or "").strip()
        if text and text not in directories:
            directories.append(text)
    packaged = packaged_profile_directory()
    if packaged not in directories:
        directories.append(packaged)
    return [d for d in directories if os.path.isdir(d)]


def _read_yaml_mapping(path: str) -> Dict[str, Any]:
    yaml = YAML(typ="safe", pure=True)
    with open(path, "r", encoding="utf-8") as handle:
        loaded = yaml.load(StringIO(handle.read()))
    return loaded if isinstance(loaded, dict) else {}


def _merge_profile_mappings(
    parent: Mapping[str, Any], child: Mapping[str, Any]
) -> Dict[str, Any]:
    """Overlay a child profile on the one it extends.

    ``styles``, ``labels`` and ``body`` merge key by key, so a jurisdiction can
    change one font without restating the whole style sheet. A section such as
    ``caption`` is replaced outright: captions are ordered layouts, and merging
    them entry by entry would silently interleave two courts' rows.
    """
    merged: Dict[str, Any] = dict(parent)
    for key, value in child.items():
        if (
            key in ("styles", "labels", "body", "page")
            and isinstance(value, Mapping)
            and isinstance(merged.get(key), Mapping)
        ):
            combined: Dict[str, Any] = dict(merged[key])
            for sub_key, sub_value in value.items():
                if (
                    key == "styles"
                    and isinstance(sub_value, Mapping)
                    and isinstance(combined.get(sub_key), Mapping)
                ):
                    style_merged = dict(combined[sub_key])
                    style_merged.update(dict(sub_value))
                    combined[sub_key] = style_merged
                else:
                    combined[sub_key] = sub_value
            merged[key] = combined
        else:
            merged[key] = value
    return merged


def _resolve_extends(
    raw: Mapping[str, Any],
    directories: Sequence[str],
    seen: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fold in the profile named by ``extends:``, following the chain."""
    parent_id = str(raw.get("extends") or "").strip()
    if not parent_id:
        return dict(raw)
    chain = list(seen or [])
    if parent_id in chain:
        # A cycle means somebody mis-edited a profile; use what we have rather
        # than recursing forever.
        return dict(raw)
    chain.append(parent_id)
    for directory in directories:
        for extension in (".yml", ".yaml"):
            path = os.path.join(directory, f"{parent_id}{extension}")
            if os.path.isfile(path):
                try:
                    parent_raw = _read_yaml_mapping(path)
                except Exception:
                    return dict(raw)
                parent_resolved = _resolve_extends(parent_raw, directories, chain)
                return _merge_profile_mappings(parent_resolved, raw)
    return dict(raw)


def _profile_from_mapping(
    profile_id: str, raw: Mapping[str, Any], source_path: str = ""
) -> CourtFormProfile:
    styles: Dict[str, StyleSpec] = {}
    raw_styles = raw.get("styles")
    if isinstance(raw_styles, Mapping):
        for style_name, style_raw in raw_styles.items():
            if isinstance(style_raw, Mapping):
                name = str(style_name)
                styles[name] = StyleSpec.from_mapping(name, style_raw)

    sections: Dict[str, List[Dict[str, Any]]] = {}
    for section in SECTION_NAMES:
        blocks = raw.get(section)
        if isinstance(blocks, list):
            sections[section] = [
                dict(block) for block in blocks if isinstance(block, Mapping)
            ]

    labels: Dict[str, str] = {}
    raw_labels = raw.get("labels")
    if isinstance(raw_labels, Mapping):
        labels = {str(k): str(v) for k, v in raw_labels.items() if v is not None}

    body: Dict[str, Any] = {}
    raw_body = raw.get("body")
    if isinstance(raw_body, Mapping):
        body = dict(raw_body)

    raw_page = raw.get("page")
    page_raw: Mapping[str, Any] = raw_page if isinstance(raw_page, Mapping) else {}

    return CourtFormProfile(
        id=str(raw.get("id") or profile_id),
        name=str(raw.get("name") or profile_id),
        jurisdiction=str(raw.get("jurisdiction") or ""),
        description=str(raw.get("description") or ""),
        page=PageSpec.from_mapping(page_raw),
        styles=styles,
        sections=sections,
        labels=labels,
        body=body,
        source_path=source_path,
    )


def list_court_form_profiles(
    extra_dirs: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    """Every profile that can be loaded, for a dropdown or an API listing.

    A profile found in an earlier directory wins, so a server's own copy of
    ``ma_trial_court.yml`` overrides the packaged one.
    """
    found: Dict[str, Dict[str, str]] = {}
    for directory in _profile_search_directories(extra_dirs):
        try:
            filenames = sorted(os.listdir(directory))
        except OSError:
            continue
        for filename in filenames:
            if not filename.lower().endswith((".yml", ".yaml")):
                continue
            profile_id = os.path.splitext(filename)[0]
            if profile_id in found:
                continue
            path = os.path.join(directory, filename)
            try:
                raw = _read_yaml_mapping(path)
            except Exception:
                continue
            found[profile_id] = {
                "id": str(raw.get("id") or profile_id),
                "name": str(raw.get("name") or profile_id),
                "jurisdiction": str(raw.get("jurisdiction") or ""),
                "description": str(raw.get("description") or ""),
                "path": path,
            }
    return sorted(found.values(), key=lambda item: item["name"].lower())


def load_court_form_profile(
    profile_id: Optional[str] = None,
    extra_dirs: Optional[Iterable[str]] = None,
    fragment_dirs: Optional[Iterable[str]] = None,
) -> CourtFormProfile:
    """Load one profile by id, falling back to the generic profile.

    An unknown id is not an error: drafting a usable generic court form beats
    refusing to draft anything because a caller named a court we do not ship.
    """
    wanted = str(profile_id or DEFAULT_PROFILE_ID).strip() or DEFAULT_PROFILE_ID
    directories = _profile_search_directories(extra_dirs)
    overrides = [str(d) for d in (fragment_dirs or []) if str(d or "").strip()]

    for candidate_id in (wanted, DEFAULT_PROFILE_ID):
        for directory in directories:
            for extension in (".yml", ".yaml"):
                path = os.path.join(directory, f"{candidate_id}{extension}")
                if os.path.isfile(path):
                    resolved = _resolve_extends(
                        _read_yaml_mapping(path), directories, [candidate_id]
                    )
                    profile = _profile_from_mapping(
                        candidate_id, resolved, source_path=path
                    )
                    profile.fragment_dirs = overrides
                    return profile

    # Nothing on disk at all: an empty profile still produces a plain document.
    return CourtFormProfile(id=wanted, name=wanted, fragment_dirs=overrides)


def find_docx_fragment(
    profile_id: str,
    section: str,
    extra_dirs: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Path to a Word-authored override for one section, when one exists.

    Looked up as ``<dir>/<profile_id>/<section>.docx``. The first hit wins, so a
    caller-supplied directory beats the packaged fragment.
    """
    section_name = str(section or "").strip()
    if not section_name:
        return None
    directories: List[str] = [
        str(d) for d in (extra_dirs or []) if str(d or "").strip()
    ]
    directories.append(packaged_fragment_directory())
    for directory in directories:
        path = os.path.join(directory, str(profile_id or ""), f"{section_name}.docx")
        if os.path.isfile(path):
            return path
    return None


def apply_profile_styles(document, profile: CourtFormProfile) -> List[str]:
    """Define the profile's styles in ``document``; return the names applied.

    Styles are created rather than inlined so that changing a court's font is an
    edit to the profile -- or to the generated document's style definition in
    Word -- instead of a sweep through every paragraph.
    """
    if docx is None:  # pragma: no cover - guarded by callers
        raise RuntimeError("python-docx is not installed in the python environment.")

    applied: List[str] = []
    for name, spec in profile.styles.items():
        style_type = (
            WD_STYLE_TYPE.CHARACTER
            if spec.style_type.startswith("char")
            else WD_STYLE_TYPE.PARAGRAPH
        )
        try:
            style = document.styles[name]
        except KeyError:
            try:
                style = document.styles.add_style(name, style_type)
            except Exception:
                continue

        if spec.based_on:
            try:
                style.base_style = document.styles[spec.based_on]
            except Exception:
                pass

        font = style.font
        if spec.font:
            font.name = spec.font
            # Word consults the east-asian font for many runs; without this the
            # requested face silently reverts for some characters.
            try:
                rpr = style.element.get_or_add_rPr()
                rfonts = rpr.get_or_add_rFonts()
                rfonts.set(qn("w:eastAsia"), spec.font)
                rfonts.set(qn("w:cs"), spec.font)
            except Exception:
                pass
        if spec.size is not None:
            font.size = Pt(spec.size)
        if spec.bold is not None:
            font.bold = spec.bold
        if spec.italic is not None:
            font.italic = spec.italic
        if spec.underline is not None:
            font.underline = spec.underline
        if spec.all_caps is not None:
            font.all_caps = spec.all_caps
        if spec.color:
            try:
                from docx.shared import RGBColor

                font.color.rgb = RGBColor.from_string(spec.color.lstrip("#").upper())
            except Exception:
                pass

        if style_type == WD_STYLE_TYPE.PARAGRAPH:
            paragraph_format = style.paragraph_format
            alignment = _alignment(spec.align)
            if alignment is not None:
                paragraph_format.alignment = alignment
            if spec.line_spacing is not None:
                paragraph_format.line_spacing = spec.line_spacing
                if abs(spec.line_spacing - 2.0) < 0.001:
                    paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
            if spec.space_before is not None:
                paragraph_format.space_before = Pt(spec.space_before)
            if spec.space_after is not None:
                paragraph_format.space_after = Pt(spec.space_after)
            if spec.left_indent is not None:
                paragraph_format.left_indent = Inches(spec.left_indent)
            if spec.first_line_indent is not None:
                paragraph_format.first_line_indent = Inches(spec.first_line_indent)
            if spec.keep_with_next is not None:
                paragraph_format.keep_with_next = spec.keep_with_next

        applied.append(name)

    return applied


def splice_docx_fragment(document, fragment_path: str, container=None) -> int:
    """Copy a Word-authored fragment into ``document``; return blocks copied.

    Used for the ``.docx`` override of a section. Everything in the fragment's
    body is copied except its ``sectPr``, which belongs to the fragment's own
    page setup and would otherwise start a new section in the assembled form.
    Styles the fragment defines are carried over when the target lacks them, so
    a caption authored in Word keeps its fonts.
    """
    if docx is None:  # pragma: no cover - guarded by callers
        raise RuntimeError("python-docx is not installed in the python environment.")

    fragment = docx.Document(fragment_path)
    _merge_missing_styles(document, fragment)

    target = container if container is not None else document.element.body
    anchor = None
    if container is None:
        # Keep the document's final sectPr last so page setup survives.
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:sectPr"):
                anchor = child
                break

    copied = 0
    for child in fragment.element.body.iterchildren():
        if child.tag == qn("w:sectPr"):
            continue
        new_child = copy.deepcopy(child)
        if anchor is not None:
            anchor.addprevious(new_child)
        else:
            target.append(new_child)
        copied += 1
    return copied


def _merge_missing_styles(document, fragment) -> None:
    """Bring over style definitions the target document does not already have."""
    try:
        target_styles = document.styles.element
        existing = {
            style.get(qn("w:styleId")) for style in target_styles.findall(qn("w:style"))
        }
        for style in fragment.styles.element.findall(qn("w:style")):
            style_id = style.get(qn("w:styleId"))
            if style_id and style_id not in existing:
                target_styles.append(copy.deepcopy(style))
                existing.add(style_id)
    except Exception:
        # A fragment without a styles part is fine; it just inherits the target's.
        pass
