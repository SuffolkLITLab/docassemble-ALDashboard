import shutil
import inspect
import importlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from docassemble.base.util import get_config, log


class PDFLabelingError(RuntimeError):
    pass


def _assert_valid_pdf_output(pdf_path: str, *, action_label: str) -> None:
    """Validate that an action produced a readable PDF file.

    Args:
        pdf_path: Path to the PDF file that should have been written.
        action_label: Human-readable label describing the action being validated.

    Raises:
        PDFLabelingError: If the file is missing or does not start with a PDF header.
    """
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
    rewrite: bool = True,
) -> Any:
    """Call ``formfyxer.parse_form`` with optional arguments when supported.

    Args:
        formfyxer_module: Imported ``formfyxer`` module.
        in_file: Path to the input PDF to parse.
        title: Title passed through to FormFyxer.
        jur: Jurisdiction code used for labeling heuristics.
        tools_token: API token for tools.suffolklitlab.org, if available.
        openai_api: OpenAI API key override, if available.
        openai_base_url: Optional OpenAI-compatible base URL.
        model: Optional model name to request when FormFyxer supports it.

    Returns:
        Any: The raw response returned by ``formfyxer.parse_form``.
    """
    parse_kwargs: Dict[str, Any] = {
        "title": title,
        "jur": jur,
        "normalize": True,
        "rewrite": rewrite,
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


def _load_formfyxer_prompt_text(formfyxer_module: Any, prompt_name: str) -> str:
    """Load a bundled FormFyxer prompt by name."""
    package_dir = Path(inspect.getfile(formfyxer_module)).resolve().parent
    prompt_path = package_dir / "prompts" / f"{prompt_name}.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


def _load_pdf_text_with_fields(formfyxer_module: Any, input_pdf_path: str) -> str:
    """Extract PDF text with inline field markers using FormFyxer."""
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".txt", delete=False
    ) as temp_file:
        temp_path = temp_file.name
    try:
        formfyxer_module.get_original_text_with_fields(input_pdf_path, temp_path)
        return Path(temp_path).read_text(encoding="utf-8")
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _field_names_in_prompt_order(
    pdf_text_with_fields: str, fallback_names: List[str]
) -> List[str]:
    """Return field names in the same order they appear in the AI prompt text."""
    marker_names = [
        match.group(1).strip()
        for match in re.finditer(
            r"\{\{(.*?)\}\}", pdf_text_with_fields, flags=re.DOTALL
        )
        if match.group(1).strip()
    ]
    if (
        marker_names
        and len(marker_names) == len(fallback_names)
        and Counter(marker_names) == Counter(fallback_names)
    ):
        return marker_names

    if marker_names:
        log(
            "ALDashboard: pdf relabel prompt marker order did not match detected field names; falling back to detected field order",
            "warning",
        )
    return fallback_names


def _ensure_unique_field_names(field_names: List[str]) -> List[str]:
    """Ensure field names remain unique while preserving order."""
    result: List[str] = []
    used: set[str] = set()
    counters: Dict[str, int] = {}

    for raw_name in field_names:
        candidate = str(raw_name or "").strip() or "field"
        if candidate not in used:
            result.append(candidate)
            used.add(candidate)
            counters.setdefault(candidate, 1)
            continue

        base_name = candidate
        if "__" in candidate:
            maybe_base, maybe_suffix = candidate.rsplit("__", 1)
            if maybe_suffix.isdigit():
                base_name = maybe_base

        counter = max(counters.get(base_name, 1) + 1, 2)
        next_name = f"{base_name}__{counter}"
        while next_name in used:
            counter += 1
            next_name = f"{base_name}__{counter}"

        counters[base_name] = counter
        result.append(next_name)
        used.add(next_name)

    return result


def _fallback_target_field_names(
    formfyxer_module: Any, current_names: List[str]
) -> List[str]:
    """Use FormFyxer's fallback renamer when AI output is unavailable."""
    fallback = getattr(formfyxer_module, "fallback_rename_fields", None)
    if callable(fallback):
        result = fallback(current_names)
        if isinstance(result, tuple) and result and isinstance(result[0], list):
            return _ensure_unique_field_names([str(name) for name in result[0]])
        if isinstance(result, list):
            return _ensure_unique_field_names([str(name) for name in result])
    return _ensure_unique_field_names([str(name) for name in current_names])


