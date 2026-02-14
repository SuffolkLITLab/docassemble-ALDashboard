# do not pre-load

from typing import Any, Dict

from docassemble.webapp.worker_common import bg_context, workerapp  # type: ignore[import-untyped]

from .api_dashboard_utils import (
    autolabel_payload_from_options,
    bootstrap_payload_from_options,
    pdf_fields_detect_payload_from_options,
    pdf_fields_relabel_payload_from_options,
    pdf_label_fields_payload_from_options,
    review_screen_payload_from_options,
    translation_payload_from_options,
    validate_docx_payload_from_options,
    validate_translation_payload_from_options,
)


@workerapp.task
def dashboard_translation_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return translation_payload_from_options(payload)


@workerapp.task
def dashboard_autolabel_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return autolabel_payload_from_options(payload)


@workerapp.task
def dashboard_bootstrap_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return bootstrap_payload_from_options(payload)


@workerapp.task
def dashboard_validate_translation_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return validate_translation_payload_from_options(payload)


@workerapp.task
def dashboard_review_screen_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return review_screen_payload_from_options(payload)


@workerapp.task
def dashboard_validate_docx_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return validate_docx_payload_from_options(payload)


@workerapp.task
def dashboard_pdf_label_fields_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return pdf_label_fields_payload_from_options(payload)


@workerapp.task
def dashboard_pdf_fields_detect_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return pdf_fields_detect_payload_from_options(payload)


@workerapp.task
def dashboard_pdf_fields_relabel_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    with bg_context():
        return pdf_fields_relabel_payload_from_options(payload)
