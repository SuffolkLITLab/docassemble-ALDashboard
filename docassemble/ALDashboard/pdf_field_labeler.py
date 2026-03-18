import shutil
import inspect
import docassemble.base.config
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

if not docassemble.base.config.loaded:
    docassemble.base.config.load()
from docassemble.base.util import get_config, log


class PDFLabelingError(RuntimeError):
    pass


def _assert_valid_pdf_output(pdf_path: str, *, action_label: str) -> None:
    path = Path(pdf_path)
    if not path.is_file():
        raise PDFLabelingError(f"{action_label} did not produce an output PDF.")
    with path.open("rb") as handle:
        header = handle.read(5)
    if not header.startswith(b"%PDF-"):
        raise PDFLabelingError(f"{action_label} did not produce a valid PDF output.")


def _parse_form_with_optional_model(
    formfyxer_module: Any,
    *,
    in_file: str,
    title: str,
    jur: str,
    tools_token: Optional[str],
    openai_api: Optional[str],
    openai_base_url: Optional[str],
    model: Optional[str],
) -> Any:
    parse_kwargs: Dict[str, Any] = {
        "title": title,
        "jur": jur,
        "normalize": True,
        "rewrite": True,
        "tools_token": tools_token,
        "openai_api_key": openai_api,
    }
    if openai_base_url:
        try:
            signature = inspect.signature(formfyxer_module.parse_form)
            if "openai_base_url" in signature.parameters:
                parse_kwargs["openai_base_url"] = openai_base_url
        except (TypeError, ValueError):
            pass
    if model:
        try:
            signature = inspect.signature(formfyxer_module.parse_form)
            if "model" in signature.parameters:
                parse_kwargs["model"] = model
        except (TypeError, ValueError):
            pass
    return formfyxer_module.parse_form(in_file, **parse_kwargs)


def _flatten_field_names(fields_per_page: List[List[Any]]) -> List[str]:
    names: List[str] = []
    for fields in fields_per_page:
        for field in fields:
            field_name = getattr(field, "name", None)
            if isinstance(field_name, str) and field_name:
                names.append(field_name)
    return names


def _resolve_formfyxer_credentials(
    *,
    tools_token: Optional[str],
    openai_api: Optional[str],
    openai_base_url: Optional[str],
) -> Dict[str, Optional[str]]:
    resolved_tools_token = tools_token
    tools_token_source = "request" if resolved_tools_token else None
    if not resolved_tools_token:
        resolved_tools_token = (
            get_config("assembly line", {}).get("tools.suffolklitlab.org api key")
        )
        if resolved_tools_token:
            tools_token_source = "config:assembly line.tools.suffolklitlab.org api key"  # nosec B105
    if not resolved_tools_token:
        resolved_tools_token = os.getenv("TOOLS_TOKEN") or os.getenv("SPOT_TOKEN")
        if resolved_tools_token:
            tools_token_source = "env"  # nosec B105

    resolved_openai_api = openai_api
    openai_api_source = "request" if resolved_openai_api else None
    if not resolved_openai_api:
        resolved_openai_api = (
            get_config("open ai", {}).get("key") or get_config("openai api key")
        )
        if resolved_openai_api:
            openai_api_source = (
                "config:open ai.key"
                if get_config("open ai", {}).get("key")
                else "config:openai api key"
            )
    if not resolved_openai_api:
        resolved_openai_api = os.getenv("OPENAI_API_KEY")
        if resolved_openai_api:
            openai_api_source = "env"

    resolved_openai_base_url = openai_base_url
    openai_base_url_source = "request" if resolved_openai_base_url else None
    if not resolved_openai_base_url:
        resolved_openai_base_url = get_config("openai base url")
        if resolved_openai_base_url:
            openai_base_url_source = "config:openai base url"
    if not resolved_openai_base_url:
        openai_config = get_config("open ai", {})
        if isinstance(openai_config, dict):
            resolved_openai_base_url = openai_config.get("base url") or openai_config.get("base_url")
            if resolved_openai_base_url:
                openai_base_url_source = (
                    "config:open ai.base url"
                    if openai_config.get("base url")
                    else "config:open ai.base_url"
                )
    if not resolved_openai_base_url:
        resolved_openai_base_url = os.getenv("OPENAI_BASE_URL")
        if resolved_openai_base_url:
            openai_base_url_source = "env"

    return {
        "tools_token": str(resolved_tools_token).strip() if resolved_tools_token else None,
        "openai_api": str(resolved_openai_api).strip() if resolved_openai_api else None,
        "openai_base_url": str(resolved_openai_base_url).strip()
        if resolved_openai_base_url
        else None,
        "tools_token_source": tools_token_source,
        "openai_api_source": openai_api_source,
        "openai_base_url_source": openai_base_url_source,
    }


def _log_formfyxer_resolution(
    action: str,
    *,
    resolved: Dict[str, Optional[str]],
    model: Optional[str],
    jur: str,
) -> None:
    log(
        "ALDashboard: "
        + action
        + " FormFyxer credential resolution "
        + f"(jur={jur}, model={model or 'default'}, "
        + f"tools_token={'yes' if resolved.get('tools_token') else 'no'} from {resolved.get('tools_token_source') or 'none'}, "
        + f"openai_api={'yes' if resolved.get('openai_api') else 'no'} from {resolved.get('openai_api_source') or 'none'}, "
        + f"openai_base_url={'yes' if resolved.get('openai_base_url') else 'no'} from {resolved.get('openai_base_url_source') or 'none'})",
        "info",
    )


def _build_mapping_from_target_list(
    current_names: List[str], target_names: List[str]
) -> Dict[str, str]:
    if len(current_names) != len(target_names):
        raise PDFLabelingError(
            f"target_field_names count ({len(target_names)}) does not match detected fields ({len(current_names)})."
        )
    return {old: new for old, new in zip(current_names, target_names)}


