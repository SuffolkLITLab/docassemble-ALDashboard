import os
from typing import Any, Dict, Iterable, List

from docassemble.base.util import user_info
from docassemble.webapp.files import SavedFile

from .api_dashboard_utils import yaml_reformat_payload_from_options
from .interview_linter import list_playground_yaml_files

__all__ = [
    "count_reformatted_rows",
    "format_yaml_text",
    "format_uploaded_yaml_file",
    "is_supported_yaml_filename",
    "rewrite_playground_yaml_files",
]


def count_reformatted_rows(before_text: str, after_text: str) -> int:
    before_lines = str(before_text or "").splitlines()
    after_lines = str(after_text or "").splitlines()
    max_rows = max(len(before_lines), len(after_lines))
    changed_rows = 0
    for row_index in range(max_rows):
        before_line = before_lines[row_index] if row_index < len(before_lines) else ""
        after_line = after_lines[row_index] if row_index < len(after_lines) else ""
        if before_line != after_line:
            changed_rows += 1
    return changed_rows


def format_yaml_text(
    source_text: str,
    *,
    line_length: int = 88,
    convert_indent_4_to_2: bool = True,
) -> Dict[str, Any]:
    payload = yaml_reformat_payload_from_options(
        {
            "yaml_text": source_text,
            "line_length": line_length,
            "convert_indent_4_to_2": convert_indent_4_to_2,
        }
    )
    formatted_text = str(payload.get("formatted_yaml") or "")
    return {
        "changed": bool(payload.get("changed")),
        "formatted_yaml": formatted_text,
        "reformatted_rows": count_reformatted_rows(source_text, formatted_text),
    }


def _formatted_output_filename(input_filename: str) -> str:
    filename = str(input_filename or "uploaded.yml")
    lowered = filename.lower()
    if lowered.endswith(".yaml"):
        return filename[:-5] + ".formatted.yaml"
    if lowered.endswith(".yml"):
        return filename[:-4] + ".formatted.yml"
    return filename + ".formatted.yml"


def is_supported_yaml_filename(filename: str) -> bool:
    ext = os.path.splitext(str(filename or ""))[1].lower()
    return ext in {".yml", ".yaml", ""}


def format_uploaded_yaml_file(
    upload_path: str,
    original_filename: str,
    *,
    line_length: int = 88,
    convert_indent_4_to_2: bool = True,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "changed": False,
        "error": None,
        "reformatted_rows": 0,
        "output_filename": "formatted.yml",
        "input_filename": str(original_filename or "uploaded.yml"),
        "formatted_yaml": "",
    }
    try:
        if not is_supported_yaml_filename(original_filename):
            raise ValueError("Please upload a .yml or .yaml file.")
        with open(upload_path, "r", encoding="utf-8") as infile:
            source_text = infile.read()
        format_result = format_yaml_text(
            source_text,
            line_length=line_length,
            convert_indent_4_to_2=convert_indent_4_to_2,
        )
        result["changed"] = bool(format_result.get("changed"))
        result["reformatted_rows"] = int(format_result.get("reformatted_rows") or 0)
        result["formatted_yaml"] = str(format_result.get("formatted_yaml") or "")
        result["output_filename"] = _formatted_output_filename(result["input_filename"])
    except Exception as err:
        result["error"] = str(err)
    return result


def rewrite_playground_yaml_files(
    selected_tokens: Iterable[str],
    *,
    selected_playground_project: str,
    line_length: int = 88,
    convert_indent_4_to_2: bool = True,
) -> Dict[str, Any]:
    project = str(selected_playground_project or "default")
    result: Dict[str, Any] = {
        "selected_count": 0,
        "processed_count": 0,
        "changed_count": 0,
        "reformatted_rows_total": 0,
        "error_count": 0,
        "items": [],
    }

    tokens: List[str] = [str(token) for token in selected_tokens]
    result["selected_count"] = len(tokens)
    allowed_token_to_name = {
        str(item.get("token")): str(
            item.get("label") or os.path.basename(str(item.get("token")))
        )
        for item in list_playground_yaml_files(project)
        if item.get("token")
    }
    allowed_tokens = set(allowed_token_to_name.keys())
    project_root = (
        os.path.realpath(next(iter(allowed_tokens), "")) if allowed_tokens else ""
    )
    if project_root:
        project_root = os.path.dirname(project_root)

    if not tokens:
        return result

    current_user = user_info()
    if current_user is None or not getattr(current_user, "id", None):
        result["error_count"] += 1
        result["items"].append(
            {
                "name": "(playground)",
                "changed": False,
                "reformatted_rows": 0,
                "error": "Could not determine current user for playground access.",
            }
        )
        return result

    playground_area = SavedFile(current_user.id, fix=True, section="playground")

    for token_path in tokens:
        filename = str(
            allowed_token_to_name.get(token_path) or os.path.basename(token_path)
        )
        item: Dict[str, Any] = {
            "name": filename,
            "changed": False,
            "reformatted_rows": 0,
            "error": None,
        }
        try:
            if token_path not in allowed_tokens:
                raise ValueError("File is not in the selected playground project.")
            if project_root and not os.path.realpath(token_path).startswith(
                project_root + os.sep
            ):
                raise ValueError(
                    "Refusing to rewrite files outside the selected playground project."
                )

            with open(token_path, "r", encoding="utf-8") as infile:
                source_text = infile.read()

            format_result = format_yaml_text(
                source_text,
                line_length=line_length,
                convert_indent_4_to_2=convert_indent_4_to_2,
            )
            item["changed"] = bool(format_result.get("changed"))
            item["reformatted_rows"] = int(format_result.get("reformatted_rows") or 0)
            result["reformatted_rows_total"] += item["reformatted_rows"]

            if item["changed"]:
                playground_area.write_content(
                    str(format_result.get("formatted_yaml") or ""),
                    filename=filename,
                    project=project,
                    save=False,
                )
                result["changed_count"] += 1
        except Exception as err:
            item["error"] = str(err)
            result["error_count"] += 1
        finally:
            result["processed_count"] += 1
        result["items"].append(item)

    if result["changed_count"] > 0:
        try:
            playground_area.finalize()
        except Exception as err:
            result["error_count"] += 1
            result["items"].append(
                {
                    "name": "(finalize)",
                    "changed": False,
                    "reformatted_rows": 0,
                    "error": str(err),
                }
            )

    return result
