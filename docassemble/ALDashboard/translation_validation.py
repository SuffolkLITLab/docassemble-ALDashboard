import re
from typing import Any, Dict, List

import pandas as pd
from mako import exceptions
import mako.runtime
import mako.template

from docassemble.base.util import DAEmpty

mako.runtime.UNDEFINED = DAEmpty()


def validate_translation_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    empty_rows: List[int] = []

    if "tr_text" not in df.columns:
        return {
            "errors": [
                {
                    "row": None,
                    "question_id": None,
                    "message": "Is this definitely a translation file? Missing column 'tr_text'",
                }
            ],
            "warnings": [],
            "empty_rows": [],
            "summary": {"error_count": 1, "warning_count": 0, "empty_row_count": 0},
        }

    indented_heading = re.compile(r"^\s+#", re.MULTILINE)
    percent_no_space = re.compile(r"^%\w", re.MULTILINE)
    percent_too_many_spaces = re.compile(r"^%\s\s+", re.MULTILINE)

    for index, row in df.fillna("").iterrows():
        row_num = index + 2
        row_text = str(row.get("tr_text", ""))
        question_id = str(row.get("question_id", ""))

        if row_text == "":
            empty_rows.append(row_num)

        if "$ {" in row_text:
            errors.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": "Space between { and $",
                }
            )

        if indented_heading.search(row_text):
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": 'A heading made with "#" may have extra spaces before it',
                }
            )

        if percent_no_space.search(row_text):
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": "No space between % and the following letter.",
                }
            )

        if percent_too_many_spaces.search(row_text):
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": "Too many spaces after %.",
                }
            )

        num_opening_curly_brackets = row_text.count("{")
        num_closing_curly_brackets = row_text.count("}")
        if num_closing_curly_brackets > num_opening_curly_brackets:
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": 'A term or Mako code may be missing its opening "{"',
                }
            )
        if num_opening_curly_brackets > num_closing_curly_brackets:
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": 'A term or Mako code may be missing its closing "}"',
                }
            )

        num_opening_parens = row_text.count("(")
        num_closing_parens = row_text.count(")")
        if num_closing_parens > num_opening_parens:
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": 'An opening "(" may be missing',
                }
            )
        if num_opening_parens > num_closing_parens:
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": 'A closing ")" may be missing',
                }
            )

        if row_text.count('"') % 2 > 0:
            warnings.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": 'A plain quotation mark (") may be missing.',
                }
            )

        try:
            mytemplate = mako.template.Template(row_text)
            mytemplate.render()
        except Exception:
            errors.append(
                {
                    "row": row_num,
                    "question_id": question_id,
                    "message": exceptions.text_error_template().render(),
                }
            )

    return {
        "errors": errors,
        "warnings": warnings,
        "empty_rows": empty_rows,
        "summary": {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "empty_row_count": len(empty_rows),
        },
    }


def validate_translation_xlsx(path: str) -> Dict[str, Any]:
    df = pd.read_excel(path)
    return validate_translation_dataframe(df)
