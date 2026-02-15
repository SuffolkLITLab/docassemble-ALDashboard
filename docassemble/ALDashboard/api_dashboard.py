# pre-load

import base64
import binascii
import json
import time
import uuid
import copy
from typing import Any, Dict, Optional
from urllib.parse import quote

from flask import Response, jsonify, request
from flask_cors import cross_origin

from docassemble.base.config import daconfig, in_celery
from docassemble.base.util import log
from docassemble.webapp.app_object import app, csrf
from docassemble.webapp.server import api_verify, jsonify_with_status, r, save_user_dict
from docassemble.webapp import worker as da_worker
from docassemble.webapp.cron import get_cron_user
from docassemble.webapp.worker_common import workerapp
from docassemble.base.parse import get_initial_dict

from .api_dashboard_utils import (
    DASHBOARD_API_BASE_PATH,
    DashboardAPIValidationError,
    _validate_upload_size,
    autolabel_payload_from_request,
    bootstrap_payload_from_request,
    docx_runs_payload_from_request,
    build_docs_html,
    build_openapi_spec,
    coerce_async_flag,
    merge_raw_options,
    pdf_fields_detect_payload_from_request,
    pdf_fields_relabel_payload_from_request,
    pdf_label_fields_payload_from_request,
    relabel_payload_from_request,
    review_screen_payload_from_request,
    translation_payload_from_request,
    validate_docx_payload_from_request,
    validate_translation_payload_from_request,
)

__all__ = []

JOB_KEY_PREFIX = "da:aldashboard:job:"
JOB_KEY_EXPIRE_SECONDS = 24 * 60 * 60
ASYNC_CELERY_MODULE = "docassemble.ALDashboard.api_dashboard_worker"

if not in_celery:
    from .api_dashboard_worker import (
        dashboard_autolabel_task,
        dashboard_bootstrap_task,
        dashboard_docx_runs_task,
        dashboard_pdf_fields_detect_task,
        dashboard_pdf_fields_relabel_task,
        dashboard_pdf_label_fields_task,
        dashboard_relabel_task,
        dashboard_review_screen_task,
        dashboard_validate_docx_task,
        dashboard_validate_translation_task,
    )


def _async_is_configured() -> bool:
    celery_modules = daconfig.get("celery modules", []) or []
    return ASYNC_CELERY_MODULE in celery_modules


def _job_key(job_id: str) -> str:
    return JOB_KEY_PREFIX + job_id


def _store_job_mapping(
    job_id: str, task_id: str, extra: Optional[Dict[str, Any]] = None
) -> None:
    payload = {"id": task_id, "created_at": time.time()}
    if extra:
        payload.update(extra)
    pipe = r.pipeline()
    pipe.set(_job_key(job_id), json.dumps(payload))
    pipe.expire(_job_key(job_id), JOB_KEY_EXPIRE_SECONDS)
    pipe.execute()


def _fetch_job_mapping(job_id: str) -> Optional[Dict[str, Any]]:
    raw = r.get(_job_key(job_id))
    if raw is None:
        return None
    try:
        return json.loads(raw.decode())
    except Exception:
        return None


def _auth_fail(request_id: str):
    return jsonify_with_status(
        {
            "success": False,
            "request_id": request_id,
            "error": {"type": "auth_error", "message": "Access denied."},
        },
        403,
    )


def _request_payload_without_files() -> Dict[str, Any]:
    post_data = request.get_json(silent=True)
    if isinstance(post_data, dict):
        return post_data
    return dict(request.form)


