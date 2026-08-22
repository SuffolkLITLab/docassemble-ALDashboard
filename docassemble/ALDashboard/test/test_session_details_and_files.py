# do not pre-load
import ast
from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple
import zipfile
import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class _HelperModule:
    def __init__(self, namespace):
        object.__setattr__(self, "_namespace", namespace)

    def __getattr__(self, name):
        return self._namespace[name]

    def __setattr__(self, name, value):
        self._namespace[name] = value


def _load_session_detail_helpers():
    source = (PACKAGE_ROOT / "aldashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    names = {
        "get_session_details",
        "get_file_ids_associated_with_session",
        "get_files_associated_with_session",
        "build_session_files_zip",
        "download_file_by_id",
        "format_session_users",
        "get_allowed_interview_filenames",
        "get_permitted_session_details",
    }
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Set": Set,
        "Tuple": Tuple,
        "Union": Any,
        "ast": ast,
        "datetime": datetime,
        "tempfile": tempfile,
        "zipfile": zipfile,
        "re": __import__("re"),
        "os": __import__("os"),
        "_get_db_session": None,
        "get_info_from_file_number": lambda *args, **kwargs: {},
        "SavedFile": None,
        "get_ext_and_mimetype": lambda fn: (
            (fn.rsplit(".", 1)[-1], "application/octet-stream")
            if "." in fn
            else (None, None)
        ),
        "secure_filename_unicode_ok": lambda fn: fn,
        "secure_filename": lambda fn: fn,
        "send_file": lambda path, **kwargs: namedtuple(
            "FlaskResponse", ["headers", "path"]
        )({}, path),
        "get_users_and_name_by_ids": lambda ids: [
            (uid, f"user{uid}@example.com", f"First{uid}", f"Last{uid}") for uid in ids
        ],
        "log": lambda *args, **kwargs: None,
        "text": lambda value: value,
        "user_has_privilege": lambda privileges: False,
        "user_privileges": lambda: ["user"],
        "get_config": lambda key, default=None: default,
        "user_logged_in": lambda: False,
        "user_info": lambda: None,
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


aldashboard = _load_session_detail_helpers()
get_session_details = aldashboard.get_session_details
get_file_ids_associated_with_session = aldashboard.get_file_ids_associated_with_session
get_files_associated_with_session = aldashboard.get_files_associated_with_session
build_session_files_zip = aldashboard.build_session_files_zip
download_file_by_id = aldashboard.download_file_by_id
format_session_users = aldashboard.format_session_users
get_allowed_interview_filenames = aldashboard.get_allowed_interview_filenames
get_permitted_session_details = aldashboard.get_permitted_session_details


def test_get_session_details_success(monkeypatch):
    Row = namedtuple(
        "Row",
        [
            "filename",
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
    now = datetime(2026, 8, 22, 12, 0, 0)
    fake_row = Row(
        "docassemble.demo:interview.yml",
        1,
        "1,2",
        now,
        "test-session-key",
        "Auto Demo Title",
        "Custom Title",
        "Demo description",
        5,
        50.0,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params):
            assert params["session_id"] == "test-session-key"
            assert "WHERE userdict.key = :session_id" in query
            assert "jsonstorage.tags = 'metadata'" in query
            assert "ORDER BY userdict.modtime DESC" in query
            return namedtuple("Result", ["fetchone"])(lambda: fake_row)

    monkeypatch.setattr(aldashboard, "_get_db_session", FakeSession)

    details = get_session_details("test-session-key")
    assert details["filename"] == "docassemble.demo:interview.yml"
    assert details["key"] == "test-session-key"
    assert details["user_id"] == 1
    assert details["user_ids"] == "1,2"
    assert details["title"] == "Custom Title"
    assert details["auto_title"] == "Auto Demo Title"
    assert details["modtime"] == now


def test_get_session_details_not_found(monkeypatch):
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params):
            return namedtuple("Result", ["fetchone"])(lambda: None)

    monkeypatch.setattr(aldashboard, "_get_db_session", FakeSession)

    with pytest.raises(ValueError, match="No session found with ID: missing-key"):
        get_session_details("missing-key")


def test_get_session_details_empty_key():
    with pytest.raises(ValueError, match="Session ID must not be empty"):
        get_session_details("   ")


def test_get_file_ids_associated_with_session(monkeypatch):
    Row = namedtuple("Row", ["indexno"])
    rows = [Row(101), Row(102), Row(103)]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params):
            assert params["session_id"] == "session-abc"
            assert "WHERE key = :session_id" in query
            return rows

    monkeypatch.setattr(aldashboard, "_get_db_session", FakeSession)

    file_ids = get_file_ids_associated_with_session("session-abc")
    assert file_ids == [101, 102, 103]
    assert get_file_ids_associated_with_session("") == []


