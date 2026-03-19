from __future__ import annotations

import copy
import importlib.resources
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, cast

from ruamel.yaml import YAML

DEFAULT_LABELER_PROMPT_LIBRARY_PATH = "labeler_prompt_library.yml"

DEFAULT_LABELER_BRANDING: Dict[str, str] = {
    "favicon_url": "/packagestatic/docassemble.ALDashboard/litlabtheme/dal-favicon.png",
    "logo_url": "/packagestatic/docassemble.ALDashboard/litlabtheme/dal-favicon.png",
    "logo_alt": "LIT Lab logo",
    "docx_page_title": "AssemblyLine DOCX Labeler",
    "docx_header_title": "AssemblyLine DOCX Labeler",
    "docx_header_subtitle": "Add or edit Jinja2 template variables",
    "pdf_page_title": "AssemblyLine PDF Labeler",
    "pdf_header_title": "AssemblyLine PDF Labeler",
    "pdf_header_subtitle": "Add and edit PDF form fields using AI detection",
}

# Minimal fallback role description used only when the YAML prompt library
# cannot be loaded.  The full prompt text lives in
# data/sources/labeler_prompt_library.yml.
DEFAULT_STANDARD_ROLE_DESCRIPTION = (
    "You will process a DOCX document and return a JSON structure that turns "
    "the DOCX file into a template."
)


def _default_person_attributes() -> Dict[str, Any]:
    """Return the shared variable-tree schema used for person-like objects.

    Returns:
        Dict[str, Any]: Nested prompt-library metadata for common person fields.
    """
    return {
        "name": {
            "_description": "Name components",
            "first": "First name",
            "middle": "Middle name",
            "middle_initial()": "Middle initial",
            "last": "Last name",
            "suffix": "Suffix (Jr., Sr., III, etc.)",
            "full()": "Full name",
        },
        "address": {
            "_description": "Address components",
            "block()": "Full address (multiple lines)",
            "on_one_line()": "Full address (single line)",
            "line_one()": "Street + unit",
            "line_two()": "City, state, zip",
            "address": "Street address",
            "unit": "Unit/Apt/Suite",
            "city": "City",
            "state": "State",
            "zip": "ZIP/Postal code",
            "county": "County",
            "country": "Country",
        },
        "birthdate": "Date of birth",
        "age_in_years()": "Age (calculated)",
        "gender": "Gender",
        "gender_female": "Is female (checkbox)",
        "gender_male": "Is male (checkbox)",
        "gender_other": "Other gender (checkbox)",
        "gender_nonbinary": "Nonbinary (checkbox)",
        "gender_undisclosed": "Undisclosed (checkbox)",
        "phone_number": "Phone number",
        "mobile_number": "Mobile phone",
        "phone_numbers()": "All phone numbers",
        "email": "Email address",
        "signature": "Signature",
    }


