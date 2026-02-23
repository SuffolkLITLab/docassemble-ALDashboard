# mypy: disable-error-code="override, assignment"
from typing import Any, Callable, Dict, List, Optional, Set
from jinja2 import Undefined, DebugUndefined, ChainableUndefined
from jinja2.utils import missing
from docxtpl import DocxTemplate
from jinja2 import Environment, BaseLoader
from jinja2.ext import Extension
from jinja2.lexer import Token
import jinja2.exceptions
import re
import xml.etree.ElementTree as ET
import zipfile

__all__ = [
    "CallAndDebugUndefined",
    "get_jinja_errors",
    "detect_docx_automation_features",
    "Environment",
    "BaseLoader",
]


class DAIndexError(IndexError):
    pass


class DAAttributeError(AttributeError):
    pass


nameerror_match = re.compile(
    r"\'(.*)\' (is not defined|referenced before assignment|is undefined)"
)


def extract_missing_name(the_error):
    m = nameerror_match.search(str(the_error))
    if m:
        return m.group(1)
    raise the_error


class DAEnvironment(Environment):
    def from_string(self, source, **kwargs):  # pylint: disable=arguments-differ
        source = re.sub(r"({[\%\{].*?[\%\}]})", fix_quotes, source)
        return super().from_string(source, **kwargs)

    def getitem(self, obj, argument):
        try:
            return obj[argument]
        except (DAAttributeError, DAIndexError) as err:
            varname = extract_missing_name(err)
            return self.undefined(obj=missing, name=varname)
        except (AttributeError, TypeError, LookupError):
            return self.undefined(obj=obj, name=argument, accesstype="item")

    def getattr(self, obj, attribute):
        try:
            return getattr(obj, attribute)
        except DAAttributeError as err:
            varname = extract_missing_name(err)
            return self.undefined(obj=missing, name=varname)
        except:
            return self.undefined(obj=obj, name=attribute, accesstype="attribute")


def fix_quotes(match):
    instring = match.group(1)
    n = len(instring)
    output = ""
    i = 0
    while i < n:
        if instring[i] == "\u201c" or instring[i] == "\u201d":
            output += '"'
        elif instring[i] == "\u2018" or instring[i] == "\u2019":
            output += "'"
        elif instring[i] == "&" and i + 4 < n and instring[i : i + 5] == "&amp;":
            output += "&"
            i += 4
        else:
            output += instring[i]
        i += 1
    return output


class CallAndDebugUndefined(DebugUndefined):
    """Handles Jinja2 undefined errors by printing the name of the undefined variable.
    Extended to handle callable methods.
    """

    def __call__(self, *pargs, **kwargs):
        return self

    def __getattr__(self, _: str) -> "CallAndDebugUndefined":
        return self

    __getitem__ = __getattr__  # type: ignore


null_func: Callable = lambda *y: y

registered_jinja_filters: dict = {}

builtin_jinja_filters = {
    "ampersand_filter": null_func,
    "markdown": null_func,
    "add_separators": null_func,
    "inline_markdown": null_func,
    "paragraphs": null_func,
    "manual_line_breaks": null_func,
    "RichText": null_func,
    "groupby": null_func,
    "max": null_func,
    "min": null_func,
    "sum": null_func,
    "unique": null_func,
    "join": null_func,
    "attr": null_func,
    "selectattr": null_func,
    "rejectattr": null_func,
    "sort": null_func,
    "dictsort": null_func,
    "nice_number": null_func,
    "ordinal": null_func,
    "ordinal_number": null_func,
    "currency": null_func,
    "comma_list": null_func,
    "comma_and_list": null_func,
    "capitalize": null_func,
    "salutation": null_func,
    "alpha": null_func,
    "roman": null_func,
    "word": null_func,
    "bold": null_func,
    "italic": null_func,
    "title_case": null_func,
    "single_paragraph": null_func,
    "phone_number_formatted": null_func,
    "phone_number_in_e164": null_func,
    "country_name": null_func,
    "fix_punctuation": null_func,
    "redact": null_func,
    "verbatim": null_func,
    "map": null_func,
    "chain": null_func,
    "catchall_options": null_func,
    "catchall_label": null_func,
    "catchall_datatype": null_func,
    "catchall_question": null_func,
    "catchall_subquestion": null_func,
    "any": any,
    "all": all,
}


