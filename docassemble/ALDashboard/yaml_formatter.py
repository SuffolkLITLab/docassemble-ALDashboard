import os
import json
import urllib.error
import urllib.request
from importlib import metadata
from typing import Any, Dict, Iterable, List, Optional, Tuple

from docassemble.base.util import user_info
from docassemble.webapp.backend import directory_for
from docassemble.webapp.files import SavedFile

try:
    from flask_login import current_user
except Exception:
    current_user = None  # type: ignore

from .api_dashboard_utils import yaml_reformat_payload_from_options
from .interview_linter import list_playground_yaml_files

__all__ = [
    "count_reformatted_rows",
    "format_yaml_text",
    "format_uploaded_yaml_file",
    "get_black_release_status",
    "is_supported_yaml_filename",
    "list_formatter_playground_yaml_files",
    "rewrite_playground_yaml_files",
]


def _resolve_current_user_id() -> Optional[int]:
    try:
        if current_user is not None and getattr(current_user, "is_authenticated", False):
            user_id = getattr(current_user, "id", None)
            if user_id is not None:
                return int(user_id)
    except Exception:
        pass

    try:
        info = user_info()
        user_id = getattr(info, "id", None)
        if user_id is not None:
            return int(user_id)
    except Exception:
        pass

    return None


def _is_excluded_black_target(filename: str) -> bool:
    return os.path.basename(str(filename or "")) in {"__init__.py", "setup.py"}


def list_formatter_playground_yaml_files(project: str = "default") -> List[Dict[str, str]]:
    uid = _resolve_current_user_id()
    if uid is None:
        return []
    try:
        area = SavedFile(uid, fix=True, section="playground")
        project_dir = directory_for(area, project or "default")
        if not project_dir or not os.path.isdir(project_dir):
            return []
        output: List[Dict[str, str]] = []
        for filename in sorted(os.listdir(project_dir)):
            full_path = os.path.join(project_dir, filename)
            if os.path.isfile(full_path) and filename.lower().endswith(
                (".yml", ".yaml")
            ):
                output.append({"label": filename, "token": filename})
        return output
    except Exception:
        return []


def _playground_module_files_via_section(project: str) -> Tuple[List[str], List[str]]:
    from docassemble.webapp.playground import PlaygroundSection

    module_section = PlaygroundSection(section="modules", project=project or "default")
    python_files: List[str] = []
    excluded_files: List[str] = []
    for filename in sorted(module_section.file_list):
        if not filename.endswith(".py"):
            continue
        if _is_excluded_black_target(filename):
            excluded_files.append(filename)
            continue
        full_path = module_section.get_file(filename)
        if os.path.isfile(full_path):
            python_files.append(full_path)
    return python_files, excluded_files


def _list_playground_python_files(
    project: str, area: SavedFile
) -> Tuple[List[str], List[str]]:
    try:
        python_files, excluded_files = _playground_module_files_via_section(project)
        if python_files or excluded_files:
            return python_files, excluded_files
    except Exception:
        pass

    project_dir = directory_for(area, project or "default")
    if not project_dir or not os.path.isdir(project_dir):
        return [], []

    python_files: List[str] = []
    excluded_files: List[str] = []
    for root, _, files in os.walk(project_dir):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            full_path = os.path.realpath(os.path.join(root, filename))
            if not os.path.isfile(full_path):
                continue
            relative_name = os.path.relpath(full_path, project_dir)
            if _is_excluded_black_target(filename):
                excluded_files.append(relative_name)
                continue
            python_files.append(full_path)
    return sorted(python_files), sorted(excluded_files)


def _split_version_parts(version: str) -> Tuple[int, ...]:
    parts = []
    for token in str(version or "").split("."):
        num = ""
        for char in token:
            if char.isdigit():
                num += char
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts)


def _is_newer_version(installed_version: str, latest_version: str) -> bool:
    installed = str(installed_version or "").strip()
    latest = str(latest_version or "").strip()
    if not installed or not latest:
        return False
    try:
        from packaging.version import Version  # type: ignore

        return Version(latest) > Version(installed)
    except Exception:
        return _split_version_parts(latest) > _split_version_parts(installed)