def list_existing_field_names(pdf_path: str) -> List[str]:
    import formfyxer  # type: ignore[import-not-found]

    fields_per_page = formfyxer.get_existing_pdf_fields(pdf_path)
    return _flatten_field_names(fields_per_page)


def relabel_existing_pdf_fields(
    *,
    input_pdf_path: str,
    output_pdf_path: str,
    field_name_mapping: Optional[Mapping[str, str]] = None,
    target_field_names: Optional[List[str]] = None,
    relabel_with_ai: bool = False,
    jur: str = "MA",
    tools_token: Optional[str] = None,
    openai_api: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    import formfyxer  # type: ignore[import-not-found]

    current_names = list_existing_field_names(input_pdf_path)
    if not current_names:
        raise PDFLabelingError("No existing PDF fields were found to relabel.")

    stats: Dict[str, Any] = {}
    if field_name_mapping:
        mapping = {str(k): str(v) for k, v in field_name_mapping.items()}
        missing = [name for name in mapping.keys() if name not in current_names]
        if missing:
            raise PDFLabelingError(
                f"field_name_mapping includes unknown source fields: {missing}"
            )
        formfyxer.rename_pdf_fields(input_pdf_path, output_pdf_path, mapping)
    elif target_field_names is not None:
        mapping = _build_mapping_from_target_list(
            current_names, [str(n) for n in target_field_names]
        )
        formfyxer.rename_pdf_fields(input_pdf_path, output_pdf_path, mapping)
    elif relabel_with_ai:
        resolved = _resolve_formfyxer_credentials(
            tools_token=tools_token,
            openai_api=openai_api,
            openai_base_url=openai_base_url,
        )
        _log_formfyxer_resolution(
            "pdf relabel",
            resolved=resolved,
            model=model,
            jur=jur,
        )
        shutil.copyfile(input_pdf_path, output_pdf_path)
        parsed = _parse_form_with_optional_model(
            formfyxer,
            in_file=output_pdf_path,
            title=Path(output_pdf_path).stem,
            jur=jur,
            tools_token=resolved["tools_token"],
            openai_api=resolved["openai_api"],
            openai_base_url=resolved["openai_base_url"],
            model=model,
        )
        if isinstance(parsed, dict):
            stats = parsed
        else:
            raise PDFLabelingError(
                "FormFyxer parse_form returned an unexpected response type."
            )
    else:
        raise PDFLabelingError(
            "Provide one of: field_name_mapping, target_field_names, or relabel_with_ai=true."
        )

    _assert_valid_pdf_output(output_pdf_path, action_label="FormFyxer relabel")
    updated_names = list_existing_field_names(output_pdf_path)

    if not stats:
        stats = {}
    stats.setdefault("fields_old", current_names)
    stats.setdefault("fields", updated_names)
    stats.setdefault("total fields", len(updated_names))
    stats.setdefault(
        "renamed fields",
        sum(1 for old, new in zip(current_names, updated_names) if old != new),
    )
    return stats


def apply_formfyxer_pdf_labeling(
    *,
    input_pdf_path: str,
    output_pdf_path: str,
    add_fields: bool = True,
    normalize_fields: bool = True,
    jur: str = "MA",
    tools_token: Optional[str] = None,
    openai_api: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    import formfyxer  # type: ignore[import-not-found]

    resolved = _resolve_formfyxer_credentials(
        tools_token=tools_token,
        openai_api=openai_api,
        openai_base_url=openai_base_url,
    )
    _log_formfyxer_resolution(
        "pdf detect/normalize",
        resolved=resolved,
        model=model,
        jur=jur,
    )

    if add_fields:
        formfyxer.auto_add_fields(input_pdf_path, output_pdf_path)
    else:
        shutil.copyfile(input_pdf_path, output_pdf_path)

    output_path = Path(output_pdf_path)
    _assert_valid_pdf_output(str(output_path), action_label="FormFyxer labeling")

    if not normalize_fields:
        return {}

    stats = _parse_form_with_optional_model(
        formfyxer,
        in_file=str(output_path),
        title=output_path.stem,
        jur=jur,
        tools_token=resolved["tools_token"],
        openai_api=resolved["openai_api"],
        openai_base_url=resolved["openai_base_url"],
        model=model,
    )
    if isinstance(stats, dict):
        return stats
    raise PDFLabelingError("FormFyxer parse_form returned an unexpected response type.")


def detect_pdf_fields_and_optionally_relabel(
    *,
    input_pdf_path: str,
    output_pdf_path: str,
    relabel_with_ai: bool = False,
    target_field_names: Optional[List[str]] = None,
    jur: str = "MA",
    tools_token: Optional[str] = None,
    openai_api: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    stats = apply_formfyxer_pdf_labeling(
        input_pdf_path=input_pdf_path,
        output_pdf_path=output_pdf_path,
        add_fields=True,
        normalize_fields=relabel_with_ai,
        jur=jur,
        tools_token=tools_token,
        openai_api=openai_api,
        openai_base_url=openai_base_url,
        model=model,
    )
    if target_field_names is not None:
        renamed_stats = relabel_existing_pdf_fields(
            input_pdf_path=output_pdf_path,
            output_pdf_path=output_pdf_path,
            target_field_names=target_field_names,
            jur=jur,
            tools_token=tools_token,
            openai_api=openai_api,
            openai_base_url=openai_base_url,
            model=model,
        )
        if isinstance(stats, dict):
            stats.update({"post_relabel": renamed_stats})
        else:
            stats = {"post_relabel": renamed_stats}
    return stats
