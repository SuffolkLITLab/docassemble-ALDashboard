import fcntl
import io
import os
import shutil
import stat
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Optional

import requests

BOOTSTRAP_VERSION = "5.3.0"
BOOTSTRAP_ROOT = f"/tmp/bootstrap-{BOOTSTRAP_VERSION}/"
BOOTSTRAP_EXPECTED_ARCHIVE_PREFIX = f"bootstrap-{BOOTSTRAP_VERSION}/"


class BootstrapCompileError(RuntimeError):
    pass


def _safe_extract_bootstrap_archive(archive: zipfile.ZipFile, destination: str) -> None:
    destination_root = Path(destination).resolve()
    for member in archive.infolist():
        member_name = member.filename.replace("\\", "/")
        if not member_name.startswith(BOOTSTRAP_EXPECTED_ARCHIVE_PREFIX):
            raise BootstrapCompileError("Unexpected file layout in Bootstrap archive.")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise BootstrapCompileError(
                "Refusing to extract symlink from Bootstrap archive."
            )
        member_path = Path(member_name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise BootstrapCompileError("Unsafe path found in Bootstrap archive.")
        target_path = (destination_root / member_path).resolve()
        if (
            destination_root not in target_path.parents
            and target_path != destination_root
        ):
            raise BootstrapCompileError(
                "Bootstrap archive extraction attempted path traversal."
            )
        archive.extract(member, path=str(destination_root))


def ensure_bootstrap_installed(bootstrap_dir: str = BOOTSTRAP_ROOT) -> None:
    p = Path(bootstrap_dir)
    ready_marker = p / ".aldashboard_bootstrap_ready"
    if ready_marker.exists():
        return

    lock_path = f"{bootstrap_dir.rstrip('/')}.install.lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        if ready_marker.exists():
            return
        if p.exists() and not p.is_dir():
            raise BootstrapCompileError(
                f"Bootstrap path exists but is not a directory: {bootstrap_dir}"
            )

        response = requests.get(
            f"https://github.com/twbs/bootstrap/archive/refs/tags/v{BOOTSTRAP_VERSION}.zip",
            timeout=120,
        )
        response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            _safe_extract_bootstrap_archive(archive, "/tmp/")

        subprocess.run(
            ["npm", "install", "--prefix", bootstrap_dir],
            check=True,
            capture_output=True,
        )
        ready_marker.write_text("ok\n", encoding="utf-8")


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
        try:
            css_dir = Path(bootstrap_dir) / "dist" / "css"
            if css_dir.is_dir():
                for artifact in css_dir.glob(f"{file_name}.css*"):
                    artifact.unlink(missing_ok=True)
        except Exception:
            pass
