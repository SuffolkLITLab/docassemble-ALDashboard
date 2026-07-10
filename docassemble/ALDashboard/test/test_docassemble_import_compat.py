# do not pre-load
import ast
from pathlib import Path
import subprocess
import sys
import textwrap

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _imported_modules(filename: str) -> set[str]:
    tree = ast.parse((PACKAGE_ROOT / filename).read_text(encoding="utf-8"))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


def _legacy_imports_outside_fallback(filename: str) -> list[str]:
    tree = ast.parse((PACKAGE_ROOT / filename).read_text(encoding="utf-8"))
    legacy_modules = {
        "docassemble.webapp.app_object",
        "docassemble.webapp.db_object",
        "docassemble.webapp.server",
    }
    found: list[str] = []

    def visit(node: ast.AST, in_fallback: bool = False) -> None:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module in legacy_modules
            and not in_fallback
        ):
            found.append(node.module)
        for child in ast.iter_child_nodes(node):
            visit(child, in_fallback or isinstance(node, ast.ExceptHandler))

    visit(tree)
    return found


def test_new_docassemble_import_locations_are_covered():
    expected_modules = {
        "api_mcp.py": {
            "docassemble.webapp.flask_app",
            "docassemble.webapp.extensions",
            "docassemble.webapp.api.helpers",
            "docassemble.webapp.utils.helpers",
        },
        "api_dashboard.py": {
            "docassemble.webapp.flask_app",
            "docassemble.webapp.extensions",
            "docassemble.webapp.api.helpers",
            "docassemble.webapp.utils.helpers",
            "docassemble.webapp.interview.helpers",
            "docassemble.webapp.daredis",
            "docassemble.webapp.cron_tasks.cli",
        },
        "api_labelers.py": {
            "docassemble.webapp.flask_app",
            "docassemble.webapp.extensions",
            "docassemble.webapp.api.helpers",
            "docassemble.webapp.utils.helpers",
            "docassemble.webapp.daredis",
        },
        "api_dashboard_worker.py": {"docassemble.webapp.tasks.context"},
        "translation.py": {"docassemble.webapp.utils.helpers"},
        "aldashboard.py": {
            "docassemble.webapp.interview.models",
            "docassemble.webapp.utils.helpers",
            "docassemble.webapp.main.helpers",
            "docassemble.webapp.utils.hooks",
            "docassemble.webapp.daredis",
            "docassemble.base.hooks",
            "docassemble.webapp.cloud.utils",
        },
        "database_compat.py": {
            "docassemble.webapp.db",
        },
        "docassemble_compat.py": {
            "docassemble.webapp.files.savedfile",
            "docassemble.webapp.utils.filenames",
        },
    }

    for filename, expected in expected_modules.items():
        assert expected <= _imported_modules(filename)
        assert _legacy_imports_outside_fallback(filename) == []


def test_dashboard_database_access_uses_version_compatible_contexts():
    source = (PACKAGE_ROOT / "aldashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "with _get_db_session() as session:" in source
    assert "with _db_session_scope() as session:" in source
    assert "db.connect()" not in source
    assert "db.session.execute" not in source
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "query"
        for node in ast.walk(tree)
    )


