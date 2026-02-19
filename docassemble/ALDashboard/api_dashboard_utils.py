import base64
import binascii
import importlib.resources
import json
import os
import re
import tempfile
from typing import Any, Dict, List, Mapping, Optional, cast

from flask import request
from docassemble.base.error import DAError

DASHBOARD_API_BASE_PATH = "/al/api/v1/dashboard"

DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class DashboardAPIValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
    raise DashboardAPIValidationError(f"Could not parse boolean value {value!r}.")


def decode_base64_content(content: Any) -> bytes:
    if not isinstance(content, str) or not content.strip():
        raise DashboardAPIValidationError(
            "file_content_base64 must be a non-empty base64-encoded string."
        )
    try:
        return base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error):
        raise DashboardAPIValidationError(
            "file_content_base64 is not valid base64 data."
        )


def coerce_async_flag(raw_options: Mapping[str, Any]) -> bool:
    mode = raw_options.get("mode")
    if mode is not None and str(mode).strip() != "":
        normalized_mode = str(mode).strip().lower()
        if normalized_mode in {"async", "asynchronous"}:
            return True
        if normalized_mode in {"sync", "synchronous"}:
            return False
        raise DashboardAPIValidationError("mode must be either 'sync' or 'async'.")
    if "async" in raw_options and raw_options.get("async") is not None:
        return parse_bool(raw_options.get("async"), default=False)
    return False


def _load_json_field(
    raw_value: Any, *, field_name: str, expected_type: type
) -> Optional[Any]:
    if raw_value is None:
        return None
    value = raw_value
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if stripped == "":
            return None
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            raise DashboardAPIValidationError(
                f"{field_name} must be valid JSON when provided as a string."
            )
    if not isinstance(value, expected_type):
        raise DashboardAPIValidationError(
            f"{field_name} must be a {expected_type.__name__}."
        )
    return value


def merge_raw_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(raw_options)
    options_blob = raw_options.get("options")
    parsed_options = _load_json_field(
        options_blob, field_name="options", expected_type=dict
    )
    if parsed_options:
        for key, value in parsed_options.items():
            merged.setdefault(key, value)
    return merged


def _request_dict() -> Dict[str, Any]:
    post_data = request.get_json(silent=True)
    if isinstance(post_data, dict):
        return post_data
    return dict(request.form)


def _require_text(value: Any, field_name: str) -> str:
    if value is None:
        raise DashboardAPIValidationError(f"{field_name} is required.")
    text = str(value).strip()
    if not text:
        raise DashboardAPIValidationError(f"{field_name} is required.")
    return text


def _coerce_tr_langs(value: Any) -> List[str]:
    if isinstance(value, list):
        langs = [str(item).strip() for item in value if str(item).strip()]
        if not langs:
            raise DashboardAPIValidationError(
                "tr_langs must contain at least one language code."
            )
        return langs
    if value is None:
        raise DashboardAPIValidationError("tr_langs is required.")
    text = str(value)
    langs = [item.strip() for item in text.replace(",", " ").split() if item.strip()]
    if not langs:
        raise DashboardAPIValidationError(
            "tr_langs must contain at least one language code."
        )
    return langs


def _parse_special_words(value: Any) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    text = str(value).strip()
    if not text:
        return None
    output: Dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if key:
            output[key] = val
    return output or None


def _validate_upload_size(
    content: bytes, max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
) -> None:
    if len(content) == 0:
        raise DashboardAPIValidationError("Uploaded file is empty.")
    if len(content) > max_upload_bytes:
        raise DashboardAPIValidationError(
            f"Uploaded file is larger than {max_upload_bytes} bytes.", status_code=413
        )


def _read_single_upload(*, field_name: str = "file") -> Dict[str, Any]:
    if field_name in request.files:
        upload = request.files[field_name]
        filename = upload.filename or "upload"
        content = upload.read()
        _validate_upload_size(content)
        return {"filename": filename, "content": content}

    post_data = request.get_json(silent=True)
    if isinstance(post_data, dict):
        filename = str(post_data.get("filename") or "upload")
        content = decode_base64_content(post_data.get("file_content_base64"))
        _validate_upload_size(content)
        return {"filename": filename, "content": content}

    raise DashboardAPIValidationError(
        "Expected multipart/form-data with a file, or JSON with file_content_base64."
    )


def _read_multi_uploads(*, field_name: str = "files") -> List[Dict[str, Any]]:
    uploads = []
    if field_name in request.files:
        for upload in request.files.getlist(field_name):
            filename = upload.filename or "upload"
            content = upload.read()
            _validate_upload_size(content)
            uploads.append({"filename": filename, "content": content})
        if uploads:
            return uploads

    if "file" in request.files and field_name == "files":
        upload = request.files["file"]
        filename = upload.filename or "upload"
        content = upload.read()
        _validate_upload_size(content)
        return [{"filename": filename, "content": content}]

    post_data = request.get_json(silent=True)
    if isinstance(post_data, dict):
        json_files = post_data.get("files")
        if isinstance(json_files, list):
            for item in json_files:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or "upload")
                content = decode_base64_content(item.get("file_content_base64"))
                _validate_upload_size(content)
                uploads.append({"filename": filename, "content": content})
            if uploads:
                return uploads

    raise DashboardAPIValidationError(
        "Expected multipart/form-data with files field, or JSON with files[]."
    )


def _write_temp_file(filename: str, content: bytes) -> str:
    suffix = os.path.splitext(filename)[1] or ".tmp"
    with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        return tmp.name


def _resolved_interview_path(yaml_filename: str) -> Optional[str]:
    if ":" not in yaml_filename:
        return None
    package_name, rel = yaml_filename.split(":", 1)
    rel = rel.strip().lstrip("/")
    if not rel.startswith("data/questions/"):
        rel = f"data/questions/{rel}"
    try:
        ref = importlib.resources.files(package_name) / rel
        with importlib.resources.as_file(ref) as path:
            if path.exists():
                return str(path)
    except Exception:
        return None
    return None


def translation_payload_from_request() -> Dict[str, Any]:
    raw = merge_raw_options(_request_dict())
    return translation_payload_from_options(raw)


def translation_payload_from_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    from .translation import translation_file

    raw = merge_raw_options(raw_options)
    yaml_filename = _require_text(
        raw.get("interview_path") or raw.get("yaml_filename"), "interview_path"
    )
    tr_langs = _coerce_tr_langs(raw.get("tr_langs") or raw.get("languages"))

    use_gpt = parse_bool(raw.get("use_gpt"), default=False)
    include_xlsx_base64 = parse_bool(raw.get("include_xlsx_base64"), default=True)
    validate_mako = parse_bool(raw.get("validate_mako"), default=True)

    special_words = _parse_special_words(raw.get("special_words"))
    interview_context = raw.get("interview_context")

    translations = []
    for tr_lang in tr_langs:
        try:
            result = translation_file(
                yaml_filename=yaml_filename,
                tr_lang=tr_lang,
                use_gpt=use_gpt,
                model=raw.get("model"),
                interview_context=str(interview_context) if interview_context else None,
                special_words=cast(Any, special_words),
                openai_api=raw.get("openai_api"),
                openai_base_url=raw.get("openai_base_url"),
                validate_mako=validate_mako,
            )
        except DAError:
            resolved_path = _resolved_interview_path(yaml_filename)
            if not resolved_path:
                raise
            result = translation_file(
                yaml_filename=resolved_path,
                tr_lang=tr_lang,
                use_gpt=use_gpt,
                model=raw.get("model"),
                interview_context=str(interview_context) if interview_context else None,
                special_words=cast(Any, special_words),
                openai_api=raw.get("openai_api"),
                openai_base_url=raw.get("openai_base_url"),
                validate_mako=validate_mako,
            )
        tr_entry: Dict[str, Any] = {
            "language": tr_lang,
            "filename": getattr(result.file, "filename", f"{tr_lang}.xlsx"),
            "untranslated_words": result.untranslated_words,
            "untranslated_segments": result.untranslated_segments,
            "total_rows": result.total_rows,
        }
        if include_xlsx_base64:
            with open(result.file.path(), "rb") as handle:
                tr_entry["xlsx_base64"] = base64.b64encode(handle.read()).decode(
                    "ascii"
                )
        translations.append(tr_entry)

    return {
        "interview_path": yaml_filename,
        "translations": translations,
    }