class DAExtension(Extension):
    def parse(self, parser):
        raise NotImplementedError()

    def filter_stream(self, stream):
        # in_var = False
        met_pipe = False
        for token in stream:
            if token.type == "variable_begin":
                # in_var = True
                met_pipe = False
            if token.type == "variable_end":
                # in_var = False
                if not met_pipe:
                    yield Token(token.lineno, "pipe", None)
                    yield Token(token.lineno, "name", "ampersand_filter")
            # if in_var and token.type == 'pipe':
            #     met_pipe = True
            yield token


def get_jinja_errors(the_file: str) -> Optional[str]:
    """Just try rendering the DOCX file as a Jinja2 template and catch any errors.
    Returns a string with the errors, if any.
    """
    env = DAEnvironment(undefined=CallAndDebugUndefined, extensions=[DAExtension])
    env.filters.update(registered_jinja_filters)
    env.filters.update(builtin_jinja_filters)

    doc = DocxTemplate(the_file)
    try:
        doc.render({}, jinja_env=env)
        return None
    except jinja2.exceptions.TemplateSyntaxError as the_error:
        errmess = str(the_error)
        extra_context = the_error.docx_context if hasattr(the_error, "docx_context") else []  # type: ignore
        if extra_context:
            errmess += "\n\nContext:\n" + "\n".join(
                map(lambda x: "  " + x, extra_context)
            )
        return errmess
    except jinja2.exceptions.TemplateError as the_error:
        return str(the_error)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _get_attr(element: ET.Element, attr_name: str) -> Optional[str]:
    for key, value in element.attrib.items():
        if key == attr_name or key.endswith("}" + attr_name):
            return str(value)
    return None


def _note_hit(
    hits: Dict[str, Set[str]], code: str, part_name: str, evidence: Optional[str] = None
) -> None:
    if code not in hits:
        hits[code] = set()
    if evidence:
        hits[code].add(f"{part_name}: {evidence}")
    else:
        hits[code].add(part_name)


