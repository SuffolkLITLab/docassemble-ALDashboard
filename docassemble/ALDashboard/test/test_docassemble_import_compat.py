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
            "docassemble.webapp.db",
            "docassemble.webapp.extensions",
            "docassemble.webapp.utils.helpers",
            "docassemble.webapp.main.helpers",
            "docassemble.webapp.utils.hooks",
            "docassemble.webapp.daredis",
            "docassemble.base.hooks",
            "docassemble.webapp.cloud.utils",
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