def autolabel_payload_from_request() -> Dict[str, Any]:
    upload = _read_single_upload(field_name="file")
    raw = merge_raw_options(_request_dict())
    return autolabel_payload_from_options(
        {
            "filename": upload["filename"],
            "file_content_base64": base64.b64encode(upload["content"]).decode("ascii"),
            **raw,
        }
    )


def docx_runs_payload_from_request() -> Dict[str, Any]:
    upload = _read_single_upload(field_name="file")
    raw = merge_raw_options(_request_dict())
    return docx_runs_payload_from_options(
        {
            "filename": upload["filename"],
            "file_content_base64": base64.b64encode(upload["content"]).decode("ascii"),
            **raw,
        }
    )


def docx_runs_payload_from_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    from .docx_wrangling import get_docx_run_items

    raw = merge_raw_options(raw_options)
    filename = str(raw.get("filename") or "upload.docx")
    file_content_base64 = raw.get("file_content_base64")
    if file_content_base64 is None:
        raise DashboardAPIValidationError("file_content_base64 is required.")
    content = decode_base64_content(file_content_base64)
    _validate_upload_size(content)

    if not filename.lower().endswith(".docx"):
        raise DashboardAPIValidationError(
            "Only DOCX uploads are supported.", status_code=415
        )

    temp_path = _write_temp_file(filename, content)
    try:
        runs = get_docx_run_items(temp_path)
        paragraph_count = 0
        if runs:
            paragraph_count = max(int(item[0]) for item in runs) + 1
        return {
            "input_filename": filename,
            "paragraph_count": paragraph_count,
            "run_count": len(runs),
            "results": runs,
        }
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def autolabel_payload_from_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    from .docx_wrangling import get_labeled_docx_runs, update_docx

    raw = merge_raw_options(raw_options)
    filename = str(raw.get("filename") or "upload.docx")
    file_content_base64 = raw.get("file_content_base64")
    if file_content_base64 is None:
        raise DashboardAPIValidationError("file_content_base64 is required.")
    content = decode_base64_content(file_content_base64)
    _validate_upload_size(content)

    if not filename.lower().endswith(".docx"):
        raise DashboardAPIValidationError(
            "Only DOCX uploads are supported.", status_code=415
        )

    include_labeled_docx_base64 = parse_bool(
        raw.get("include_labeled_docx_base64"), default=False
    )
    openai_api_override = raw.get("openai_api")
    if openai_api_override is not None:
        openai_api_override = str(openai_api_override)
    openai_base_url_override = raw.get("openai_base_url")
    if openai_base_url_override is not None:
        openai_base_url_override = str(openai_base_url_override)
    openai_model_override = raw.get("openai_model")
    if openai_model_override is not None:
        openai_model_override = str(openai_model_override)
    custom_prompt_override = raw.get("custom_prompt")
    if custom_prompt_override is not None:
        custom_prompt_override = str(custom_prompt_override)
        if not custom_prompt_override.strip():
            custom_prompt_override = None
    additional_instructions_override = raw.get("additional_instructions")
    if additional_instructions_override is not None:
        additional_instructions_override = str(additional_instructions_override)
    max_output_tokens_override = raw.get("max_output_tokens")
    if max_output_tokens_override in (None, ""):
        parsed_max_output_tokens = None
    else:
        if max_output_tokens_override is None:
            raise DashboardAPIValidationError("max_output_tokens must be an integer.")
        try:
            parsed_max_output_tokens = int(max_output_tokens_override)
        except (TypeError, ValueError) as exc:
            raise DashboardAPIValidationError(
                "max_output_tokens must be an integer."
            ) from exc
        if parsed_max_output_tokens <= 0:
            raise DashboardAPIValidationError(
                "max_output_tokens must be a positive integer."
            )

    custom_people_names = _load_json_field(
        raw.get("custom_people_names"),
        field_name="custom_people_names",
        expected_type=list,
    )

    temp_path = _write_temp_file(filename, content)
    try:
        guesses = get_labeled_docx_runs(
            temp_path,
            custom_people_names=custom_people_names,
            openai_api=openai_api_override,
            openai_base_url=openai_base_url_override,
            model=openai_model_override or "gpt-5-nano",
            custom_prompt=custom_prompt_override,
            additional_instructions=additional_instructions_override,
            max_output_tokens=parsed_max_output_tokens,
        )
        payload: Dict[str, Any] = {
            "input_filename": filename,
            "results": guesses,
        }
        if include_labeled_docx_base64:
            updated = update_docx(temp_path, guesses)
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".docx", delete=False
            ) as out:
                out_path = out.name
            try:
                updated.save(out_path)
                with open(out_path, "rb") as handle:
                    payload["labeled_docx_base64"] = base64.b64encode(
                        handle.read()
                    ).decode("ascii")
            finally:
                if os.path.exists(out_path):
                    os.remove(out_path)
        return payload
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def relabel_payload_from_request() -> Dict[str, Any]:
    upload: Optional[Dict[str, Any]] = None
    if "file" in request.files:
        upload = _read_single_upload(field_name="file")
    raw = merge_raw_options(_request_dict())
    options: Dict[str, Any] = dict(raw)
    if upload is not None:
        options.setdefault("filename", upload["filename"])
        options.setdefault(
            "file_content_base64", base64.b64encode(upload["content"]).decode("ascii")
        )
    return relabel_payload_from_options(options)


def _coerce_label_item(item: Any, *, field_name: str) -> List[Any]:
    if isinstance(item, dict):
        paragraph = item.get("paragraph")
        run = item.get("run")
        text = item.get("text")
        new_paragraph = item.get("new_paragraph", 0)
    elif isinstance(item, (list, tuple)) and len(item) >= 4:
        paragraph, run, text, new_paragraph = item[:4]
    else:
        raise DashboardAPIValidationError(
            f"{field_name} entries must be [paragraph, run, text, new_paragraph] or objects."
        )

    try:
        if paragraph is None or run is None:
            raise DashboardAPIValidationError(
                f"{field_name} paragraph/run values must be integers."
            )
        paragraph_num = int(paragraph)
        run_num = int(run)
    except (TypeError, ValueError):
        raise DashboardAPIValidationError(
            f"{field_name} paragraph/run values must be integers."
        )
    if paragraph_num < 0 or run_num < 0:
        raise DashboardAPIValidationError(
            f"{field_name} paragraph/run values must be non-negative."
        )
    if text is None:
        raise DashboardAPIValidationError(f"{field_name} text cannot be null.")
    try:
        new_paragraph_num = int(new_paragraph)
    except (TypeError, ValueError):
        new_paragraph_num = 0
    if new_paragraph_num not in (-1, 0, 1):
        new_paragraph_num = 0
    return [paragraph_num, run_num, str(text), new_paragraph_num]