def _generate_ai_relabel_target_field_names(
    formfyxer_module: Any,
    *,
    input_pdf_path: str,
    current_names: List[str],
    pdf_text_with_fields: Optional[str] = None,
    openai_api: Optional[str],
    openai_base_url: Optional[str],
    model: Optional[str],
) -> List[str]:
    """Request occurrence-aware target field names directly from FormFyxer's LLM helpers."""
    try:
        system_message = _load_formfyxer_prompt_text(formfyxer_module, "field_labeling")
        system_message += """

Important override for this request:
- Original field names may repeat.
- Return JSON only.
- Do not return a field_mappings object.
- Return a JSON object with a target_field_names array aligned exactly to the provided original field names list.
- Preserve duplicates by occurrence order in the returned array.
"""

        if pdf_text_with_fields is None:
            pdf_text_with_fields = _load_pdf_text_with_fields(
                formfyxer_module, input_pdf_path
            )

        if not pdf_text_with_fields.strip():
            return _fallback_target_field_names(formfyxer_module, current_names)

        user_message = f"""Here is the PDF form text with field markers:

{pdf_text_with_fields[:300000]}

Original field names in order (duplicates matter):
{json.dumps(current_names, indent=2)}

Return target_field_names as an ordered array aligned exactly with the original field names list."""

        response = formfyxer_module.text_complete(
            system_message=system_message,
            user_message=user_message,
            max_tokens=15000,
            api_key=openai_api,
            model=model or "gpt-5-nano",
            openai_base_url=openai_base_url,
        )

        if isinstance(response, dict):
            payload = response
        elif isinstance(response, str):
            payload = json.loads(response)
        else:
            raise ValueError(f"Unexpected response type: {type(response)}")

        target_field_names = payload.get("target_field_names")
        if not isinstance(target_field_names, list):
            raise ValueError("target_field_names is not a list")
        if len(target_field_names) != len(current_names):
            raise ValueError(
                "target_field_names length does not match the current field count"
            )
        return _ensure_unique_field_names([str(name) for name in target_field_names])
    except Exception as exc:
        log(
            f"ALDashboard: pdf relabel falling back to deterministic renaming after AI target generation failure: {exc}",
            "warning",
        )
        return _fallback_target_field_names(formfyxer_module, current_names)


def _generate_ai_relabel_mapping_for_unique_fields(
    formfyxer_module: Any,
    *,
    input_pdf_path: str,
    current_names: List[str],
    openai_api: Optional[str],
    openai_base_url: Optional[str],
    model: Optional[str],
) -> Optional[Dict[str, str]]:
    """Request direct old-to-new mappings when source field names are unique."""
    rename_with_context = getattr(
        formfyxer_module, "rename_pdf_fields_with_context", None
    )
    if not callable(rename_with_context):
        return None

    mapping = rename_with_context(
        pdf_path=input_pdf_path,
        original_field_names=current_names,
        api_key=openai_api,
        model=model or "gpt-5-nano",
        openai_base_url=openai_base_url,
    )
    if not isinstance(mapping, dict):
        raise PDFLabelingError(
            "FormFyxer rename_pdf_fields_with_context returned an unexpected response type."
        )

    ordered_targets = _ensure_unique_field_names(
        [
            re.sub(r"^\*", "", str(mapping.get(name, name) or name))
            for name in current_names
        ]
    )
    return {
        original_name: target_name
        for original_name, target_name in zip(current_names, ordered_targets)
    }


def _rewrite_pdf_fields_in_order(
    formfyxer_module: Any,
    *,
    input_pdf_path: str,
    output_pdf_path: str,
    current_names: List[str],
    target_field_names: List[str],
) -> None:
    """Rewrite PDF fields by occurrence order, including duplicate source names."""
    normalized_targets = _ensure_unique_field_names(
        [str(name) for name in target_field_names]
    )
    if len(current_names) != len(normalized_targets):
        raise PDFLabelingError(
            f"target_field_names count ({len(normalized_targets)}) does not match detected fields ({len(current_names)})."
        )

    if Path(input_pdf_path).resolve() != Path(output_pdf_path).resolve():
        shutil.copyfile(input_pdf_path, output_pdf_path)

    try:
        lit_explorer = importlib.import_module("formfyxer.lit_explorer")
        rewrite_helper = getattr(lit_explorer, "_rewrite_pdf_fields_in_place", None)
        if callable(rewrite_helper):
            rewrite_helper(output_pdf_path, current_names, normalized_targets)
            return
    except Exception as exc:
        log(
            f"ALDashboard: pdf relabel could not use ordered FormFyxer rewrite helper: {exc}",
            "warning",
        )

    if len(set(current_names)) != len(current_names):
        raise PDFLabelingError(
            "This FormFyxer version cannot safely relabel duplicate source field names."
        )

    mapping = _build_mapping_from_target_list(current_names, normalized_targets)
    formfyxer_module.rename_pdf_fields(output_pdf_path, output_pdf_path, mapping)


