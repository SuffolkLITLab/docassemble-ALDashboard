"""
Flask endpoints for the DOCX and PDF labeler tools.

These provide interactive browser-based interfaces for:
- al/docx-labeler: Add Jinja2 labels to DOCX templates
- al/pdf-labeler: Add/edit PDF form fields

Both tools use AI to suggest labels and follow AssemblyLine conventions.
"""

import base64
import copy
import inspect
import io
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from urllib.parse import quote, urlsplit
from typing import Any, Dict, List, Optional

from flask import Response, jsonify, request, send_file
from flask_cors import cross_origin
from flask_login import current_user

from docassemble.base.config import daconfig
import docassemble.base.functions
from docassemble.base.util import get_config, log
from docassemble.webapp.app_object import app, csrf
from docassemble.webapp.server import api_verify, jsonify_with_status, r
from docassemble.webapp.worker_common import workerapp

from .api_dashboard_utils import (
    DashboardAPIValidationError,
    _validate_upload_size,
    coerce_async_flag,
    decode_base64_content,
    merge_raw_options,
    parse_bool,
)

__all__ = []

LABELER_BASE_PATH = "/al"

OPENAI_LABELER_MODELS = [
    "gpt-5-mini",
    "gpt-5",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-5-nano",
]
GEMINI_LABELER_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5",
    "gemini-3-pro",
]
CLAUDE_LABELER_MODELS = [
    "claude-4.5-sonnet",
    "claude-4.6-sonnet",
    "claude-4.6-opus",
]
LABELER_PROVIDER_MODEL_SETS = {
    "openai": OPENAI_LABELER_MODELS,
    "gemini": GEMINI_LABELER_MODELS,
    "claude": CLAUDE_LABELER_MODELS,
}
LABELER_MODEL_SET_PRIORITY = [
    OPENAI_LABELER_MODELS,
    GEMINI_LABELER_MODELS,
    CLAUDE_LABELER_MODELS,
]
LABELER_DEFAULT_MODEL = "gpt-5-mini"
LABELER_JOB_KEY_PREFIX = "da:aldashboard:labeler-job:"
LABELER_JOB_EXPIRE_SECONDS = 24 * 60 * 60
ASYNC_CELERY_MODULE = "docassemble.ALDashboard.api_dashboard_worker"


def _normalize_provider_family(family_name: Optional[str]) -> str:
    family = str(family_name or "").strip().lower()
    if family in {"google", "gemini"}:
        return "gemini"
    if family in {"anthropic", "claude"}:
        return "claude"
    return "openai"


def _build_labeler_model_catalog() -> Dict[str, Any]:
    """Build model metadata for labeler UIs using ALToolbox llms helpers."""
    default_model = LABELER_DEFAULT_MODEL
    provider_family = "openai"
    recommended_models = list(OPENAI_LABELER_MODELS)
    available_models: List[str] = []

    try:
        from docassemble.ALToolbox.llms import (  # type: ignore[import-untyped]
            detect_model_family,
            get_default_model,
            get_first_available_model_set,
            list_available_models,
        )

        available_models = list_available_models()
        selected_set = get_first_available_model_set(
            LABELER_MODEL_SET_PRIORITY,
            require_full_set=False,
            return_partial_if_needed=True,
            fallback_to_first_small_model=True,
        )
        if selected_set:
            recommended_models = selected_set
            provider_family = _normalize_provider_family(
                detect_model_family(selected_set[0])
            )
        else:
            medium_default = get_default_model("medium")
            provider_family = _normalize_provider_family(
                detect_model_family(medium_default)
            )
            recommended_models = list(
                LABELER_PROVIDER_MODEL_SETS.get(provider_family, OPENAI_LABELER_MODELS)
            )

        if provider_family == "openai":
            default_model = "gpt-5-mini"
        elif recommended_models:
            default_model = recommended_models[0]
        else:
            default_model = get_default_model("medium")

        if default_model not in recommended_models and default_model:
            recommended_models = [default_model] + recommended_models
    except Exception as exc:
        log(
            f"ALDashboard: failed to build model catalog from ALToolbox.llms; using fallback list ({exc!r})",
            "warning",
        )

    return {
        "default_model": default_model or LABELER_DEFAULT_MODEL,
        "recommended_models": recommended_models,
        "available_models": available_models,
        "provider_family": provider_family,
    }


def _labeler_session_identity() -> Dict[str, Any]:
    """Return session identity details for browser users."""
    try:
        if current_user.is_authenticated:
            email_value = getattr(current_user, "email", None)
            if email_value is not None:
                email_value = str(email_value)
            return {
                "is_authenticated": True,
                "email": email_value,
                "user_id": getattr(current_user, "id", None),
            }
    except Exception:
        pass
    return {"is_authenticated": False, "email": None, "user_id": None}


def _labeler_ai_auth_check() -> bool:
    """AI features require API key auth or a logged-in browser user."""
    if api_verify():
        return True
    identity = _labeler_session_identity()
    return bool(identity.get("is_authenticated"))


def _safe_labeler_return_target(raw_target: Optional[str]) -> Optional[str]:
    """Allow only same-origin or relative return targets for auth redirects."""
    target = str(raw_target or "").strip()
    if not target:
        return None

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        if parsed.netloc != request.host:
            return None
        path = parsed.path or "/"
    else:
        path = parsed.path or target

    if not path.startswith("/") or path.startswith("//"):
        return None

    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def _labeler_auth_return_target() -> str:
    """Resolve the page the labeler should return to after auth."""
    explicit_target = _safe_labeler_return_target(request.args.get("next"))
    if explicit_target:
        return explicit_target

    referer_target = _safe_labeler_return_target(request.headers.get("Referer"))
    if referer_target:
        return referer_target

    return LABELER_BASE_PATH


def _labeler_playground_auth_check() -> bool:
    try:
        return bool(current_user.is_authenticated)
    except Exception:
        return False


def _playground_auth_fail(request_id: str):
    return jsonify_with_status(
        {
            "success": False,
            "request_id": request_id,
            "error": {
                "type": "auth_error",
                "message": "Login required for Playground interview access.",
            },
        },
        401,
    )


def _normalize_playground_project(project: Optional[str]) -> str:
    value = str(project or "default").strip() or "default"
    if "/" in value or "\\" in value or value.startswith("."):
        raise DashboardAPIValidationError("Invalid Playground project.", status_code=400)
    return value


def _normalize_playground_filename(filename: Optional[str]) -> str:
    value = os.path.basename(str(filename or "").strip())
    if not value or value in {".", ".."}:
        raise DashboardAPIValidationError(
            "Playground YAML filename is required.", status_code=400
        )
    if not value.lower().endswith((".yml", ".yaml")):
        raise DashboardAPIValidationError(
            "Playground file must be a YAML interview.", status_code=400
        )
    return value