def _scan_xml_part(part_name: str, content: bytes, hits: Dict[str, Set[str]]) -> None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return

    for element in root.iter():
        if _local_name(element.tag) != "sdt":
            continue
        is_docpart = False
        page_number_docpart = False
        docpart_gallery_values: Set[str] = set()
        for desc in element.iter():
            desc_name = _local_name(desc.tag)
            if desc_name == "docPartObj":
                is_docpart = True
            if desc_name == "docPartGallery":
                gallery = (_get_attr(desc, "val") or "").lower()
                if gallery:
                    docpart_gallery_values.add(gallery)
                if "page numbers" in gallery:
                    page_number_docpart = True
        if is_docpart and page_number_docpart:
            _note_hit(hits, "benign_page_number_sdt", part_name)
        else:
            _note_hit(hits, "structured_document_tags", part_name)
        if is_docpart and not page_number_docpart:
            gallery_text = ", ".join(sorted(docpart_gallery_values))
            _note_hit(
                hits,
                "sdt_docpart_non_page_numbers",
                part_name,
                gallery_text or "docPartObj",
            )

    for element in root.iter():
        name = _local_name(element.tag)

        if name == "sdtPr":
            for child in element:
                child_name = _local_name(child.tag)
                if child_name in {
                    "comboBox",
                    "dropDownList",
                    "date",
                    "checkBox",
                    "text",
                    "group",
                    "richText",
                    "picture",
                    "repeatingSection",
                    "repeatingSectionItem",
                }:
                    _note_hit(
                        hits,
                        "sdt_specialized_controls",
                        part_name,
                        f"<{child_name}>",
                    )
                if child_name == "text":
                    _note_hit(hits, "sdt_plain_text_control", part_name, "<text>")
                if child_name == "group":
                    _note_hit(hits, "sdt_group_control", part_name, "<group>")
                if child_name in {"tag", "alias"}:
                    tag_value = _get_attr(child, "val")
                    _note_hit(
                        hits,
                        "sdt_metadata",
                        part_name,
                        f"{child_name}={tag_value}" if tag_value else child_name,
                    )
                if child_name in {"dataBinding", "placeholder", "lock"}:
                    value = _get_attr(child, "val")
                    _note_hit(
                        hits,
                        "sdt_bound_or_locked",
                        part_name,
                        f"{child_name}={value}" if value else child_name,
                    )
        if name == "dataBinding":
            _note_hit(hits, "data_binding", part_name)
        if name == "fldSimple":
            _note_hit(hits, "classic_fields", part_name, "fldSimple")
        if name == "fldChar":
            field_type = _get_attr(element, "fldCharType")
            _note_hit(
                hits,
                "classic_fields",
                part_name,
                f"fldCharType={field_type}" if field_type else "fldChar",
            )
        if name == "instrText":
            instr = (element.text or "").strip()
            if instr:
                _note_hit(hits, "classic_fields", part_name, "instrText")
            upper_instr = instr.upper()
            for keyword in (
                "MERGEFIELD",
                "DOCPROPERTY",
                "REF",
                "PAGEREF",
                "IF",
                "SET",
                "ASK",
                "FILLIN",
                "FORMTEXT",
                "HYPERLINK",
            ):
                if keyword in upper_instr:
                    _note_hit(
                        hits,
                        "field_instructions",
                        part_name,
                        f"instrText contains {keyword}",
                    )
            if "REF" in upper_instr or "PAGEREF" in upper_instr:
                _note_hit(
                    hits,
                    "bookmark_ref_fields",
                    part_name,
                    "instrText contains REF/PAGEREF",
                )
        if name == "ffData":
            _note_hit(hits, "legacy_form_fields", part_name)
        if name in {"object", "OLEObject"}:
            _note_hit(hits, "ole_or_object_controls", part_name, f"<{name}>")
        if name in {"shape", "textbox", "pict"}:
            _note_hit(hits, "textboxes_or_vml_shapes", part_name, f"<{name}>")
        if name in {"ins", "del", "moveFrom", "moveTo"}:
            _note_hit(hits, "track_changes", part_name, f"<{name}>")
        if name in {"commentRangeStart", "commentRangeEnd", "commentReference"}:
            _note_hit(hits, "comments_or_annotations", part_name, f"<{name}>")
        if name in {"bookmarkStart", "bookmarkEnd"}:
            _note_hit(hits, "bookmarks", part_name, f"<{name}>")
        if name == "hyperlink":
            _note_hit(hits, "hyperlinks", part_name)
        if name in {"vanish", "webHidden"}:
            _note_hit(hits, "generated_run_properties", part_name, f"<{name}>")
        if name == "AlternateContent":
            _note_hit(hits, "drawing_or_compat_content", part_name, f"<{name}>")
        if name == "customXml":
            _note_hit(hits, "custom_xml_wrappers", part_name)

    for paragraph in root.iter():
        if _local_name(paragraph.tag) != "p":
            continue
        run_count = 0
        text_chars = 0
        for child in paragraph.iter():
            child_name = _local_name(child.tag)
            if child_name == "r":
                run_count += 1
            elif child_name == "t":
                text_chars += len((child.text or "").strip())
        if text_chars >= 40 and run_count >= 12:
            _note_hit(
                hits,
                "fragmented_runs",
                part_name,
                f"paragraph has {run_count} runs over {text_chars} visible chars",
            )


