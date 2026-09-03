# do not pre-load
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Set

from ruamel.yaml import YAML

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _function_sources(names):
    source = (PACKAGE_ROOT / "aldashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    return {
        node.name: "".join(lines[node.lineno - 1 : node.end_lineno])
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    }


def _load_permission_helpers():
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "Set": Set,
        "user_logged_in": lambda: True,
        "user_has_privilege": lambda roles: False,
        "user_info": lambda: SimpleNamespace(permissions=[]),
        "is_user_privileged": lambda user_id: False,
        "_resolve_current_user_id": lambda: 7,
    }
    sources = _function_sources(
        {"_current_user_permissions", "can_access_user_sessions"}
    )
    for name in ("_current_user_permissions", "can_access_user_sessions"):
        exec(sources[name], namespace)
    return namespace


def test_support_user_session_access_requires_both_permissions():
    namespace = _load_permission_helpers()
    namespace["user_info"] = lambda: SimpleNamespace(
        permissions=["access_user_info", "access_sessions"]
    )

    assert namespace["can_access_user_sessions"](42) is True

    namespace["user_info"] = lambda: SimpleNamespace(permissions=["access_user_info"])
    assert namespace["can_access_user_sessions"](42) is False


def test_user_can_still_inspect_their_own_allowed_interview_session():
    namespace = _load_permission_helpers()

    assert namespace["can_access_user_sessions"](7) is True


def test_support_user_cannot_inspect_a_protected_account():
    namespace = _load_permission_helpers()
    namespace["user_info"] = lambda: SimpleNamespace(
        permissions=["access_user_info", "access_sessions"]
    )
    namespace["is_user_privileged"] = lambda user_id: True

    assert namespace["can_access_user_sessions"](42) is False


def test_admin_can_inspect_a_protected_account():
    namespace = _load_permission_helpers()
    namespace["user_has_privilege"] = lambda roles: "admin" in roles
    namespace["is_user_privileged"] = lambda user_id: True

    assert namespace["can_access_user_sessions"](42) is True


def test_send_reset_email_uses_docassemble_user_manager():
    sent_to = []
    user = SimpleNamespace(email="client@example.com")

    class Statement:
        def where(self, *args):
            return self

    class UserColumn:
        def __eq__(self, other):
            return ("id", other)

    class UserModel:
        id = UserColumn()

    class Result:
        def scalar_one_or_none(self):
            return user

    @contextmanager
    def database_session():
        yield SimpleNamespace(execute=lambda statement: Result())

    manager = SimpleNamespace(
        enable_forgot_password=True,
        send_reset_password_email=sent_to.append,
    )
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "UserModel": UserModel,
        "current_app": SimpleNamespace(
            user_manager=manager,
            config={"ALLOW_CHANGING_PASSWORD": True},
        ),
        "_current_user_permissions": lambda: {"edit_user_password"},
        "_get_db_session": database_session,
        "is_user_privileged": lambda user_id: False,
        "log": lambda *args: None,
        "select": lambda model: Statement(),
        "user_has_privilege": lambda roles: False,
        "user_logged_in": lambda: True,
    }
    source = _function_sources({"send_password_reset_email"})[
        "send_password_reset_email"
    ]
    exec(source, namespace)

    result = namespace["send_password_reset_email"](42)

    assert result == {
        "sent": True,
        "message": "The password reset email was sent.",
    }
    assert sent_to == ["client@example.com"]


def test_manage_user_page_links_to_prefiltered_session_viewer():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    view_screen = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("event") == "view_user_info_screen"
    )

    assert "Last login" in view_screen["subquestion"]
    assert "Last interview activity" in view_screen["subquestion"]
    assert 'url_action("view_user_sessions_screen")' in view_screen["under"]
    assert "can_access_user_sessions" in view_screen["under"]


def test_manage_user_picker_adapts_to_server_size():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    picker = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("id") == "select user and management task"
    )
    search_event = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("event") == "manage_user_search_ajax"
    )

    user_fields = [
        field for field in picker["fields"] if field.get("User") == "chosen_user"
    ]
    assert len(user_fields) == 2
    assert user_fields[0]["input type"] == "combobox"
    assert user_fields[0]["show if"]["code"].strip() == "manage_user_count <= 200"
    assert user_fields[1]["input type"] == "ajax"
    assert user_fields[1]["action"] == "manage_user_search_ajax"
    assert user_fields[1]["show if"]["code"].strip() == "manage_user_count > 200"
    assert "search_users_by_email" in search_event["code"]
    assert "exclude_privileged=" in search_event["code"]


