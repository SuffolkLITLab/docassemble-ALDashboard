import base64
import binascii
import importlib.resources
import json
import os
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
                        "can override configured API key for this request."
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
  <p>Celery config: <code>celery modules: [docassemble.ALDashboard.api_dashboard_worker]</code></p>
  <h2>Endpoints</h2>
  <ul>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/translation</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/docx/auto-label</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/bootstrap/compile</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/translation/validate</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/review-screen/draft</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/docx/validate</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/pdf/label-fields</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/pdf/fields/detect</code></li>
    <li><code>POST {DASHBOARD_API_BASE_PATH}/pdf/fields/relabel</code></li>
  </ul>
  <h2>Notes</h2>
  <ul>
    <li><code>/docx/auto-label</code> uses <code>docassemble.ALToolbox.llms</code> for key/config lookup.</li>
    <li>You can pass optional <code>openai_api</code> to <code>/docx/auto-label</code> to override key per request.</li>
    <li>Most endpoints accept <code>mode=async</code> and can be polled via <code>/jobs/&lt;job_id&gt;</code>.</li>
    <li><code>/bootstrap/compile</code> requires <code>node</code>/<code>npm</code> on PATH and outbound HTTPS; first run may be slower while dependencies install.</li>
    <li><code>/pdf/label-fields</code> is a backward-compatible alias for <code>/pdf/fields/detect</code>.</li>
    <li><code>/pdf/fields/detect</code> supports <code>relabel_with_ai</code> and ordered <code>target_field_names</code>.</li>
    <li><code>/pdf/fields/relabel</code> supports <code>field_name_mapping</code>, ordered <code>target_field_names</code>, or <code>relabel_with_ai</code>.</li>
  </ul>
</body>
</html>
"""