def _extract_payload_for_async(base_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = dict(base_payload)
    if request.files:
        files_payload = []
        for _, upload in request.files.items(multi=True):
            content = upload.read()
            _validate_upload_size(content)
            try:
                upload.stream.seek(0)
            except Exception:
                pass
            files_payload.append(
                {
                    "filename": upload.filename or "upload",
                    "file_content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        if len(files_payload) == 1:
            payload.update(files_payload[0])
        if files_payload:
            payload["files"] = files_payload
    return payload


def _run_endpoint(sync_func, async_task):
    request_id = str(uuid.uuid4())
    if not api_verify():
        return _auth_fail(request_id)

    try:
        merged_options = merge_raw_options(_request_payload_without_files())
        use_async = coerce_async_flag(merged_options)
        if use_async:
            if not _async_is_configured():
                return jsonify_with_status(
                    {
                        "success": False,
                        "request_id": request_id,
                        "error": {
                            "type": "async_not_configured",
                            "message": (
                                "Async mode is not configured. Add "
                                f"{ASYNC_CELERY_MODULE!r} to the docassemble "
                                "'celery modules' configuration list."
                            ),
                        },
                    },
                    503,
                )
            task_payload = _extract_payload_for_async(merged_options)
            task = async_task.delay(payload=task_payload)
            job_id = str(uuid.uuid4())
            _store_job_mapping(job_id, task.id)
            return jsonify_with_status(
                {
                    "success": True,
                    "api_version": "v1",
                    "request_id": request_id,
                    "status": "queued",
                    "job_id": job_id,
                    "job_url": f"{DASHBOARD_API_BASE_PATH}/jobs/{job_id}",
                },
                202,
            )

        data = sync_func()
        return jsonify(
            {
                "success": True,
                "api_version": "v1",
                "request_id": request_id,
                "status": "succeeded",
                "data": data,
            }
        )
    except DashboardAPIValidationError as exc:
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "validation_error", "message": exc.message},
            },
            exc.status_code,
        )
    except Exception as exc:
        log(f"ALDashboard API error: {exc!r}")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {
                    "type": "server_error",
                    "message": "ALDashboard API action failed.",
                },
            },
            500,
        )


def _build_cron_user_info() -> Dict[str, Any]:
    cron_user = get_cron_user()
    return {
        "is_anonymous": False,
        "is_authenticated": True,
        "email": cron_user.email,
        "theid": cron_user.id,
        "the_user_id": cron_user.id,
        "roles": [role.name for role in cron_user.roles],
        "firstname": cron_user.first_name,
        "lastname": cron_user.last_name,
        "nickname": cron_user.nickname,
        "country": cron_user.country,
        "subdivisionfirst": cron_user.subdivisionfirst,
        "subdivisionsecond": cron_user.subdivisionsecond,
        "subdivisionthird": cron_user.subdivisionthird,
        "organization": cron_user.organization,
        "location": None,
        "session_uid": "api",
        "device_id": "api",
    }


def _queue_translation_background(payload: Dict[str, Any]) -> Any:
    yaml_filename = (
        "docassemble.ALDashboard:data/questions/api_translation_background.yml"
    )
    user_info = _build_cron_user_info()
    session_code = f"api-translation-{uuid.uuid4()}"
    seed_user_dict: Dict[str, Any] = copy.deepcopy(get_initial_dict())
    save_user_dict(
        session_code,
        seed_user_dict,
        yaml_filename,
        secret=None,
        encrypt=False,
        manual_user_id=user_info["theid"],
        steps=1,
    )
    action = {"action": "api_translation_background", "arguments": payload}
    return da_worker.background_action.delay(
        yaml_filename, user_info, session_code, None, None, None, action
    )