def test_user_and_task_are_gathered_together_without_server_side_cycle():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    user_screen = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("id") == "select user and management task"
    )
    period_screen = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("id") == "select recent activity period"
    )

    assert any(
        field.get("What do you want to do?") == "user_task"
        for field in user_screen["fields"]
    )
    assert "recent_activity" in str(user_screen["fields"])
    assert not any(
        isinstance(field.get("show if"), dict)
        and field["show if"].get("variable") == "user_task"
        for field in user_screen["fields"]
    )
    assert (
        period_screen["fields"][0]["Days of activity to include"]
        == "recent_activity_days"
    )


def test_reset_email_confirmation_is_a_data_gathering_screen_not_an_event():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    confirmation = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("id") == "confirm send reset email"
    )
    start_event = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("event") == "start_send_reset_email"
    )

    assert confirmation["field"] == "confirm_reset_email_delivery"
    assert "event" not in confirmation
    assert "confirm_reset_email_delivery" in start_event["code"]


def test_user_centered_session_view_reuses_answer_viewer_actions():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    user_session_action = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("event") == "view_user_sessions_screen"
    )
    session_screen = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("event") == "view_managed_sessions_screen"
    )

    assert "managed_user_sessions" in user_session_action["code"]
    assert "managed_sessions" in session_screen["subquestion"]
    assert "list_sessions.yml" in session_screen["subquestion"]
    assert "view_single_session" in session_screen["subquestion"]
    assert "view_session_variables" in session_screen["subquestion"]
    assert 'target="_blank"' in session_screen["subquestion"]
    assert "redirect(" not in path.read_text(encoding="utf-8")


def test_recent_activity_report_links_registered_users_to_user_info():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    report_screen = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("event") == "recent_activity_report_screen"
    )
    user_action = next(
        document
        for document in documents
        if isinstance(document, dict) and document.get("event") == "view_activity_user"
    )

    assert "Registered users who logged in" in report_screen["subquestion"]
    assert "temp_user_id" in report_screen["subquestion"]
    assert "view_activity_user" in report_screen["subquestion"]
    assert "recent_session_count" in report_screen["subquestion"]
    assert "last_session_activity" in report_screen["subquestion"]
    assert "view_anonymous_sessions_screen" in report_screen["subquestion"]
    assert "is_user_privileged(chosen_user)" in user_action["code"]


def test_recent_activity_report_is_permission_gated_and_groups_anonymous_users():
    source = _function_sources({"recent_account_activity_report"})[
        "recent_account_activity_report"
    ]
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "can_access_user_sessions": lambda: False,
    }
    exec(source, namespace)

    assert namespace["recent_account_activity_report"]() == {
        "authorized": False,
        "users": [],
        "anonymous": [],
    }
    assert "GROUP BY userdictkeys.temp_user_id" in source
    assert "userdict.modtime >= :cutoff" in source
    assert "LIMIT :anonymous_limit" in source
    assert "COUNT(DISTINCT userdictkeys.key) AS recent_session_count" in source
    assert ".order_by(UserModel.last_login.desc())" in source
    assert "except (TypeError, ValueError):\n        user_limit = 200" in source
    assert "except (TypeError, ValueError):\n        anonymous_limit = 100" in source


def test_recent_activity_user_action_rejects_invalid_user_ids():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    user_action = next(
        document
        for document in documents
        if isinstance(document, dict) and document.get("event") == "view_activity_user"
    )

    assert "except (TypeError, ValueError)" in user_action["code"]
    assert "response_code=400" in user_action["code"]


def test_reset_email_confirmation_labels_an_account_without_email():
    path = PACKAGE_ROOT / "data" / "questions" / "manage_users.yml"
    documents = list(YAML(typ="safe").load_all(path.read_text(encoding="utf-8")))
    confirmation = next(
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("id") == "confirm send reset email"
    )

    assert "no email address" in confirmation["subquestion"]
