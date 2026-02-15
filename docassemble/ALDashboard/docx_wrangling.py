import copy
import docx
import sys

import tiktoken
import json
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import re
from docassemble.ALToolbox.llms import chat_completion

from typing import Any, List, Tuple, Optional, Union

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

    try:
        paragraph_number = int(paragraph_number)
    except (TypeError, ValueError):
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
    modified_runs: List[Tuple[int, int, str, int]],
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
        mapped_results.append([paragraph_number, run_number, str(text_value), new_paragraph])
    return mapped_results


def _append_text_content(run_element: OxmlElement, text: str) -> None:
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


def _build_paragraph_with_text(source_paragraph: Any, text: str) -> OxmlElement:
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
    """Return [paragraph_index, run_index, run_text] across body/tables/headers/footers."""
    if isinstance(document, str):
        document = docx.Document(document)
    paragraphs = _collect_target_paragraphs(document)
    items: List[List[Any]] = []
    for pnum, paragraph in enumerate(paragraphs):
        for rnum, run in enumerate(paragraph.runs):
            items.append([pnum, rnum, run.text])
    return items


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
    custom_prompt: Optional[str] = None,
    additional_instructions: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
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
    paragraphs = _collect_target_paragraphs(doc)

    items = []
    for pnum, para in enumerate(paragraphs):
        for rnum, run in enumerate(para.runs):
            items.append([pnum, rnum, run.text])

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
    response = chat_completion(
        model=model,
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
    guesses = _normalize_modified_runs(results)
    return guesses


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