def test_get_files_associated_with_session(monkeypatch, tmp_path):
    f1 = tmp_path / "document.pdf"
    f1.write_text("fake pdf content")
    f2 = tmp_path / "data.docx"
    f2.write_text("fake docx content")

    Row = namedtuple("Row", ["indexno", "filename"])
    rows = [Row(201, "document.pdf"), Row(202, "data.docx")]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params):
            return rows

    def fake_get_info(file_id, **kwargs):
        if file_id == 201:
            return {
                "file_id": 201,
                "filename": "document.pdf",
                "path": str(f1),
                "fullpath": str(f1),
                "mimetype": "application/pdf",
                "extension": "pdf",
            }
        elif file_id == 202:
            return {
                "file_id": 202,
                "filename": "data.docx",
                "path": str(f2),
                "fullpath": str(f2),
                "mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "extension": "docx",
            }
        raise FileNotFoundError()

    monkeypatch.setattr(aldashboard, "_get_db_session", FakeSession)
    monkeypatch.setattr(aldashboard, "get_info_from_file_number", fake_get_info)

    files = get_files_associated_with_session("session-xyz")
    assert len(files) == 2
    assert files[0]["file_id"] == 201
    assert files[0]["filename"] == "document.pdf"
    assert files[0]["extension"] == "pdf"
    assert files[1]["file_id"] == 202
    assert files[1]["filename"] == "data.docx"


def test_build_session_files_zip(monkeypatch, tmp_path):
    f1 = tmp_path / "a.pdf"
    f1.write_text("content A")
    f2 = tmp_path / "a_duplicate.pdf"
    f2.write_text("content B")

    fake_files = [
        {"file_id": 1, "filename": "summary.pdf", "path": str(f1), "fullpath": str(f1)},
        {"file_id": 2, "filename": "summary.pdf", "path": str(f2), "fullpath": str(f2)},
    ]

    monkeypatch.setattr(
        aldashboard,
        "get_files_associated_with_session",
        lambda *args, **kwargs: fake_files,
    )

    zip_path = build_session_files_zip("session-test-zip")
    assert zip_path is not None
    assert Path(zip_path).is_file()

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "summary.pdf" in names
        assert "2-summary.pdf" in names
        assert zf.read("summary.pdf") == b"content A"
        assert zf.read("2-summary.pdf") == b"content B"

    Path(zip_path).unlink(missing_ok=True)


def test_download_file_by_id(monkeypatch, tmp_path):
    f = tmp_path / "testfile.pdf"
    f.write_text("sample content")

    fake_info = {
        "file_id": 55,
        "filename": "testfile.pdf",
        "path": str(f),
        "fullpath": str(f),
        "mimetype": "application/pdf",
        "extension": "pdf",
    }

    monkeypatch.setattr(
        aldashboard, "get_info_from_file_number", lambda *args, **kwargs: fake_info
    )

    # Test return_file_info = True
    info = download_file_by_id("55", return_file_info=True)
    assert info["file_id"] == 55
    assert info["path"] == str(f)
    assert info["mimetype"] == "application/pdf"

    # Test Flask send_file response
    resp = download_file_by_id("55", return_file_info=False)
    assert "FlaskResponse" in str(resp)


