import copy
import docx
import sys

import tiktoken
import json
import html
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import re
from docassemble.ALToolbox.llms import chat_completion
from openai import OpenAI

from typing import Any, Dict, List, Tuple, Optional, Union, Sequence, Set

__all__ = [
    "get_labeled_docx_runs",
    "get_docx_run_text",
    "get_docx_run_items",
    "update_docx",
    "modify_docx_with_openai_guesses",
]


def _coerce_modified_run_item(
    item: Any,
) -> Optional[Tuple[int, int, str, int]]:
    """Normalize one model result into (paragraph, run, text, paragraph_delta)."""
    if isinstance(item, dict):
        paragraph_number = item.get("paragraph")
        run_number = item.get("run")
        modified_text = item.get("text")
        new_paragraph = item.get("new_paragraph", 0)
    elif isinstance(item, (list, tuple)) and len(item) >= 4:
        paragraph_number, run_number, modified_text, new_paragraph = item[:4]
    else:
        return None

    if paragraph_number is None:
        return None
    try:
        paragraph_number = int(paragraph_number)
    except (TypeError, ValueError):
        return None
    if run_number is None:
        return None
    try:
        run_number = int(run_number)
    except (TypeError, ValueError):
        # Some models emit [paragraph, original_text, replacement_text, ...].
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 3
            and isinstance(item[1], str)
            and item[2] is not None
        ):
            run_number = 0
            modified_text = item[2]
            new_paragraph = 0
        else:
            return None

    if paragraph_number < 0:
        return None
    if run_number < 0:
        run_number = 0

    if isinstance(new_paragraph, bool):
        new_paragraph = 0
    else:
        try:
            new_paragraph = int(new_paragraph)
        except (TypeError, ValueError):
            new_paragraph = 0
    if new_paragraph not in (-1, 0, 1):
        new_paragraph = 0

    if modified_text is None:
        return None

    return (paragraph_number, run_number, str(modified_text), new_paragraph)


def _normalize_modified_runs(
    modified_runs: Sequence[Any],
) -> List[Tuple[int, int, str, int]]:
    normalized: List[Tuple[int, int, str, int]] = []
    for item in modified_runs:
        coerced = _coerce_modified_run_item(item)
        if coerced is not None:
            normalized.append(coerced)
    return normalized


def _extract_model_results(response: Any) -> List[Any]:
    """Extract a best-effort list of run updates from varied model JSON shapes."""
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        return []

    results = response.get("results")
    if isinstance(results, list):
        return results

    for alt_key in ("suggestions", "items", "changes", "labels"):
        alt = response.get(alt_key)
        if isinstance(alt, list):
            return alt

    # Some lightweight models return {"p,r": "replacement text"} maps.
    mapped_results: List[Any] = []
    for key, value in response.items():
        if not isinstance(key, str):
            continue
        match = re.match(r"^\s*(\d+)\s*,\s*(\d+)\s*$", key)
        if not match:
            continue
        if value is None:
            continue
        paragraph_number = int(match.group(1))
        run_number = int(match.group(2))
        if isinstance(value, dict):
            text_value = value.get("text")
            new_paragraph = value.get("new_paragraph", 0)
        else:
            text_value = value
            new_paragraph = 0
        mapped_results.append(
            [paragraph_number, run_number, str(text_value), new_paragraph]
        )
    return mapped_results


def _append_text_content(run_element: Any, text: str) -> None:
    """Append text to a w:r element, preserving tabs/newlines in WordprocessingML."""
    parts = re.split(r"(\t|\n)", text)
    for part in parts:
        if part == "\t":
            run_element.append(OxmlElement("w:tab"))
            continue
        if part == "\n":
            run_element.append(OxmlElement("w:br"))
            continue
        if not part:
            continue

        text_element = OxmlElement("w:t")
        # Preserve leading/trailing spaces exactly when present.
        if part[:1].isspace() or part[-1:].isspace():
            text_element.set(qn("xml:space"), "preserve")
        text_element.text = part
        run_element.append(text_element)


def _collect_paragraphs_from_table(
    table: Any, collected: List[Any], seen_elements: set
) -> None:
    for row in table.rows:
        for cell in row.cells:
            _collect_paragraphs_from_container(cell, collected, seen_elements)


