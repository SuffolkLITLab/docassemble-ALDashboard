#!/usr/bin/env python3
"""Local smoke test for DOCX auto-labeling with Azure-compatible endpoint credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from docassemble.ALDashboard.docx_wrangling import get_labeled_docx_runs, update_docx


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _normalize_endpoint(endpoint_url: str) -> str:
    parsed = urlparse(endpoint_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("AZURE_OPENAI_ENDPOINT_URL must be a full URL")

    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]

    # ALToolbox/OpenAI appends /chat/completions to this base URL.
    base_url = f"{parsed.scheme}://{parsed.netloc}{path}"
    if parsed.query:
        base_url = f"{base_url}?{parsed.query}"
    return base_url


def _is_reasonable_output(results: List[Tuple[int, int, str, int]]) -> Tuple[bool, str]:
    if not results:
        return False, "no suggestions returned"
    with_jinja = sum(1 for _, _, text, _ in results if "{{" in text or "{%" in text)
    ratio = with_jinja / len(results)
    if ratio < 0.5:
        return (
            False,
            f"only {with_jinja}/{len(results)} suggestions contained Jinja syntax",
        )
    return True, f"{with_jinja}/{len(results)} suggestions contain Jinja syntax"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    _load_env_file(repo_root / ".env")

    endpoint_url = os.getenv("AZURE_OPENAI_ENDPOINT_URL", "").strip()
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    model = os.getenv("AZURE_OPENAI_MODEL", "gpt-5-nano").strip()

    if not endpoint_url or not api_key:
        raise ValueError(
            "Set AZURE_OPENAI_ENDPOINT_URL and AZURE_OPENAI_API_KEY in .env"
        )

    base_url = _normalize_endpoint(endpoint_url)

    samples_dir = Path("/home/quinten/WordJinjaPoC/sample_files")
    sample_paths = sorted(samples_dir.glob("*.docx"))
    sample_limit = os.getenv("DOCX_TEST_LIMIT")
    if sample_limit:
        sample_paths = sample_paths[: max(1, int(sample_limit))]
    if not sample_paths:
        raise FileNotFoundError(f"No .docx files found in {samples_dir}")

    out_dir = Path("/tmp/aldashboard_azure_autolabel")
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for path in sample_paths:
        print(f"\n=== Testing {path.name} ===", flush=True)
        try:
            results = get_labeled_docx_runs(
                str(path),
                openai_api=api_key,
                openai_base_url=base_url,
                model=model,
            )
            ok, detail = _is_reasonable_output(results)

            updated = update_docx(str(path), results)
            output_path = out_dir / f"{path.stem}.labeled.docx"
            updated.save(str(output_path))

            print(f"suggestions: {len(results)}", flush=True)
            print(f"reasonable: {ok} ({detail})", flush=True)
            print("sample suggestions:", flush=True)
            for row in results[:5]:
                print(f"  {row}", flush=True)
            print(f"saved: {output_path}", flush=True)

            summary.append(
                {
                    "file": path.name,
                    "suggestions": len(results),
                    "reasonable": ok,
                    "detail": detail,
                    "output": str(output_path),
                    "preview": results[:5],
                }
            )
        except Exception as exc:  # pragma: no cover - smoke test script
            print(f"ERROR: {exc}", flush=True)
            summary.append(
                {
                    "file": path.name,
                    "error": str(exc),
                    "reasonable": False,
                }
            )

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote summary: {summary_path}", flush=True)

    any_success = any("suggestions" in row for row in summary)
    all_reasonable = all(row.get("reasonable", False) for row in summary if "suggestions" in row)
    return 0 if any_success and all_reasonable else 2


if __name__ == "__main__":
    raise SystemExit(main())
