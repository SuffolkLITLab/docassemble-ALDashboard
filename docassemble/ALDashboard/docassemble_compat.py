"""File compatibility imports for APIs moved in docassemble 1.10."""

try:
    from docassemble.webapp.files.savedfile import SavedFile
except ModuleNotFoundError as err:
    if err.name not in {
        "docassemble.webapp.files",
        "docassemble.webapp.files.savedfile",
    }:
        raise
    # docassemble < 1.10 provides SavedFile from a single files module.
    from docassemble.webapp.files import SavedFile

try:
    from docassemble.webapp.utils.filenames import (
        directory_for,
        get_ext_and_mimetype,
        secure_filename_unicode_ok,
    )
except ModuleNotFoundError as err:
    if err.name not in {
        "docassemble.webapp.utils",
        "docassemble.webapp.utils.filenames",
    }:
        raise
    # docassemble < 1.10 keeps filename helpers in backend/files/server modules.
    from docassemble.webapp.backend import directory_for
    from docassemble.webapp.files import get_ext_and_mimetype
    from docassemble.webapp.server import secure_filename_unicode_ok

try:
    from docassemble.webapp.files.file_access import get_info_from_file_number
except ModuleNotFoundError as err:
    if err.name not in {
        "docassemble.webapp.files",
        "docassemble.webapp.files.file_access",
    }:
        raise
    # docassemble < 1.10 keeps file lookup in backend module.
    from docassemble.webapp.backend import get_info_from_file_number


__all__ = [
    "SavedFile",
    "directory_for",
    "get_ext_and_mimetype",
    "secure_filename_unicode_ok",
    "get_info_from_file_number",
]
