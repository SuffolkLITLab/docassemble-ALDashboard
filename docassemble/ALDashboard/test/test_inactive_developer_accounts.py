import ast
import calendar
from datetime import datetime, timezone
from pathlib import Path

from ruamel.yaml import YAML

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _extract_functions(names):
    source = (PACKAGE_ROOT / "aldashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    return {
        node.name: "".join(lines[node.lineno - 1 : node.end_lineno])
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    }


def _event_code(event_name):
    yaml = YAML(typ="safe")
    path = PACKAGE_ROOT / "data/questions/inactive_developer_accounts.yml"
    for document in yaml.load_all(path.read_text(encoding="utf-8")):
        if isinstance(document, dict) and document.get("event") == event_name:
            return document["code"]
    raise AssertionError(f"Event not found: {event_name}")


def test_datetime_helpers_use_utc():
    functions = _extract_functions({"_epoch_to_datetime", "_months_ago"})
    namespace = {
        "Optional": __import__("typing").Optional,
        "datetime": datetime,
        "timezone": timezone,
        "calendar": calendar,
    }
    for source in functions.values():
        exec(source, namespace)

    assert namespace["_epoch_to_datetime"](0) == datetime(1970, 1, 1)
    cutoff = namespace["_months_ago"](1)
    assert cutoff.tzinfo is None
    assert cutoff <= datetime.now(timezone.utc).replace(tzinfo=None)


def test_developer_query_filters_in_sql():
    source = _extract_functions({"_developer_user_rows"})["_developer_user_rows"]
    assert ".join(UserRoles" in source
    assert ".join(Role" in source
    assert 'Role.name == "developer"' in source


def test_report_background_event_clamps_invalid_months():
    responses = []
    arguments = {"months": "bad", "requesting_user_id": "42"}
    namespace = {
        "action_argument": arguments.get,
        "background_response": responses.append,
        "inactive_developer_account_report": lambda months, requesting_user_id: [
            months,
            requesting_user_id,
        ],
        "inactive_developer_login_summary": lambda months: {"months": months},
    }

    exec(_event_code("generate_inactive_developer_report"), namespace)

    assert responses == [{"candidates": [1, 42], "login_summary": {"months": 1}}]


def test_delete_background_event_returns_structured_invalid_request():
    responses = []
    arguments = {
        "months": None,
        "requesting_user_id": "42",
        "delete_shared": False,
        "user_ids": [99],
    }
    namespace = {
        "action_argument": arguments.get,
        "background_response": responses.append,
        "delete_inactive_developer_accounts": lambda *args, **kwargs: None,
    }

    exec(
        _event_code("delete_inactive_developer_accounts_in_background"),
        namespace,
    )

    assert responses == [
        {
            "deleted_count": 0,
            "skipped": [],
            "restart_requested": False,
            "error_type": "Invalid deletion request",
            "error_message": "Invalid or missing months value: None",
        }
    ]