@contextmanager
def _playground_user_context(user_id: int):
    original_info = copy.deepcopy(
        getattr(docassemble.base.functions.this_thread, "current_info", {}) or {}
    )
    current_info = copy.deepcopy(original_info)
    current_info.setdefault("user", {})
    current_info["user"].update({"is_anonymous": False, "theid": user_id})
    docassemble.base.functions.this_thread.current_info = current_info
    try:
        yield
    finally:
        docassemble.base.functions.this_thread.current_info = original_info


def _list_playground_projects() -> List[str]:
    from docassemble.webapp.files import SavedFile

    uid = getattr(current_user, "id", None)
    if uid is None:
        return ["default"]
    playground = SavedFile(uid, fix=False, section="playground")
    projects = playground.list_of_dirs() or []
    projects = [proj for proj in projects if isinstance(proj, str) and proj]
    if "default" not in projects:
        projects.append("default")
    return sorted(set(projects))


def _list_playground_yaml_files(project: str) -> List[Dict[str, str]]:
    from docassemble.webapp.playground import Playground

    uid = getattr(current_user, "id", None)
    if uid is None:
        return []
    with _playground_user_context(uid):
        playground = Playground(project=project)
        return [
            {"filename": filename, "label": filename}
            for filename in playground.file_list
            if isinstance(filename, str) and filename.lower().endswith((".yml", ".yaml"))
        ]


def _get_playground_variable_info(project: str, filename: str) -> Dict[str, Any]:
    from docassemble.webapp.playground import Playground

    uid = getattr(current_user, "id", None)
    if uid is None:
        raise DashboardAPIValidationError(
            "Login required for Playground interview access.", status_code=401
        )
    with _playground_user_context(uid):
        pg = Playground(project=project)
        if filename not in pg.file_list:
            raise DashboardAPIValidationError(
                "Selected Playground YAML file was not found.", status_code=404
            )
        variable_info = pg.variables_from_file(filename)
    if not isinstance(variable_info, dict):
        variable_info = {}
    all_names = sorted(
        str(name).strip()
        for name in (variable_info.get("all_names_reduced") or [])
        if str(name).strip()
    )
    top_level_names = sorted(
        {name.split(".", 1)[0].split("[", 1)[0] for name in all_names if name}
    )
    return {
        "project": project,
        "filename": filename,
        "all_names": all_names,
        "top_level_names": top_level_names,
    }


def _normalize_installed_package_name(package_name: Optional[str]) -> str:
    value = str(package_name or "").strip()
    if not value or "/" in value or "\\" in value or ":" in value:
        raise DashboardAPIValidationError(
            "Installed interview package name is required.", status_code=400
        )
    if not value.startswith("docassemble."):
        raise DashboardAPIValidationError(
            "Installed interview package must start with docassemble.",
            status_code=400,
        )
    return value


def _normalize_installed_interview_path(interview_path: Optional[str]) -> str:
    value = str(interview_path or "").strip()
    if ":" not in value:
        raise DashboardAPIValidationError(
            "Installed interview path must be in package:file format.",
            status_code=400,
        )
    package_name, filename = value.split(":", 1)
    package_name = _normalize_installed_package_name(package_name)
    filename = _normalize_playground_filename(filename)
    return f"{package_name}:{filename}"


def _extract_variable_names_from_var_json(variable_json: Any) -> Dict[str, List[str]]:
    if not isinstance(variable_json, dict):
        variable_json = {}

    names: List[str] = []
    seen = set()

    def add_name(entry: Any) -> None:
        candidate = None
        if isinstance(entry, str):
            candidate = entry
        elif isinstance(entry, dict):
            for key in ("name", "variable", "var_name", "varName"):
                if isinstance(entry.get(key), str):
                    candidate = entry.get(key)
                    break
            if candidate is None and isinstance(entry.get(0), str):
                candidate = entry.get(0)
        elif isinstance(entry, (list, tuple)) and entry:
            if isinstance(entry[0], str):
                candidate = entry[0]
        if candidate is None:
            return
        candidate = str(candidate).strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        names.append(candidate)

    for key in ("var_list", "undefined_names"):
        values = variable_json.get(key)
        if isinstance(values, list):
            for entry in values:
                add_name(entry)

    names.sort()
    top_level_names = sorted(
        {name.split(".", 1)[0].split("[", 1)[0] for name in names if name}
    )
    return {"all_names": names, "top_level_names": top_level_names}


def _list_installed_interview_packages() -> List[str]:
    from .aldashboard import list_question_files_in_docassemble_packages

    package_map = list_question_files_in_docassemble_packages()
    return sorted(
        package_name
        for package_name, filenames in package_map.items()
        if isinstance(package_name, str) and filenames
    )


def _list_installed_interview_files(package_name: str) -> List[Dict[str, str]]:
    from .aldashboard import list_question_files_in_docassemble_packages

    package_map = list_question_files_in_docassemble_packages()
    filenames = package_map.get(package_name, [])
    return [
        {
            "filename": filename,
            "label": filename,
            "interview_path": f"{package_name}:{filename}",
        }
        for filename in sorted(filenames)
        if isinstance(filename, str) and filename.lower().endswith((".yml", ".yaml"))
    ]


def _get_installed_interview_variable_info(interview_path: str) -> Dict[str, Any]:
    from docassemble.base.parse import InterviewStatus, interview_source_from_string
    from docassemble.webapp.server import current_info, get_vars_in_use

    normalized_path = _normalize_installed_interview_path(interview_path)
    try:
        interview_source = interview_source_from_string(normalized_path, testing=True)
        interview = interview_source.get_interview()
    except Exception as exc:
        raise DashboardAPIValidationError(
            f"Unable to load installed interview: {exc}", status_code=400
        ) from exc

    device_id = request.cookies.get("ds", None)
    interview_status = InterviewStatus(
        current_info=current_info(
            yaml=normalized_path, req=request, action=None, device_id=device_id
        )
    )
    try:
        variable_json, vocab_list, vocab_dict, ac_list = get_vars_in_use(  # pylint: disable=unused-variable
            interview,
            interview_status,
            debug_mode=False,
            return_json=True,
            use_playground=False,
            current_project="default",
        )
    except Exception as exc:
        raise DashboardAPIValidationError(
            f"Unable to analyze installed interview variables: {exc}",
            status_code=400,
        ) from exc

    parsed_names = _extract_variable_names_from_var_json(variable_json)
    return {
        "interview_path": normalized_path,
        "all_names": parsed_names["all_names"],
        "top_level_names": parsed_names["top_level_names"],
    }