def _normalize_result_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_result_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_result_for_json(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_result_for_json(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        inner_value = getattr(value, "value")
        if inner_value is not None:
            return _normalize_result_for_json(inner_value)
        if hasattr(value, "__dict__"):
            return _normalize_result_for_json(vars(value))
        return None
    if hasattr(value, "__dict__"):
        return _normalize_result_for_json(vars(value))
    return str(value)


def _guess_download_metadata(field_name: str) -> Dict[str, str]:
    lowered = field_name.lower()
    if "docx" in lowered:
        return {
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "ext": ".docx",
            "fallback_name": "output.docx",
        }
    if "pdf" in lowered:
        return {"mime": "application/pdf", "ext": ".pdf", "fallback_name": "output.pdf"}
    if "xlsx" in lowered:
        return {
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ext": ".xlsx",
            "fallback_name": "output.xlsx",
        }
    if "css" in lowered:
        return {"mime": "text/css", "ext": ".css", "fallback_name": "output.css"}
    return {
        "mime": "application/octet-stream",
        "ext": ".bin",
        "fallback_name": "output.bin",
    }


def _collect_base64_artifacts(
    value: Any, *, path: str = "", parent: Optional[Dict[str, Any]] = None
) -> list:
    artifacts = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else key
            if key.endswith("_base64") and isinstance(item, str):
                meta = _guess_download_metadata(key)
                suggested_name = value.get("filename") or value.get(
                    f"{key[:-7]}_filename"
                )
                if not isinstance(suggested_name, str) or not suggested_name.strip():
                    suggested_name = meta["fallback_name"]
                if "." not in suggested_name:
                    suggested_name = suggested_name + meta["ext"]
                artifacts.append(
                    {
                        "path": child_path,
                        "base64": item,
                        "filename": suggested_name,
                        "mime": meta["mime"],
                    }
                )
            artifacts.extend(_collect_base64_artifacts(item, path=child_path, parent=value))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            artifacts.extend(_collect_base64_artifacts(item, path=child_path, parent=parent))
    return artifacts


@app.route(f"{DASHBOARD_API_BASE_PATH}/translation", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_translation():
    request_id = str(uuid.uuid4())
    if not api_verify():
        return _auth_fail(request_id)

    try:
        merged_options = merge_raw_options(_request_payload_without_files())
        use_async = coerce_async_flag(merged_options)
        if use_async:
            if not _async_is_configured():
                return jsonify_with_status(
                    {
                        "success": False,
                        "request_id": request_id,
                        "error": {
                            "type": "async_not_configured",
                            "message": (
                                "Async mode is not configured. Add "
                                f"{ASYNC_CELERY_MODULE!r} to the docassemble "
                                "'celery modules' configuration list."
                            ),
                        },
                    },
                    503,
                )
            try:
                task = _queue_translation_background(
                    _extract_payload_for_async(merged_options)
                )
            except Exception as exc:
                raise DashboardAPIValidationError(
                    f"Failed to queue translation background task: {exc}",
                    status_code=500,
                )
            job_id = str(uuid.uuid4())
            _store_job_mapping(
                job_id, task.id, extra={"kind": "translation_background"}
            )
            return jsonify_with_status(
                {
                    "success": True,
                    "api_version": "v1",
                    "request_id": request_id,
                    "status": "queued",
                    "job_id": job_id,
                    "job_url": f"{DASHBOARD_API_BASE_PATH}/jobs/{job_id}",
                },
                202,
            )
        data = translation_payload_from_request()
        return jsonify(
            {
                "success": True,
                "api_version": "v1",
                "request_id": request_id,
                "status": "succeeded",
                "data": data,
            }
        )
    except DashboardAPIValidationError as exc:
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "validation_error", "message": exc.message},
            },
            exc.status_code,
        )
    except Exception as exc:
        log(f"ALDashboard API error: {exc!r}")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {
                    "type": "server_error",
                    "message": "ALDashboard API action failed.",
                },
            },
            500,
        )