def test_format_session_users_with_dict():
    users_dict = {1: "Alice Admin", 2: "Bob Beneficiary"}

    # Single user_id in dict
    assert format_session_users({"user_id": 1}, users_dict) == "Alice Admin"

    # Multiple user_ids string in dict
    assert (
        format_session_users({"user_ids": "1,2"}, users_dict)
        == "Alice Admin, Bob Beneficiary"
    )

    # Multiple user_ids list in dict
    assert (
        format_session_users({"user_ids": [1, 2]}, users_dict)
        == "Alice Admin, Bob Beneficiary"
    )

    # Empty user
    assert format_session_users({"user_id": None}, users_dict) == "Anonymous"

    # Missing from cache, resolves lazily
    assert "user99@example.com" in format_session_users({"user_id": 99}, {})


def test_get_allowed_interview_filenames_is_none_for_privileged_users(monkeypatch):
    monkeypatch.setattr(aldashboard, "user_has_privilege", lambda privileges: True)

    assert get_allowed_interview_filenames() is None


def test_get_allowed_interview_filenames_uses_interview_viewers_config(monkeypatch):
    monkeypatch.setattr(aldashboard, "user_has_privilege", lambda privileges: False)
    monkeypatch.setattr(aldashboard, "user_privileges", lambda: ["user", "advocate"])
    monkeypatch.setattr(
        aldashboard,
        "get_config",
        lambda key, default=None: {
            "interview viewers": {
                "advocate": ["docassemble.MyPackage:data/questions/allowed.yml"],
                "other": ["docassemble.MyPackage:data/questions/forbidden.yml"],
            }
        },
    )

    assert get_allowed_interview_filenames() == {
        "docassemble.MyPackage:data/questions/allowed.yml"
    }


def test_get_permitted_session_details_enforces_the_allow_list(monkeypatch):
    monkeypatch.setattr(
        aldashboard,
        "get_session_details",
        lambda session_id: {
            "filename": "docassemble.MyPackage:data/questions/forbidden.yml",
            "key": session_id,
        },
    )

    # An unrestricted user sees the session.
    monkeypatch.setattr(aldashboard, "get_allowed_interview_filenames", lambda: None)
    assert get_permitted_session_details("some-session")["key"] == "some-session"

    # A user restricted to other interviews does not.
    monkeypatch.setattr(
        aldashboard,
        "get_allowed_interview_filenames",
        lambda: {"docassemble.MyPackage:data/questions/allowed.yml"},
    )
    assert get_permitted_session_details("some-session") is None

    # ...but does see a session for an interview they are allowed to view.
    monkeypatch.setattr(
        aldashboard,
        "get_allowed_interview_filenames",
        lambda: {"docassemble.MyPackage:data/questions/forbidden.yml"},
    )
    assert get_permitted_session_details("some-session")["key"] == "some-session"


def test_get_permitted_session_details_returns_none_for_missing_sessions(monkeypatch):
    def missing(session_id):
        raise ValueError(f"No session found with ID: {session_id}")

    monkeypatch.setattr(aldashboard, "get_session_details", missing)
    monkeypatch.setattr(aldashboard, "get_allowed_interview_filenames", lambda: None)

    assert get_permitted_session_details("no-such-session") is None


def test_format_session_users_only_looks_up_an_unknown_user_once(monkeypatch):
    lookups = []

    def fake_lookup(user_ids):
        lookups.append(list(user_ids))
        return []

    monkeypatch.setattr(aldashboard, "get_users_and_name_by_ids", fake_lookup)

    users_by_id = {}
    assert format_session_users({"user_id": 42}, users_by_id) == "User ID 42"
    assert format_session_users({"user_id": 42}, users_by_id) == "User ID 42"

    assert lookups == [[42]]
    assert users_by_id == {42: "User ID 42"}
