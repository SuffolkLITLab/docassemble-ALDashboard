# do not pre-load
import ast
from collections import namedtuple
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@contextmanager
def _unavailable_database_session():
    raise RuntimeError("Database access is not available in this unit test.")
    yield


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class _HelperModule:
    def __init__(self, namespace):
        object.__setattr__(self, "_namespace", namespace)

    def __getattr__(self, name):
        return self._namespace[name]

    def __setattr__(self, name, value):
        self._namespace[name] = value


def _load_session_search_helpers():
    source = (PACKAGE_ROOT / "aldashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    names = {
        "SessionSearchCriteriaError",
        "_SessionSearchPathPart",
        "_split_session_search_criterion",
        "_parse_session_search_path",
        "parse_session_search_criteria",
        "build_session_search_criteria_text",
        "resolve_session_variable",
        "_display_session_value",
        "_session_matches_criteria",
        "_iso_date_text",
        "format_session_users",
        "speedy_get_sessions",
    }
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Set": Set,
        "Tuple": Tuple,
        "ast": ast,
        "date": date,
        "_get_db_session": _unavailable_database_session,
        "get_session_variables": lambda *args, **kwargs: {},
        "log": lambda *args, **kwargs: None,
        "text": lambda value: value,
        "user_has_privilege": lambda privileges: False,
    }
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names:
            exec("".join(lines[node.lineno - 1 : node.end_lineno]), namespace)
            found.add(node.name)
    missing = names - found
    if missing:
        raise AssertionError(f"Missing helpers in aldashboard.py: {sorted(missing)}")
    return _HelperModule(namespace)


aldashboard = _load_session_search_helpers()
SessionSearchCriteriaError = aldashboard.SessionSearchCriteriaError
build_session_search_criteria_text = aldashboard.build_session_search_criteria_text
format_session_users = aldashboard.format_session_users
parse_session_search_criteria = aldashboard.parse_session_search_criteria
resolve_session_variable = aldashboard.resolve_session_variable
speedy_get_sessions = aldashboard.speedy_get_sessions


def test_build_session_search_criteria_text_from_simple_fields():
    criteria_text = build_session_search_criteria_text(
        'legalserver_data["case_number"]',
        "12345",
    )

    assert criteria_text == 'legalserver_data["case_number"] = 12345'
    assert parse_session_search_criteria(criteria_text) == [
        {"path": 'legalserver_data["case_number"]', "query": "12345"}
    ]


def test_build_session_search_criteria_text_uses_advanced_text():
    criteria_text = build_session_search_criteria_text(
        "ignored",
        "ignored",
        use_advanced_filters=True,
        advanced_criteria_text="clients[0].name.last = Smith\ndocket_number = 123",
    )

    assert criteria_text == "clients[0].name.last = Smith\ndocket_number = 123"
    assert parse_session_search_criteria(criteria_text) == [
        {"path": "clients[0].name.last", "query": "Smith"},
        {"path": "docket_number", "query": "123"},
    ]


def test_resolve_session_variable_supports_names_indexes_and_keys():
    variables = {
        "clients": [
            {
                "name": {
                    "last": "Smith",
                }
            }
        ],
        "legalserver_data": {"case_number": "12345"},
    }

    assert (
        resolve_session_variable(variables, 'legalserver_data["case_number"]')
        == "12345"
    )
    assert resolve_session_variable(variables, "clients[0].name.last") == "Smith"


def test_parse_session_search_criteria_rejects_unsafe_paths():
    try:
        parse_session_search_criteria("__import__ = os")
    except SessionSearchCriteriaError as error:
        assert "Unsafe variable path" in str(error)
    else:
        raise AssertionError("Expected unsafe path to be rejected")


def test_format_session_users_lists_all_joined_users():
    Row = namedtuple("Row", ["user_id", "user_ids"])
    users_by_id = {
        1: "admin@example.com Admin User",
        7: "client@example.com Client User",
    }

    assert (
        format_session_users(Row(1, "1,7"), users_by_id)
        == "admin@example.com Admin User, client@example.com Client User"
    )
    assert format_session_users(Row(None, None), users_by_id) == "Anonymous"
    assert format_session_users(Row(42, "42"), users_by_id) == "User ID 42"