def test_api_imports_fall_back_on_docassemble_1_9_layout():
    probe = textwrap.dedent("""
        import importlib
        import sys
        import types
        from flask import Flask

        flask_cors = types.ModuleType("flask_cors")
        flask_cors.cross_origin = lambda *args, **kwargs: lambda func: func
        sys.modules["flask_cors"] = flask_cors

        for module_name in (
            "docassemble.webapp.flask_app",
            "docassemble.webapp.extensions",
            "docassemble.webapp.api.helpers",
            "docassemble.webapp.utils.helpers",
        ):
            sys.modules[module_name] = None

        fake_app = Flask("legacy_import_test")
        app_object = types.ModuleType("docassemble.webapp.app_object")
        app_object.app = fake_app
        app_object.csrf = types.SimpleNamespace(exempt=lambda func: func)

        server = types.ModuleType("docassemble.webapp.server")
        server.api_verify = lambda: False
        server.jsonify_with_status = lambda body, status: (body, status)

        registry = types.ModuleType("docassemble.ALDashboard.mcp_registry")
        registry.MCP_API_BASE_PATH = "/al/api/v1/mcp"
        registry.get_discovered_tools = lambda: []
        registry.handle_jsonrpc_request = lambda payload: ({}, 200)

        sys.modules["docassemble.webapp.app_object"] = app_object
        sys.modules["docassemble.webapp.server"] = server
        sys.modules["docassemble.ALDashboard.mcp_registry"] = registry
        sys.modules.pop("docassemble.ALDashboard.api_mcp", None)

        module = importlib.import_module("docassemble.ALDashboard.api_mcp")
        assert module.app is fake_app
        assert module.api_verify() is False
    """)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_database_compat_falls_back_to_docassemble_1_9_session_api():
    probe = textwrap.dedent("""
        import importlib
        import sys
        import types

        events = []

        class LegacySession:
            def commit(self):
                events.append("commit")

            def rollback(self):
                events.append("rollback")

        legacy_db = types.SimpleNamespace(session=LegacySession())
        db_object = types.ModuleType("docassemble.webapp.db_object")
        db_object.init_sqlalchemy = lambda: legacy_db

        files = types.ModuleType("docassemble.webapp.files")
        files.__path__ = []
        files.SavedFile = object
        backend = types.ModuleType("docassemble.webapp.backend")
        backend.directory_for = lambda area, project: (area, project)

        sys.modules["docassemble.webapp.db"] = None
        sys.modules["docassemble.webapp.files.savedfile"] = None
        sys.modules["docassemble.webapp.utils"] = None
        sys.modules["docassemble.webapp.db_object"] = db_object
        sys.modules["docassemble.webapp.files"] = files
        sys.modules["docassemble.webapp.backend"] = backend
        sys.modules.pop("docassemble.ALDashboard.database_compat", None)

        compat = importlib.import_module("docassemble.ALDashboard.database_compat")
        with compat.get_database_session() as session:
            assert session is legacy_db.session
        with compat.database_session_scope() as session:
            assert session is legacy_db.session
        assert events == ["commit"]

        try:
            with compat.database_session_scope():
                raise RuntimeError("test")
        except RuntimeError:
            pass
        assert events == ["commit", "rollback"]
    """)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_database_compat_prefers_docassemble_1_10_session_api():
    probe = textwrap.dedent("""
        import importlib
        import sys
        import types

        modern_db = types.ModuleType("docassemble.webapp.db")
        modern_db.get_session = object()
        modern_db.session_scope = object()
        sys.modules["docassemble.webapp.db"] = modern_db
        sys.modules.pop("docassemble.ALDashboard.database_compat", None)

        compat = importlib.import_module("docassemble.ALDashboard.database_compat")
        assert compat.get_database_session is modern_db.get_session
        assert compat.database_session_scope is modern_db.session_scope
    """)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_file_compat_falls_back_to_docassemble_1_9_layout():
    probe = textwrap.dedent("""
        import importlib
        import sys
        import types

        legacy_saved_file = object()
        legacy_directory_for = object()
        files = types.ModuleType("docassemble.webapp.files")
        files.__path__ = []
        files.SavedFile = legacy_saved_file
        backend = types.ModuleType("docassemble.webapp.backend")
        backend.directory_for = legacy_directory_for

        sys.modules["docassemble.webapp.files.savedfile"] = None
        sys.modules["docassemble.webapp.utils"] = None
        sys.modules["docassemble.webapp.files"] = files
        sys.modules["docassemble.webapp.backend"] = backend
        sys.modules.pop("docassemble.ALDashboard.docassemble_compat", None)

        compat = importlib.import_module("docassemble.ALDashboard.docassemble_compat")
        assert compat.SavedFile is legacy_saved_file
        assert compat.directory_for is legacy_directory_for
    """)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