def _get_static_content(filename: str) -> str:
    """Read a static file from the data/static directory."""
    import importlib.resources

    try:
        ref = importlib.resources.files("docassemble.ALDashboard") / "data" / "static" / filename
        with importlib.resources.as_file(ref) as path:
            if path.exists():
                return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _get_template_content(filename: str) -> str:
    """Read a template file from the data/templates directory."""
    import importlib.resources

    try:
        ref = importlib.resources.files("docassemble.ALDashboard") / "data" / "templates" / filename
        with importlib.resources.as_file(ref) as path:
            if path.exists():
                return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _render_template_content(
    filename: str, *, bootstrap_data: Optional[Dict[str, Any]] = None
) -> str:
    """Read a template and inject bootstrap JSON when requested."""
    html_content = _get_template_content(filename)
    if not html_content:
        return ""
    if bootstrap_data is None:
        return html_content
    return html_content.replace(
        "__LABELER_BOOTSTRAP_JSON__",
        json.dumps(bootstrap_data, sort_keys=True),
    )


def _build_pdf_labeler_bootstrap() -> Dict[str, Any]:
    """Build bootstrap data for the PDF labeler page."""
    from .labeler_config import get_pdf_labeler_ui_config

    pdf_ui_config = get_pdf_labeler_ui_config()
    return {
        "apiBasePath": LABELER_BASE_PATH,
        "branding": pdf_ui_config.get("branding", {}),
        "pdf": {
            "fieldNameLibrary": pdf_ui_config.get("field_name_library", {}),
        },
    }


def _auth_fail(request_id: str):
    return jsonify_with_status(
        {
            "success": False,
            "request_id": request_id,
            "error": {"type": "auth_error", "message": "Access denied."},
        },
        403,
    )


def _ai_auth_fail(request_id: str):
    return jsonify_with_status(
        {
            "success": False,
            "request_id": request_id,
            "error": {
                "type": "auth_error",
                "message": "Login required for AI features in the labeler.",
            },
        },
        401,
    )


def _labeler_async_is_configured() -> bool:
    celery_modules = daconfig.get("celery modules", []) or []
    return ASYNC_CELERY_MODULE in celery_modules


def _labeler_job_key(job_id: str) -> str:
    return LABELER_JOB_KEY_PREFIX + job_id


def _store_labeler_job_mapping(
    job_id: str, task_id: str, extra: Optional[Dict[str, Any]] = None
) -> None:
    payload = {"id": task_id, "created_at": time.time()}
    if extra:
        payload.update(extra)
    pipe = r.pipeline()
    pipe.set(_labeler_job_key(job_id), json.dumps(payload))
    pipe.expire(_labeler_job_key(job_id), LABELER_JOB_EXPIRE_SECONDS)
    pipe.execute()


def _fetch_labeler_job_mapping(job_id: str) -> Optional[Dict[str, Any]]:
    raw = r.get(_labeler_job_key(job_id))
    if raw is None:
        return None
    try:
        return json.loads(raw.decode())
    except Exception:
        return None


def _normalize_labeler_result_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_labeler_result_for_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_labeler_result_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_labeler_result_for_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        inner_value = getattr(value, "value")
        if inner_value is not None:
            return _normalize_labeler_result_for_json(inner_value)
    if hasattr(value, "__dict__"):
        return _normalize_labeler_result_for_json(vars(value))
    return str(value)


def _queue_labeler_async_job(task: Any, *, kind: str, request_id: str):
    job_id = str(uuid.uuid4())
    _store_labeler_job_mapping(job_id, task.id, extra={"kind": kind})
    return jsonify_with_status(
        {
            "success": True,
            "request_id": request_id,
            "status": "queued",
            "job_id": job_id,
            "job_url": f"{LABELER_BASE_PATH}/pdf-labeler/api/jobs/{job_id}",
        },
        202,
    )


# =============================================================================
# DOCX Labeler Routes
# =============================================================================


