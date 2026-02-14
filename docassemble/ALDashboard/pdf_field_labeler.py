import shutil
from pathlib import Path
from typing import Any, Dict, Optional


class PDFLabelingError(RuntimeError):
    pass


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