def _flatten_field_names(fields_per_page: List[List[Any]]) -> List[str]:
    """Flatten field names from FormFyxer's per-page field structure.

    Args:
        fields_per_page: Field objects grouped by page.

    Returns:
        List[str]: All non-empty field names in traversal order.
    """
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
    """Resolve FormFyxer credentials from request values, config, and environment.

    Args:
        tools_token: Optional tools token supplied by the caller.
        openai_api: Optional OpenAI API key supplied by the caller.
        openai_base_url: Optional OpenAI base URL supplied by the caller.

    Returns:
        Dict[str, Optional[str]]: Resolved credentials and source labels for logging.
    """
    resolved_tools_token = tools_token
    tools_token_source = "request" if resolved_tools_token else None
    if not resolved_tools_token:
        resolved_tools_token = get_config("assembly line", {}).get(
            "tools.suffolklitlab.org api key"
        )
        if resolved_tools_token:
            tools_token_source = (
                "config:assembly line.tools.suffolklitlab.org api key"  # nosec B105
            )
    if not resolved_tools_token:
        resolved_tools_token = os.getenv("TOOLS_TOKEN") or os.getenv("SPOT_TOKEN")
        if resolved_tools_token:
            tools_token_source = "env"  # nosec B105

    resolved_openai_api = openai_api
    openai_api_source = "request" if resolved_openai_api else None
    if not resolved_openai_api:
        resolved_openai_api = get_config("open ai", {}).get("key") or get_config(
            "openai api key"
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
            resolved_openai_base_url = openai_config.get(
                "base url"
            ) or openai_config.get("base_url")
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
        "tools_token": (
            str(resolved_tools_token).strip() if resolved_tools_token else None
        ),
        "openai_api": str(resolved_openai_api).strip() if resolved_openai_api else None,
        "openai_base_url": (
            str(resolved_openai_base_url).strip() if resolved_openai_base_url else None
        ),
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
    """Log how FormFyxer credentials were resolved for a request.

    Args:
        action: Short label describing the current labeling action.
        resolved: Resolved credential values and their sources.
        model: Requested model name, if any.
        jur: Jurisdiction code used for the request.
    """
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
    """Pair detected field names with a caller-supplied ordered target list.

    Args:
        current_names: Field names currently detected in the PDF.
        target_names: Replacement names in the same order.

    Returns:
        Dict[str, str]: Mapping from existing field names to replacement names.

    Raises:
        PDFLabelingError: If the supplied target list length does not match.
    """
    if len(current_names) != len(target_names):
        raise PDFLabelingError(
            f"target_field_names count ({len(target_names)}) does not match detected fields ({len(current_names)})."
        )
    return {old: new for old, new in zip(current_names, target_names)}


def list_existing_field_names(pdf_path: str) -> List[str]:
    """Read current field names from an existing PDF.

    Args:
        pdf_path: Path to the PDF to inspect.

    Returns:
        List[str]: Field names discovered in the PDF.
    """
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
    """Rename existing PDF fields using explicit mappings or FormFyxer AI relabeling.

    Args:
        input_pdf_path: Path to the source PDF.
        output_pdf_path: Path where the relabeled PDF should be written.
        field_name_mapping: Explicit old-to-new field name mapping.
        target_field_names: Ordered replacement names aligned with detected fields.
        relabel_with_ai: Whether to ask FormFyxer to relabel with AI.
        jur: Jurisdiction code used for FormFyxer heuristics.
        tools_token: Optional tools.suffolklitlab.org token override.
        openai_api: Optional OpenAI API key override.
        openai_base_url: Optional OpenAI-compatible base URL override.
        model: Optional model override for FormFyxer.

    Returns:
        Dict[str, Any]: Relabeling statistics including old and new field names.
    """
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
        _rewrite_pdf_fields_in_order(
            formfyxer,
            input_pdf_path=input_pdf_path,
            output_pdf_path=output_pdf_path,
            current_names=current_names,
            target_field_names=[str(n) for n in target_field_names],
        )
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
        if len(set(current_names)) == len(current_names):
            ai_mapping = _generate_ai_relabel_mapping_for_unique_fields(
                formfyxer,
                input_pdf_path=input_pdf_path,
                current_names=current_names,
                openai_api=resolved["openai_api"],
                openai_base_url=resolved["openai_base_url"],
                model=model,
            )
            if ai_mapping is not None:
                formfyxer.rename_pdf_fields(input_pdf_path, output_pdf_path, ai_mapping)
            else:
                pdf_text_with_fields = _load_pdf_text_with_fields(
                    formfyxer, input_pdf_path
                )
                ai_current_names = _field_names_in_prompt_order(
                    pdf_text_with_fields, current_names
                )
                ai_target_field_names = _generate_ai_relabel_target_field_names(
                    formfyxer,
                    input_pdf_path=input_pdf_path,
                    current_names=ai_current_names,
                    pdf_text_with_fields=pdf_text_with_fields,
                    openai_api=resolved["openai_api"],
                    openai_base_url=resolved["openai_base_url"],
                    model=model,
                )
                _rewrite_pdf_fields_in_order(
                    formfyxer,
                    input_pdf_path=input_pdf_path,
                    output_pdf_path=output_pdf_path,
                    current_names=ai_current_names,
                    target_field_names=ai_target_field_names,
                )
        else:
            pdf_text_with_fields = _load_pdf_text_with_fields(formfyxer, input_pdf_path)
            ai_current_names = _field_names_in_prompt_order(
                pdf_text_with_fields, current_names
            )
            ai_target_field_names = _generate_ai_relabel_target_field_names(
                formfyxer,
                input_pdf_path=input_pdf_path,
                current_names=ai_current_names,
                pdf_text_with_fields=pdf_text_with_fields,
                openai_api=resolved["openai_api"],
                openai_base_url=resolved["openai_base_url"],
                model=model,
            )
            _rewrite_pdf_fields_in_order(
                formfyxer,
                input_pdf_path=input_pdf_path,
                output_pdf_path=output_pdf_path,
                current_names=ai_current_names,
                target_field_names=ai_target_field_names,
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
    preferred_variable_names: Optional[List[str]] = None,
    jur: str = "MA",
    tools_token: Optional[str] = None,
    openai_api: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Add PDF fields and optionally normalize them with FormFyxer.

    Args:
        input_pdf_path: Path to the source PDF.
        output_pdf_path: Path where the labeled PDF should be written.
        add_fields: Whether to detect and add new fields before normalization.
        normalize_fields: Whether to run FormFyxer normalization after field creation.
        jur: Jurisdiction code used for FormFyxer heuristics.
        tools_token: Optional tools.suffolklitlab.org token override.
        openai_api: Optional OpenAI API key override.
        openai_base_url: Optional OpenAI-compatible base URL override.
        model: Optional model override for FormFyxer.

    Returns:
        Dict[str, Any]: FormFyxer parsing statistics for the output PDF.
    """
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
        auto_add_kwargs: Dict[str, Any] = {}
        if preferred_variable_names:
            try:
                signature = inspect.signature(formfyxer.auto_add_fields)
                if "preferred_names" in signature.parameters:
                    auto_add_kwargs["preferred_names"] = preferred_variable_names
            except (TypeError, ValueError):
                pass
        formfyxer.auto_add_fields(input_pdf_path, output_pdf_path, **auto_add_kwargs)
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
    preferred_variable_names: Optional[List[str]] = None,
    jur: str = "MA",
    tools_token: Optional[str] = None,
    openai_api: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Detect PDF fields, optionally relabel them, and return aggregate stats.

    Args:
        input_pdf_path: Path to the source PDF.
        output_pdf_path: Path where the processed PDF should be written.
        relabel_with_ai: Whether to run FormFyxer AI relabeling.
        target_field_names: Optional ordered field names to apply after detection.
        jur: Jurisdiction code used for FormFyxer heuristics.
        tools_token: Optional tools.suffolklitlab.org token override.
        openai_api: Optional OpenAI API key override.
        openai_base_url: Optional OpenAI-compatible base URL override.
        model: Optional model override for FormFyxer.

    Returns:
        Dict[str, Any]: Detection and optional relabeling statistics.
    """
    stats = apply_formfyxer_pdf_labeling(
        input_pdf_path=input_pdf_path,
        output_pdf_path=output_pdf_path,
        add_fields=True,
        normalize_fields=relabel_with_ai,
        preferred_variable_names=preferred_variable_names,
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