def _parse_index_text_map(raw_value: Any) -> Dict[int, str]:
    parsed = _load_json_field(
        raw_value, field_name="replace_labels_by_index", expected_type=(dict)
    )
    if parsed is None:
        return {}
    output: Dict[int, str] = {}
    for key, value in parsed.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            raise DashboardAPIValidationError(
                "replace_labels_by_index keys must be integers."
            )
        if idx < 0:
            raise DashboardAPIValidationError(
                "replace_labels_by_index keys must be non-negative."
            )
        if value is None:
            raise DashboardAPIValidationError(
                "replace_labels_by_index values cannot be null."
            )
        output[idx] = str(value)
    return output


def _parse_skip_indexes(raw_value: Any) -> List[int]:
    parsed = _load_json_field(
        raw_value, field_name="skip_label_indexes", expected_type=list
    )
    if parsed is None:
        return []
    output: List[int] = []
    for item in parsed:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            raise DashboardAPIValidationError(
                "skip_label_indexes must contain integers."
            )
        if idx < 0:
            raise DashboardAPIValidationError(
                "skip_label_indexes values must be non-negative."
            )
        output.append(idx)
    return output


def _apply_add_label_rules(
    rules: List[dict], doc_path: str, existing_labels: List[List[Any]]
) -> List[List[Any]]:
    from .docx_wrangling import get_docx_run_items

    doc_runs = get_docx_run_items(doc_path)
    output: List[List[Any]] = []
    existing_keys = {(int(item[0]), int(item[1])) for item in existing_labels}

    for rule in rules:
        if not isinstance(rule, dict):
            raise DashboardAPIValidationError(
                "add_label_rules entries must be objects."
            )
        if "paragraph_start" not in rule:
            raise DashboardAPIValidationError(
                "Each add_label_rules entry requires paragraph_start."
            )
        paragraph_start_raw = rule.get("paragraph_start")
        paragraph_end_raw = rule.get("paragraph_end")
        if paragraph_start_raw is None:
            raise DashboardAPIValidationError(
                "Each add_label_rules entry requires paragraph_start."
            )
        try:
            paragraph_start = int(paragraph_start_raw)
            paragraph_end = int(
                paragraph_end_raw if paragraph_end_raw is not None else paragraph_start
            )
        except (TypeError, ValueError):
            raise DashboardAPIValidationError(
                "add_label_rules paragraph_start/paragraph_end must be integers."
            )
        if paragraph_start < 0 or paragraph_end < paragraph_start:
            raise DashboardAPIValidationError(
                "add_label_rules paragraph range is invalid."
            )

        contains = rule.get("contains")
        regex_text = rule.get("regex")
        replacement = rule.get("replacement")
        if replacement is None:
            raise DashboardAPIValidationError(
                "Each add_label_rules entry requires replacement."
            )
        replacement = str(replacement)
        action = str(rule.get("on_match", "whole_run"))
        try:
            max_matches = int(rule.get("max_matches", 0))
        except (TypeError, ValueError):
            raise DashboardAPIValidationError(
                "add_label_rules max_matches must be an integer."
            )
        if max_matches < 0:
            raise DashboardAPIValidationError(
                "add_label_rules max_matches must be non-negative."
            )
        try:
            new_paragraph = int(rule.get("new_paragraph", 0))
        except (TypeError, ValueError):
            new_paragraph = 0
        if new_paragraph not in (-1, 0, 1):
            new_paragraph = 0
        overwrite_existing = parse_bool(rule.get("overwrite_existing"), default=False)

        pattern = None
        if regex_text:
            try:
                pattern = re.compile(str(regex_text))
            except re.error as exc:
                raise DashboardAPIValidationError(
                    f"add_label_rules regex is invalid: {exc}"
                )

        matches = 0
        for pnum, rnum, run_text in doc_runs:
            pnum = int(pnum)
            rnum = int(rnum)
            if pnum < paragraph_start or pnum > paragraph_end:
                continue
            if contains is not None and str(contains) not in str(run_text):
                continue
            regex_match = None
            if pattern is not None:
                regex_match = pattern.search(str(run_text))
                if regex_match is None:
                    continue

            key = (pnum, rnum)
            if key in existing_keys and not overwrite_existing:
                continue

            if action == "replace_contains" and contains is not None:
                new_text = str(run_text).replace(str(contains), replacement)
            elif action == "regex_sub" and pattern is not None:
                new_text = pattern.sub(replacement, str(run_text))
            elif action == "regex_group_1" and regex_match is not None:
                group_text = (
                    regex_match.group(1)
                    if regex_match.groups()
                    else regex_match.group(0)
                )
                new_text = replacement.replace("{match}", group_text)
            else:
                new_text = replacement

            output.append([pnum, rnum, new_text, new_paragraph])
            existing_keys.add(key)
            matches += 1
            if max_matches and matches >= max_matches:
                break
    return output


