"""
Flask endpoints for the DOCX and PDF labeler tools.

These provide interactive browser-based interfaces for:
- al/docx-labeler: Add Jinja2 labels to DOCX templates
- al/pdf-labeler: Add/edit PDF form fields

Both tools use AI to suggest labels and follow AssemblyLine conventions.
"""

import base64
import io
import json
import os
import tempfile
import uuid
from typing import Any, Dict, List, Optional

from flask import Response, jsonify, request, send_file
from flask_cors import cross_origin
from flask_login import current_user

from docassemble.base.config import daconfig
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


def _labeler_auth_check() -> bool:
    """Check if user is authorized via API key OR browser session with admin/developer privileges."""
    if api_verify():
        return True
    # Fallback to session-based auth using Flask-Login
    try:
        if current_user.is_authenticated:
            return current_user.has_role('admin', 'developer')
    except Exception:
        pass
    return False


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


# =============================================================================
# DOCX Labeler Routes
# =============================================================================


@app.route(f"{LABELER_BASE_PATH}/docx-labeler", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def docx_labeler_page():
    """Serve the DOCX labeler interactive UI."""
    html_content = _get_template_content("docx_labeler.html")
    if not html_content:
        # Inline fallback if template not found
        html_content = _generate_docx_labeler_html()
    return Response(html_content, mimetype="text/html")


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/extract-runs", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def docx_labeler_extract_runs():
    """Extract paragraph runs from a DOCX file for labeling."""
    request_id = str(uuid.uuid4())
    if not _labeler_auth_check():
        return _auth_fail(request_id)

    try:
        from .docx_wrangling import get_docx_run_items

        # Handle both multipart and JSON uploads
        if "file" in request.files:
            upload = request.files["file"]
            filename = upload.filename or "upload.docx"
            content = upload.read()
        else:
            post_data = request.get_json(silent=True) or {}
            filename = str(post_data.get("filename") or "upload.docx")
            content = decode_base64_content(post_data.get("file_content_base64"))

        _validate_upload_size(content)

        if not filename.lower().endswith(".docx"):
            raise DashboardAPIValidationError(
                "Only DOCX files are supported.", status_code=415
            )

        # Write to temp file for processing
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        try:
            runs = get_docx_run_items(temp_path)
            paragraph_count = 0
            if runs:
                paragraph_count = max(int(item[0]) for item in runs) + 1

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": filename,
                    "paragraph_count": paragraph_count,
                    "run_count": len(runs),
                    "runs": runs,
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


@app.route(f"{LABELER_BASE_PATH}/docx-labeler/api/suggest-labels", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def docx_labeler_suggest_labels():
    """Use AI to suggest Jinja2 labels for a DOCX file."""
    request_id = str(uuid.uuid4())
    if not _labeler_auth_check():
        return _auth_fail(request_id)

    try:
        from .docx_wrangling import get_labeled_docx_runs

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
        custom_prompt = post_data.get("custom_prompt")
        additional_instructions = post_data.get("additional_instructions")
        model = post_data.get("model", "gpt-4.1-mini")
        openai_api = post_data.get("openai_api")
        openai_base_url = post_data.get("openai_base_url")

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
            suggestions = get_labeled_docx_runs(
                docx_path=temp_path,
                custom_people_names=custom_people_names,
                openai_api=openai_api,
                openai_base_url=openai_base_url,
                model=model,
                custom_prompt=custom_prompt,
                additional_instructions=additional_instructions,
            )

            # Convert to a more friendly format for the UI
            formatted_suggestions = []
            for para_num, run_num, text, new_paragraph in suggestions:
                formatted_suggestions.append({
                    "paragraph": para_num,
                    "run": run_num,
                    "text": text,
                    "new_paragraph": new_paragraph,
                    "id": str(uuid.uuid4()),
                })

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": filename,
                    "suggestions": formatted_suggestions,
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
    if not _labeler_auth_check():
        return _auth_fail(request_id)

    try:
        from .docx_wrangling import update_docx
        import docx
        from lxml import etree

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

            return jsonify({
                "success": True,
                "request_id": request_id,
                "data": {
                    "filename": output_filename,
                    "docx_base64": base64.b64encode(output_bytes).decode("ascii"),
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


# =============================================================================
# PDF Labeler Routes
# =============================================================================


@app.route(f"{LABELER_BASE_PATH}/pdf-labeler", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def pdf_labeler_page():
    """Serve the PDF labeler interactive UI."""
    html_content = _get_template_content("pdf_labeler.html")
    if not html_content:
        # Inline fallback if template not found
        html_content = _generate_pdf_labeler_html()
    return Response(html_content, mimetype="text/html")


@app.route(f"{LABELER_BASE_PATH}/pdf-labeler/api/detect-fields", methods=["POST"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "HEAD"], automatic_options=True)
def pdf_labeler_detect_fields():
    """Detect existing form fields in a PDF."""
    request_id = str(uuid.uuid4())
    if not _labeler_auth_check():
        return _auth_fail(request_id)

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
    if not _labeler_auth_check():
        return _auth_fail(request_id)

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
                stats = formfyxer.parse_form(
                    output_path,
                    title=os.path.splitext(filename)[0],
                    jur=jur,
                    normalize=True,
                    rewrite=True,
                )

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
    if not _labeler_auth_check():
        return _auth_fail(request_id)

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
    if not _labeler_auth_check():
        return _auth_fail(request_id)

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
    if not _labeler_auth_check():
        return _auth_fail(request_id)

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