def _fetch_latest_black_version() -> Optional[str]:
    request = urllib.request.Request(
        "https://pypi.org/pypi/black/json",
        headers={"User-Agent": "docassemble.ALDashboard/black-version-check"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:  # nosec B310
        payload = json.loads(response.read().decode("utf-8"))
    latest_version = str(payload.get("info", {}).get("version") or "").strip()
    return latest_version or None


def get_black_release_status() -> Dict[str, Any]:
    installed_version: Optional[str] = None
    latest_version: Optional[str] = None
    check_error: Optional[str] = None

    try:
        installed_version = metadata.version("black")
    except metadata.PackageNotFoundError:
        installed_version = None
    except Exception as err:
        check_error = str(err)

    try:
        latest_version = _fetch_latest_black_version()
    except Exception as err:
        check_error = check_error or str(err)

    update_available = False
    if installed_version and latest_version:
        update_available = _is_newer_version(installed_version, latest_version)

    return {
        "installed_version": installed_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "error": check_error,
    }


def _format_playground_python_files_with_black(
    project: str,
    user_id: Optional[int],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "requested": True,
        "processed_count": 0,
        "changed_count": 0,
        "error_count": 0,
        "changed_files": [],
        "errors": [],
    }

    black_status = get_black_release_status()
    result["installed_version"] = black_status.get("installed_version")
    result["latest_version"] = black_status.get("latest_version")
    result["update_available"] = bool(black_status.get("update_available"))

    try:
        import black as black_module  # type: ignore
    except Exception:
        result["error_count"] = 1
        result["errors"].append(
            {
                "name": "(black)",
                "error": "The black package is not installed.",
            }
        )
        return result

    if user_id is None:
        result["error_count"] = 1
        result["errors"].append(
            {
                "name": "(playgroundmodules)",
                "error": "Could not determine current user for playground module access.",
            }
        )
        return result

    mode = black_module.FileMode()
    module_area = SavedFile(user_id, fix=True, section="playgroundmodules")
    python_files, _ = _list_playground_python_files(project, module_area)
    project_dir = directory_for(module_area, project or "default") or ""
    module_section = None
    try:
        from docassemble.webapp.playground import PlaygroundSection

        module_section = PlaygroundSection(section="modules", project=project or "default")
    except Exception:
        module_section = None

    for py_path in python_files:
        if module_section is not None:
            relative_name = os.path.basename(py_path)
        else:
            relative_name = (
                os.path.relpath(py_path, project_dir) if project_dir else py_path
            )
        try:
            with open(py_path, "r", encoding="utf-8") as infile:
                source_text = infile.read()

            try:
                formatted_text = black_module.format_file_contents(
                    source_text,
                    fast=False,
                    mode=mode,
                )
            except black_module.NothingChanged:
                formatted_text = source_text

            if formatted_text != source_text:
                if module_section is not None:
                    module_section.write_file(relative_name, formatted_text)
                else:
                    module_area.write_content(
                        formatted_text,
                        filename=relative_name,
                        project=project,
                        save=False,
                    )
                result["changed_count"] += 1
                result["changed_files"].append(relative_name)
        except Exception as err:
            result["error_count"] += 1
            result["errors"].append({"name": relative_name, "error": str(err)})
        finally:
            result["processed_count"] += 1

    if result["changed_count"] > 0 and module_section is None:
        try:
            module_area.finalize()
        except Exception as err:
            result["error_count"] += 1
            result["errors"].append({"name": "(finalize)", "error": str(err)})

    return result


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
    run_black_python_modules: bool = False,
) -> Dict[str, Any]:
    project = str(selected_playground_project or "default")
    result: Dict[str, Any] = {
        "selected_count": 0,
        "processed_count": 0,
        "changed_count": 0,
        "reformatted_rows_total": 0,
        "error_count": 0,
        "items": [],
        "black": {
            "requested": bool(run_black_python_modules),
            "processed_count": 0,
            "changed_count": 0,
            "error_count": 0,
            "changed_files": [],
            "errors": [],
            "installed_version": None,
            "latest_version": None,
            "update_available": False,
        },
    }

    tokens: List[str] = [str(token) for token in selected_tokens]
    result["selected_count"] = len(tokens)
    allowed_token_to_name = {
        str(item.get("token")): str(
            item.get("label") or os.path.basename(str(item.get("token")))
        )
        for item in list_formatter_playground_yaml_files(project)
        if item.get("token")
    }
    allowed_tokens = set(allowed_token_to_name.keys())

    project_root = ""

    if not tokens and not run_black_python_modules:
        return result

    current_user_id = _resolve_current_user_id()

    if not tokens and run_black_python_modules:
        black_result = _format_playground_python_files_with_black(
            project,
            current_user_id,
        )
        result["black"] = black_result
        result["error_count"] += int(black_result.get("error_count") or 0)
        return result

    if current_user_id is None:
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

    playground_area = SavedFile(current_user_id, fix=True, section="playground")
    try:
        project_root = directory_for(playground_area, project) or ""
        if project_root:
            project_root = os.path.realpath(project_root)
    except Exception:
        project_root = ""

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
            resolved_token_path = os.path.join(project_root, filename)
            normalized_token_path = os.path.realpath(resolved_token_path)
            if project_root and not (
                normalized_token_path == project_root
                or normalized_token_path.startswith(project_root + os.sep)
            ):
                raise ValueError(
                    "Refusing to rewrite files outside the selected playground project."
                )

            with open(resolved_token_path, "r", encoding="utf-8") as infile:
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

    if run_black_python_modules:
        black_result = _format_playground_python_files_with_black(
            project,
            current_user_id,
        )
        result["black"] = black_result
        result["error_count"] += int(black_result.get("error_count") or 0)

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
