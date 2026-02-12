import io
import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Optional

import requests

BOOTSTRAP_VERSION = "5.3.0"
BOOTSTRAP_ROOT = f"/tmp/bootstrap-{BOOTSTRAP_VERSION}/"


class BootstrapCompileError(RuntimeError):
    pass


def ensure_bootstrap_installed(bootstrap_dir: str = BOOTSTRAP_ROOT) -> None:
    p = Path(bootstrap_dir)
    if p.is_dir():
        return

    response = requests.get(
        f"https://github.com/twbs/bootstrap/archive/refs/tags/v{BOOTSTRAP_VERSION}.zip",
        timeout=120,
    )
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall("/tmp/")

    subprocess.run(["npm", "install", "--prefix", bootstrap_dir], check=True, capture_output=True)


def compile_bootstrap_theme(
    *,
    scss_text: Optional[str] = None,
    scss_path: Optional[str] = None,
    bootstrap_dir: str = BOOTSTRAP_ROOT,
) -> Dict[str, str]:
    if not scss_text and not scss_path:
        raise ValueError("Either scss_text or scss_path must be provided.")

    ensure_bootstrap_installed(bootstrap_dir)

    file_name = str(uuid.uuid4())
    full_path = os.path.join(bootstrap_dir, "scss", f"{file_name}.scss")

    if scss_text:
        with open(full_path, "w", encoding="utf-8") as text_to_file:
            text_to_file.write(scss_text)
    else:
        shutil.copy(str(scss_path), full_path)

    try:
        compile_output = subprocess.run(
            ["npm", "run", "css-compile", "--prefix", bootstrap_dir],
            capture_output=True,
            check=False,
        )
        out_path = Path(os.path.join(bootstrap_dir, "dist", "css", f"{file_name}.css"))
        if not out_path.is_file():
            stderr = compile_output.stderr.decode("utf-8", errors="replace")
            raise BootstrapCompileError(stderr or "Bootstrap compilation failed.")

        css_text = out_path.read_text(encoding="utf-8", errors="replace")
        return {
            "css_text": css_text,
            "scss_filename": f"{file_name}.scss",
            "css_filename": f"{file_name}.css",
            "stderr": compile_output.stderr.decode("utf-8", errors="replace"),
            "stdout": compile_output.stdout.decode("utf-8", errors="replace"),
        }
    finally:
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except Exception:
            pass