def relabel_payload_from_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    from .docx_wrangling import get_labeled_docx_runs, update_docx

    raw = merge_raw_options(raw_options)
    filename = str(raw.get("filename") or "upload.docx")
    file_content_base64 = raw.get("file_content_base64")
    include_labeled_docx_base64 = parse_bool(
        raw.get("include_labeled_docx_base64"), default=False
    )

    raw_results = _load_json_field(
        raw.get("results"), field_name="results", expected_type=list
    )
    if raw_results is None and file_content_base64 is None:
        raise DashboardAPIValidationError(
            "Provide results or upload a DOCX file to relabel."
        )

    content: Optional[bytes] = None
    temp_path: Optional[str] = None
    if file_content_base64 is not None:
        content = decode_base64_content(file_content_base64)
        _validate_upload_size(content)
        if not filename.lower().endswith(".docx"):
            raise DashboardAPIValidationError(
                "Only DOCX uploads are supported.", status_code=415
            )
        temp_path = _write_temp_file(filename, content)

    openai_api_override = raw.get("openai_api")
    if openai_api_override is not None:
        openai_api_override = str(openai_api_override)
    openai_base_url_override = raw.get("openai_base_url")
    if openai_base_url_override is not None:
        openai_base_url_override = str(openai_base_url_override)
    openai_model_override = raw.get("openai_model")
    if openai_model_override is not None:
        openai_model_override = str(openai_model_override)
    custom_prompt_override = raw.get("custom_prompt")
    if custom_prompt_override is not None:
        custom_prompt_override = str(custom_prompt_override)
        if not custom_prompt_override.strip():
            custom_prompt_override = None
    additional_instructions_override = raw.get("additional_instructions")
    if additional_instructions_override is not None:
        additional_instructions_override = str(additional_instructions_override)

    max_output_tokens_override = raw.get("max_output_tokens")
    if max_output_tokens_override in (None, ""):
        parsed_max_output_tokens = None
    else:
        if max_output_tokens_override is None:
            raise DashboardAPIValidationError("max_output_tokens must be an integer.")
        try:
            parsed_max_output_tokens = int(max_output_tokens_override)
        except (TypeError, ValueError) as exc:
            raise DashboardAPIValidationError(
                "max_output_tokens must be an integer."
            ) from exc
        if parsed_max_output_tokens <= 0:
            raise DashboardAPIValidationError(
                "max_output_tokens must be a positive integer."
            )

    custom_people_names = _load_json_field(
        raw.get("custom_people_names"),
        field_name="custom_people_names",
        expected_type=list,
    )

    try:
        if raw_results is not None:
            labels = [
                _coerce_label_item(item, field_name="results") for item in raw_results
            ]
        else:
            assert temp_path is not None
            labels = [
                list(item)
                for item in get_labeled_docx_runs(
                    temp_path,
                    custom_people_names=custom_people_names,
                    openai_api=openai_api_override,
                    openai_base_url=openai_base_url_override,
                    model=openai_model_override or "gpt-5-nano",
                    custom_prompt=custom_prompt_override,
                    additional_instructions=additional_instructions_override,
                    max_output_tokens=parsed_max_output_tokens,
                )
            ]

        replace_by_index = _parse_index_text_map(raw.get("replace_labels_by_index"))
        for idx, replacement_text in replace_by_index.items():
            if idx < len(labels):
                labels[idx][2] = replacement_text

        skip_indexes = set(_parse_skip_indexes(raw.get("skip_label_indexes")))
        labels = [label for idx, label in enumerate(labels) if idx not in skip_indexes]

        add_labels_raw = _load_json_field(
            raw.get("add_labels"), field_name="add_labels", expected_type=list
        )
        if add_labels_raw:
            for item in add_labels_raw:
                labels.append(_coerce_label_item(item, field_name="add_labels"))

        add_label_rules = _load_json_field(
            raw.get("add_label_rules"), field_name="add_label_rules", expected_type=list
        )
        if add_label_rules:
            if temp_path is None:
                raise DashboardAPIValidationError(
                    "add_label_rules requires an uploaded DOCX file."
                )
            labels.extend(_apply_add_label_rules(add_label_rules, temp_path, labels))

        payload: Dict[str, Any] = {"input_filename": filename, "results": labels}

        if include_labeled_docx_base64:
            if temp_path is None:
                raise DashboardAPIValidationError(
                    "include_labeled_docx_base64 requires an uploaded DOCX file."
                )
            labels_for_update = [
                (int(item[0]), int(item[1]), str(item[2]), int(item[3]))
                for item in labels
            ]
            updated = update_docx(temp_path, labels_for_update)
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".docx", delete=False
            ) as out:
                out_path = out.name
            try:
                updated.save(out_path)
                with open(out_path, "rb") as handle:
                    payload["labeled_docx_base64"] = base64.b64encode(
                        handle.read()
                    ).decode("ascii")
            finally:
                if os.path.exists(out_path):
                    os.remove(out_path)

        return payload
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def bootstrap_payload_from_request() -> Dict[str, Any]:
    raw = merge_raw_options(_request_dict())

    scss_text = raw.get("scss_text")
    scss_path = None

    if "file" in request.files:
        upload = request.files["file"]
        filename = upload.filename or "upload.scss"
        if not filename.lower().endswith((".scss", ".sass")):
            raise DashboardAPIValidationError(
                "Bootstrap compile requires a .scss or .sass file.", status_code=415
            )
        content = upload.read()
        _validate_upload_size(content)
        scss_path = _write_temp_file(filename, content)
    elif isinstance(request.get_json(silent=True), dict):
        post_data = request.get_json(silent=True)
        if isinstance(post_data, dict) and post_data.get("file_content_base64"):
            filename = str(post_data.get("filename") or "upload.scss")
            content = decode_base64_content(post_data.get("file_content_base64"))
            _validate_upload_size(content)
            scss_path = _write_temp_file(filename, content)

    options: Dict[str, Any] = dict(raw)
    if scss_path:
        with open(scss_path, "rb") as handle:
            options["file_content_base64"] = base64.b64encode(handle.read()).decode(
                "ascii"
            )
            options["filename"] = os.path.basename(scss_path)
    try:
        return bootstrap_payload_from_options(options)
    finally:
        if scss_path and os.path.exists(scss_path):
            os.remove(scss_path)


def bootstrap_payload_from_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    from .bootstrap_compiler import BootstrapCompileError, compile_bootstrap_theme

    raw = merge_raw_options(raw_options)
    include_css_base64 = parse_bool(raw.get("include_css_base64"), default=True)

    scss_text = raw.get("scss_text")
    scss_path = None
    file_content_base64 = raw.get("file_content_base64")
    filename = str(raw.get("filename") or "upload.scss")
    if file_content_base64:
        content = decode_base64_content(file_content_base64)
        _validate_upload_size(content)
        if not filename.lower().endswith((".scss", ".sass")):
            raise DashboardAPIValidationError(
                "Bootstrap compile requires a .scss or .sass file.", status_code=415
            )
        scss_path = _write_temp_file(filename, content)

    if not scss_text and not scss_path:
        raise DashboardAPIValidationError("Provide scss_text or upload a .scss file.")

    try:
        compiled = compile_bootstrap_theme(
            scss_text=str(scss_text) if scss_text else None, scss_path=scss_path
        )
        payload: Dict[str, Any] = {
            "scss_filename": compiled["scss_filename"],
            "css_filename": compiled["css_filename"],
            "stderr": compiled.get("stderr", ""),
            "stdout": compiled.get("stdout", ""),
        }
        if include_css_base64:
            payload["css_base64"] = base64.b64encode(
                compiled["css_text"].encode("utf-8")
            ).decode("ascii")
        else:
            payload["css_text"] = compiled["css_text"]
        return payload
    except BootstrapCompileError as err:
        raise DashboardAPIValidationError(str(err), status_code=400)
    finally:
        if scss_path and os.path.exists(scss_path):
            os.remove(scss_path)


def validate_translation_payload_from_request() -> Dict[str, Any]:
    upload = _read_single_upload(field_name="file")
    raw = merge_raw_options(_request_dict())
    return validate_translation_payload_from_options(
        {
            "filename": upload["filename"],
            "file_content_base64": base64.b64encode(upload["content"]).decode("ascii"),
            **raw,
        }
    )


