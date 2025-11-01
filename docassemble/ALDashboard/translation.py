import hashlib
import math
import os
import re
import tempfile
from typing import List, Optional, Tuple, Union, Literal
import xml.etree.ElementTree as ET
import zipfile

import docassemble.base.config

if not docassemble.base.config.loaded:
    docassemble.base.config.load()
from docassemble.base.config import in_celery

import docassemble.webapp.setup
import docassemble.base.astparser
from docassemble.base.error import DAError
import docassemble.base.functions
from docassemble.base.functions import word
import docassemble.base.DA
import docassemble.base.interview_cache
import docassemble.base.parse
import docassemble.base.pdftk
import docassemble.base.util
import docassemble.base.core  # for backward-compatibility with data pickled in earlier versions

import docassemble.webapp.backend
from docassemble.webapp.backend import url_for
import docassemble.webapp.clicksend
import docassemble.webapp.telnyx
import docassemble.webapp.machinelearning
from docassemble.webapp.translations import setup_translation

if not in_celery:
    import docassemble.webapp.worker

from flask import send_file, redirect, flash
import pandas

import xlsxwriter

from docassemble.base.util import DAFile, language_name, get_config, log, DAEmpty
from docassemble.webapp.server import mako_parts
from typing import NamedTuple, Dict
from docassemble.ALToolbox.llms import chat_completion

import tiktoken
import mako.template
import mako.runtime

mako.runtime.UNDEFINED = DAEmpty()

MAX_MAKO_RETRIES = 3


def is_valid_mako_block(text: str) -> Tuple[bool, Optional[str]]:
    """
    Return True if the provided text can be rendered as Mako without raising an error.
    Empty strings are treated as valid.
    """
    if not text:
        return True, None
    try:
        mako.template.Template(text).render()
        return True, None
    except Exception as err:  # pragma: no cover - logging only
        return False, str(err)

DEFAULT_LANGUAGE = "en"

__all__ = [
    "Translation",
    "translation_file",
    "gpt_is_available",
    "translate_fragments_gpt",
]


def gpt_is_available() -> bool:
    """
    Return True if the GPT API is available.
    """
    return get_config("open ai", {}).get("key") is not None


def may_have_mako(text: str) -> bool:
    """
    Return True if the text appears to contain any Mako code, such as ${...} or % at the beginning of a line.
    """
    return re.search(r"\${|^\s*%", text, flags=re.MULTILINE) is not None


def may_have_html(text: str) -> bool:
    """
    Return True if the text appears to contain any HTML code, such as <p> or <div>.
    """
    return re.search(r"<\w+.*?>.*?<\/\w+>", text, flags=re.MULTILINE) is not None