def test_speedy_get_sessions_groups_user_rows_in_sql(monkeypatch):
    Row = namedtuple(
        "Row",
        [
            "filename",
            "num_keys",
            "user_id",
            "user_ids",
            "modtime",
            "key",
            "auto_title",
            "title",
            "description",
            "steps",
            "progress",
        ],
    )
    rows = [
        Row(
            "pkg:data/questions/a.yml",
            3,
            1,
            "1,7",
            "2026-01-02",
            "abc",
            "",
            "A",
            "",
            "",
            "",
        ),
        Row(
            "pkg:data/questions/a.yml",
            4,
            1,
            "1",
            "2026-01-03",
            "def",
            "",
            "B",
            "",
            "",
            "",
        ),
    ]
    executed = {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            executed["query"] = args[0]
            executed["params"] = args[1]
            return rows

    monkeypatch.setattr(aldashboard, "_get_db_session", Connection)

    sessions = speedy_get_sessions(
        filename="pkg:data/questions/a.yml",
        start_date="2026-01-01",
        end_date="2026-01-31",
    )

    assert [session.key for session in sessions] == ["abc", "def"]
    assert "MIN(user_id) AS user_id" in executed["query"]
    assert (
        "STRING_AGG(DISTINCT CAST(user_id AS TEXT), ',') AS user_ids"
        in executed["query"]
    )
    assert ") joined_users ON joined_users.key = userdict.key" in executed["query"]
    assert "DATE(mostrecent.modtime) >= CAST(:start_date AS DATE)" in executed["query"]
    assert "DATE(mostrecent.modtime) <= CAST(:end_date AS DATE)" in executed["query"]
    assert executed["params"]["start_date"] == "2026-01-01"
    assert executed["params"]["end_date"] == "2026-01-31"


def test_speedy_get_sessions_can_filter_by_answer_criteria(monkeypatch):
    Row = namedtuple(
        "Row",
        [
            "filename",
            "num_keys",
            "user_id",
            "user_ids",
            "modtime",
            "key",
            "auto_title",
            "title",
            "description",
            "steps",
            "progress",
        ],
    )
    rows = [
        Row(
            "pkg:data/questions/a.yml",
            3,
            1,
            "1",
            "2026-01-02",
            "abc",
            "",
            "A",
            "",
            "",
            "",
        ),
        Row(
            "pkg:data/questions/a.yml",
            4,
            1,
            "1",
            "2026-01-03",
            "def",
            "",
            "B",
            "",
            "",
            "",
        ),
    ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return rows

    monkeypatch.setattr(aldashboard, "_get_db_session", Connection)
    monkeypatch.setattr(
        aldashboard,
        "get_session_variables",
        lambda filename, session_id, **kwargs: {
            "client": {"last_name": "Smith" if session_id == "abc" else "Jones"}
        },
    )

    sessions = speedy_get_sessions(
        filename="pkg:data/questions/a.yml",
        search_criteria_text="client.last_name = smith",
    )

    assert [session.key for session in sessions] == ["abc"]


def test_speedy_get_sessions_skips_unloadable_sessions_during_search(monkeypatch):
    Row = namedtuple(
        "Row",
        [
            "filename",
            "num_keys",
            "user_id",
            "user_ids",
            "modtime",
            "key",
            "auto_title",
            "title",
            "description",
            "steps",
            "progress",
        ],
    )
    rows = [
        Row(
            "pkg:data/questions/a.yml",
            3,
            1,
            "1",
            "2026-01-02",
            "bad",
            "",
            "Bad",
            "",
            "",
            "",
        ),
        Row(
            "pkg:data/questions/a.yml",
            4,
            1,
            "1",
            "2026-01-03",
            "abc",
            "",
            "A",
            "",
            "",
            "",
        ),
    ]
    log_messages = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return rows

    def get_variables(filename, session_id, **kwargs):
        if session_id == "bad":
            raise Exception(
                "Unable to decrypt interview dictionary: could not find MARK"
            )
        return {"client": {"last_name": "Smith"}}

    monkeypatch.setattr(aldashboard, "_get_db_session", Connection)
    monkeypatch.setattr(aldashboard, "get_session_variables", get_variables)
    monkeypatch.setattr(aldashboard, "log", log_messages.append)

    sessions = speedy_get_sessions(
        filename="pkg:data/questions/a.yml",
        search_criteria_text="client.last_name = smith",
    )

    assert [session.key for session in sessions] == ["abc"]
    assert "could not find MARK" in log_messages[0]