def validate_translation_payload_from_options(
    raw_options: Mapping[str, Any],
) -> Dict[str, Any]:
    from .translation_validation import validate_translation_xlsx

    raw = merge_raw_options(raw_options)
    filename = str(raw.get("filename") or "upload.xlsx")
    file_content_base64 = raw.get("file_content_base64")
    if file_content_base64 is None:
        raise DashboardAPIValidationError("file_content_base64 is required.")
    content = decode_base64_content(file_content_base64)
    _validate_upload_size(content)

    if not filename.lower().endswith(".xlsx"):
        raise DashboardAPIValidationError(
            "Only XLSX uploads are supported.", status_code=415
        )

    temp_path = _write_temp_file(filename, content)
    try:
        result = validate_translation_xlsx(temp_path)
        result["input_filename"] = filename
        return result
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def review_screen_payload_from_request() -> Dict[str, Any]:
    raw = merge_raw_options(_request_dict())
    yaml_texts: List[str] = []

    if "files" in request.files or "file" in request.files:
        uploads = _read_multi_uploads(field_name="files")
        yaml_texts = [
            upload["content"].decode("utf-8", errors="replace") for upload in uploads
        ]
    else:
        post_data = request.get_json(silent=True)
        if isinstance(post_data, dict):
            if isinstance(post_data.get("yaml_texts"), list):
                yaml_texts = [
                    str(item) for item in post_data["yaml_texts"] if str(item).strip()
                ]
            elif post_data.get("yaml_text"):
                yaml_texts = [str(post_data.get("yaml_text"))]
            elif isinstance(post_data.get("files"), list):
                uploads = _read_multi_uploads(field_name="files")
                yaml_texts = [
                    upload["content"].decode("utf-8", errors="replace")
                    for upload in uploads
                ]

    if not yaml_texts:
        raise DashboardAPIValidationError(
            "Provide YAML files (multipart files[]) or JSON yaml_text/yaml_texts."
        )

    return review_screen_payload_from_options(
        {
            "yaml_texts": yaml_texts,
            "build_revisit_blocks": raw.get("build_revisit_blocks"),
            "point_sections_to_review": raw.get("point_sections_to_review"),
        }
    )


def review_screen_payload_from_options(
    raw_options: Mapping[str, Any],
) -> Dict[str, Any]:
    from .review_screen_generator import generate_review_screen_yaml

    raw = merge_raw_options(raw_options)
    yaml_texts = []
    if isinstance(raw.get("yaml_texts"), list):
        yaml_texts = [str(item) for item in raw["yaml_texts"] if str(item).strip()]
    elif raw.get("yaml_text"):
        yaml_texts = [str(raw["yaml_text"])]
    elif isinstance(raw.get("files"), list):
        for item in raw["files"]:
            if isinstance(item, dict) and item.get("file_content_base64"):
                yaml_texts.append(
                    decode_base64_content(item["file_content_base64"]).decode(
                        "utf-8", errors="replace"
                    )
                )

    if not yaml_texts:
        raise DashboardAPIValidationError(
            "Provide YAML files (multipart files[]) or JSON yaml_text/yaml_texts."
        )

    review_yaml = generate_review_screen_yaml(
        yaml_texts,
        build_revisit_blocks=parse_bool(raw.get("build_revisit_blocks"), default=True),
        point_sections_to_review=parse_bool(
            raw.get("point_sections_to_review"), default=True
        ),
    )
    return {"review_yaml": review_yaml}


def validate_docx_payload_from_request() -> Dict[str, Any]:
    uploads = _read_multi_uploads(field_name="files")
    raw = merge_raw_options(_request_dict())
    return validate_docx_payload_from_options(
        {
            "files": [
                {
                    "filename": upload["filename"],
                    "file_content_base64": base64.b64encode(upload["content"]).decode(
                        "ascii"
                    ),
                }
                for upload in uploads
            ],
            **raw,
        }
    )


def validate_docx_payload_from_options(
    raw_options: Mapping[str, Any],
) -> Dict[str, Any]:
    from .validate_docx import get_jinja_errors

    raw = merge_raw_options(raw_options)
    files_option = raw.get("files")
    if not isinstance(files_option, list) or not files_option:
        raise DashboardAPIValidationError(
            "Expected files[] payload for DOCX validation."
        )

    files = []
    for upload in files_option:
        if not isinstance(upload, dict):
            continue
        filename = str(upload.get("filename") or "upload.docx")
        content = decode_base64_content(upload.get("file_content_base64"))
        _validate_upload_size(content)
        if not filename.lower().endswith(".docx"):
            raise DashboardAPIValidationError(
                "Only DOCX uploads are supported.", status_code=415
            )

        temp_path = _write_temp_file(filename, content)
        try:
            files.append({"file": filename, "errors": get_jinja_errors(temp_path)})
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    return {"files": files}


def interview_lint_payload_from_request() -> Dict[str, Any]:
    raw = merge_raw_options(_request_dict())
    uploads: List[Dict[str, Any]] = []
    try:
        uploads = _read_multi_uploads(field_name="files")
    except DashboardAPIValidationError:
        uploads = []
    return interview_lint_payload_from_options(raw, uploads=uploads)