def _collect_paragraphs_from_container(
    container: Any, collected: List[Any], seen_elements: set
) -> None:
    for paragraph in getattr(container, "paragraphs", []):
        paragraph_element_id = id(paragraph._element)
        if paragraph_element_id not in seen_elements:
            seen_elements.add(paragraph_element_id)
            collected.append(paragraph)

    for table in getattr(container, "tables", []):
        _collect_paragraphs_from_table(table, collected, seen_elements)


def _collect_target_paragraphs(document: Any) -> List[Any]:
    """Collect paragraphs from body, tables, headers, and footers."""
    collected: List[Any] = []
    seen_elements: set = set()

    _collect_paragraphs_from_container(document, collected, seen_elements)

    for section in document.sections:
        section_parts = [
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ]
        for part in section_parts:
            _collect_paragraphs_from_container(part, collected, seen_elements)

    return collected


def _build_paragraph_with_text(source_paragraph: Any, text: str) -> Any:
    paragraph_element = OxmlElement("w:p")

    # Carry paragraph-level style/formatting so inserted tags don't look out of place.
    if source_paragraph is not None and source_paragraph._p.pPr is not None:
        paragraph_element.append(copy.deepcopy(source_paragraph._p.pPr))

    run_element = OxmlElement("w:r")
    _append_text_content(run_element, text)
    paragraph_element.append(run_element)
    return paragraph_element


def add_paragraph_after(paragraph: Any, text: str) -> None:
    paragraph._element.addnext(_build_paragraph_with_text(paragraph, text))


def add_paragraph_before(paragraph: Any, text: str) -> None:
    paragraph._element.addprevious(_build_paragraph_with_text(paragraph, text))


def get_docx_run_text(
    document: Union[docx.document.Document, str], paragraph_number: int, run_number: int
) -> str:
    """Get run text by unified paragraph index across body/tables/headers/footers."""
    if isinstance(document, str):
        document = docx.Document(document)

    paragraphs = _collect_target_paragraphs(document)
    if paragraph_number < 0 or paragraph_number >= len(paragraphs):
        return ""

    paragraph = paragraphs[paragraph_number]
    if 0 <= run_number < len(paragraph.runs):
        return paragraph.runs[run_number].text
    return paragraph.text


def get_docx_run_items(document: Union[docx.document.Document, str]) -> List[List[Any]]:
    """Return [paragraph_index, run_index, run_text] across body/tables/headers/footers.

    Includes synthetic run items for legacy/content controls where visible run text can be
    missing or incomplete.
    """
    if isinstance(document, str):
        document = docx.Document(document)
    paragraphs = _collect_target_paragraphs(document)
    items: List[List[Any]] = []
    for pnum, paragraph in enumerate(paragraphs):
        for rnum, run in enumerate(paragraph.runs):
            items.append([pnum, rnum, run.text])
        synthetic_hints = _collect_form_control_hints(paragraph)
        if synthetic_hints:
            synthetic_text = " [FORM_CONTROL_HINT: " + "; ".join(synthetic_hints) + "]"
            synthetic_run_index = len(paragraph.runs)
            items.append([pnum, synthetic_run_index, synthetic_text])
    return items


