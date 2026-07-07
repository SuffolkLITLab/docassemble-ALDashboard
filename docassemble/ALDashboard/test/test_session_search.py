# do not pre-load
import importlib
import sys
import types
from collections import namedtuple
from unittest.mock import patch


class _DummyDB:
    def connect(self):
        raise RuntimeError("Database access is not available in this unit test.")


def _stub_module(name, **attrs):
    module = types.ModuleType(name)
    for attr_name, attr_value in attrs.items():
        setattr(module, attr_name, attr_value)
    return module


_IMPORT_STUBS = {
    "docassemble.webapp.users": _stub_module("docassemble.webapp.users", __path__=[]),
    "docassemble.webapp.users.models": _stub_module(
        "docassemble.webapp.users.models",
        UserModel=object,
        Role=object,
        UserDict=object,
        UserRoles=object,
    ),
    "docassemble.webapp.db_object": _stub_module(
        "docassemble.webapp.db_object",
        init_sqlalchemy=lambda: _DummyDB(),
    ),
    "github": _stub_module("github", Github=object),
    "flask": _stub_module("flask", current_app=None),
    "sqlalchemy.sql": _stub_module("sqlalchemy.sql", text=lambda value: value),
    "sqlalchemy": _stub_module("sqlalchemy", or_=lambda *args: args),
    "sqlalchemy.orm": _stub_module(
        "sqlalchemy.orm",
        joinedload=lambda *args, **kwargs: None,
    ),
    "docassemble.webapp.worker": _stub_module("docassemble.webapp.worker"),
    "docassemble.webapp.server": _stub_module(
        "docassemble.webapp.server",
        user_can_edit_package=lambda *args, **kwargs: False,
        get_master_branch=lambda *args, **kwargs: "",
        install_git_package=lambda *args, **kwargs: None,
        redirect=lambda *args, **kwargs: None,
        should_run_create=lambda *args, **kwargs: False,
        flash=lambda *args, **kwargs: None,
        url_for=lambda *args, **kwargs: "",
        restart_all=lambda *args, **kwargs: None,
        install_pip_package=lambda *args, **kwargs: None,
        get_package_info=lambda *args, **kwargs: {},
        get_session_variables=lambda *args, **kwargs: {},
    ),
    "docassemble.webapp.backend": _stub_module("docassemble.webapp.backend", cloud=None),
    "docassemble.base.config": _stub_module("docassemble.base.config", daconfig={}),
    "docassemble.base.functions": _stub_module(
        "docassemble.base.functions",
        serializable_dict=lambda value, **kwargs: value,
    ),
    "docassemble.base.util": _stub_module(
        "docassemble.base.util",
        log=lambda *args, **kwargs: None,
        DAFile=object,
        DAObject=object,
        DAList=list,
        word=lambda value: value,
        DAFileList=list,
        get_config=lambda *args, **kwargs: {},
        user_has_privilege=lambda *args, **kwargs: False,
        DACloudStorage=object,
        user_info=lambda: None,
        user_logged_in=lambda: False,
        get_user_info=lambda *args, **kwargs: {},
    ),
    "docassemble.webapp.files": _stub_module(
        "docassemble.webapp.files",
        SavedFile=object,
    ),
}

with patch.dict(sys.modules, _IMPORT_STUBS):
    aldashboard = importlib.import_module("docassemble.ALDashboard.aldashboard")
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

    assert resolve_session_variable(variables, 'legalserver_data["case_number"]') == "12345"
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
        Row("pkg:data/questions/a.yml", 3, 1, "1,7", "2026-01-02", "abc", "", "A", "", "", ""),
        Row("pkg:data/questions/a.yml", 4, 1, "1", "2026-01-03", "def", "", "B", "", "", ""),
    ]
    executed = {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            executed["query"] = args[0]
            return rows

    class DB:
        def connect(self):
            return Connection()

    monkeypatch.setattr(aldashboard, "db", DB())

    sessions = speedy_get_sessions(filename="pkg:data/questions/a.yml")

    assert [session.key for session in sessions] == ["abc", "def"]
    assert "MIN(user_id) AS user_id" in executed["query"]
    assert "STRING_AGG(DISTINCT CAST(user_id AS TEXT), ',') AS user_ids" in executed["query"]
    assert ") joined_users ON joined_users.key = userdict.key" in executed["query"]


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
        Row("pkg:data/questions/a.yml", 3, 1, "1", "2026-01-02", "abc", "", "A", "", "", ""),
        Row("pkg:data/questions/a.yml", 4, 1, "1", "2026-01-03", "def", "", "B", "", "", ""),
    ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return rows

    class DB:
        def connect(self):
            return Connection()

    monkeypatch.setattr(aldashboard, "db", DB())
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
        Row("pkg:data/questions/a.yml", 3, 1, "1", "2026-01-02", "bad", "", "Bad", "", "", ""),
        Row("pkg:data/questions/a.yml", 4, 1, "1", "2026-01-03", "abc", "", "A", "", "", ""),
    ]
    log_messages = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return rows

    class DB:
        def connect(self):
            return Connection()

    def get_variables(filename, session_id, **kwargs):
        if session_id == "bad":
            raise Exception("Unable to decrypt interview dictionary: could not find MARK")
        return {"client": {"last_name": "Smith"}}

    monkeypatch.setattr(aldashboard, "db", DB())
    monkeypatch.setattr(aldashboard, "get_session_variables", get_variables)
    monkeypatch.setattr(aldashboard, "log", log_messages.append)

    sessions = speedy_get_sessions(
        filename="pkg:data/questions/a.yml",
        search_criteria_text="client.last_name = smith",
    )

    assert [session.key for session in sessions] == ["abc"]
    assert "could not find MARK" in log_messages[0]
