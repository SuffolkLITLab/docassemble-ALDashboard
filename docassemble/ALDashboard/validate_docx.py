# mypy: disable-error-code="override, assignment"
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from jinja2 import Undefined, DebugUndefined, ChainableUndefined
from jinja2.utils import missing
from docxtpl import DocxTemplate
import docx
from jinja2 import Environment, BaseLoader
from jinja2.ext import Extension
from jinja2.lexer import Token
import jinja2.exceptions
import os
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from urllib.request import urlopen
import zipfile
from lxml import etree as LET

try:
    import xmlschema
except ImportError:  # pragma: no cover - dependency may be optional in some envs
    xmlschema = None  # type: ignore[assignment]

__all__ = [
    "CallAndDebugUndefined",
    "analyze_docx_template_markup",
    "get_jinja_errors",
    "get_jinja_template_validation",
    "detect_docx_automation_features",
    "strip_docx_problem_controls",
    "validate_docx_ooxml_schema",
    "Environment",
    "BaseLoader",
]


_SPECIAL_DOCXTPL_PREFIX_PATTERN = re.compile(r"\{\{\s*(tr|tc|p|r)(?=[^\s\}])")
_SPECIAL_PARAGRAPH_TAG_PATTERN = re.compile(
    r"(\{\{\s*p(?=\s).*?\}\}|\{%\s*p(?=\s).*?%\})",
    re.DOTALL,
)
_OOXML_TRANSITIONAL_NS_PREFIX = "http://schemas.openxmlformats.org/"
_OOXML_STRICT_NS_PREFIX = "http://purl.oclc.org/ooxml/"
_OOXML_SCHEMA_DOWNLOADS = {
    "transitional": (
        "https://ecma-international.org/wp-content/uploads/ECMA-376-4_5th_edition_december_2016.zip",
        "OfficeOpenXML-XMLSchema-Transitional.zip",
    ),
    "strict": (
        "https://ecma-international.org/wp-content/uploads/ECMA-376-1_5th_edition_december_2016.zip",
        "OfficeOpenXML-XMLSchema-Strict.zip",
    ),
    "opc": (
        "https://ecma-international.org/wp-content/uploads/ECMA-376-2_5th_edition_december_2021.zip",
        "OpenPackagingConventions-XMLSchema.zip",
    ),
}
_OOXML_SCHEMA_CACHE: Dict[str, Any] = {}


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


def _build_da_environment() -> DAEnvironment:
    env = DAEnvironment(undefined=CallAndDebugUndefined, extensions=[DAExtension])
    env.filters.update(registered_jinja_filters)
    env.filters.update(builtin_jinja_filters)
    return env


def _normalize_jinja_source(source: str) -> str:
    return re.sub(r"({[%\{].*?[%\}]})", fix_quotes, source)


def _build_template_issue(
    code: str,
    message: str,
    exception: Optional[BaseException] = None,
) -> Dict[str, Any]:
    issue: Dict[str, Any] = {
        "code": code,
        "message": message,
    }
    lineno = getattr(exception, "lineno", None)
    if lineno is not None:
        issue["line"] = int(lineno)
    name = getattr(exception, "name", None)
    if name:
        issue["name"] = str(name)
    return issue


def _is_nonblocking_template_assertion(message: str) -> bool:
    return "No filter named" in message or "No test named" in message


def get_jinja_template_validation(source: str) -> Dict[str, Any]:
    """Parse Jinja source and return blocking errors and non-blocking warnings.

    The validation reuses the docx validator's custom environment so AssemblyLine's
    common filters behave the same here as they do during DOCX validation.
    Unknown filters/tests are returned as warnings so callers can warn without
    blocking a save.
    """
    env = _build_da_environment()
    normalized_source = _normalize_jinja_source(source)
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    try:
        parsed = env.parse(normalized_source)
    except jinja2.exceptions.TemplateSyntaxError as err:
        errors.append(_build_template_issue("template_syntax_error", str(err), err))
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
        }

    try:
        env.compile(parsed)
    except jinja2.exceptions.TemplateAssertionError as err:
        message = str(err)
        issue = _build_template_issue("template_assertion_error", message, err)
        if _is_nonblocking_template_assertion(message):
            warnings.append(issue)
        else:
            errors.append(issue)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def get_jinja_errors(the_file: str) -> Optional[str]:
    """Just try rendering the DOCX file as a Jinja2 template and catch any errors.
    Returns a string with the errors, if any.
    """
    env = _build_da_environment()

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