@app.route(f"{DASHBOARD_API_BASE_PATH}/docx/auto-label", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_docx_auto_label():
    return _run_endpoint(autolabel_payload_from_request, dashboard_autolabel_task)


@app.route(f"{DASHBOARD_API_BASE_PATH}/docx/runs", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_docx_runs():
    return _run_endpoint(docx_runs_payload_from_request, dashboard_docx_runs_task)


@app.route(f"{DASHBOARD_API_BASE_PATH}/docx/relabel", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_docx_relabel():
    return _run_endpoint(relabel_payload_from_request, dashboard_relabel_task)


@app.route(f"{DASHBOARD_API_BASE_PATH}/bootstrap/compile", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_bootstrap_compile():
    return _run_endpoint(bootstrap_payload_from_request, dashboard_bootstrap_task)


@app.route(f"{DASHBOARD_API_BASE_PATH}/translation/validate", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_translation_validate():
    return _run_endpoint(
        validate_translation_payload_from_request,
        dashboard_validate_translation_task,
    )


@app.route(f"{DASHBOARD_API_BASE_PATH}/review-screen/draft", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_review_screen_draft():
    return _run_endpoint(
        review_screen_payload_from_request, dashboard_review_screen_task
    )


@app.route(f"{DASHBOARD_API_BASE_PATH}/docx/validate", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_docx_validate():
    return _run_endpoint(
        validate_docx_payload_from_request, dashboard_validate_docx_task
    )


@app.route(f"{DASHBOARD_API_BASE_PATH}/pdf/label-fields", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_pdf_label_fields():
    return _run_endpoint(
        pdf_label_fields_payload_from_request,
        dashboard_pdf_label_fields_task,
    )


@app.route(f"{DASHBOARD_API_BASE_PATH}/pdf/fields/detect", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_pdf_fields_detect():
    return _run_endpoint(
        pdf_fields_detect_payload_from_request,
        dashboard_pdf_fields_detect_task,
    )


@app.route(f"{DASHBOARD_API_BASE_PATH}/pdf/fields/relabel", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def dashboard_pdf_fields_relabel():
    return _run_endpoint(
        pdf_fields_relabel_payload_from_request,
        dashboard_pdf_fields_relabel_task,
    )


@app.route(f"{DASHBOARD_API_BASE_PATH}/jobs/<job_id>", methods=["GET", "DELETE"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "DELETE", "HEAD"], automatic_options=True)
def dashboard_job(job_id: str):
    request_id = str(uuid.uuid4())
    if not api_verify():
        return _auth_fail(request_id)

    if request.method == "DELETE":
        task_info = _fetch_job_mapping(job_id)
        if not task_info:
            return jsonify_with_status(
                {
                    "success": False,
                    "request_id": request_id,
                    "error": {"type": "not_found", "message": "Job not found."},
                },
                404,
            )
        try:
            workerapp.AsyncResult(id=task_info["id"]).forget()
        except Exception:
            pass
        r.delete(_job_key(job_id))
        return jsonify(
            {
                "success": True,
                "api_version": "v1",
                "request_id": request_id,
                "job_id": job_id,
                "deleted": True,
            }
        )

    task_info = _fetch_job_mapping(job_id)
    if not task_info:
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "not_found", "message": "Job not found."},
            },
            404,
        )

    result = workerapp.AsyncResult(id=task_info["id"])
    state = (result.state or "").upper()
    if state == "SUCCESS":
        status = "succeeded"
    elif state in {"RECEIVED", "STARTED", "RETRY"}:
        status = "running"
    elif state == "FAILURE":
        status = "failed"
    else:
        status = "queued"

    response_body: Dict[str, Any] = {
        "success": True,
        "api_version": "v1",
        "request_id": request_id,
        "job_id": job_id,
        "task_id": task_info.get("id"),
        "status": status,
        "celery_state": state,
        "created_at": task_info.get("created_at"),
    }
    if state == "SUCCESS":
        normalized_data = _normalize_result_for_json(result.get())
        response_body["data"] = normalized_data
        artifacts = _collect_base64_artifacts(normalized_data)
        if artifacts:
            response_body["download_url"] = (
                f"{DASHBOARD_API_BASE_PATH}/jobs/{job_id}/download"
            )
    elif state == "FAILURE":
        error_obj = result.result
        response_body["error"] = {
            "type": getattr(error_obj, "__class__", type(error_obj)).__name__,
            "message": str(error_obj),
        }
    return jsonify(response_body)


@app.route(f"{DASHBOARD_API_BASE_PATH}/jobs/<job_id>/download", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def dashboard_job_download(job_id: str):
    request_id = str(uuid.uuid4())
    if not api_verify():
        return _auth_fail(request_id)

    task_info = _fetch_job_mapping(job_id)
    if not task_info:
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "not_found", "message": "Job not found."},
            },
            404,
        )

    result = workerapp.AsyncResult(id=task_info["id"])
    state = (result.state or "").upper()
    if state != "SUCCESS":
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {
                    "type": "job_not_ready",
                    "message": "Job is not completed yet.",
                },
            },
            409,
        )

    normalized_data = _normalize_result_for_json(result.get())
    artifacts = _collect_base64_artifacts(normalized_data)
    if not artifacts:
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {
                    "type": "not_found",
                    "message": "No downloadable file was found in the job result.",
                },
            },
            404,
        )

    requested_field = request.args.get("field")
    selected = None
    if requested_field:
        selected = next((item for item in artifacts if item["path"] == requested_field), None)
        if selected is None:
            return jsonify_with_status(
                {
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "type": "not_found",
                        "message": f"Requested field {requested_field!r} was not found.",
                    },
                },
                404,
            )
    else:
        index_raw = request.args.get("index")
        if index_raw is None:
            index = 0
        else:
            try:
                index = int(index_raw)
            except ValueError:
                return jsonify_with_status(
                    {
                        "success": False,
                        "request_id": request_id,
                        "error": {
                            "type": "validation_error",
                            "message": "index must be an integer.",
                        },
                    },
                    400,
                )
        if index < 0 or index >= len(artifacts):
            return jsonify_with_status(
                {
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "type": "not_found",
                        "message": f"No downloadable artifact at index {index}.",
                    },
                },
                404,
            )
        selected = artifacts[index]

    try:
        file_bytes = base64.b64decode(selected["base64"], validate=True)
    except (binascii.Error, ValueError):
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {
                    "type": "server_error",
                    "message": "Stored file content is not valid base64.",
                },
            },
            500,
        )

    filename = selected["filename"]
    content_disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        file_bytes,
        mimetype=selected["mime"],
        headers={
            "Content-Disposition": content_disposition,
            "X-ALDashboard-Artifact-Field": selected["path"],
        },
    )


@app.route(f"{DASHBOARD_API_BASE_PATH}/openapi.json", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def dashboard_openapi():
    return jsonify(build_openapi_spec())


@app.route(f"{DASHBOARD_API_BASE_PATH}/docs", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def dashboard_docs():
    return Response(build_docs_html(), mimetype="text/html")