def interview_lint_payload_from_options(
    raw_options: Mapping[str, Any], *, uploads: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    from .interview_linter import lint_multiple_sources

    raw = merge_raw_options(raw_options)
    include_llm = parse_bool(raw.get("include_llm"), default=False)
    language = str(raw.get("language") or "en")

    temp_paths: List[str] = []
    lint_sources: List[Dict[str, str]] = []

    source_items = _load_json_field(
        raw.get("sources"), field_name="sources", expected_type=list
    )
    if isinstance(source_items, list):
        for item in source_items:
            if not isinstance(item, dict):
                raise DashboardAPIValidationError(
                    "sources entries must be objects with token."
                )
            token = item.get("token")
            if token is None:
                raise DashboardAPIValidationError("Each sources entry requires token.")
            name = str(item.get("name") or token)
            lint_sources.append({"name": name, "token": str(token)})

    source_tokens = _load_json_field(
        raw.get("source_tokens"), field_name="source_tokens", expected_type=list
    )
    if isinstance(source_tokens, list):
        for token in source_tokens:
            lint_sources.append({"name": str(token), "token": str(token)})

    yaml_filenames = _load_json_field(
        raw.get("yaml_filenames"), field_name="yaml_filenames", expected_type=list
    )
    if isinstance(yaml_filenames, list):
        for yaml_filename in yaml_filenames:
            filename = str(yaml_filename)
            lint_sources.append({"name": filename, "token": f"ref:{filename}"})

    if uploads:
        for upload in uploads:
            filename = str(upload.get("filename") or "upload.yml")
            content = upload.get("content")
            if not isinstance(content, (bytes, bytearray)):
                raise DashboardAPIValidationError("Upload content must be bytes.")
            temp_path = _write_temp_file(filename, bytes(content))
            temp_paths.append(temp_path)
            lint_sources.append({"name": filename, "token": temp_path})

    if not lint_sources:
        raise DashboardAPIValidationError(
            "Provide at least one source via multipart files[], JSON files[], sources[], source_tokens[], or yaml_filenames[]."
        )

    try:
        reports = lint_multiple_sources(
            lint_sources, language=language, include_llm=include_llm
        )
    finally:
        for temp_path in temp_paths:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    return {
        "include_llm": include_llm,
        "language": language,
        "count": len(reports),
        "reports": reports,
    }


def _dayaml_issue_severity(message: str) -> str:
    normalized = message.strip().lower()
    if (
        "does not call validation_error" in normalized
        or normalized.startswith("warning:")
    ):
        return "warning"
    return "error"


def _run_dayaml_checker(yaml_text: str, *, input_file: str) -> List[Any]:
    try:
        from dayamlchecker.yaml_structure import find_errors_from_string
    except Exception as exc:
        raise DashboardAPIValidationError(
            "DAYamlChecker is not installed; install it to use /yaml/check."
        ) from exc

    try:
        return list(find_errors_from_string(yaml_text, input_file=input_file))
    except Exception as exc:
        raise DashboardAPIValidationError(
            f"DAYamlChecker validation failed: {exc}"
        ) from exc


def _run_dayaml_reformat(
    yaml_text: str, *, line_length: int, convert_indent_4_to_2: bool
) -> Any:
    try:
        from dayamlchecker.code_formatter import FormatterConfig, format_yaml_string
    except Exception as exc:
        raise DashboardAPIValidationError(
            "DAYamlChecker formatter is not available; install a DAYamlChecker "
            "version that provides dayamlchecker.code_formatter to use /yaml/reformat."
        ) from exc

    try:
        config = FormatterConfig(
            black_line_length=line_length,
            convert_indent_4_to_2=convert_indent_4_to_2,
        )
        return format_yaml_string(yaml_text, config=config)
    except Exception as exc:
        raise DashboardAPIValidationError(f"DAYamlChecker reformat failed: {exc}") from exc


def _coerce_yaml_text(raw: Mapping[str, Any], *, required_field: str = "yaml_text") -> str:
    yaml_raw = raw.get("yaml_text")
    if yaml_raw is None:
        yaml_raw = raw.get("yaml_content")
    if yaml_raw is None:
        raise DashboardAPIValidationError(
            f"{required_field} is required (or provide yaml_content)."
        )
    yaml_text = str(yaml_raw)
    if not yaml_text.strip():
        raise DashboardAPIValidationError(
            f"{required_field} is required (or provide yaml_content)."
        )
    return yaml_text


def yaml_check_payload_from_request() -> Dict[str, Any]:
    raw = merge_raw_options(_request_dict())
    return yaml_check_payload_from_options(raw)


def yaml_check_payload_from_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    raw = merge_raw_options(raw_options)
    yaml_text = _coerce_yaml_text(raw)
    input_file = str(raw.get("filename") or raw.get("input_file") or "<string input>")
    raw_issues = _run_dayaml_checker(yaml_text, input_file=input_file)

    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for issue in raw_issues:
        message = str(getattr(issue, "err_str", issue))
        line_value = getattr(issue, "line_number", 1)
        try:
            line = int(line_value)
        except Exception:
            line = 1
        file_name = str(getattr(issue, "file_name", input_file))
        experimental = bool(getattr(issue, "experimental", True))
        severity = _dayaml_issue_severity(message)
        normalized_issue = {
            "severity": severity,
            "message": message,
            "line": line,
            "filename": file_name,
            "experimental": experimental,
        }
        issues.append(normalized_issue)
        if severity == "warning":
            warnings.append(normalized_issue)
        else:
            errors.append(normalized_issue)

    return {
        "valid": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "issues": issues,
        "errors": errors,
        "warnings": warnings,
    }


def yaml_reformat_payload_from_request() -> Dict[str, Any]:
    raw = merge_raw_options(_request_dict())
    return yaml_reformat_payload_from_options(raw)


def yaml_reformat_payload_from_options(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    raw = merge_raw_options(raw_options)
    yaml_text = _coerce_yaml_text(raw)

    raw_line_length = raw.get("line_length")
    if raw_line_length in (None, ""):
        line_length = 88
    else:
        try:
            line_length = int(raw_line_length)
        except (TypeError, ValueError) as exc:
            raise DashboardAPIValidationError("line_length must be an integer.") from exc
        if line_length <= 0:
            raise DashboardAPIValidationError("line_length must be a positive integer.")

    convert_indent_4_to_2 = parse_bool(raw.get("convert_indent_4_to_2"), default=True)
    formatted_yaml, changed = _run_dayaml_reformat(
        yaml_text,
        line_length=line_length,
        convert_indent_4_to_2=convert_indent_4_to_2,
    )

    return {
        "changed": bool(changed),
        "line_length": line_length,
        "convert_indent_4_to_2": convert_indent_4_to_2,
        "formatted_yaml": str(formatted_yaml),
    }


def _prepare_pdf_upload(raw_options: Mapping[str, Any]) -> Dict[str, Any]:
    raw = merge_raw_options(raw_options)
    filename = str(raw.get("filename") or "upload.pdf")
    file_content_base64 = raw.get("file_content_base64")
    if file_content_base64 is None:
        raise DashboardAPIValidationError("file_content_base64 is required.")
    content = decode_base64_content(file_content_base64)
    _validate_upload_size(content)
    if not filename.lower().endswith(".pdf"):
        raise DashboardAPIValidationError(
            "Only PDF uploads are supported.", status_code=415
        )
    input_path = _write_temp_file(filename, content)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out_file:
        output_path = out_file.name
    if os.path.exists(output_path):
        os.remove(output_path)
    return {
        "raw": raw,
        "filename": filename,
        "input_path": input_path,
        "output_path": output_path,
    }


def _finalize_pdf_payload(
    *,
    filename: str,
    output_path: str,
    stats: Dict[str, Any],
    include_pdf_base64: bool,
    include_parse_stats: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "input_filename": filename,
        "output_filename": f"labeled_{filename}",
        "field_count": (
            stats.get("total fields")
            if isinstance(stats.get("total fields"), int)
            else None
        ),
        "fields": stats.get("fields", []),
        "fields_old": stats.get("fields_old", []),
    }
    if include_parse_stats:
        payload["parse_stats"] = stats
    if include_pdf_base64:
        with open(output_path, "rb") as handle:
            payload["pdf_base64"] = base64.b64encode(handle.read()).decode("ascii")
    return payload


def pdf_label_fields_payload_from_request() -> Dict[str, Any]:
    upload = _read_single_upload(field_name="file")
    raw = merge_raw_options(_request_dict())
    return pdf_fields_detect_payload_from_options(
        {
            "filename": upload["filename"],
            "file_content_base64": base64.b64encode(upload["content"]).decode("ascii"),
            **raw,
        }
    )


def pdf_label_fields_payload_from_options(
    raw_options: Mapping[str, Any],
) -> Dict[str, Any]:
    # Backward-compatible alias for detect+label operation.
    return pdf_fields_detect_payload_from_options(raw_options)


def pdf_fields_detect_payload_from_request() -> Dict[str, Any]:
    upload = _read_single_upload(field_name="file")
    raw = merge_raw_options(_request_dict())
    return pdf_fields_detect_payload_from_options(
        {
            "filename": upload["filename"],
            "file_content_base64": base64.b64encode(upload["content"]).decode("ascii"),
            **raw,
        }
    )


def pdf_fields_detect_payload_from_options(
    raw_options: Mapping[str, Any],
) -> Dict[str, Any]:
    from .pdf_field_labeler import (
        PDFLabelingError,
        detect_pdf_fields_and_optionally_relabel,
    )

    prepared = _prepare_pdf_upload(raw_options)
    raw = prepared["raw"]
    filename = prepared["filename"]
    input_path = prepared["input_path"]
    output_path = prepared["output_path"]

    include_pdf_base64 = parse_bool(raw.get("include_pdf_base64"), default=True)
    include_parse_stats = parse_bool(raw.get("include_parse_stats"), default=True)
    relabel_with_ai = parse_bool(raw.get("relabel_with_ai"), default=False)
    jur = str(raw.get("jur") or "MA").strip() or "MA"
    tools_token = (
        str(raw.get("tools_token")) if raw.get("tools_token") is not None else None
    )
    openai_api = (
        str(raw.get("openai_api")) if raw.get("openai_api") is not None else None
    )
    target_field_names = _load_json_field(
        raw.get("target_field_names"),
        field_name="target_field_names",
        expected_type=list,
    )
    if isinstance(target_field_names, list):
        target_field_names = [str(item) for item in target_field_names]
    try:
        stats = detect_pdf_fields_and_optionally_relabel(
            input_pdf_path=input_path,
            output_pdf_path=output_path,
            relabel_with_ai=relabel_with_ai,
            target_field_names=target_field_names,
            jur=jur,
            tools_token=tools_token,
            openai_api=openai_api,
        )
        return _finalize_pdf_payload(
            filename=filename,
            output_path=output_path,
            stats=stats,
            include_pdf_base64=include_pdf_base64,
            include_parse_stats=include_parse_stats,
        )
    except PDFLabelingError as err:
        raise DashboardAPIValidationError(str(err), status_code=400)
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)


def pdf_fields_relabel_payload_from_request() -> Dict[str, Any]:
    upload = _read_single_upload(field_name="file")
    raw = merge_raw_options(_request_dict())
    return pdf_fields_relabel_payload_from_options(
        {
            "filename": upload["filename"],
            "file_content_base64": base64.b64encode(upload["content"]).decode("ascii"),
            **raw,
        }
    )


def pdf_fields_relabel_payload_from_options(
    raw_options: Mapping[str, Any],
) -> Dict[str, Any]:
    from .pdf_field_labeler import PDFLabelingError, relabel_existing_pdf_fields

    prepared = _prepare_pdf_upload(raw_options)
    raw = prepared["raw"]
    filename = prepared["filename"]
    input_path = prepared["input_path"]
    output_path = prepared["output_path"]

    include_pdf_base64 = parse_bool(raw.get("include_pdf_base64"), default=True)
    include_parse_stats = parse_bool(raw.get("include_parse_stats"), default=True)
    relabel_with_ai = parse_bool(raw.get("relabel_with_ai"), default=False)
    jur = str(raw.get("jur") or "MA").strip() or "MA"
    tools_token = (
        str(raw.get("tools_token")) if raw.get("tools_token") is not None else None
    )
    openai_api = (
        str(raw.get("openai_api")) if raw.get("openai_api") is not None else None
    )
    target_field_names = _load_json_field(
        raw.get("target_field_names"),
        field_name="target_field_names",
        expected_type=list,
    )
    if isinstance(target_field_names, list):
        target_field_names = [str(item) for item in target_field_names]
    field_name_mapping = _load_json_field(
        raw.get("field_name_mapping"),
        field_name="field_name_mapping",
        expected_type=dict,
    )
    if isinstance(field_name_mapping, dict):
        field_name_mapping = {str(k): str(v) for k, v in field_name_mapping.items()}
    try:
        stats = relabel_existing_pdf_fields(
            input_pdf_path=input_path,
            output_pdf_path=output_path,
            field_name_mapping=field_name_mapping,
            target_field_names=target_field_names,
            relabel_with_ai=relabel_with_ai,
            jur=jur,
            tools_token=tools_token,
            openai_api=openai_api,
        )
        return _finalize_pdf_payload(
            filename=filename,
            output_path=output_path,
            stats=stats,
            include_pdf_base64=include_pdf_base64,
            include_parse_stats=include_parse_stats,
        )
    except PDFLabelingError as err:
        raise DashboardAPIValidationError(str(err), status_code=400)
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)