def _collect_paragraphs_from_table(table: Any, collected: List[Any], seen: Set[int]) -> None:
    for row in getattr(table, "rows", []):
        for cell in getattr(row, "cells", []):
            _collect_paragraphs_from_container(cell, collected, seen)


def _collect_paragraphs_from_container(
    container: Any, collected: List[Any], seen: Set[int]
) -> None:
    for paragraph in getattr(container, "paragraphs", []):
        paragraph_element_id = id(getattr(paragraph, "_p", paragraph))
        if paragraph_element_id in seen:
            continue
        seen.add(paragraph_element_id)
        collected.append(paragraph)

    for table in getattr(container, "tables", []):
        _collect_paragraphs_from_table(table, collected, seen)


def _collect_docx_paragraphs(document: Any) -> List[Any]:
    collected: List[Any] = []
    seen: Set[int] = set()
    _collect_paragraphs_from_container(document, collected, seen)
    for section in getattr(document, "sections", []):
        for part in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            _collect_paragraphs_from_container(part, collected, seen)
    return collected


def _build_markup_warning(
    *,
    code: str,
    message: str,
    paragraph: int,
    paragraph_text: str,
    match_text: Optional[str] = None,
) -> Dict[str, Any]:
    warning: Dict[str, Any] = {
        "code": code,
        "severity": "low",
        "message": message,
        "paragraph": paragraph,
        "paragraph_text": paragraph_text,
    }
    if match_text:
        warning["match_text"] = match_text
    return warning