def build_default_prompt_library() -> Dict[str, Any]:
    """Build the default prompt, branding, and variable configuration.

    Returns:
        Dict[str, Any]: The complete default labeler prompt library.
    """
    person_attributes = _default_person_attributes()
    attorney_attributes = copy.deepcopy(person_attributes)
    attorney_attributes["bar_number"] = "Bar/License number"
    return {
        "branding": copy.deepcopy(DEFAULT_LABELER_BRANDING),
        "docx": {
            "default_prompt_profile": "standard",
            "prompt_profiles": {
                "standard": {
                    "label": "General forms",
                    "help_text": "Use for most standard forms and letters.",
                    "role_description": DEFAULT_STANDARD_ROLE_DESCRIPTION,
                    "rules_addendum": "",
                    "temperature": 0.5,
                },
                "litigation_template": {
                    "label": "Litigation / pleading templates",
                    "help_text": "Use for pleadings with captions, drafting notes, and many visible blanks.",
                    "role_description": DEFAULT_STANDARD_ROLE_DESCRIPTION,
                    "rules_addendum": "",
                    "temperature": 0.5,
                },
            },
            "variable_tree": {
                "users": {
                    "_description": "People benefiting from the form (pro se filers)",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "other_parties": {
                    "_description": "Opposing/transactional parties",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "plaintiffs": {
                    "_description": "Plaintiffs in lawsuit",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "defendants": {
                    "_description": "Defendants in lawsuit",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "petitioners": {
                    "_description": "Petitioners",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "respondents": {
                    "_description": "Respondents",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "children": {
                    "_description": "Children involved",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "spouses": {
                    "_description": "Spouses",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "parents": {
                    "_description": "Parents",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "caregivers": {
                    "_description": "Caregivers",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "guardians": {
                    "_description": "Guardians",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "guardians_ad_litem": {
                    "_description": "Guardians ad litem",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "witnesses": {
                    "_description": "Witnesses",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "attorneys": {
                    "_description": "Attorneys",
                    "[0]": copy.deepcopy(attorney_attributes),
                },
                "translators": {
                    "_description": "Translators/Interpreters",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "creditors": {
                    "_description": "Creditors",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "debt_collectors": {
                    "_description": "Debt collectors",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "decedents": {
                    "_description": "Deceased persons",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "interested_parties": {
                    "_description": "Other interested parties",
                    "[0]": copy.deepcopy(person_attributes),
                },
                "trial_court": {
                    "_description": "Court information",
                    "name": "Court name",
                    "address": {
                        "county": "County",
                        "address": "Street address",
                        "city": "City",
                        "state": "State",
                    },
                    "division": "Division",
                    "department": "Department",
                },
                "docket_number": "Case/Docket number",
                "docket_numbers": "Multiple docket numbers (comma-separated)",
                "case_name": "Case name/caption",
                "signature_date": "Date form is signed",
                "user_needs_interpreter": "User needs interpreter (checkbox)",
                "user_preferred_language": "User's preferred language",
            },
        },
        "pdf": {
            "field_name_library": {
                "text": [
                    "users1_name_first",
                    "users1_name_last",
                    "users1_name_full",
                    "users1_address_address",
                    "users1_address_city",
                    "users1_address_state",
                    "users1_address_zip",
                    "users1_phone_number",
                    "users1_email",
                    "other_parties1_name_full",
                    "docket_number",
                    "case_name",
                ],
                "signature": [
                    "users1_signature",
                    "other_parties1_signature",
                    "attorney_signature",
                ],
                "checkbox": [
                    "user_agrees",
                    "is_plaintiff",
                    "is_defendant",
                    "has_children",
                ],
            }
        },
    }


def _deep_merge(
    base: Mapping[str, Any], overrides: Mapping[str, Any]
) -> Dict[str, Any]:
    """Recursively merge prompt-library overrides onto a default mapping.

    Args:
        base: The default mapping to merge into.
        overrides: User-provided override values.

    Returns:
        Dict[str, Any]: A deep-copied merged mapping.
    """
    merged: Dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in overrides.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(cast(Mapping[str, Any], merged[key]), value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def read_package_text_resource(
    resource_path: Optional[str],
    *,
    default_package: str = "docassemble.ALDashboard",
    default_folder: str,
) -> str:
    """Read text from an absolute path or package resource reference.

    Args:
        resource_path: Absolute path or ``package:path`` resource reference.
        default_package: Package name to use when no package prefix is supplied.
        default_folder: Default package folder prepended to relative paths.

    Returns:
        str: Resource text, or an empty string when it cannot be read.
    """
    raw_path = str(resource_path or "").strip()
    if not raw_path:
        return ""

    filesystem_path = Path(raw_path)
    if filesystem_path.is_absolute():
        try:
            return filesystem_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    package_name = default_package
    relative_path = raw_path
    if ":" in raw_path:
        package_name, relative_path = raw_path.split(":", 1)
        package_name = package_name.strip() or default_package
        relative_path = relative_path.strip()

    if not relative_path.startswith("data/"):
        relative_path = f"{default_folder.rstrip('/')}/{relative_path.lstrip('/')}"

    try:
        ref = importlib.resources.files(package_name) / relative_path
        with importlib.resources.as_file(ref) as path:
            if path.exists():
                return path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return ""


def load_labeler_prompt_library(
    prompt_library_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Load the prompt library and merge any configured overrides.

    Args:
        prompt_library_path: Optional override path for the prompt library YAML.

    Returns:
        Dict[str, Any]: The merged prompt library configuration.
    """
    library = build_default_prompt_library()
    resolved_path = prompt_library_path or DEFAULT_LABELER_PROMPT_LIBRARY_PATH
    raw_yaml = read_package_text_resource(
        resolved_path,
        default_folder="data/sources",
    )
    if not raw_yaml:
        return library

    try:
        yaml = YAML(typ="safe")
        parsed = yaml.load(raw_yaml)
    except Exception:
        return library

    if not isinstance(parsed, Mapping):
        return library
    return _deep_merge(library, cast(Mapping[str, Any], parsed))


def get_docx_prompt_profile(
    prompt_profile: Optional[str],
    *,
    prompt_library_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the requested DOCX prompt profile with sane fallbacks.

    Args:
        prompt_profile: Requested profile name.
        prompt_library_path: Optional override path for the prompt library YAML.

    Returns:
        Dict[str, Any]: The resolved prompt profile configuration.
    """
    library = load_labeler_prompt_library(prompt_library_path)
    docx_config = library.get("docx", {})
    if not isinstance(docx_config, Mapping):
        docx_config = {}
    prompt_profiles = docx_config.get("prompt_profiles", {})
    if not isinstance(prompt_profiles, Mapping):
        prompt_profiles = {}

    default_profile = (
        str(docx_config.get("default_prompt_profile") or "standard").strip()
        or "standard"
    )
    requested_profile = (
        str(prompt_profile or default_profile).strip() or default_profile
    )

    profile = prompt_profiles.get(requested_profile)
    if not isinstance(profile, Mapping):
        profile = prompt_profiles.get(default_profile)
    if not isinstance(profile, Mapping):
        profile = prompt_profiles.get("standard")
    if not isinstance(profile, Mapping):
        return {
            "name": default_profile,
            "label": "General forms",
            "help_text": "",
            "role_description": DEFAULT_STANDARD_ROLE_DESCRIPTION,
            "rules_addendum": "",
            "temperature": 0.5,
        }

    resolved = dict(profile)
    resolved["name"] = (
        requested_profile if requested_profile in prompt_profiles else default_profile
    )
    return resolved


def get_pdf_labeler_ui_config(
    *, prompt_library_path: Optional[str] = None
) -> Dict[str, Any]:
    """Return branding and field-name suggestions for the PDF labeler UI."""
    library = load_labeler_prompt_library(prompt_library_path)

    branding = library.get("branding", {})
    if not isinstance(branding, Mapping):
        branding = {}

    pdf_config = library.get("pdf", {})
    if not isinstance(pdf_config, Mapping):
        pdf_config = {}

    raw_field_name_library = pdf_config.get("field_name_library", {})
    if not isinstance(raw_field_name_library, Mapping):
        raw_field_name_library = {}

    field_name_library: Dict[str, Any] = {}
    for field_type, raw_names in raw_field_name_library.items():
        if not isinstance(raw_names, list):
            continue
        names = [str(name).strip() for name in raw_names if str(name).strip()]
        if names:
            field_name_library[str(field_type)] = names

    return {
        "branding": dict(branding),
        "field_name_library": field_name_library,
    }
