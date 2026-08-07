"""Pytest-only import shims for environments without docassemble.webapp.

The CI test environment installs ``docassemble.base`` but intentionally avoids
``docassemble.webapp`` because the upstream sdist is currently broken. A small
subset of tests imports modules that transitively load ``docassemble.base.util``
or ``docassemble.webapp.screenreader`` during collection. Provide the minimal
symbols those imports require so collection can proceed without the full webapp
stack.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from typing import Any, Callable


def _install_docassemble_webapp_stubs() -> None:
    if importlib.util.find_spec("docassemble.webapp") is not None:
        return

    webapp_pkg = types.ModuleType("docassemble.webapp")
    webapp_pkg.__path__ = []  # type: ignore[attr-defined]

    da_flask_mail = types.ModuleType("docassemble.webapp.da_flask_mail")

    class Message:  # pragma: no cover - simple import shim
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    setattr(da_flask_mail, "Message", Message)

    screenreader = types.ModuleType("docassemble.webapp.screenreader")
    to_text: Callable[[str], str] = lambda html_text: html_text
    setattr(screenreader, "to_text", to_text)

    setattr(webapp_pkg, "da_flask_mail", da_flask_mail)
    setattr(webapp_pkg, "screenreader", screenreader)

    sys.modules["docassemble.webapp"] = webapp_pkg
    sys.modules["docassemble.webapp.da_flask_mail"] = da_flask_mail
    sys.modules["docassemble.webapp.screenreader"] = screenreader

    try:
        import docassemble

        setattr(docassemble, "webapp", webapp_pkg)
    except Exception:
        pass


def _preimport_pikepdf() -> None:
    """Load pikepdf before any test patches ``sys.modules``.

    ``unittest.mock.patch.dict("sys.modules", ...)`` restores the original
    module dict on exit, dropping anything that was imported for the first
    time inside the patched block. pikepdf cannot survive a second import in
    the same process -- its ``@augments`` class patching raises
    ``RuntimeError: ... both define the same non-abstract method`` -- so a test
    that first pulls it in transitively (via ``docassemble.base.util``) while
    ``sys.modules`` is patched would break every later pikepdf import.
    Importing it up front keeps it in the dict that ``patch.dict`` restores.
    """
    try:
        import pikepdf  # noqa: F401
    except Exception:  # pragma: no cover - pikepdf is optional at import time
        pass


def _install_empty_docassemble_configuration() -> None:
    """Make ``get_config()`` usable without a running docassemble server.

    docassemble 1.10 routes ``get_configuration()`` through a pluggy hook that
    only the webapp registers. Outside a real server the hook has no
    implementation and returns ``None``, so ``docassemble.base.functions``'s
    ``get_config()`` dies with ``AttributeError: 'NoneType' object has no
    attribute 'get'``. ALToolbox's ``llms`` module calls ``get_config`` at
    import time, which breaks collection of every test that imports
    ``docx_wrangling``. Fall back to an empty configuration, which is what
    these tests want anyway.
    """
    try:
        import docassemble.base.functions as da_functions
    except Exception:
        return

    try:
        da_functions.get_config("open ai")
    except Exception:
        setattr(da_functions, "get_configuration", lambda: {})


_install_docassemble_webapp_stubs()
_install_empty_docassemble_configuration()
_preimport_pikepdf()
