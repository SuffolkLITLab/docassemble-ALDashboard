"""
Flask endpoints for the DOCX and PDF labeler tools.

These provide interactive browser-based interfaces for:
- al/docx-labeler: Add Jinja2 labels to DOCX templates
- al/pdf-labeler: Add/edit PDF form fields

Both tools use AI to suggest labels and follow AssemblyLine conventions.
"""

import base64
import inspect
import io
import json
import os
import tempfile
import uuid
from urllib.parse import quote, urlsplit
from typing import Any, Dict, List, Optional

from flask import Response, jsonify, request, send_file
from flask_cors import cross_origin
from flask_login import current_user

from docassemble.base.config import daconfig
from docassemble.base.util import log
from docassemble.webapp.app_object import app, csrf
from docassemble.webapp.server import api_verify, jsonify_with_status

from .api_dashboard_utils import (
    DashboardAPIValidationError,
    _validate_upload_size,
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


def _labeler_session_identity() -> Dict[str, Optional[str]]:
    """Return session identity details for browser users."""
    try:
        if current_user.is_authenticated:
            email_value = getattr(current_user, "email", None)
            if email_value is not None:
                email_value = str(email_value)
            return {"is_authenticated": True, "email": email_value}
    except Exception:
        pass
    return {"is_authenticated": False, "email": None}


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
        log("ALDashboard: DOCX labeler template not found, using inline fallback", "warning")
        html_content = _generate_docx_labeler_html()
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
                "login_url": login_url,
                "logout_url": logout_url,
                "ai_enabled": _labeler_ai_auth_check(),
            },
        }
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
# PDF Labeler Routes
# =============================================================================


@app.route(f"{LABELER_BASE_PATH}/pdf-labeler", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def pdf_labeler_page():
    """Serve the PDF labeler interactive UI."""
    log("ALDashboard: Serving PDF labeler page", "info")
    html_content = _get_template_content("pdf_labeler.html")
    if not html_content:
        log("ALDashboard: PDF labeler template not found, using inline fallback", "warning")
        html_content = _generate_pdf_labeler_html()
    return Response(html_content, mimetype="text/html")


@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/detect-fields", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def pdf_labeler_detect_fields():
    """Detect existing form fields in a PDF."""
    request_id = str(uuid.uuid4())
    log(f"ALDashboard: detect-fields request {request_id}", "info")
    try:
        from .pdf_field_labeler import list_existing_field_names
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

        _validate_upload_size(content)

        if not filename.lower().endswith(".pdf"):
            raise DashboardAPIValidationError(
                "Only PDF files are supported.", status_code=415
            )

        # Options
        normalize_fields = parse_bool(post_data.get("normalize_fields"), default=True)
        jur = str(post_data.get("jur", "MA"))
        model = post_data.get("model")
        if model is None or str(model).strip() == "":
            model = _build_labeler_model_catalog()["default_model"]
        model = str(model)

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
                parse_form_kwargs: Dict[str, Any] = {
                    "title": os.path.splitext(filename)[0],
                    "jur": jur,
                    "normalize": True,
                    "rewrite": True,
                }
                try:
                    parse_signature = inspect.signature(formfyxer.parse_form)
                    if "model" in parse_signature.parameters:
                        parse_form_kwargs["model"] = model
                except Exception:
                    pass
                stats = formfyxer.parse_form(output_path, **parse_form_kwargs)

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

        _validate_upload_size(content)

        if not filename.lower().endswith(".pdf"):
            raise DashboardAPIValidationError(
                "Only PDF files are supported.", status_code=415
            )

        jur = str(post_data.get("jur", "MA"))
        model = post_data.get("model")
        if model is None or str(model).strip() == "":
            model = _build_labeler_model_catalog()["default_model"]
        model = str(model)

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

                form_field = FormField(
                    field_name=str(field_data.get("name", "field")),
                    type_name=field_type,
                    x=int(field_data.get("x", 0)),
                    y=int(field_data.get("y", 0)),
                    font_size=int(field_data.get("fontSize") or 12),
                    configs={"width": width, "height": height},
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


# =============================================================================
# Inline HTML Generators (fallbacks if templates don't exist)
# =============================================================================


def _generate_docx_labeler_html() -> str:
    """Generate inline HTML for the DOCX labeler."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AssemblyLine DOCX Labeler</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="/packagestatic/docassemble.ALDashboard/al_dashboard.css">
</head>
<body class="bg-slate-100 min-h-screen">
    <div id="app" class="container mx-auto p-6">
        <header class="mb-8">
            <h1 class="text-3xl font-bold text-slate-800">AssemblyLine DOCX Labeler</h1>
            <p class="text-slate-600 mt-2">Add Jinja2 template variables to your DOCX files using AI suggestions.</p>
        </header>
        <div id="docx-labeler-root"></div>
    </div>
    <script src="/packagestatic/docassemble.ALDashboard/docx_labeler.js"></script>
</body>
</html>'''


def _generate_pdf_labeler_html() -> str:
    """Generate inline HTML for the PDF labeler."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AssemblyLine PDF Labeler</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="/packagestatic/docassemble.ALDashboard/al_dashboard.css">
</head>
<body class="bg-slate-100 min-h-screen">
    <div id="app" class="container mx-auto p-6">
        <header class="mb-8">
            <h1 class="text-3xl font-bold text-slate-800">AssemblyLine PDF Labeler</h1>
            <p class="text-slate-600 mt-2">Add and edit PDF form fields using AI-powered detection.</p>
        </header>
        <div id="pdf-labeler-root"></div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.min.mjs" type="module"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf-lib/1.17.1/pdf-lib.min.js"></script>
    <script src="/packagestatic/docassemble.ALDashboard/pdf_labeler.js"></script>
</body>
</html>'''
