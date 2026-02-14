import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


class PDFLabelingError(RuntimeError):
    pass


def _flatten_field_names(fields_per_page: List[List[Any]]) -> List[str]:
    names: List[str] = []
    for fields in fields_per_page:
        for field in fields:
            field_name = getattr(field, "name", None)
            if isinstance(field_name, str) and field_name:
                names.append(field_name)
    return names


def _build_mapping_from_target_list(
    current_names: List[str], target_names: List[str]
) -> Dict[str, str]:
    if len(current_names) != len(target_names):
        raise PDFLabelingError(
            f"target_field_names count ({len(target_names)}) does not match detected fields ({len(current_names)})."
        )
    return {old: new for old, new in zip(current_names, target_names)}


def list_existing_field_names(pdf_path: str) -> List[str]:
    import formfyxer

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
) -> Dict[str, Any]:
    import formfyxer

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
        shutil.copyfile(input_pdf_path, output_pdf_path)
        parsed = formfyxer.parse_form(
            output_pdf_path,
            title=Path(output_pdf_path).stem,
            jur=jur,
            normalize=True,
            rewrite=True,
            tools_token=tools_token,
            openai_api_key=openai_api,
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

    if not Path(output_pdf_path).is_file():
        raise PDFLabelingError("FormFyxer did not produce a relabeled output PDF.")

    if not stats:
        stats = {
            "fields_old": current_names,
            "fields": list_existing_field_names(output_pdf_path),
            "total fields": len(current_names),
        }
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
) -> Dict[str, Any]:
    import formfyxer

    if add_fields:
        formfyxer.auto_add_fields(input_pdf_path, output_pdf_path)
    else:
        shutil.copyfile(input_pdf_path, output_pdf_path)

    output_path = Path(output_pdf_path)
    if not output_path.is_file():
        raise PDFLabelingError("FormFyxer did not produce an output PDF.")

    if not normalize_fields:
        return {}

    stats = formfyxer.parse_form(
        str(output_path),
        title=output_path.stem,
        jur=jur,
        normalize=True,
        rewrite=True,
        tools_token=tools_token,
        openai_api_key=openai_api,
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
) -> Dict[str, Any]:
    stats = apply_formfyxer_pdf_labeling(
        input_pdf_path=input_pdf_path,
        output_pdf_path=output_pdf_path,
        add_fields=True,
        normalize_fields=relabel_with_ai,
        jur=jur,
        tools_token=tools_token,
        openai_api=openai_api,
    )
    if target_field_names is not None:
        renamed_stats = relabel_existing_pdf_fields(
            input_pdf_path=output_pdf_path,
            output_pdf_path=output_pdf_path,
            target_field_names=target_field_names,
            jur=jur,
            tools_token=tools_token,
            openai_api=openai_api,
        )
        if isinstance(stats, dict):
            stats.update({"post_relabel": renamed_stats})
        else:
            stats = {"post_relabel": renamed_stats}
    return stats
