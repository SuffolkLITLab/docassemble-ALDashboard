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
    from docassemble.webapp.utils.filenames import directory_for
except ModuleNotFoundError as err:
    if err.name not in {
        "docassemble.webapp.utils",
        "docassemble.webapp.utils.filenames",
    }:
        raise
    # docassemble < 1.10 keeps filename helpers in the backend module.
    from docassemble.webapp.backend import directory_for


__all__ = [
    "SavedFile",
    "directory_for",
]