@app.route(f"{LABELER_BASE_PATH}/docx-labeler", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_page():
    """Serve the DOCX labeler interactive UI."""
    log("ALDashboard: Serving DOCX labeler page", "info")
    html_content = _get_template_content("docx_labeler.html")
    if not html_content:
        log("ALDashboard: DOCX labeler template not found", "error")
        return Response("DOCX labeler template not found.", status=500, mimetype="text/plain")
    return Response(html_content, mimetype="text/html")


@app.route(f"{LABELER_BASE_PATH}/labeler/api/models", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def labeler_models():
    """Return AI model metadata for DOCX/PDF labeler settings UIs."""
    request_id = str(uuid.uuid4())
    return jsonify(
        {
            "success": True,
            "request_id": request_id,
            "data": _build_labeler_model_catalog(),
        }
    )


@app.route(f"{LABELER_BASE_PATH}/labeler/api/auth-status", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def labeler_auth_status():
    """Return browser-session auth status for labeler UI controls."""
    request_id = str(uuid.uuid4())
    identity = _labeler_session_identity()
    next_target = _labeler_auth_return_target()
    login_url = f"/user/sign-in?next={quote(next_target, safe='')}"
    logout_url = f"/user/sign-out?next={quote(next_target, safe='')}"
    return jsonify(
        {
            "success": True,
            "request_id": request_id,
            "data": {
                "is_authenticated": bool(identity.get("is_authenticated")),
                "email": identity.get("email"),
                "user_id": identity.get("user_id"),
                "login_url": login_url,
                "logout_url": logout_url,
                "ai_enabled": _labeler_ai_auth_check(),
            },
        }
    )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/playground-projects", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_playground_projects():
    request_id = str(uuid.uuid4())
    if not _labeler_playground_auth_check():
        return _playground_auth_fail(request_id)
    try:
        return jsonify(
            {
                "success": True,
                "request_id": request_id,
                "data": {"projects": _list_playground_projects()},
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
        log(f"ALDashboard: playground-projects {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "server_error", "message": str(exc)},
            },
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/playground-files", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_playground_files():
    request_id = str(uuid.uuid4())
    if not _labeler_playground_auth_check():
        return _playground_auth_fail(request_id)
    try:
        project = _normalize_playground_project(request.args.get("project"))
        return jsonify(
            {
                "success": True,
                "request_id": request_id,
                "data": {"project": project, "files": _list_playground_yaml_files(project)},
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
        log(f"ALDashboard: playground-files {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "server_error", "message": str(exc)},
            },
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/playground-variables", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_playground_variables():
    request_id = str(uuid.uuid4())
    if not _labeler_playground_auth_check():
        return _playground_auth_fail(request_id)
    try:
        project = _normalize_playground_project(request.args.get("project"))
        filename = _normalize_playground_filename(request.args.get("filename"))
        return jsonify(
            {
                "success": True,
                "request_id": request_id,
                "data": _get_playground_variable_info(project, filename),
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
        log(f"ALDashboard: playground-variables {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "server_error", "message": str(exc)},
            },
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/installed-packages", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_installed_packages():
    request_id = str(uuid.uuid4())
    if not _labeler_playground_auth_check():
        return _playground_auth_fail(request_id)
    try:
        return jsonify(
            {
                "success": True,
                "request_id": request_id,
                "data": {"packages": _list_installed_interview_packages()},
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
        log(f"ALDashboard: installed-packages {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "server_error", "message": str(exc)},
            },
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/installed-files", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_installed_files():
    request_id = str(uuid.uuid4())
    if not _labeler_playground_auth_check():
        return _playground_auth_fail(request_id)
    try:
        package_name = _normalize_installed_package_name(request.args.get("package"))
        return jsonify(
            {
                "success": True,
                "request_id": request_id,
                "data": {
                    "package": package_name,
                    "files": _list_installed_interview_files(package_name),
                },
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
        log(f"ALDashboard: installed-files {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "server_error", "message": str(exc)},
            },
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/installed-variables", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_installed_variables():
    request_id = str(uuid.uuid4())
    if not _labeler_playground_auth_check():
        return _playground_auth_fail(request_id)
    try:
        interview_path = _normalize_installed_interview_path(
            request.args.get("interview_path")
        )
        return jsonify(
            {
                "success": True,
                "request_id": request_id,
                "data": _get_installed_interview_variable_info(interview_path),
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
        log(f"ALDashboard: installed-variables {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {
                "success": False,
                "request_id": request_id,
                "error": {"type": "server_error", "message": str(exc)},
            },
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/extract-runs", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def docx_labeler_extract_runs():
    """Extract paragraph runs from a DOCX file for labeling."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: extract-runs request {request_id}", "info")
    try:
        import docx

        from .docx_wrangling import defragment_docx_runs, get_docx_run_items

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.docx"
            content = upload.read()
            post_data = dict(request.form)
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.docx")
            content = decode_base64_content(post_data.get("file_content_base64"))

        _validate_upload_size(content)

        if not filename.lower().endswith(".docx"):
            raise DashboardAPIValidationError(
                "Only DOCX files are supported.", status_code=415
            )
        defragment_runs = parse_bool(post_data.get("defragment_runs"), default=True)

        # Write to temp file for processing
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        try:
            doc = docx.Document(temp_path)
            defragmentation = {"paragraphs_defragmented": 0, "runs_removed": 0}
            if defragment_runs:
                doc, defragmentation = defragment_docx_runs(doc)
            runs = get_docx_run_items(doc)
            paragraph_count = 0
            if runs:
                paragraph_count = max(int(item[0]) for item in runs) + 1

            log(f"ALDashboard: extract-runs {request_id} extracted {len(runs)} runs from {paragraph_count} paragraphs in '{filename}'", "info")
            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": filename,
                    "paragraph_count": paragraph_count,
                    "run_count": len(runs),
                    "runs": runs,
                    "defragment_runs": defragment_runs,
                    "defragmentation": defragmentation,
                }
            })
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except DashboardAPIValidationError as exc:
        log(f"ALDashboard: extract-runs {request_id} validation error: {exc.message}", "warning")
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        log(f"ALDashboard: extract-runs {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/suggest-labels", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def docx_labeler_suggest_labels():
    """Use AI to suggest Jinja2 labels for a DOCX file."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: suggest-labels request {request_id}", "info")
    if not _labeler_ai_auth_check():
        log(f"ALDashboard: suggest-labels auth failed for request {request_id}", "warning")
        return _ai_auth_fail(request_id)

    try:
        import docx

        from .docx_wrangling import (
            defragment_docx_runs,
            get_voted_docx_label_suggestions,
        )
        from .validate_docx import detect_docx_automation_features

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.docx"
            content = upload.read()
            post_data = dict(request.form)
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.docx")
            content = decode_base64_content(post_data.get("file_content_base64"))

        _validate_upload_size(content)

        if not filename.lower().endswith(".docx"):
            raise DashboardAPIValidationError(
                "Only DOCX files are supported.", status_code=415
            )

        # Extract options
        prompt_profile = str(
            post_data.get("prompt_profile") or "standard"
        ).strip() or "standard"
        optional_context = post_data.get("context_text")
        custom_prompt = post_data.get("custom_prompt")
        additional_instructions = post_data.get("additional_instructions")
        defragment_runs = parse_bool(post_data.get("defragment_runs"), default=True)
        model = post_data.get("model")
        if model is None or str(model).strip() == "":
            model = _build_labeler_model_catalog()["default_model"]
        model = str(model)
        judge_model = post_data.get("judge_model")
        if judge_model is not None:
            judge_model = str(judge_model).strip() or None
        openai_api = post_data.get("openai_api")
        openai_base_url = post_data.get("openai_base_url")
        generator_models = None
        generator_models_raw = post_data.get("generator_models")
        if generator_models_raw:
            if isinstance(generator_models_raw, str):
                try:
                    parsed_models = json.loads(generator_models_raw)
                except json.JSONDecodeError:
                    parsed_models = [
                        item.strip()
                        for item in generator_models_raw.split(",")
                        if item.strip()
                    ]
            elif isinstance(generator_models_raw, list):
                parsed_models = generator_models_raw
            else:
                parsed_models = []
            generator_models = [
                str(item).strip() for item in parsed_models if str(item).strip()
            ] or None

        # Parse custom people names if provided
        custom_people_names = None
        custom_people_raw = post_data.get("custom_people_names")
        if custom_people_raw:
            if isinstance(custom_people_raw, str):
                try:
                    custom_people_names = json.loads(custom_people_raw)
                except json.JSONDecodeError:
                    pass
            elif isinstance(custom_people_raw, list):
                custom_people_names = custom_people_raw

        preferred_variable_names = None
        interview_source_mode = (
            str(post_data.get("interview_source_mode") or "playground").strip().lower()
            or "playground"
        )
        selected_playground_project = None
        selected_playground_filename = None
        selected_installed_interview_path = None
        preferred_variable_names_raw = post_data.get("preferred_variable_names")
        if preferred_variable_names_raw:
            if isinstance(preferred_variable_names_raw, str):
                try:
                    parsed_preferred_names = json.loads(preferred_variable_names_raw)
                except json.JSONDecodeError:
                    parsed_preferred_names = [
                        item.strip()
                        for item in preferred_variable_names_raw.split(",")
                        if item.strip()
                    ]
            elif isinstance(preferred_variable_names_raw, list):
                parsed_preferred_names = preferred_variable_names_raw
            else:
                parsed_preferred_names = []
            preferred_variable_names = [
                str(item).strip()
                for item in parsed_preferred_names
                if str(item).strip()
            ] or None
        use_playground_variables = parse_bool(
            post_data.get("use_playground_variables"), default=False
        )
        if use_playground_variables:
            if interview_source_mode == "installed":
                selected_installed_interview_path = _normalize_installed_interview_path(
                    post_data.get("installed_interview_path")
                    or (
                        (
                            str(post_data.get("installed_package") or "").strip()
                            + ":"
                            + str(post_data.get("installed_yaml_file") or "").strip()
                        )
                        if (
                            str(post_data.get("installed_package") or "").strip()
                            and str(post_data.get("installed_yaml_file") or "").strip()
                        )
                        else None
                    )
                )
                if preferred_variable_names is None:
                    preferred_variable_names = _get_installed_interview_variable_info(
                        selected_installed_interview_path
                    )["all_names"]
            else:
                selected_playground_project = _normalize_playground_project(
                    post_data.get("playground_project")
                )
                selected_playground_filename = _normalize_playground_filename(
                    post_data.get("playground_yaml_file")
                )
                if preferred_variable_names is None:
                    preferred_variable_names = _get_playground_variable_info(
                        selected_playground_project, selected_playground_filename
                    )["all_names"]

        # Write to temp file for processing
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        try:
            review_document = docx.Document(temp_path)
            if defragment_runs:
                review_document, _ = defragment_docx_runs(review_document)
            automation_findings = detect_docx_automation_features(temp_path)
            document_warnings = [
                finding
                for finding in automation_findings.get("findings", [])
                if finding.get("code")
                in {
                    "track_changes",
                    "structured_document_tags",
                    "sdt_specialized_controls",
                    "sdt_plain_text_control",
                    "sdt_group_control",
                    "sdt_docpart_non_page_numbers",
                    "sdt_metadata",
                    "sdt_bound_or_locked",
                    "data_binding",
                    "custom_xml_parts",
                    "custom_xml_relationships",
                }
            ]

            aggregated = get_voted_docx_label_suggestions(
                docx_path=temp_path,
                custom_people_names=custom_people_names,
                preferred_variable_names=preferred_variable_names,
                openai_api=openai_api,
                openai_base_url=openai_base_url,
                model=model,
                generator_models=generator_models,
                judge_model=judge_model,
                prompt_profile=prompt_profile,
                optional_context=optional_context,
                custom_prompt=custom_prompt,
                additional_instructions=additional_instructions,
                defragment_runs=defragment_runs,
                judge_max_output_tokens=2000,
            )
            suggestions = aggregated.get("suggestions", [])
            aggregation_summary = aggregated.get("aggregation", {})
            judge_review = aggregated.get("judge_review", {})
            generation_runs = aggregated.get("generation_runs", [])
            flagged_selected_count = sum(
                1 for suggestion in suggestions if suggestion.get("validation_flags")
            )

            # Convert to a more friendly format for the UI
            formatted_suggestions = []
            for suggestion in suggestions:
                alternates = []
                for alternate in suggestion.get("alternates", []):
                    alternates.append(
                        {
                            "text": alternate.get("text", ""),
                            "paragraph": alternate.get("paragraph"),
                            "run": alternate.get("run"),
                            "new_paragraph": alternate.get("new_paragraph", 0),
                            "validation_flags": alternate.get("validation_flags", []),
                            "confidence": alternate.get("confidence", "low"),
                            "vote_count": alternate.get("vote_count", 0),
                            "clean_vote_count": alternate.get("clean_vote_count", 0),
                            "vote_total": suggestion.get(
                                "vote_total", len(generation_runs)
                            ),
                            "sources": alternate.get("sources", []),
                        }
                    )
                formatted_suggestions.append({
                    "paragraph": suggestion.get("paragraph"),
                    "run": suggestion.get("run"),
                    "text": suggestion.get("text", ""),
                    "new_paragraph": suggestion.get("new_paragraph", 0),
                    "id": str(uuid.uuid4()),
                    "validation_flags": suggestion.get("validation_flags", []),
                    "judge_review": suggestion.get("judge_review"),
                    "confidence": suggestion.get("confidence", "low"),
                    "vote_count": suggestion.get("vote_count", 0),
                    "clean_vote_count": suggestion.get("clean_vote_count", 0),
                    "vote_total": suggestion.get("vote_total", len(generation_runs)),
                    "sources": suggestion.get("sources", []),
                    "alternates": alternates,
                })

            log(f"ALDashboard: suggest-labels {request_id} generated {len(formatted_suggestions)} suggestions for '{filename}'", "info")
            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": filename,
                    "suggestions": formatted_suggestions,
                    "defragment_runs": defragment_runs,
                    "interview_source_mode": interview_source_mode,
                    "playground_project": selected_playground_project,
                    "playground_yaml_file": selected_playground_filename,
                    "installed_interview_path": selected_installed_interview_path,
                    "playground_variable_count": len(preferred_variable_names or []),
                    "validation": {
                        "deterministic": {
                            "flagged_count": flagged_selected_count,
                            "ai_review_recommended": bool(
                                aggregation_summary.get("ambiguous_group_count")
                            ),
                        },
                        "ai_review": judge_review,
                        "document_warnings": document_warnings,
                        "aggregation": aggregation_summary,
                    },
                }
            })
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except DashboardAPIValidationError as exc:
        log(f"ALDashboard: suggest-labels {request_id} validation error: {exc.message}", "warning")
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        log(f"ALDashboard: suggest-labels {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/apply-labels", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def docx_labeler_apply_labels():
    """Apply accepted labels and/or renames to a DOCX file and return the modified file.
    
    Supports two types of modifications:
    - labels: New AI-suggested labels to insert (paragraph, run, text, new_paragraph)
    - renames: Find/replace operations on existing labels (original, replacement)
    """
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: apply-labels request {request_id}", "info")
    try:
        from .docx_wrangling import defragment_docx_runs, update_docx
        import docx

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.docx"
            content = upload.read()
            post_data = dict(request.form)
            labels_raw = post_data.get("labels")
            renames_raw = post_data.get("renames")
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.docx")
            content = decode_base64_content(post_data.get("file_content_base64"))
            labels_raw = post_data.get("labels")
            renames_raw = post_data.get("renames")

        _validate_upload_size(content)

        if not filename.lower().endswith(".docx"):
            raise DashboardAPIValidationError(
                "Only DOCX files are supported.", status_code=415
            )
        defragment_runs = parse_bool(post_data.get("defragment_runs"), default=True)

        # Parse labels (new insertions)
        labels = []
        if labels_raw:
            if isinstance(labels_raw, str):
                labels = json.loads(labels_raw)
            elif isinstance(labels_raw, list):
                labels = labels_raw

        # Parse renames (find/replace on existing)
        renames = []
        if renames_raw:
            if isinstance(renames_raw, str):
                renames = json.loads(renames_raw)
            elif isinstance(renames_raw, list):
                renames = renames_raw

        if not labels and not renames:
            raise DashboardAPIValidationError("Either labels or renames must be provided.")

        log(f"ALDashboard: apply-labels {request_id} processing {len(labels)} label insertions and {len(renames)} renames for '{filename}'", "info")

        # Convert labels to the format expected by update_docx
        modified_runs = []
        for label in labels:
            para = int(label.get("paragraph", 0))
            run = int(label.get("run", 0))
            text = str(label.get("text", ""))
            new_para = int(label.get("new_paragraph", 0))
            modified_runs.append((para, run, text, new_para))

        # Write to temp file for processing
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        try:
            doc = docx.Document(temp_path)
            defragmentation = {"paragraphs_defragmented": 0, "runs_removed": 0}

            if defragment_runs and modified_runs:
                target_paragraph_numbers = sorted(
                    {
                        para
                        for para, _run, _text, new_para in modified_runs
                        if new_para == 0 and para >= 0
                    }
                )
                if target_paragraph_numbers:
                    doc, defragmentation = defragment_docx_runs(
                        doc, paragraph_numbers=target_paragraph_numbers
                    )

            # Apply renames first (find/replace existing Jinja2 labels)
            if renames:
                rename_count = 0
                for rename in renames:
                    original = str(rename.get("original", ""))
                    replacement = str(rename.get("replacement", ""))
                    if original and replacement and original != replacement:
                        # Search through all paragraphs and runs
                        for para in doc.paragraphs:
                            for run in para.runs:
                                if original in run.text:
                                    run.text = run.text.replace(original, replacement)
                                    rename_count += 1
                        # Also search tables
                        for table in doc.tables:
                            for row in table.rows:
                                for cell in row.cells:
                                    for para in cell.paragraphs:
                                        for run in para.runs:
                                            if original in run.text:
                                                run.text = run.text.replace(original, replacement)
                                                rename_count += 1

            # Apply new label insertions
            if modified_runs:
                doc = update_docx(doc, modified_runs)

            # Save to bytes
            output_buffer = io.BytesIO()
            doc.save(output_buffer)
            output_buffer.seek(0)
            output_bytes = output_buffer.read()

            output_filename = filename.replace(".docx", "-labeled.docx")

            log(f"ALDashboard: apply-labels {request_id} successfully produced '{output_filename}'", "info")
            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": output_filename,
                    "docx_base64": base64.b64encode(output_bytes).decode("ascii"),
                    "defragment_runs": defragment_runs,
                    "defragmented_before_apply": bool(
                        defragmentation["paragraphs_defragmented"]
                        or defragmentation["runs_removed"]
                    ),
                    "defragmentation": defragmentation,
                }
            })
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except DashboardAPIValidationError as exc:
        log(f"ALDashboard: apply-labels {request_id} validation error: {exc.message}", "warning")
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        log(f"ALDashboard: apply-labels {request_id} server error: {exc!r}", "error")
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )


# =============================================================================
# =============================================================================
# PDF Labeler Routes
# =============================================================================


@app.route("/pdf-labeler", methods=["GET"])
@app.route(f"{LABELER_BASE_PATH}/pdf-labeler", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def pdf_labeler_page():
    """Serve the PDF labeler interactive UI."""
    log("ALDashboard: Serving PDF labeler page", "info")
    html_content = _render_template_content(
        "pdf_labeler.html", bootstrap_data=_build_pdf_labeler_bootstrap()
    )
    if not html_content:
        log("ALDashboard: PDF labeler template not found", "error")
        return Response("PDF labeler template not found.", status=500, mimetype="text/plain")
    return Response(html_content, mimetype="text/html")


@app.route("/pdf-labeler/api/jobs/<job_id>", methods=["GET"])
@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/jobs/<job_id>", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def pdf_labeler_job(job_id: str):
    request_id = str(uuid.uuid4())
    if not _labeler_ai_auth_check():
        return _ai_auth_fail(request_id)

    task_info = _fetch_labeler_job_mapping(job_id)
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

    body: Dict[str, Any] = {
        "success": True,
        "request_id": request_id,
        "job_id": job_id,
        "task_id": task_info.get("id"),
        "status": status,
        "celery_state": state,
        "created_at": task_info.get("created_at"),
    }
    if state == "SUCCESS":
        body["data"] = _normalize_labeler_result_for_json(result.get())
    elif state == "FAILURE":
        error_obj = result.result
        body["error"] = {
            "type": getattr(error_obj, "__class__", type(error_obj)).__name__,
            "message": str(error_obj),
        }
    return jsonify(body)


@app.route("/pdf-labeler/api/detect-fields", methods=["POST"])
@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/detect-fields", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def pdf_labeler_detect_fields():
    """Detect existing form fields in a PDF."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: detect-fields request {request_id}", "info")

    try:
        import formfyxer  # type: ignore[import-not-found]

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.pdf"
            content = upload.read()
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.pdf")
            content = decode_base64_content(post_data.get("file_content_base64"))

        _validate_upload_size(content)

        if not filename.lower().endswith(".pdf"):
            raise DashboardAPIValidationError(
                "Only PDF files are supported.", status_code=415
            )

        # Write to temp file for processing
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        try:
            # Get existing fields with positions
            fields_per_page = formfyxer.get_existing_pdf_fields(temp_path)

            # Format for the UI
            formatted_fields = []
            for page_idx, page_fields in enumerate(fields_per_page):
                for field in page_fields:
                    field_data = {
                        "id": str(uuid.uuid4()),
                        "name": field.name,
                        "type": str(field.type).lower().replace("fieldtype.", ""),
                        "pageIndex": page_idx,
                        "x": field.x,
                        "y": field.y,
                        "width": field.configs.get("width", 100),
                        "height": field.configs.get("height", 20),
                        "fontSize": field.font_size or 12,
                    }
                    formatted_fields.append(field_data)

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": filename,
                    "page_count": len(fields_per_page),
                    "fields": formatted_fields,
                }
            })
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except DashboardAPIValidationError as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )


@app.route("/pdf-labeler/api/auto-detect", methods=["POST"])
@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/auto-detect", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def pdf_labeler_auto_detect():
    """Use AI to automatically detect and add fields to a PDF."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: auto-detect request {request_id}", "info")
    if not _labeler_ai_auth_check():
        log(f"ALDashboard: auto-detect auth failed for request {request_id}", "warning")
        return _ai_auth_fail(request_id)

    try:
        import formfyxer  # type: ignore[import-not-found]

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.pdf"
            content = upload.read()
            post_data = dict(request.form)
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.pdf")
            content = decode_base64_content(post_data.get("file_content_base64"))

        post_data = merge_raw_options(post_data)
        _validate_upload_size(content)

        if not filename.lower().endswith(".pdf"):
            raise DashboardAPIValidationError(
                "Only PDF files are supported.", status_code=415
            )

        if coerce_async_flag(post_data):
            if not _labeler_async_is_configured():
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
            from .api_dashboard_worker import dashboard_pdf_fields_detect_task

            task = dashboard_pdf_fields_detect_task.delay(
                payload={
                    "filename": filename,
                    "file_content_base64": base64.b64encode(content).decode("ascii"),
                    "relabel_with_ai": True,
                    "include_pdf_base64": True,
                    "include_parse_stats": True,
                    **post_data,
                }
            )
            return _queue_labeler_async_job(
                task, kind="pdf_auto_detect", request_id=request_id
            )

        # Options
        normalize_fields = parse_bool(post_data.get("normalize_fields"), default=True)
        jur = str(post_data.get("jur", "MA"))
        model = str(post_data.get("model") or "").strip() or None
        tools_token = (
            str(post_data.get("tools_token")).strip()
            if post_data.get("tools_token") is not None
            else str(
                get_config("assembly line", {}).get(
                    "tools.suffolklitlab.org api key", ""
                )
            ).strip()
            or None
        )
        openai_api = (
            str(post_data.get("openai_api")).strip()
            if post_data.get("openai_api") is not None
            else str(
                get_config("open ai", {}).get("key")
                or get_config("openai api key")
                or ""
            ).strip()
            or None
        )
        openai_base_url = (
            str(post_data.get("openai_base_url")).strip()
            if post_data.get("openai_base_url") is not None
            else str(get_config("openai base url") or "").strip() or None
        )

        # Write to temp files for processing
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
            tmp_in.write(content)
            input_path = tmp_in.name

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
            output_path = tmp_out.name

        try:
            # Auto-add fields using FormFyxer
            formfyxer.auto_add_fields(input_path, output_path)

            # Optionally normalize with AI
            stats = {}
            if normalize_fields:
                parse_kwargs: Dict[str, Any] = {
                    "title": os.path.splitext(filename)[0],
                    "jur": jur,
                    "normalize": True,
                    "rewrite": True,
                }
                if tools_token:
                    parse_kwargs["tools_token"] = tools_token
                if openai_api:
                    parse_kwargs["openai_api_key"] = openai_api
                if openai_base_url:
                    parse_kwargs["openai_base_url"] = openai_base_url
                if model:
                    parse_kwargs["model"] = model
                try:
                    stats = formfyxer.parse_form(output_path, **parse_kwargs)
                except TypeError:
                    parse_kwargs.pop("model", None)
                    parse_kwargs.pop("openai_base_url", None)
                    stats = formfyxer.parse_form(output_path, **parse_kwargs)

            # Get the resulting fields
            fields_per_page = formfyxer.get_existing_pdf_fields(output_path)

            # Read the output file
            with open(output_path, "rb") as f:
                output_bytes = f.read()

            # Format fields for the UI
            formatted_fields = []
            for page_idx, page_fields in enumerate(fields_per_page):
                for field in page_fields:
                    field_data = {
                        "id": str(uuid.uuid4()),
                        "name": field.name,
                        "type": str(field.type).lower().replace("fieldtype.", ""),
                        "pageIndex": page_idx,
                        "x": field.x,
                        "y": field.y,
                        "width": field.configs.get("width", 100),
                        "height": field.configs.get("height", 20),
                        "fontSize": field.font_size or 12,
                    }
                    formatted_fields.append(field_data)

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": filename,
                    "page_count": len(fields_per_page),
                    "fields": formatted_fields,
                    "stats": stats if isinstance(stats, dict) else {},
                    "pdf_base64": base64.b64encode(output_bytes).decode("ascii"),
                }
            })
        finally:
            if os.path.exists(input_path):
                os.remove(input_path)
            if os.path.exists(output_path):
                os.remove(output_path)

    except DashboardAPIValidationError as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )


@app.route("/pdf-labeler/api/relabel", methods=["POST"])
@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/relabel", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def pdf_labeler_relabel():
    """Relabel PDF fields using AI suggestions."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: pdf-relabel request {request_id}", "info")
    if not _labeler_ai_auth_check():
        log(f"ALDashboard: pdf-relabel auth failed for request {request_id}", "warning")
        return _ai_auth_fail(request_id)

    try:
        from .pdf_field_labeler import relabel_existing_pdf_fields

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.pdf"
            content = upload.read()
            post_data = dict(request.form)
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.pdf")
            content = decode_base64_content(post_data.get("file_content_base64"))

        post_data = merge_raw_options(post_data)
        _validate_upload_size(content)

        if not filename.lower().endswith(".pdf"):
            raise DashboardAPIValidationError(
                "Only PDF files are supported.", status_code=415
            )

        if coerce_async_flag(post_data):
            if not _labeler_async_is_configured():
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
            from .api_dashboard_worker import dashboard_pdf_fields_relabel_task

            task = dashboard_pdf_fields_relabel_task.delay(
                payload={
                    "filename": filename,
                    "file_content_base64": base64.b64encode(content).decode("ascii"),
                    "relabel_with_ai": True,
                    "include_pdf_base64": True,
                    "include_parse_stats": True,
                    **post_data,
                }
            )
            return _queue_labeler_async_job(
                task, kind="pdf_relabel", request_id=request_id
            )

        jur = str(post_data.get("jur", "MA"))
        model = str(post_data.get("model") or "").strip() or None

        # Write to temp files for processing
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
            tmp_in.write(content)
            input_path = tmp_in.name

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
            output_path = tmp_out.name

        try:
            stats = relabel_existing_pdf_fields(
                input_pdf_path=input_path,
                output_pdf_path=output_path,
                relabel_with_ai=True,
                jur=jur,
                model=model,
            )

            # Read the output file
            with open(output_path, "rb") as f:
                output_bytes = f.read()

            output_filename = filename.replace(".pdf", "-labeled.pdf")

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": output_filename,
                    "stats": stats,
                    "pdf_base64": base64.b64encode(output_bytes).decode("ascii"),
                }
            })
        finally:
            if os.path.exists(input_path):
                os.remove(input_path)
            if os.path.exists(output_path):
                os.remove(output_path)

    except DashboardAPIValidationError as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )


@app.route("/pdf-labeler/api/apply-fields", methods=["POST"])
@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/apply-fields", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def pdf_labeler_apply_fields():
    """Apply field definitions to a PDF and return the modified file."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: apply-fields request {request_id}", "info")

    try:
        import formfyxer  # type: ignore[import-not-found]
        from formfyxer.pdf_wrangling import FormField, FieldType, set_fields
        from reportlab.lib.colors import HexColor

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.pdf"
            content = upload.read()
            post_data = dict(request.form)
            fields_raw = post_data.get("fields")
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.pdf")
            content = decode_base64_content(post_data.get("file_content_base64"))
            fields_raw = post_data.get("fields")

        _validate_upload_size(content)

        if not filename.lower().endswith(".pdf"):
            raise DashboardAPIValidationError(
                "Only PDF files are supported.", status_code=415
            )

        # Parse fields
        if isinstance(fields_raw, str):
            fields_data = json.loads(fields_raw)
        elif isinstance(fields_raw, list):
            fields_data = fields_raw
        else:
            raise DashboardAPIValidationError("fields is required and must be a list.")

        # Get page count from original PDF
        import pikepdf
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
            tmp_in.write(content)
            input_path = tmp_in.name

        try:
            with pikepdf.open(input_path) as pdf:
                page_count = len(pdf.pages)

            # Organize fields by page
            fields_per_page: List[List[FormField]] = [[] for _ in range(page_count)]

            for field_data in fields_data:
                page_idx = int(field_data.get("pageIndex", 0))
                if page_idx < 0 or page_idx >= page_count:
                    continue

                field_type_str = str(field_data.get("type", "text")).lower()
                if field_type_str in ("text", "multiline"):
                    field_type = FieldType.TEXT if field_type_str == "text" else FieldType.AREA
                elif field_type_str == "checkbox":
                    field_type = FieldType.CHECK_BOX
                elif field_type_str == "signature":
                    field_type = FieldType.SIGNATURE
                elif field_type_str == "radio":
                    field_type = FieldType.RADIO
                elif field_type_str in ("dropdown", "choice"):
                    field_type = FieldType.CHOICE
                elif field_type_str == "listbox":
                    field_type = FieldType.LIST_BOX
                else:
                    field_type = FieldType.TEXT

                width = float(field_data.get("width", 100))
                height = float(field_data.get("height", 20))
                font_name = str(field_data.get("font") or "Helvetica").strip() or "Helvetica"
                auto_size = parse_bool(field_data.get("autoSize"), default=False)
                allow_scroll = parse_bool(field_data.get("allowScroll"), default=True)
                font_size_raw = field_data.get("fontSize")
                font_size = None if auto_size else int(font_size_raw or 12)
                field_configs: Dict[str, Any] = {
                    "width": width,
                    "height": height,
                    "fontName": font_name,
                }
                field_flag_parts: List[str] = []
                if field_type == FieldType.AREA:
                    field_flag_parts.append("multiline")
                if not allow_scroll:
                    field_flag_parts.append("doNotScroll")
                field_configs["fieldFlags"] = " ".join(field_flag_parts)
                checkbox_style = str(field_data.get("checkboxStyle") or "").strip()
                if checkbox_style:
                    field_configs["buttonStyle"] = checkbox_style
                background_color = field_data.get("backgroundColor")
                if isinstance(background_color, str) and background_color.strip():
                    try:
                        field_configs["fillColor"] = HexColor(background_color.strip())
                    except Exception:
                        pass
                if field_type in (FieldType.CHOICE, FieldType.LIST_BOX, FieldType.RADIO):
                    raw_options = field_data.get("options")
                    if isinstance(raw_options, list):
                        options = [str(option) for option in raw_options if str(option).strip()]
                    else:
                        options = []
                    if options:
                        field_configs["options"] = options
                        if field_type == FieldType.RADIO:
                            field_configs["value"] = options[0]

                form_field = FormField(
                    field_name=str(field_data.get("name", "field")),
                    type_name=field_type,
                    x=int(field_data.get("x", 0)),
                    y=int(field_data.get("y", 0)),
                    font_size=font_size,
                    configs=field_configs,
                )
                fields_per_page[page_idx].append(form_field)

            # Apply fields using FormFyxer
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
                output_path = tmp_out.name

            set_fields(input_path, output_path, fields_per_page, overwrite=True)

            # Read the output file
            with open(output_path, "rb") as f:
                output_bytes = f.read()

            output_filename = filename.replace(".pdf", "-with-fields.pdf")

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": output_filename,
                    "pdf_base64": base64.b64encode(output_bytes).decode("ascii"),
                }
            })
        finally:
            if os.path.exists(input_path):
                os.remove(input_path)
            if os.path.exists(output_path):
                os.remove(output_path)

    except DashboardAPIValidationError as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )


@app.route("/pdf-labeler/api/rename-fields", methods=["POST"])
@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/rename-fields", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def pdf_labeler_rename_fields():
    """Rename fields in an existing PDF."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: rename-fields request {request_id}", "info")

    try:
        import formfyxer  # type: ignore[import-not-found]

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.pdf"
            content = upload.read()
            post_data = dict(request.form)
            mapping_raw = post_data.get("mapping")
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.pdf")
            content = decode_base64_content(post_data.get("file_content_base64"))
            mapping_raw = post_data.get("mapping")

        _validate_upload_size(content)

        if not filename.lower().endswith(".pdf"):
            raise DashboardAPIValidationError(
                "Only PDF files are supported.", status_code=415
            )

        # Parse mapping
        if isinstance(mapping_raw, str):
            mapping = json.loads(mapping_raw)
        elif isinstance(mapping_raw, dict):
            mapping = mapping_raw
        else:
            raise DashboardAPIValidationError("mapping is required and must be an object.")

        # Write to temp files for processing
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
            tmp_in.write(content)
            input_path = tmp_in.name

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
            output_path = tmp_out.name

        try:
            formfyxer.rename_pdf_fields(input_path, output_path, mapping)

            # Read the output file
            with open(output_path, "rb") as f:
                output_bytes = f.read()

            output_filename = filename.replace(".pdf", "-renamed.pdf")

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": output_filename,
                    "pdf_base64": base64.b64encode(output_bytes).decode("ascii"),
                }
            })
        finally:
            if os.path.exists(input_path):
                os.remove(input_path)
            if os.path.exists(output_path):
                os.remove(output_path)

    except DashboardAPIValidationError as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "validation_error", "message": exc.message}},
            exc.status_code,
        )
    except Exception as exc:
        return jsonify_with_status(
            {"success": False, "request_id": request_id, "error": {"type": "server_error", "message": str(exc)}},
            500,
        )