def detect_docx_automation_features(the_file: str) -> Dict[str, Any]:
    """Detect non-plain-text DOCX constructs that often come from Word-centric automation systems."""
    hits: Dict[str, Set[str]] = {}

    with zipfile.ZipFile(the_file, "r") as archive:
        part_names = archive.namelist()
        xml_parts = [
            name
            for name in part_names
            if name.endswith(".xml")
            and (
                name.startswith("word/")
                or name.startswith("customXml/")
                or name.endswith(".rels")
            )
        ]

        for part_name in xml_parts:
            try:
                _scan_xml_part(part_name, archive.read(part_name), hits)
            except KeyError:
                continue

        lower_names = {name.lower() for name in part_names}
        if any(name.startswith("customxml/") for name in lower_names):
            _note_hit(hits, "custom_xml_parts", "customXml/")
        if any(name.startswith("word/activex/") for name in lower_names):
            _note_hit(hits, "activex_controls", "word/activeX/")
        if any(name.startswith("word/embeddings/") for name in lower_names):
            _note_hit(hits, "embedded_ole_payloads", "word/embeddings/")
        if "word/comments.xml" in lower_names:
            _note_hit(hits, "comments_or_annotations", "word/comments.xml")
        if any(name.startswith("word/header") and name.endswith(".xml") for name in lower_names):
            _note_hit(hits, "header_footer_content", "word/header*.xml")
        if any(name.startswith("word/footer") and name.endswith(".xml") for name in lower_names):
            _note_hit(hits, "header_footer_content", "word/footer*.xml")
        if "word/attachedtemplate.xml" in lower_names:
            _note_hit(hits, "attached_template", "word/attachedTemplate.xml")
        if "word/vbaproject.bin" in lower_names:
            _note_hit(hits, "macro_project", "word/vbaProject.bin")
        if any(name.startswith("word/diagrams/") for name in lower_names):
            _note_hit(hits, "diagram_content", "word/diagrams/")
        if any(name.startswith("word/charts/") for name in lower_names):
            _note_hit(hits, "chart_content", "word/charts/")
        if any(name.startswith("word/drawings/") for name in lower_names):
            _note_hit(hits, "drawing_parts", "word/drawings/")

        if "word/_rels/document.xml.rels" in lower_names:
            try:
                rels_text = archive.read("word/_rels/document.xml.rels").decode(
                    "utf-8", errors="ignore"
                )
            except KeyError:
                rels_text = ""
            if "customxml" in rels_text.lower():
                _note_hit(
                    hits,
                    "custom_xml_relationships",
                    "word/_rels/document.xml.rels",
                    "references customXml",
                )
            if "attachedtemplate" in rels_text.lower() or "template" in rels_text.lower():
                _note_hit(
                    hits,
                    "attached_template",
                    "word/_rels/document.xml.rels",
                    "template relationship present",
                )

    rulebook: Dict[str, Dict[str, str]] = {
        "data_binding": {
            "severity": "high",
            "message": "Data-bound content controls (w:dataBinding) detected.",
        },
        "custom_xml_parts": {
            "severity": "high",
            "message": "customXml parts detected in the DOCX package.",
        },
        "custom_xml_relationships": {
            "severity": "high",
            "message": "Document relationships reference customXml parts.",
        },
        "classic_fields": {
            "severity": "high",
            "message": "Classic Word fields (w:fldSimple / w:fldChar / w:instrText) detected.",
        },
        "field_instructions": {
            "severity": "high",
            "message": "Field instructions like MERGEFIELD/DOCPROPERTY/IF/REF detected.",
        },
        "legacy_form_fields": {
            "severity": "high",
            "message": "Legacy form field data (w:ffData) detected.",
        },
        "activex_controls": {
            "severity": "high",
            "message": "ActiveX controls present (word/activeX/).",
        },
        "embedded_ole_payloads": {
            "severity": "high",
            "message": "Embedded OLE payloads present (word/embeddings/).",
        },
        "ole_or_object_controls": {
            "severity": "high",
            "message": "OLE/object elements detected in document XML.",
        },
        "structured_document_tags": {
            "severity": "medium",
            "message": "Structured Document Tags (content controls, w:sdt) detected.",
        },
        "sdt_specialized_controls": {
            "severity": "medium",
            "message": "Specialized SDT controls (dropdown/date/checkbox/etc.) detected.",
        },
        "sdt_plain_text_control": {
            "severity": "medium",
            "message": "Plain-text SDT controls (w:text) detected.",
        },
        "sdt_group_control": {
            "severity": "medium",
            "message": "Grouped SDT controls (w:group) detected.",
        },
        "sdt_docpart_non_page_numbers": {
            "severity": "medium",
            "message": "Non-page-number SDT docPart controls detected.",
        },
        "sdt_metadata": {
            "severity": "medium",
            "message": "SDT metadata tags/aliases detected.",
        },
        "sdt_bound_or_locked": {
            "severity": "medium",
            "message": "SDTs include dataBinding/placeholder/lock settings.",
        },
        "track_changes": {
            "severity": "medium",
            "message": "Track changes elements (w:ins/w:del/move*) detected.",
        },
        "comments_or_annotations": {
            "severity": "medium",
            "message": "Comments/annotation markers detected.",
        },
        "header_footer_content": {
            "severity": "medium",
            "message": "Document contains header/footer XML parts that may hold template content.",
        },
        "textboxes_or_vml_shapes": {
            "severity": "medium",
            "message": "Textbox/shape content detected (pict/shape/textbox).",
        },
        "drawing_or_compat_content": {
            "severity": "medium",
            "message": "Compatibility fallback content blocks (mc:AlternateContent) detected.",
        },
        "attached_template": {
            "severity": "medium",
            "message": "Attached template metadata/relationship detected.",
        },
        "macro_project": {
            "severity": "high",
            "message": "Macro project payload detected (vbaProject.bin).",
        },
        "bookmark_ref_fields": {
            "severity": "low",
            "message": "REF/PAGEREF field instructions detected (often paired with bookmarks in Word templates).",
        },
        "hyperlinks": {
            "severity": "low",
            "message": "Hyperlink elements detected.",
        },
        "diagram_content": {
            "severity": "low",
            "message": "SmartArt/diagram parts detected (word/diagrams/).",
        },
        "chart_content": {
            "severity": "low",
            "message": "Chart parts detected (word/charts/).",
        },
        "drawing_parts": {
            "severity": "low",
            "message": "Drawing parts detected (word/drawings/).",
        },
        "custom_xml_wrappers": {
            "severity": "low",
            "message": "customXml wrappers detected in XML content.",
        },
        "generated_run_properties": {
            "severity": "low",
            "message": "Hidden run properties (vanish/webHidden) detected.",
        },
        "fragmented_runs": {
            "severity": "low",
            "message": "Heavily fragmented runs detected in visible text paragraphs.",
        },
    }

    details: List[Dict[str, Any]] = []
    for code, meta in rulebook.items():
        if code not in hits:
            continue
        evidence = sorted(hits[code])
        details.append(
            {
                "code": code,
                "severity": meta["severity"],
                "message": meta["message"],
                "count": len(evidence),
                "evidence": evidence[:8],
            }
        )

    details.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(str(item.get("severity")), 3),
            str(item.get("code")),
        )
    )

    # Suppress benign page-number fields in headers/footers.
    if "classic_fields" in hits:
        classic_evidence = hits["classic_fields"]
        only_header_footer = all(
            evidence.startswith("word/header") or evidence.startswith("word/footer")
            for evidence in classic_evidence
        )
        has_risky_field_instructions = (
            "field_instructions" in hits or "bookmark_ref_fields" in hits
        )
        if only_header_footer and not has_risky_field_instructions:
            details = [item for item in details if item.get("code") != "classic_fields"]

    # Suppress benign page-number SDT wrappers.
    if "structured_document_tags" in hits and "benign_page_number_sdt" in hits:
        sdt_evidence = hits["structured_document_tags"]
        benign_evidence = hits["benign_page_number_sdt"]
        if sdt_evidence.issubset(benign_evidence):
            details = [
                item for item in details if item.get("code") != "structured_document_tags"
            ]

    # Keep only actionable warnings for Jinja/docxtpl workflows.
    keep_codes = {
        "fragmented_runs",
        "classic_fields",
        "field_instructions",
        "bookmark_ref_fields",
        "structured_document_tags",
        "sdt_specialized_controls",
        "sdt_metadata",
        "sdt_bound_or_locked",
        "sdt_plain_text_control",
        "sdt_group_control",
        "sdt_docpart_non_page_numbers",
        "data_binding",
        "custom_xml_parts",
        "custom_xml_relationships",
        "legacy_form_fields",
        "activex_controls",
        "embedded_ole_payloads",
        "ole_or_object_controls",
    }
    details = [item for item in details if str(item.get("code")) in keep_codes]

    # customXml without any paired automation markers (SDT/dataBinding/field controls)
    # is often package residue and too noisy to warn on by itself.
    if any(
        str(item.get("code")) in {"custom_xml_parts", "custom_xml_relationships"}
        for item in details
    ):
        non_custom_codes = {
            str(item.get("code"))
            for item in details
            if str(item.get("code"))
            not in {"custom_xml_parts", "custom_xml_relationships", "fragmented_runs"}
        }
        if not non_custom_codes:
            details = [
                item
                for item in details
                if str(item.get("code"))
                not in {"custom_xml_parts", "custom_xml_relationships"}
            ]

    return {
        "has_warnings": bool(details),
        "warnings": [str(item["message"]) for item in details],
        "warning_details": details,
    }