def _collect_form_control_hints(paragraph: Any) -> List[str]:
    """Collect content-control hints from paragraph XML for prompting context."""
    hints: List[str] = []
    seen: Set[str] = set()
    paragraph_xml = paragraph._element.xml

    def _add_hint(value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            return
        if cleaned in seen:
            return
        seen.add(cleaned)
        hints.append(cleaned)

    for pattern in (
        r'<w:alias[^>]*w:val="([^"]+)"',
        r'<w:tag[^>]*w:val="([^"]+)"',
        r'<w:name[^>]*w:val="([^"]+)"',
        r'<w:instrText[^>]*>([^<]+)</w:instrText>',
        r'<w:fldSimple[^>]*w:instr="([^"]+)"',
    ):
        for match in re.findall(pattern, paragraph_xml):
            _add_hint(match)

    return hints


def _collect_sdt_context_hints(paragraph: Any) -> List[str]:
    """Collect content-control (w:sdt) hints that may not appear in paragraph.runs."""
    hints: List[str] = []
    seen: Set[str] = set()
    paragraph_xml = paragraph._element.xml
    sdt_blocks = re.findall(r"<w:sdt[\s\S]*?</w:sdt>", paragraph_xml)
    for block in sdt_blocks:
        control_type = "text"
        if "<w:dropDownList" in block:
            control_type = "dropdown"
        elif "<w:comboBox" in block:
            control_type = "combobox"
        elif "<w:date" in block:
            control_type = "date"

        text_chunks = [
            html.unescape(chunk).strip()
            for chunk in re.findall(r"<w:t(?:\s[^>]*)?>([\s\S]*?)</w:t>", block)
            if isinstance(chunk, str) and html.unescape(chunk).strip()
        ]
        text_hint = " ".join(text_chunks)

        placeholder_parts = re.findall(r'<w:docPart[^>]*w:val="([^"]+)"', block)
        placeholder_hint = (
            f" placeholder={placeholder_parts[0]}" if placeholder_parts else ""
        )

        if text_hint or placeholder_hint:
            hint = f"SDT({control_type}): {text_hint}{placeholder_hint}"
            if hint not in seen:
                seen.add(hint)
                hints.append(hint)

    return hints


def _variable_name_from_hint(text: str) -> str:
    cleaned = re.sub(r"\{\{|\}\}|\{%\s*|\s*%}", " ", text)
    cleaned = re.sub(r"[{}[\]()<>\"]", " ", cleaned)
    parts = [p.lower() for p in re.split(r"[^a-zA-Z0-9]+", cleaned) if p]
    if not parts:
        return "value"
    filtered = [p for p in parts if p not in {"the", "a", "an", "of", "for", "and"}]
    if not filtered:
        filtered = parts
    stem = "_".join(filtered[:4])
    if stem != "date" and "date" in filtered and not stem.endswith("_date"):
        stem += "_date"
    return stem[:80]


def _regex_placeholder_fallback(
    items: Sequence[Sequence[Any]],
) -> List[Tuple[int, int, str, int]]:
    """Generate deterministic label candidates for obvious placeholders."""
    fallback: List[Tuple[int, int, str, int]] = []
    seen_targets: Set[Tuple[int, int]] = set()
    patterns = [
        r"\{[^{}]{1,80}\}",
        r"_{3,}",
        r"<[^<>]{1,80}>",
        r"\[[^\[\]]{1,80}\]",
    ]
    for item in items:
        if len(item) < 3:
            continue
        try:
            paragraph_number = int(item[0])
            run_number = int(item[1])
        except (TypeError, ValueError):
            continue
        original_text = str(item[2] or "")
        if not original_text.strip():
            continue
        if "{{" in original_text or "{%" in original_text:
            continue
        combined = re.compile("|".join(patterns))
        matches = list(combined.finditer(original_text))
        if not matches:
            continue
        if (paragraph_number, run_number) in seen_targets:
            continue
        first = matches[0].group(0)
        replacement_var = _variable_name_from_hint(first)
        replacement_text = original_text.replace(first, "{{ " + replacement_var + " }}", 1)
        fallback.append((paragraph_number, run_number, replacement_text, 0))
        seen_targets.add((paragraph_number, run_number))
    return fallback


def _merge_label_candidates(
    *candidate_lists: Sequence[Tuple[int, int, str, int]],
) -> List[Tuple[int, int, str, int]]:
    """Merge candidate lists by target run, preferring Jinja-bearing text."""
    best_by_target: Dict[Tuple[int, int, int], Tuple[int, int, str, int]] = {}

    def _score(item: Tuple[int, int, str, int]) -> Tuple[int, int]:
        text = item[2]
        has_jinja = 1 if ("{{" in text or "{%" in text) else 0
        return (has_jinja, len(text))

    for candidates in candidate_lists:
        for item in candidates:
            key = (item[0], item[1], item[3])
            existing = best_by_target.get(key)
            if existing is None or _score(item) > _score(existing):
                best_by_target[key] = item

    merged = list(best_by_target.values())
    merged.sort(key=lambda x: (x[0], x[1], x[3], x[2]))
    return merged


def _is_mistral_model(model: str) -> bool:
    return "mistral" in (model or "").lower()


def _is_max_completion_tokens_unsupported_error(error: Exception) -> bool:
    lowered = str(error).lower()
    if "max_completion_tokens" not in lowered:
        return False
    return (
        "extra_forbidden" in lowered
        or "extra inputs are not permitted" in lowered
        or "invalid input" in lowered
    )


def _chat_completion_with_model_compat(
    *,
    model: str,
    messages: List[Dict[str, str]],
    json_mode: bool,
    temperature: float,
    max_output_tokens: Optional[int],
    openai_client: Optional[Any],
    openai_api: Optional[str],
    openai_base_url: Optional[str],
) -> Any:
    """Call ALToolbox wrapper, with provider-specific fallback for Mistral max_tokens."""
    try:
        return chat_completion(
            model=model,
            messages=messages,
            json_mode=json_mode,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            openai_client=openai_client,
            openai_api=openai_api,
            openai_base_url=openai_base_url,
        )
    except Exception as exc:
        if not (_is_mistral_model(model) and _is_max_completion_tokens_unsupported_error(exc)):
            raise
        # Retry with OpenAI-compatible client call using `max_tokens`, which some
        # Mistral-compatible endpoints require.
        client = openai_client
        if client is None:
            if not openai_api:
                raise
            client_kwargs: Dict[str, Any] = {"api_key": openai_api}
            if openai_base_url:
                client_kwargs["base_url"] = openai_base_url
            client = OpenAI(**client_kwargs)
        completion_args: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            completion_args["response_format"] = {"type": "json_object"}
        if max_output_tokens is not None:
            completion_args["max_tokens"] = max_output_tokens
        response = client.chat.completions.create(**completion_args)
        if not response.choices:
            raise ValueError("Provider returned no choices.")
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Provider returned empty content.")
        return content


def update_docx(
    document: Union[docx.document.Document, str],
    modified_runs: List[Tuple[int, int, str, int]],
) -> docx.document.Document:
    """Update the document with modified runs.

    Args:
        document: the docx.Document object, or the path to the DOCX file
        modified_runs: a tuple of paragraph number, run number, the modified text, and
            a number from -1 to 1 indicating whether a new paragraph should be inserted
            before or after the current paragraph.

    Returns:
        The modified document.
    """
    normalized_runs = _normalize_modified_runs(modified_runs)
    normalized_runs.sort(key=lambda x: (x[0], x[1]), reverse=True)

    if isinstance(document, str):
        document = docx.Document(document)

    paragraphs = _collect_target_paragraphs(document)
    for paragraph_number, run_number, modified_text, new_paragraph in normalized_runs:
        if paragraph_number >= len(paragraphs):
            continue  # Skip invalid paragraph index

        paragraph = paragraphs[paragraph_number]

        if new_paragraph == 1:
            add_paragraph_after(paragraph, modified_text)
            continue
        if new_paragraph == -1:
            add_paragraph_before(paragraph, modified_text)
            continue

        if run_number < len(paragraph.runs):
            paragraph.runs[run_number].text = modified_text
        else:
            # Empty or run-mismatched paragraphs are common in legal forms.
            # Fall back to appending a run so we do not silently drop a valid label.
            paragraph.add_run(modified_text)

    return document


def get_labeled_docx_runs(
    docx_path: str,
    custom_people_names: Optional[List[Tuple[str, str]]] = None,
    openai_client: Optional[Any] = None,
    openai_api: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    model: str = "gpt-5-nano",
    ensemble_models: Optional[List[str]] = None,
    custom_prompt: Optional[str] = None,
    additional_instructions: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    min_label_count: Optional[int] = None,
    use_regex_fallback: bool = True,
) -> List[Tuple[int, int, str, int]]:
    """Scan the DOCX and return a list of modified text with Jinja2 variable names inserted.

    Args:
        docx_path: path to the DOCX file
        custom_people_names: optional list of custom (name, description) pairs, e.g.
            [("clients", "the person benefiting from the form")]
        openai_api: optional API key override. If omitted, ALToolbox default resolution is used.

    Returns:
        A list of tuples, each containing a paragraph number, run number, and the modified text of the run.
    """
    role_description = custom_prompt or """
    You will process a DOCX document and return a JSON structure that turns the DOCX file into a template 
    based on the following guidelines and examples. The DOCX will be provided as an annotated series of
    paragraphs and runs.

    Steps:
    1. Analyze the document. Identify placeholder text and repeated _____ that should be replaced with a variable name.
    2. Insert jinja2 tags around a new variable name that represents the placeholder text.
    3. Mark optional paragraphs with conditional Jinja2 tags.
    4. Text intended for verbatim output in the final document will remain unchanged.
    5. The result will be a JSON structure that indicates which paragraphs and runs in the DOCX require modifications,
    the new text of the modified run with Jinja2 inserted, and a draft question to provide a definition of the variable.

    Example input, with paragraph and run numbers indicated:
    [
        [0, 1, "Dear John Smith:"],
        [1, 0, "This sentence can stay as is in the output and will not be in the reply."],
        [2, 0, "[Optional: if you are a tenant, include this paragraph]"],
    ]

    Example reply, indicating paragraph, run, the new text, and a number indicating if this changes the 
    current paragraph, adds one before, or adds one after (-1, 0, 1):

    {
        "results": [
            [0, 1, "Dear {{ other_parties[0] }}:", 0],
            [2, 0, "{%p if is_tenant %}", -1],
            [3, 0, "{%p endif %}", "", 1],
        ]
    }

    The reply ONLY contains the runs that have modified text.
    """

    custom_name_text = ""
    if custom_people_names is not None:
        if not isinstance(custom_people_names, list):
            raise ValueError(
                "custom_people_names must be a list of [name, description] pairs."
            )
        for item in custom_people_names:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError(
                    "Each custom_people_names item must be a [name, description] pair."
                )
            name, description = item
            custom_name_text += f"    {name} ({description}), \n"

    rules = f"""
    Rules for variable names:
        1. Variables usually refer to people or their attributes.
        2. People are stored in lists.
        3. We use Docassemble objects and conventions.
        4. Use variable names and patterns from the list below. Invent new variable names when it is appropriate.

    List names for people:
{custom_name_text}
        users (for the person benefiting from the form, especially when for a pro se filer)
        other_parties (the opposing party in a lawsuit or transactional party)
        plaintiffs
        defendants
        petitioners
        respondents
        children
        spouses
        parents
        caregivers
        attorneys
        translators
        debt_collectors
        creditors
        witnesses
        guardians_ad_litem
        guardians
        decedents
        interested_parties

        Name Forms:
            users (full name of all users)
            users[0] (full name of first user)
            users[0].name.full() (Alternate full name of first user)
            users[0].name.first (First name only)
            users[0].name.middle (Middle name only)
            users[0].name.middle_initial() (First letter of middle name)
            users[0].name.last (Last name only)
            users[0].name.suffix (Suffix of user's name only)

    Attribute names (replace `users` with the appropriate list name):
        Demographic Data:
            users[0].birthdate (Birthdate)
            users[0].age_in_years() (Calculated age based on birthdate)
            users[0].gender (Gender)
            users[0].gender_female (User is female, for checkbox field)
            users[0].gender_male (User is male, for checkbox field)
            users[0].gender_other (User is not male or female, for checkbox field)
            users[0].gender_nonbinary (User identifies as nonbinary, for checkbox field)
            users[0].gender_undisclosed (User chose not to disclose gender, for checkbox field)
            users[0].gender_self_described (User chose to self-describe gender, for checkbox field)
            user_needs_interpreter (User needs an interpreter, for checkbox field)
            user_preferred_language (User's preferred language)

        Addresses:
            users[0].address.block() (Full address, on multiple lines)
            users[0].address.on_one_line() (Full address on one line)
            users[0].address.line_one() (Line one of the address, including unit or apartment number)
            users[0].address.line_two() (Line two of the address, usually city, state, and Zip/postal code)
            users[0].address.address (Street address)
            users[0].address.unit (Apartment, unit, or suite)
            users[0].address.city (City or town)
            users[0].address.state (State, province, or sub-locality)
            users[0].address.zip (Zip or postal code)
            users[0].address.county (County or parish)
            users[0].address.country (Country)

        Other Contact Information:
            users[0].phone_number (Phone number)
            users[0].mobile_number (A phone number explicitly labeled as the "mobile" number)
            users[0].phone_numbers() (A list of both mobile and other phone numbers)
            users[0].email (Email)

        Signatures:
            users[0].signature (Signature)
            signature_date (Date the form is completed)

        Information about Court and Court Processes:
            trial_court (Court's full name)
            trial_court.address.county (County where court is located)
            trial_court.division (Division of court)
            trial_court.department (Department of court)
            docket_number (Case or docket number)
            docket_numbers (A comma-separated list of docket numbers)
            
    When No Existing Variable Name Exists:
        1. Craft short, readable variable names in python snake_case.
        2. Represent people with lists, even if only one person.
        3. Use valid Python variable names within complete Jinja2 tags, like: {{ new_variable_name }}.

        Special endings:
            Suffix _date for date values.
            Suffix _value or _amount for currency values.

        Examples: 
        "(State the reason for eviction)" transforms into `{{ eviction_reason }}`.
    """
    if additional_instructions and additional_instructions.strip():
        role_description += (
            "\n\nAdditional instructions:\n" + additional_instructions.strip()
        )

    encoding = tiktoken.encoding_for_model("gpt-4")

    doc = docx.Document(docx_path)
    items = get_docx_run_items(doc)
    paragraphs = _collect_target_paragraphs(doc)
    sdt_context_lines: List[str] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        sdt_hints = _collect_sdt_context_hints(paragraph)
        if not sdt_hints:
            continue
        for hint in sdt_hints:
            sdt_context_lines.append(f"[paragraph {paragraph_index}] {hint}")
    if sdt_context_lines:
        role_description += (
            "\n\nAdditional DOCX content-control context (reference only, do not edit these lines directly):\n"
            + "\n".join(sdt_context_lines)
        )

    encoding = tiktoken.encoding_for_model("gpt-4")
    token_count = len(encoding.encode(role_description + rules + repr(items)))
    if token_count > 128000:
        raise Exception(
            f"Input to OpenAI is too long ({token_count} tokens). Maximum is 128000 tokens."
        )

    messages = [
        {"role": "system", "content": role_description + rules},
        {"role": "user", "content": repr(items)},
    ]
    def _run_model(target_model: str) -> List[Tuple[int, int, str, int]]:
        response = _chat_completion_with_model_compat(
            model=target_model,
            messages=messages,
            json_mode=True,
            temperature=0.5,
            max_output_tokens=max_output_tokens,
            openai_client=openai_client,
            openai_api=openai_api,
            openai_base_url=openai_base_url,
        )

        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError as exc:
                raise ValueError("chat_completion returned non-JSON output") from exc
        results = _extract_model_results(response)
        return _normalize_modified_runs(results)

    model_candidates = _run_model(model)

    merged = list(model_candidates)
    normalized_ensemble = [m for m in (ensemble_models or []) if m and m != model]
    if normalized_ensemble:
        ensemble_results: List[List[Tuple[int, int, str, int]]] = []
        for ensemble_model in normalized_ensemble:
            try:
                ensemble_results.append(_run_model(ensemble_model))
            except Exception:
                # Ensemble is best-effort; keep primary model output if an extra model fails.
                continue
        if ensemble_results:
            merged = _merge_label_candidates(merged, *ensemble_results)

    fallback_candidates: List[Tuple[int, int, str, int]] = []
    if use_regex_fallback:
        fallback_candidates = _regex_placeholder_fallback(items)
        if fallback_candidates:
            merged = _merge_label_candidates(merged, fallback_candidates)

    if min_label_count and len(merged) < int(min_label_count) and fallback_candidates:
        merged = _merge_label_candidates(merged, fallback_candidates)

    return merged


def modify_docx_with_openai_guesses(docx_path: str) -> docx.document.Document:
    """Uses OpenAI to guess the variable names for a document and then modifies the document with the guesses.

    Args:
        docx_path (str): Path to the DOCX file to modify.

    Returns:
        docx.Document: The modified document, ready to be saved to the same or a new path
    """
    guesses = get_labeled_docx_runs(docx_path)

    return update_docx(docx.Document(docx_path), guesses)


if __name__ == "__main__":
    new_doc = modify_docx_with_openai_guesses(sys.argv[1])
    new_doc.save(sys.argv[1] + ".output.docx")