def translate_fragments_gpt(
    fragments: Union[str, List[Tuple[int, str]]],
    source_language: str,
    tr_lang: str,
    interview_context: Optional[str] = None,
    special_words: Optional[Dict[int, str]] = None,
    model: Optional[str] = "gpt-5-nano",
    openai_base_url: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    max_input_tokens: Optional[int] = None,
    openai_api: Optional[str] = None,
    reasoning_effort: Optional[Literal["minimal", "low", "medium", "high"]] = "low",
) -> Dict[int, str]:
    """Use an AI model to translate a list of fragments (strings) from one language to another and provide a dictionary
    with the original text and the translated text.

    You can optionally provide an alternative model, but it must support JSON mode.

    Args:
        fragments: A list of strings to be translated.
        source_language: The language of the original text.
        tr_lang: The language to translate the text into.
        special_words: A dictionary of special words that should be translated in a specific way.
        model: The GPT model to use. The default is "gpt-5-nano"
        openai_base_url: The base URL for the OpenAI API. If not provided, the default OpenAI URL will be used.
        max_output_tokens: The maximum number of tokens to generate in the output.
        max_input_tokens: The maximum number of tokens in the input. If not provided, it will be set to 4000.
        openai_api: The OpenAI API key. If not provided, it will use the key from the configuration.
        reasoning_effort: Controls the reasoning effort for thinking models like GPT-5. Defaults to "low".
    Returns:
        A dictionary where the keys are the indices of the fragments and the values are the translated text.
    """
    results: Dict[int, str] = {}
    if not model:
        model = "gpt-5-nano"
    is_gpt5_model = "gpt-5" in model
    applied_reasoning_effort = reasoning_effort or "low"
    try:
        language_in_english = language_name(source_language)
    except:
        language_in_english = source_language
    try:
        tr_language_in_english = language_name(tr_lang)
    except:
        tr_language_in_english = tr_lang

    if isinstance(fragments, str):
        fragments = [(0, fragments)]

    system_prompt = f"""You translate Docassemble interviews from "{language_in_english}" to "{tr_language_in_english}". You
    preserve the meaning of all sentences while aiming to produce a translation at or below a 9th grade reading level.

    Whenever you see anything that looks like code, variable interpolation, Mako template syntax, HTML tags, or Python keywords, assume it is code and do not touch it.
    You are only translating natural-language content.

    **Do not translate** any text matching these patterns (pass it through verbatim):
    • `% if …:` / `% endif` / `% for …:` / `% endfor`
    • `% elif …:` / `% else:`
    • `${{…}}`  
    • `{{% … %}}`
    • `<…>` HTML tags  
    • Python keywords: def, if, else, elif, import, for, while, return

    You only translate natural-language text.  
    Preserve all whitespace exactly.  
    Reply *only* with the translated text—no extra commentary.
    """
    if interview_context is not None:
        system_prompt += f"""When translating, keep in mind the purpose of this interview: ```{ interview_context }```
        """

    if special_words is not None:
        system_prompt += f"""
    When you see one of the special words in the following table in the first column, you use a form of the suggested replacement rather than inventing a new translation:

    ```
    {special_words}
    ```
    """

    #           row number: text to translate
    results: Dict[int, str] = {}

    for row_number, text_to_translate in fragments:
        try:
            chat_kwargs = dict(
                system_message=system_prompt,
                user_message=text_to_translate,
                model=model,
                max_output_tokens=max_output_tokens,
                openai_base_url=openai_base_url,
                max_input_tokens=max_input_tokens,
                openai_api=openai_api,
            )
            if is_gpt5_model:
                chat_kwargs["reasoning_effort"] = applied_reasoning_effort
            else:
                chat_kwargs["temperature"] = 0.0
            response = chat_completion(**chat_kwargs)
            if isinstance(response, str):
                results[row_number] = (
                    response.rstrip()
                )  # Remove any trailing whitespace some LLM models might add
            else:
                log(f"Unexpected response type from chat completion: {type(response)}")
        # Get the exception and log it
        except Exception as e:
            log(f"Exception when calling chatcompletion: { e }")
            response = str(e)
    return results


class Translation(NamedTuple):
    file: DAFile  # an XLSX or XLIFF file
    untranslated_words: (
        int  # Word count for all untranslated segments that are not Mako or HTML
    )
    untranslated_segments: int  # Number of rows in the output that have untranslated text - one for each question, subquestion, field, etc.
    total_rows: int