def analyze_docx_template_markup(
    document: Union[docx.document.Document, str],
) -> List[Dict[str, Any]]:
    """Warn about likely-accidental docxtpl paragraph-tag usage patterns."""
    if isinstance(document, str):
        document = docx.Document(document)

    warnings: List[Dict[str, Any]] = []
    for paragraph_number, paragraph in enumerate(_collect_docx_paragraphs(document)):
        paragraph_text = paragraph.text or ""
        for match in _SPECIAL_DOCXTPL_PREFIX_PATTERN.finditer(paragraph_text):
            prefix = match.group(1)
            warnings.append(
                _build_markup_warning(
                    code="docxtpl_special_tag_missing_space",
                    message=(
                        f"docxtpl special tag '{{{{ {prefix} ... }}}}' should include a space after '{prefix}'. "
                        "Without that space, python-docx-template may treat it as a special structural tag by accident."
                    ),
                    paragraph=paragraph_number,
                    paragraph_text=paragraph_text,
                    match_text=match.group(0),
                )
            )

        paragraph_tags = _SPECIAL_PARAGRAPH_TAG_PATTERN.findall(paragraph_text)
        if paragraph_tags:
            remaining_text = paragraph_text
            for tag in paragraph_tags:
                remaining_text = remaining_text.replace(tag, " ")
            if remaining_text.strip():
                warnings.append(
                    _build_markup_warning(
                        code="docxtpl_paragraph_tag_with_surrounding_content",
                        message=(
                            "Paragraph-level docxtpl tags like '{{p ...}}' and '{%p ... %}' should usually be the only content in their paragraph. "
                            "Any surrounding text in that paragraph will be removed when the template renders."
                        ),
                        paragraph=paragraph_number,
                        paragraph_text=paragraph_text,
                    )
                )

    deduped: List[Dict[str, Any]] = []
    seen_keys: Set[str] = set()
    for warning in warnings:
        key = "|".join(
            [
                str(warning.get("code")),
                str(warning.get("paragraph")),
                str(warning.get("match_text") or ""),
            ]
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(warning)
    return deduped


def _discover_ooxml_schema_dir() -> Optional[str]:
    package_dir = Path(__file__).resolve().parent / "data" / "ooxml-schemas"
    candidates = [
        os.environ.get("ALDASHBOARD_OOXML_SCHEMA_DIR"),
        str(package_dir),
        "/usr/local/share/ooxml-schemas",
        "/usr/share/ooxml-schemas",
        "/opt/ooxml-schemas",
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def _directory_supports_writes(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            pass
        return True
    except OSError:
        return False


def _package_ooxml_schema_cache_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "ooxml-schemas"


def _default_ooxml_schema_cache_dir() -> Path:
    configured = os.environ.get("ALDASHBOARD_OOXML_SCHEMA_DIR")
    if configured:
        return Path(configured)

    package_dir = _package_ooxml_schema_cache_dir()
    if package_dir.exists() or _directory_supports_writes(package_dir):
        return package_dir

    return Path(tempfile.gettempdir()) / "aldashboard-ooxml-schemas"


def _download_extract_nested_zip(
    outer_url: str, nested_zip_name: str, target_dir: Path
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        outer_zip_path = Path(temp_dir) / "outer.zip"
        with urlopen(outer_url) as response, open(outer_zip_path, "wb") as out_handle:
            shutil.copyfileobj(response, out_handle)
        with zipfile.ZipFile(outer_zip_path, "r") as outer_zip:
            nested_bytes = outer_zip.read(nested_zip_name)
        nested_zip_path = Path(temp_dir) / "nested.zip"
        nested_zip_path.write_bytes(nested_bytes)
        with zipfile.ZipFile(nested_zip_path, "r") as nested_zip:
            nested_zip.extractall(target_dir)


def ensure_ooxml_schema_cache() -> Dict[str, str]:
    base_dir = _default_ooxml_schema_cache_dir()
    schema_dirs = {
        "transitional": str(base_dir / "transitional"),
        "strict": str(base_dir / "strict"),
        "opc": str(base_dir / "opc"),
    }
    required_files = {
        "transitional": "wml.xsd",
        "strict": "wml.xsd",
        "opc": "opc-relationships.xsd",
    }

    for key, (outer_url, nested_name) in _OOXML_SCHEMA_DOWNLOADS.items():
        target_dir = Path(schema_dirs[key])
        marker = target_dir / required_files[key]
        if marker.exists():
            continue

        try:
            _download_extract_nested_zip(outer_url, nested_name, target_dir)
        except OSError:
            fallback_base_dir = Path(tempfile.gettempdir()) / "aldashboard-ooxml-schemas"
            if fallback_base_dir == base_dir:
                raise
            base_dir = fallback_base_dir
            schema_dirs = {
                "transitional": str(base_dir / "transitional"),
                "strict": str(base_dir / "strict"),
                "opc": str(base_dir / "opc"),
            }
            target_dir = Path(schema_dirs[key])
            marker = target_dir / required_files[key]
            if marker.exists():
                continue
            _download_extract_nested_zip(outer_url, nested_name, target_dir)

    return schema_dirs


def _local_name_from_root_tag(tag: str) -> Tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _get_schema_entry_for_part(part_name: str, root: LET._Element, schema_dirs: Dict[str, str]) -> Optional[str]:
    namespace, local_name = _local_name_from_root_tag(str(root.tag))
    lower_name = part_name.lower()

    if lower_name == "[content_types].xml":
        return str(Path(schema_dirs["opc"]) / "opc-contentTypes.xsd")
    if lower_name.endswith(".rels"):
        return str(Path(schema_dirs["opc"]) / "opc-relationships.xsd")
    if lower_name == "docprops/core.xml":
        return str(Path(schema_dirs["opc"]) / "opc-coreProperties.xsd")
    if lower_name == "docprops/app.xml":
        return str(Path(schema_dirs["transitional"]) / "shared-documentPropertiesExtended.xsd")
    if lower_name == "docprops/custom.xml":
        return str(Path(schema_dirs["transitional"]) / "shared-documentPropertiesCustom.xsd")

    schema_family = None
    if namespace.startswith(_OOXML_TRANSITIONAL_NS_PREFIX):
        schema_family = "transitional"
    elif namespace.startswith(_OOXML_STRICT_NS_PREFIX):
        schema_family = "strict"
    if schema_family is None:
        return None

    family_dir = Path(schema_dirs[schema_family])
    if "wordprocessingml" in namespace:
        return str(family_dir / "wml.xsd")
    if "drawingml/chart" in namespace:
        return str(family_dir / "dml-chart.xsd")
    if "drawingml/spreadsheetDrawing" in namespace:
        return str(family_dir / "dml-spreadsheetDrawing.xsd")
    if "drawingml/wordprocessingDrawing" in namespace:
        return str(family_dir / "dml-wordprocessingDrawing.xsd")
    if "drawingml" in namespace or local_name == "theme":
        return str(family_dir / "dml-main.xsd")
    if "presentationml" in namespace:
        return str(family_dir / "pml.xsd")
    if "spreadsheetml" in namespace:
        return str(family_dir / "sml.xsd")
    if local_name == "Properties":
        return str(family_dir / "shared-customXmlDataProperties.xsd")
    return None


def _load_xmlschema(schema_path: str) -> Any:
    if schema_path not in _OOXML_SCHEMA_CACHE:
        if xmlschema is None:
            raise RuntimeError("xmlschema is not installed.")
        _OOXML_SCHEMA_CACHE[schema_path] = xmlschema.XMLSchema(schema_path)
    return _OOXML_SCHEMA_CACHE[schema_path]


def validate_docx_ooxml_schema(the_file: str) -> Dict[str, Any]:
    """Run strict XML checks and, when configured, OOXML schema validation."""
    report: Dict[str, Any] = {
        "available": False,
        "schema_dir": None,
        "validated_parts": [],
        "xml_parse_errors": [],
        "schema_errors": [],
        "skipped_parts": [],
    }

    if xmlschema is None:
        report["message"] = "xmlschema is not installed in the active Python environment."
        return report

    try:
        schema_dirs = ensure_ooxml_schema_cache()
    except Exception as err:
        report["message"] = f"Failed to prepare OOXML schemas: {err}"
        return report

    report["available"] = True
    report["schema_dir"] = str(Path(schema_dirs["transitional"]).parent)

    with zipfile.ZipFile(the_file, "r") as archive:
        xml_parts = [
            name for name in archive.namelist() if name.endswith(".xml") or name.endswith(".rels")
        ]
        for part_name in xml_parts:
            try:
                xml_bytes = archive.read(part_name)
                root = LET.fromstring(xml_bytes)
            except LET.XMLSyntaxError as err:
                report["xml_parse_errors"].append(
                    {"part": part_name, "error": str(err)}
                )
                continue

            schema_path = _get_schema_entry_for_part(part_name, root, schema_dirs)
            if not schema_path or not Path(schema_path).exists():
                report["skipped_parts"].append(
                    {"part": part_name, "reason": "no_schema_mapping"}
                )
                continue

            try:
                schema = _load_xmlschema(schema_path)
                schema.validate(xml_bytes)
                report["validated_parts"].append(part_name)
            except Exception as err:
                report["schema_errors"].append(
                    {
                        "part": part_name,
                        "schema": os.path.basename(schema_path),
                        "error": str(err),
                    }
                )

    report["message"] = (
        "Validated OOXML parts against cached ECMA-376 schemas."
        if not report["xml_parse_errors"] and not report["schema_errors"]
        else "OOXML validation found parse or schema issues."
    )
    return report


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
        if any(
            name.startswith("word/header") and name.endswith(".xml")
            for name in lower_names
        ):
            _note_hit(hits, "header_footer_content", "word/header*.xml")
        if any(
            name.startswith("word/footer") and name.endswith(".xml")
            for name in lower_names
        ):
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
            if (
                "attachedtemplate" in rels_text.lower()
                or "template" in rels_text.lower()
            ):
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
                item
                for item in details
                if item.get("code") != "structured_document_tags"
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


def _is_page_number_docpart_sdt(sdt_element: ET.Element) -> bool:
    is_docpart = False
    for desc in sdt_element.iter():
        desc_name = _local_name(desc.tag)
        if desc_name == "docPartObj":
            is_docpart = True
        if desc_name == "docPartGallery":
            gallery = (_get_attr(desc, "val") or "").lower()
            if "page numbers" in gallery:
                return True
    return False if is_docpart else False


def _is_allowed_simple_field(instr: str) -> bool:
    upper_instr = instr.upper()
    allowed_keywords = ("PAGE", "NUMPAGES", "SECTIONPAGES", "REF", "PAGEREF")
    return any(keyword in upper_instr for keyword in allowed_keywords)


def _replace_element_with_children(
    parent: ET.Element, index: int, element: ET.Element, children: List[ET.Element]
) -> None:
    parent.remove(element)
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)


def _replace_element_with_children_lxml(
    parent: LET._Element,
    index: int,
    element: LET._Element,
    children: List[LET._Element],
) -> None:
    parent.remove(element)
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)


def _strip_controls_from_parent(parent: ET.Element, counts: Dict[str, int]) -> bool:
    changed = False
    i = 0
    while i < len(parent):
        child = parent[i]
        name = _local_name(child.tag)

        if name in {"ins", "moveTo"}:
            _replace_element_with_children(parent, i, child, list(child))
            counts["unwrapped_track_changes"] += 1
            changed = True
            continue

        if name in {"del", "moveFrom"}:
            parent.remove(child)
            counts["removed_track_changes"] += 1
            changed = True
            continue

        if name == "rPr":
            removed_hidden = False
            for prop in list(child):
                if _local_name(prop.tag) in {"vanish", "webHidden"}:
                    child.remove(prop)
                    counts["removed_hidden_run_properties"] += 1
                    removed_hidden = True
            if removed_hidden:
                changed = True

        if name == "sdt":
            if _is_page_number_docpart_sdt(child):
                if _strip_controls_from_parent(child, counts):
                    changed = True
                i += 1
                continue
            sdt_content = None
            for sub in child:
                if _local_name(sub.tag) == "sdtContent":
                    sdt_content = sub
                    break
            if sdt_content is not None:
                _replace_element_with_children(parent, i, child, list(sdt_content))
            else:
                parent.remove(child)
            counts["removed_sdt"] += 1
            changed = True
            continue

        if name == "fldSimple":
            instr = _get_attr(child, "instr") or ""
            if _is_allowed_simple_field(instr):
                if _strip_controls_from_parent(child, counts):
                    changed = True
                i += 1
                continue
            _replace_element_with_children(parent, i, child, list(child))
            counts["removed_fldSimple"] += 1
            changed = True
            continue

        if _strip_controls_from_parent(child, counts):
            changed = True
        i += 1
    return changed


def _strip_controls_from_parent_lxml(
    parent: LET._Element, counts: Dict[str, int]
) -> bool:
    changed = False
    i = 0
    while i < len(parent):
        child = parent[i]
        name = _local_name(str(child.tag))

        if name in {"ins", "moveTo"}:
            _replace_element_with_children_lxml(parent, i, child, list(child))
            counts["unwrapped_track_changes"] += 1
            changed = True
            continue

        if name in {"del", "moveFrom"}:
            parent.remove(child)
            counts["removed_track_changes"] += 1
            changed = True
            continue

        if name == "rPr":
            removed_hidden = False
            for prop in list(child):
                if _local_name(str(prop.tag)) in {"vanish", "webHidden"}:
                    child.remove(prop)
                    counts["removed_hidden_run_properties"] += 1
                    removed_hidden = True
            if removed_hidden:
                changed = True

        if name == "sdt":
            if _is_page_number_docpart_sdt(child):  # type: ignore[arg-type]
                if _strip_controls_from_parent_lxml(child, counts):
                    changed = True
                i += 1
                continue
            sdt_content = None
            for sub in child:
                if _local_name(str(sub.tag)) == "sdtContent":
                    sdt_content = sub
                    break
            if sdt_content is not None:
                _replace_element_with_children_lxml(parent, i, child, list(sdt_content))
            else:
                parent.remove(child)
            counts["removed_sdt"] += 1
            changed = True
            continue

        if name == "fldSimple":
            instr = _get_attr(child, "instr") or ""  # type: ignore[arg-type]
            if _is_allowed_simple_field(instr):
                if _strip_controls_from_parent_lxml(child, counts):
                    changed = True
                i += 1
                continue
            _replace_element_with_children_lxml(parent, i, child, list(child))
            counts["removed_fldSimple"] += 1
            changed = True
            continue

        if _strip_controls_from_parent_lxml(child, counts):
            changed = True
        i += 1
    return changed


def strip_docx_problem_controls(input_file: str, output_file: str) -> Dict[str, Any]:
    """Create a cleaned DOCX with risky SDTs and non-whitelisted simple fields removed.

    Keeps page-number docpart SDTs and simple fields for page numbers/cross-references.
    """
    counts: Dict[str, int] = {
        "removed_sdt": 0,
        "removed_fldSimple": 0,
        "removed_track_changes": 0,
        "unwrapped_track_changes": 0,
        "removed_hidden_run_properties": 0,
    }
    modified_parts: Dict[str, bytes] = {}

    with zipfile.ZipFile(input_file, "r") as archive:
        part_infos = archive.infolist()
        parser = LET.XMLParser(remove_blank_text=False, recover=True)
        for info in part_infos:
            part_name = info.filename
            is_target_part = (
                part_name == "word/document.xml"
                or (part_name.startswith("word/header") and part_name.endswith(".xml"))
                or (part_name.startswith("word/footer") and part_name.endswith(".xml"))
            )
            if not is_target_part:
                continue
            try:
                original_bytes = archive.read(part_name)
                root = LET.fromstring(original_bytes, parser=parser)
            except LET.XMLSyntaxError:
                continue
            if _strip_controls_from_parent_lxml(root, counts):
                modified_parts[part_name] = LET.tostring(
                    root, encoding="utf-8", xml_declaration=True
                )

        with zipfile.ZipFile(output_file, "w") as out_zip:
            for info in part_infos:
                part_name = info.filename
                if part_name in modified_parts:
                    out_zip.writestr(info, modified_parts[part_name])
                else:
                    out_zip.writestr(info, archive.read(part_name))

    return {
        "modified": bool(modified_parts),
        "parts_modified": len(modified_parts),
        **counts,
    }
