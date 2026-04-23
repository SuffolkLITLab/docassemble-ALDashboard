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


_install_docassemble_webapp_stubs()