def translation_file(
    yaml_filename: str,
    tr_lang: str,
    use_gpt=False,
    use_google_translate=False,
    openai_api: Optional[str] = None,
    max_tokens=4000,
    interview_context: Optional[str] = None,
    special_words: Optional[Dict[int, str]] = None,
    model: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    max_input_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    reasoning_effort: Optional[Literal["minimal", "low", "medium", "high"]] = None,
    validate_mako: Optional[bool] = True,
) -> Translation:
    """
    Return a tuple of the translation file in XLSX format, plus a count of the
    number of words and segments that need to be translated.

    The word and segment count only apply when filetype="XLSX".

    This code was adjusted from the Flask endpoint-only version in server.py. XLIFF support was removed
    for now but can be added later.

    Args:
        yaml_filename: Fully qualified interview YAML path.
        tr_lang: Target translation language (ISO code).
        use_gpt: Whether to include GPT draft translations.
        use_google_translate: Placeholder for legacy Google support.
        openai_api: API key override.
        max_tokens: Legacy max token setting (kept for backward compatibility).
        interview_context: Optional context prompt to send with GPT calls.
        special_words: Optional glossary to enforce terminology.
        model: Preferred OpenAI model.
        openai_base_url: Override the OpenAI base URL.
        max_input_tokens: Optional override for input token limits.
        max_output_tokens: Optional override for completion token limits.
        reasoning_effort: Reasoning effort setting, used for GPT-5 models.
        validate_mako: When True, retry GPT translations that break Mako syntax (default).
    """
    filetype: str = (
        "XLSX"  # Look in server.py for support of XLIFF format, but we won't implement it here
    )
    output_file = DAFile()
    setup_translation()
    if yaml_filename is None or not re.search(r"\S", yaml_filename):
        raise ValueError("YAML filename was not valid")
    if tr_lang is None or not re.search(r"\S", tr_lang):
        raise ValueError("You must provide a language")
    try:
        interview_source = docassemble.base.parse.interview_source_from_string(
            yaml_filename
        )
    except DAError:
        raise ValueError("Invalid interview")
    interview_source.update()
    interview_source.translating = True
    interview = interview_source.get_interview()
    if not model:
        model = "gpt-5-nano"
    if reasoning_effort is None:
        reasoning_effort = "low"
    if validate_mako in ("", None):
        validate_mako = True

    # Load the existing translation files and build a cache
    tr_cache: Dict = {}
    if len(interview.translations) > 0:
        for item in interview.translations:
            if item.lower().endswith(".xlsx"):
                the_xlsx_file = docassemble.base.functions.package_data_filename(item)
                if not os.path.isfile(the_xlsx_file):
                    continue
                df = pandas.read_excel(
                    the_xlsx_file,
                    na_values=["NaN", "-NaN", "#NA", "#N/A"],
                    keep_default_na=False,
                )
                invalid = False
                for column_name in (
                    "interview",
                    "question_id",
                    "index_num",
                    "hash",
                    "orig_lang",
                    "tr_lang",
                    "orig_text",
                    "tr_text",
                ):
                    if column_name not in df.columns:
                        invalid = True
                        break
                if invalid:
                    continue
                for indexno in df.index:
                    try:
                        assert df["interview"][indexno]
                        assert df["question_id"][indexno]
                        assert df["index_num"][indexno] >= 0
                        assert df["hash"][indexno]
                        assert df["orig_lang"][indexno]
                        assert df["tr_lang"][indexno]
                        assert df["orig_text"][indexno] != ""
                        assert df["tr_text"][indexno] != ""
                        if isinstance(df["orig_text"][indexno], float):
                            assert not math.isnan(df["orig_text"][indexno])
                        if isinstance(df["tr_text"][indexno], float):
                            assert not math.isnan(df["tr_text"][indexno])
                    except:
                        continue
                    the_dict = {
                        "interview": str(df["interview"][indexno]),
                        "question_id": str(df["question_id"][indexno]),
                        "index_num": df["index_num"][indexno],
                        "hash": str(df["hash"][indexno]),
                        "orig_lang": str(df["orig_lang"][indexno]),
                        "tr_lang": str(df["tr_lang"][indexno]),
                        "orig_text": str(df["orig_text"][indexno]),
                        "tr_text": str(df["tr_text"][indexno]),
                    }
                    if df["orig_text"][indexno] not in tr_cache:
                        tr_cache[df["orig_text"][indexno]] = {}
                    if (
                        df["orig_lang"][indexno]
                        not in tr_cache[df["orig_text"][indexno]]
                    ):
                        tr_cache[df["orig_text"][indexno]][
                            df["orig_lang"][indexno]
                        ] = {}
                    tr_cache[df["orig_text"][indexno]][df["orig_lang"][indexno]][
                        df["tr_lang"][indexno]
                    ] = the_dict
            elif item.lower().endswith(".xlf") or item.lower().endswith(".xliff"):
                the_xlf_file = docassemble.base.functions.package_data_filename(item)
                if not os.path.isfile(the_xlf_file):
                    continue
                tree = ET.parse(the_xlf_file)
                root = tree.getroot()
                indexno = 1
                if root.attrib["version"] == "1.2":
                    for the_file in root.iter(
                        "{urn:oasis:names:tc:xliff:document:1.2}file"
                    ):
                        source_lang = the_file.attrib.get("source-language", "en")
                        target_lang = the_file.attrib.get("target-language", "en")
                        source_filename = the_file.attrib.get("original", yaml_filename)
                        for transunit in the_file.iter(
                            "{urn:oasis:names:tc:xliff:document:1.2}trans-unit"
                        ):
                            orig_text = ""
                            tr_text = ""
                            for source in transunit.iter(
                                "{urn:oasis:names:tc:xliff:document:1.2}source"
                            ):
                                if source.text:
                                    orig_text += source.text
                                for mrk in source:
                                    if mrk.text:
                                        orig_text += mrk.text
                                    if mrk.tail:
                                        orig_text += mrk.tail
                            for target in transunit.iter(
                                "{urn:oasis:names:tc:xliff:document:1.2}target"
                            ):
                                if target.text:
                                    tr_text += target.text
                                for mrk in target:
                                    if mrk.text:
                                        tr_text += mrk.text
                                    if mrk.tail:
                                        tr_text += mrk.tail
                            if orig_text == "" or tr_text == "":
                                continue
                            the_dict = {
                                "interview": source_filename,
                                "question_id": "Unknown" + str(indexno),
                                "index_num": transunit.attrib.get("id", str(indexno)),
                                "hash": hashlib.md5(
                                    orig_text.encode("utf-8")
                                ).hexdigest(),
                                "orig_lang": source_lang,
                                "tr_lang": target_lang,
                                "orig_text": orig_text,
                                "tr_text": tr_text,
                            }
                            if orig_text not in tr_cache:
                                tr_cache[orig_text] = {}
                            if source_lang not in tr_cache[orig_text]:
                                tr_cache[orig_text][source_lang] = {}
                            tr_cache[orig_text][source_lang][target_lang] = the_dict
                            indexno += 1
                elif root.attrib["version"] == "2.0":
                    source_lang = root.attrib["srcLang"]
                    target_lang = root.attrib["trgLang"]
                    for the_file in root.iter(
                        "{urn:oasis:names:tc:xliff:document:2.0}file"
                    ):
                        source_filename = the_file.attrib.get("original", yaml_filename)
                        for unit in the_file.iter(
                            "{urn:oasis:names:tc:xliff:document:2.0}unit"
                        ):
                            question_id = unit.attrib.get(
                                "id", "Unknown" + str(indexno)
                            )
                            for segment in unit.iter(
                                "{urn:oasis:names:tc:xliff:document:2.0}segment"
                            ):
                                orig_text = ""
                                tr_text = ""
                                for source in transunit.iter(
                                    "{urn:oasis:names:tc:xliff:document:2.0}source"
                                ):
                                    if source.text:
                                        orig_text += source.text
                                    for mrk in source:
                                        if mrk.text:
                                            orig_text += mrk.text
                                        if mrk.tail:
                                            orig_text += mrk.tail
                                for target in transunit.iter(
                                    "{urn:oasis:names:tc:xliff:document:2.0}target"
                                ):
                                    if target.text:
                                        tr_text += target.text
                                    for mrk in target:
                                        if mrk.text:
                                            tr_text += mrk.text
                                        if mrk.tail:
                                            tr_text += mrk.tail
                                if orig_text == "" or tr_text == "":
                                    continue
                                the_dict = {
                                    "interview": source_filename,
                                    "question_id": question_id,
                                    "index_num": segment.attrib.get("id", str(indexno)),
                                    "hash": hashlib.md5(
                                        orig_text.encode("utf-8")
                                    ).hexdigest(),
                                    "orig_lang": source_lang,
                                    "tr_lang": target_lang,
                                    "orig_text": orig_text,
                                    "tr_text": tr_text,
                                }
                                if orig_text not in tr_cache:
                                    tr_cache[orig_text] = {}
                                if source_lang not in tr_cache[orig_text]:
                                    tr_cache[orig_text][source_lang] = {}
                                tr_cache[orig_text][source_lang][target_lang] = the_dict
                                indexno += 1

    # Create the output file
    if (
        filetype == "XLSX"
    ):  # We only support XLSX for now, but this came from upstream implementation
        xlsx_filename = (
            docassemble.base.functions.space_to_underscore(
                os.path.splitext(os.path.basename(re.sub(r".*:", "", yaml_filename)))[0]
            )
            + "_"
            + tr_lang
            + ".xlsx"
        )
        output_file.initialize(filename=xlsx_filename)
        workbook = xlsxwriter.Workbook(output_file.path())
        worksheet = workbook.add_worksheet()

        # Add a bold format for the header
        bold = workbook.add_format({"bold": 1})

        # Add the table headings
        worksheet.write("A1", "interview", bold)
        worksheet.write("B1", "question_id", bold)
        worksheet.write("C1", "index_num", bold)
        worksheet.write("D1", "hash", bold)
        worksheet.write("E1", "orig_lang", bold)
        worksheet.write("F1", "tr_lang", bold)
        worksheet.write("G1", "orig_text", bold)
        worksheet.write("H1", "tr_text", bold)

        # Set column widths
        worksheet.set_column(0, 0, 25)  # interview source
        worksheet.set_column(1, 1, 15)  # question_id
        worksheet.set_column(2, 2, 12)  # index_num
        worksheet.set_column(6, 6, 75)  # orig_text
        worksheet.set_column(6, 7, 75)  # tr_text

        # Create some formats to use for syntax highlighting
        text_format = workbook.add_format()
        text_format.set_align("top")
        fixedcell = workbook.add_format()
        fixedcell.set_align("top")
        fixedcell.set_text_wrap()
        fixedunlockedcell = workbook.add_format()
        fixedunlockedcell.set_align("top")
        fixedunlockedcell.set_text_wrap()
        # fixedunlockedcell.set_locked(False)
        fixed = workbook.add_format()
        fixedone = workbook.add_format()
        fixedone.set_bold()
        fixedone.set_font_color("green")
        fixedtwo = workbook.add_format()
        fixedtwo.set_bold()
        fixedtwo.set_font_color("blue")
        fixedunlocked = workbook.add_format()
        fixedunlockedone = workbook.add_format()
        fixedunlockedone.set_bold()
        fixedunlockedone.set_font_color("green")
        fixedunlockedtwo = workbook.add_format()
        fixedunlockedtwo.set_bold()
        fixedunlockedtwo.set_font_color("blue")
        wholefixed = workbook.add_format()
        wholefixed.set_align("top")
        wholefixed.set_text_wrap()
        wholefixedone = workbook.add_format()
        wholefixedone.set_bold()
        wholefixedone.set_font_color("green")
        wholefixedone.set_align("top")
        wholefixedone.set_text_wrap()
        wholefixedtwo = workbook.add_format()
        wholefixedtwo.set_bold()
        wholefixedtwo.set_font_color("blue")
        wholefixedtwo.set_align("top")
        wholefixedtwo.set_text_wrap()
        wholefixedunlocked = workbook.add_format()
        wholefixedunlocked.set_align("top")
        wholefixedunlocked.set_text_wrap()
        # wholefixedunlocked.set_locked(False)
        wholefixedunlockedone = workbook.add_format()
        wholefixedunlockedone.set_bold()
        wholefixedunlockedone.set_font_color("green")
        wholefixedunlockedone.set_align("top")
        wholefixedunlockedone.set_text_wrap()
        # wholefixedunlockedone.set_locked(False)
        wholefixedunlockedtwo = workbook.add_format()
        wholefixedunlockedtwo.set_bold()
        wholefixedunlockedtwo.set_font_color("blue")
        wholefixedunlockedtwo.set_align("top")
        wholefixedunlockedtwo.set_text_wrap()
        # wholefixedunlockedtwo.set_locked(False)

        # This is a variation on above formats to be used to mark "draft" translations (from GPT-4)
        draft_translation_format = workbook.add_format()
        draft_translation_format.set_bg_color("yellow")
        draft_translation_format_one = workbook.add_format()
        draft_translation_format_one.set_bg_color("yellow")
        draft_translation_format_one.set_bold()
        draft_translation_format_one.set_font_color("green")

        draft_translation_format_two = workbook.add_format()
        draft_translation_format_two.set_bg_color("yellow")
        draft_translation_format_two.set_bold()
        draft_translation_format_two.set_font_color("blue")

        whole_draft_translation_format = workbook.add_format()
        whole_draft_translation_format.set_bg_color("yellow")
        whole_draft_translation_format.set_font_color("black")
        whole_draft_translation_format.set_align("top")
        whole_draft_translation_format.set_text_wrap()

        whole_draft_translation_format_one = workbook.add_format()
        whole_draft_translation_format_one.set_bg_color("yellow")
        whole_draft_translation_format_one.set_bold()
        whole_draft_translation_format_one.set_font_color("green")
        whole_draft_translation_format_one.set_align("top")
        whole_draft_translation_format_one.set_text_wrap()

        whole_draft_translation_format_two = workbook.add_format()
        whole_draft_translation_format_two.set_bg_color("yellow")
        whole_draft_translation_format_two.set_bold()
        whole_draft_translation_format_two.set_font_color("blue")
        whole_draft_translation_format_two.set_align("top")
        whole_draft_translation_format_two.set_text_wrap()

        draft_fixedcell = workbook.add_format()
        draft_fixedcell.set_align("top")
        draft_fixedcell.set_text_wrap()
        draft_fixedcell.set_bg_color("yellow")

        # Default number format
        numb = workbook.add_format()
        numb.set_align("top")

        # Write the data
        row = 1
        seen = []
        untranslated_segments = 0
        untranslated_text = ""
        total_rows = 0

        hold_for_draft_translation = []
        for question in interview.all_questions:
            if not hasattr(question, "translations"):
                continue
            language = question.language
            if language == "*":
                language = question.from_source.get_language()
            if language == "*":
                language = interview.default_language
            if language == tr_lang:
                continue
            indexno = 0
            if hasattr(question, "id"):
                question_id = question.id
            else:
                question_id = question.name
            for item in question.translations:
                if item in seen:
                    continue
                total_rows += 1
                # The segment has already been translated and the translation is still valid
                if (
                    item in tr_cache
                    and language in tr_cache[item]
                    and tr_lang in tr_cache[item][language]
                ):
                    tr_text = str(tr_cache[item][language][tr_lang]["tr_text"])
                else:  # This string needs to be translated
                    tr_text = ""
                    hold_for_draft_translation.append(
                        (row, item, language)
                    )  # item is the original untranslated string, pre-mako parsing
                    untranslated_segments += 1

                # Add the metadata

                worksheet.write_string(
                    row, 0, question.from_source.get_name(), text_format
                )
                worksheet.write_string(row, 1, question_id, text_format)
                worksheet.write_number(row, 2, indexno, numb)
                worksheet.write_string(
                    row, 3, hashlib.md5(item.encode("utf-8")).hexdigest(), text_format
                )
                worksheet.write_string(row, 4, language, text_format)
                worksheet.write_string(row, 5, tr_lang, text_format)
                mako = mako_parts(item)

                if not tr_text:
                    for phrase in mako:
                        if phrase[1] == 0:
                            untranslated_text += phrase[0]

                if len(mako) == 0:  # Can this case occur? Not in tests
                    worksheet.write_string(row, 6, "", wholefixed)
                elif len(mako) == 1:
                    if mako[0][1] == 0:
                        worksheet.write_string(row, 6, item, wholefixed)
                    elif mako[0][1] == 1:
                        worksheet.write_string(row, 6, item, wholefixedone)
                    elif mako[0][1] == 2:
                        worksheet.write_string(row, 6, item, wholefixedtwo)
                else:
                    parts = [row, 6]
                    for part in mako:
                        if part[1] == 0:
                            parts.extend([fixed, part[0]])
                        elif part[1] == 1:
                            parts.extend([fixedone, part[0]])
                        elif part[1] == 2:
                            parts.extend([fixedtwo, part[0]])
                    parts.append(fixedcell)
                    worksheet.write_rich_string(*parts)

                #

                mako = mako_parts(tr_text)
                if len(mako) == 0:
                    worksheet.write_string(row, 7, "", wholefixedunlocked)
                elif len(mako) == 1:
                    # mode 0 is normal text, mode 1 is Mako or HTML, mode 2 is a variable
                    if mako[0][1] == 0:
                        worksheet.write_string(row, 7, tr_text, wholefixedunlocked)
                    elif mako[0][1] == 1:
                        worksheet.write_string(row, 7, tr_text, wholefixedunlockedone)
                    elif mako[0][1] == 2:
                        worksheet.write_string(row, 7, tr_text, wholefixedunlockedtwo)
                else:
                    parts = [row, 7]
                    for part in mako:
                        if part[1] == 0:
                            parts.extend([fixedunlocked, part[0]])
                        elif part[1] == 1:
                            parts.extend([fixedunlockedone, part[0]])
                        elif part[1] == 2:
                            parts.extend([fixedunlockedtwo, part[0]])
                    parts.append(fixedunlockedcell)
                    worksheet.write_rich_string(*parts)
                num_lines = item.count("\n")
                # if num_lines > 25:
                #    num_lines = 25
                if num_lines > 0:
                    worksheet.set_row(row, 15 * (num_lines + 1))
                indexno += 1
                row += 1
                seen.append(item)

        # Now we need to translate the hold_for_draft_translation items
        if use_gpt:
            fragments_by_language: Dict[str, List[Tuple[int, str]]] = {}
            for row_number, original_text, source_language in hold_for_draft_translation:
                fragments_by_language.setdefault(source_language, []).append(
                    (row_number, original_text)
                )

            translated_fragments: Dict[int, str] = {}
            for source_language, fragments in fragments_by_language.items():
                if not fragments:
                    continue
                response = translate_fragments_gpt(
                    fragments,
                    source_language=source_language,
                    tr_lang=tr_lang,
                    openai_api=openai_api,
                    interview_context=interview_context,
                    special_words=special_words,
                    model=model,
                    openai_base_url=openai_base_url,
                    max_input_tokens=max_input_tokens,
                    max_output_tokens=max_output_tokens,
                    reasoning_effort=reasoning_effort,
                )
                for row_key, translated_text in response.items():
                    try:
                        translated_fragments[int(row_key)] = translated_text
                    except (TypeError, ValueError):
                        log(
                            f"Unexpected row identifier returned from translation: {row_key}"
                        )

            final_translations: Dict[int, str] = {}
            if validate_mako:
                def translate_with_retries(
                    row_number: int,
                    original_text: str,
                    initial_translation: Optional[str],
                    source_language: str,
                ) -> str:
                    candidate = initial_translation or ""
                    attempts = 0
                    valid, error_message = is_valid_mako_block(candidate)
                    fallback_chain = [
                        "gpt-5-nano",
                        "gpt-5-mini",
                        "gpt-5",
                        "gpt-4.1-nano",
                        "gpt-4.1-mini",
                        "gpt-4.1",
                    ]
                    models_to_try: List[Optional[str]] = []
                    if model not in (None, "") and model not in models_to_try:
                        models_to_try.append(model)
                    else:
                        models_to_try.append(None)
                    if model in fallback_chain:
                        start_index = fallback_chain.index(model) + 1
                    else:
                        start_index = 0
                    for fallback_model in fallback_chain[start_index:]:
                        if fallback_model not in models_to_try:
                            models_to_try.append(fallback_model)
                    if "gpt-5" not in models_to_try:
                        models_to_try.append("gpt-5")
                    if "gpt-4.1" not in models_to_try:
                        models_to_try.append("gpt-4.1")

                    while attempts < MAX_MAKO_RETRIES and (not candidate or not valid):
                        if error_message:
                            log(
                                f"Regenerating draft translation for row {row_number} due to Mako error: {error_message}"
                            )
                        retry_model = models_to_try[min(attempts, len(models_to_try) - 1)]
                        attempts += 1
                        retry_context = interview_context
                        if error_message:
                            extra_context = (
                                "Previous translation attempt broke Mako syntax. "
                                "Regenerate the translation while preserving valid Mako/HTML code. "
                                f"Mako parser error: {error_message}"
                            )
                            retry_context = (
                                interview_context + "\n\n" + extra_context
                                if interview_context
                                else extra_context
                            )
                        retry_response = translate_fragments_gpt(
                            [(row_number, original_text)],
                            source_language=source_language,
                            tr_lang=tr_lang,
                            openai_api=openai_api,
                            interview_context=retry_context,
                            special_words=special_words,
                            model=retry_model,
                            openai_base_url=openai_base_url,
                            max_input_tokens=max_input_tokens,
                            max_output_tokens=max_output_tokens,
                            reasoning_effort=reasoning_effort,
                        )
                        candidate = retry_response.get(
                            row_number,
                            retry_response.get(str(row_number), candidate),
                        )
                        valid, error_message = is_valid_mako_block(candidate)

                    if not candidate or not valid:
                        fallback_valid, _ = is_valid_mako_block(original_text)
                        if fallback_valid:
                            if error_message:
                                log(
                                    f"Falling back to original text for row {row_number} after repeated Mako errors: {error_message}"
                                )
                            return original_text
                        log(
                            f"Unable to create valid Mako translation for row {row_number}; leaving draft empty."
                        )
                        return ""
                    return candidate

                for row_number, original_text, source_language in hold_for_draft_translation:
                    final_translations[row_number] = translate_with_retries(
                        row_number,
                        original_text,
                        translated_fragments.get(row_number),
                        source_language,
                    )
            else:
                for row_number, _original_text, _source_language in hold_for_draft_translation:
                    translation_text = translated_fragments.get(row_number)
                    if translation_text is None:
                        translation_text = ""
                    final_translations[row_number] = translation_text

            for row_number, _original_text, _source_language in hold_for_draft_translation:
                item = final_translations.get(row_number, "") or ""
                row = row_number
                mako = mako_parts(item)
                if len(mako) == 0:
                    worksheet.write_string(row, 7, item, whole_draft_translation_format)
                elif len(mako) == 1:
                    if mako[0][1] == 0:
                        worksheet.write_string(
                            row, 7, item, whole_draft_translation_format
                        )
                    elif mako[0][1] == 1:
                        worksheet.write_string(
                            row, 7, item, whole_draft_translation_format_one
                        )
                    elif mako[0][1] == 2:
                        worksheet.write_string(
                            row, 7, item, whole_draft_translation_format_two
                        )
                else:
                    parts = [row, 7]
                    for part in mako:
                        if part[1] == 0:
                            parts.extend([whole_draft_translation_format, part[0]])
                        elif part[1] == 1:
                            parts.extend([whole_draft_translation_format_one, part[0]])
                        elif part[1] == 2:
                            parts.extend([whole_draft_translation_format_two, part[0]])
                    parts.append(draft_fixedcell)
                    worksheet.write_rich_string(*parts)

        for item, cache_item in tr_cache.items():
            if (
                item in seen
                or language not in cache_item
                or tr_lang not in cache_item[language]
            ):
                continue
            worksheet.write_string(
                row, 0, cache_item[language][tr_lang]["interview"], text_format
            )
            worksheet.write_string(
                row, 1, cache_item[language][tr_lang]["question_id"], text_format
            )
            worksheet.write_number(
                row, 2, 1000 + cache_item[language][tr_lang]["index_num"], numb
            )
            worksheet.write_string(
                row, 3, cache_item[language][tr_lang]["hash"], text_format
            )
            worksheet.write_string(
                row, 4, cache_item[language][tr_lang]["orig_lang"], text_format
            )
            worksheet.write_string(
                row, 5, cache_item[language][tr_lang]["tr_lang"], text_format
            )
            mako = mako_parts(cache_item[language][tr_lang]["orig_text"])
            if len(mako) == 1:
                if mako[0][1] == 0:
                    worksheet.write_string(
                        row, 6, cache_item[language][tr_lang]["orig_text"], wholefixed
                    )
                elif mako[0][1] == 1:
                    worksheet.write_string(
                        row,
                        6,
                        cache_item[language][tr_lang]["orig_text"],
                        wholefixedone,
                    )
                elif mako[0][1] == 2:
                    worksheet.write_string(
                        row,
                        6,
                        cache_item[language][tr_lang]["orig_text"],
                        wholefixedtwo,
                    )
            else:
                parts = [row, 6]
                for part in mako:
                    if part[1] == 0:
                        parts.extend([fixed, part[0]])
                    elif part[1] == 1:
                        parts.extend([fixedone, part[0]])
                    elif part[1] == 2:
                        parts.extend([fixedtwo, part[0]])
                parts.append(fixedcell)
                worksheet.write_rich_string(*parts)
            mako = mako_parts(cache_item[language][tr_lang]["tr_text"])
            if len(mako) == 1:
                if mako[0][1] == 0:
                    worksheet.write_string(
                        row,
                        7,
                        cache_item[language][tr_lang]["tr_text"],
                        wholefixedunlocked,
                    )
                elif mako[0][1] == 1:
                    worksheet.write_string(
                        row,
                        7,
                        cache_item[language][tr_lang]["tr_text"],
                        wholefixedunlockedone,
                    )
                elif mako[0][1] == 2:
                    worksheet.write_string(
                        row,
                        7,
                        cache_item[language][tr_lang]["tr_text"],
                        wholefixedunlockedtwo,
                    )
            else:
                parts = [row, 7]
                for part in mako:
                    if part[1] == 0:
                        parts.extend([fixedunlocked, part[0]])
                    elif part[1] == 1:
                        parts.extend([fixedunlockedone, part[0]])
                    elif part[1] == 2:
                        parts.extend([fixedunlockedtwo, part[0]])
                parts.append(fixedunlockedcell)
                worksheet.write_rich_string(*parts)
            num_lines = cache_item[language][tr_lang]["orig_text"].count("\n")
            if num_lines > 0:
                worksheet.set_row(row, 15 * (num_lines + 1))
            row += 1
        workbook.close()
        untranslated_words = len(re.findall(r"\w+", untranslated_text))
        return Translation(
            output_file, untranslated_words, untranslated_segments, total_rows
        )

    raise ValueError("That's not a valid filetype for a translation file")