def build_openapi_spec() -> Dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "ALDashboard API",
            "version": "1.0.0",
            "description": "REST API for ALDashboard tools.",
        },
        "paths": {
            f"{DASHBOARD_API_BASE_PATH}/translation": {
                "post": {
                    "summary": "Generate interview translation XLSX",
                    "description": (
                        "Create translation spreadsheet(s) from an interview path. "
                        "Supports sync and async execution."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/docx/auto-label": {
                "post": {
                    "summary": "Auto-label DOCX with variable placeholders",
                    "description": (
                        "Uses ALToolbox LLM helpers. Optional `openai_api` request field "
                        "can override configured API key for this request. Supports optional "
                        "`custom_prompt`, `additional_instructions`, and `max_output_tokens`."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/docx/runs": {
                "post": {
                    "summary": "Return parsed DOCX runs with paragraph/run indexes",
                    "description": (
                        "Returns `results` as `[paragraph_index, run_index, run_text]` "
                        "using the same traversal as auto-labeling (body, tables, headers, footers)."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/docx/relabel": {
                "post": {
                    "summary": "Relabel/edit DOCX suggestions by index and rules",
                    "description": (
                        "Accepts first-pass `results` and supports `replace_labels_by_index`, "
                        "`skip_label_indexes`, explicit `add_labels`, and range-based "
                        "`add_label_rules`. Can optionally return labeled DOCX base64."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/bootstrap/compile": {
                "post": {
                    "summary": "Compile Bootstrap theme from SCSS",
                    "description": "Accepts SCSS text or uploaded SCSS file.",
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/translation/validate": {
                "post": {"summary": "Validate translation XLSX"}
            },
            f"{DASHBOARD_API_BASE_PATH}/review-screen/draft": {
                "post": {"summary": "Generate review screen draft YAML"}
            },
            f"{DASHBOARD_API_BASE_PATH}/docx/validate": {
                "post": {"summary": "Validate DOCX Jinja template"}
            },
            f"{DASHBOARD_API_BASE_PATH}/interview/lint": {
                "post": {
                    "summary": "Lint interview YAML text",
                    "description": (
                        "Run deterministic (and optional LLM) lint checks on one or more interview YAML files. "
                        "Accepts multipart uploads (files[]) and/or JSON source tokens."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/yaml/check": {
                "post": {
                    "summary": "Check and warn on docassemble YAML with DAYamlChecker",
                    "description": (
                        "Runs DAYamlChecker against `yaml_text`/`yaml_content` and returns "
                        "structured issues split into errors and warnings."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/yaml/reformat": {
                "post": {
                    "summary": "Reformat docassemble YAML with DAYamlChecker formatter",
                    "description": (
                        "Formats embedded Python code blocks in YAML (for example `code` and "
                        "`validation code`) and returns `formatted_yaml` plus `changed`."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/pdf/label-fields": {
                "post": {
                    "summary": "Detect and optionally relabel PDF fields (alias)",
                    "description": (
                        "Backward-compatible alias for /pdf/fields/detect. "
                        "Supports adding fields and optional AI/manual relabeling."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/pdf/fields/detect": {
                "post": {
                    "summary": "Detect/add PDF fields; optionally relabel",
                    "description": (
                        "Adds detected fields to uploaded PDF and optionally relabels "
                        "with AI (relabel_with_ai=true) or explicit ordered target_field_names."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/pdf/fields/relabel": {
                "post": {
                    "summary": "Relabel existing PDF fields",
                    "description": (
                        "Renames existing fields using one of: field_name_mapping, "
                        "ordered target_field_names, or relabel_with_ai=true."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/jobs/{{job_id}}": {
                "get": {"summary": "Get async job status and result"},
                "delete": {"summary": "Delete async job metadata"},
            },
            f"{DASHBOARD_API_BASE_PATH}/jobs/{{job_id}}/download": {
                "get": {
                    "summary": "Download file artifact from completed async job",
                    "description": (
                        "Streams the first base64 file artifact from job result by default. "
                        "Optional query params: `index` or `field`."
                    ),
                }
            },
            f"{DASHBOARD_API_BASE_PATH}/openapi.json": {
                "get": {"summary": "Get OpenAPI document"}
            },
            f"{DASHBOARD_API_BASE_PATH}/docs": {
                "get": {"summary": "Human-readable docs"}
            },
        },
    }


def build_docs_html() -> str:
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>ALDashboard API Docs</title>
  <style>
    body {{ font-family: \"IBM Plex Sans\", \"Segoe UI\", sans-serif; margin: 2rem auto; max-width: 900px; line-height: 1.45; padding: 0 1rem; color: #1f2937; background: linear-gradient(180deg, #f8fafc, #ffffff); }}
    code {{ background: #f1f5f9; padding: 0.1rem 0.3rem; border-radius: 4px; }}
    pre {{ background: #0f172a; color: #e2e8f0; padding: 1rem; border-radius: 8px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>ALDashboard API v1</h1>
  <p><strong>Base:</strong> <code>{DASHBOARD_API_BASE_PATH}</code></p>
  <p><strong>OpenAPI:</strong> <a href=\"{DASHBOARD_API_BASE_PATH}/openapi.json\">{DASHBOARD_API_BASE_PATH}/openapi.json</a></p>
  <h2>Auth</h2>
  <p>Uses docassemble API key authentication (<code>api_verify()</code>).</p>
  <h2>Async mode</h2>
  <p>Send <code>mode=async</code> (or <code>async=true</code>) and poll <code>GET {DASHBOARD_API_BASE_PATH}/jobs/&lt;job_id&gt;</code>.</p>
  <p>When the job is complete and includes file output, download binary content at <code>GET {DASHBOARD_API_BASE_PATH}/jobs/&lt;job_id&gt;/download</code>.</p>
  <p>Celery config: <code>celery modules: [docassemble.ALDashboard.api_dashboard_worker]</code></p>
  <h2>Endpoints</h2>
  <ul>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/translation</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/docx/auto-label</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/docx/runs</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/docx/relabel</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/bootstrap/compile</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/translation/validate</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/review-screen/draft</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/docx/validate</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/interview/lint</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/yaml/check</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/yaml/reformat</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/pdf/label-fields</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/pdf/fields/detect</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/pdf/fields/relabel</code></li>
    <li><code>GET {DASHBOARD_API_BASE_PATH}/jobs/&lt;job_id&gt;/download</code></li>
  </ul>
  <h2>Notes</h2>
  <ul>
    <li><code>/docx/auto-label</code> uses <code>docassemble.ALToolbox.llms</code> for key/config lookup.</li>
    <li><code>/docx/runs</code> returns parsed run coordinates as <code>[paragraph_index, run_index, run_text]</code>.</li>
    <li>You can pass optional <code>openai_api</code>, <code>openai_base_url</code>, and <code>openai_model</code> to <code>/docx/auto-label</code>.</li>
    <li>You can customize labeling behavior with <code>custom_prompt</code>, <code>additional_instructions</code>, and optional <code>max_output_tokens</code>.</li>
    <li><code>/docx/relabel</code> can replace or skip labels by index and add labels via explicit updates or paragraph-range rules.</li>
    <li><code>/yaml/check</code> runs DAYamlChecker and classifies returned issues into <code>errors</code> and <code>warnings</code>.</li>
    <li><code>/yaml/reformat</code> uses DAYamlChecker's formatter and returns the updated YAML as <code>formatted_yaml</code>.</li>
    <li><code>/jobs/&lt;job_id&gt;/download</code> streams file outputs from async job results. Use <code>?index=1</code> or <code>?field=...</code> when multiple file artifacts exist.</li>
    <li>Most endpoints accept <code>mode=async</code> and can be polled via <code>/jobs/&lt;job_id&gt;</code>.</li>
    <li><code>/bootstrap/compile</code> requires <code>node</code>/<code>npm</code> on PATH and outbound HTTPS; first run may be slower while dependencies install.</li>
    <li><code>/pdf/label-fields</code> is a backward-compatible alias for <code>/pdf/fields/detect</code>.</li>
    <li><code>/pdf/fields/detect</code> supports <code>relabel_with_ai</code> and ordered <code>target_field_names</code>.</li>
    <li><code>/pdf/fields/relabel</code> supports <code>field_name_mapping</code>, ordered <code>target_field_names</code>, or <code>relabel_with_ai</code>.</li>
  </ul>
  <h2>DOCX Modes</h2>
  <ul>
    <li><code>/docx/runs</code>: inspection mode (returns run coordinates).</li>
    <li><code>/docx/auto-label</code>: draft generation mode (returns initial <code>results</code> labels).</li>
    <li><code>/docx/relabel</code>: edit/apply mode (change, delete, add labels; optionally build output DOCX).</li>
    <li><code>/jobs/&lt;job_id&gt;/download</code>: async file download mode (streams final DOCX/PDF/XLSX artifacts).</li>
  </ul>
  <h2>End-to-End Workflow</h2>
  <p><strong>Step 1: Upload DOCX and generate draft labels (async):</strong></p>
  <pre><code>curl -X POST "https://YOURSERVER{DASHBOARD_API_BASE_PATH}/docx/auto-label" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -F "mode=async" \\
  -F "file=@/path/to/input.docx" \\
  -F "openai_base_url=https://YOURRESOURCE.openai.azure.com/openai/v1/" \\
  -F "openai_api=YOUR_AZURE_OPENAI_KEY" \\
  -F "openai_model=gpt-5-mini"</code></pre>
  <p><strong>Step 2: Poll job and read <code>data.results</code>:</strong></p>
  <pre><code>curl -H "X-API-Key: YOUR_API_KEY" \\
  "https://YOURSERVER{DASHBOARD_API_BASE_PATH}/jobs/JOB_ID"</code></pre>
  <p><strong>Step 3: Manual edit pass (change one, delete one, add one), request final DOCX (async):</strong></p>
  <pre><code>curl -X POST "https://YOURSERVER{DASHBOARD_API_BASE_PATH}/docx/relabel" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -d '{{
    "mode": "async",
    "filename": "input.docx",
    "file_content_base64": "BASE64_DOCX_HERE",
    "results": [[1,0,"{{{{ letter_date }}}}",0],[2,0,"{{{{ old_name }}}}",0],[3,0,"{{{{ keep_me }}}}",0]],
    "replace_labels_by_index": {{"0":"{{{{ edited_letter_date }}}}"}},
    "skip_label_indexes": [1],
    "add_labels": [[0,0,"{{{{ added_new_label }}}}",0]],
    "include_labeled_docx_base64": true
  }}'</code></pre>
  <p><strong>Step 4: Poll relabel job and download final edited DOCX:</strong></p>
  <pre><code>curl -H "X-API-Key: YOUR_API_KEY" \\
  "https://YOURSERVER{DASHBOARD_API_BASE_PATH}/jobs/JOB_ID"

curl -L -o final_labeled.docx \\
  -H "X-API-Key: YOUR_API_KEY" \\
  "https://YOURSERVER{DASHBOARD_API_BASE_PATH}/jobs/JOB_ID/download"</code></pre>
  <p>Default behavior: <code>/docx/auto-label</code> and <code>/docx/relabel</code> return a <code>results</code> array. To produce a downloadable DOCX from relabel, include DOCX content and set <code>include_labeled_docx_base64=true</code>.</p>
</body>
</html>
"""
