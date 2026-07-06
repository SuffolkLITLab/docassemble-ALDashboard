from datetime import date

from docassemble.ALDashboard.session_search import (
    build_session_search_criteria_text,
    build_session_sql_filters_text,
    parse_session_search_criteria,
    parse_session_sql_filters,
)


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


def test_build_session_sql_filters_text_from_hardcoded_fields():
    filters_text = build_session_sql_filters_text(
        skip_first_step_sessions=True,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
    )

    assert filters_text == "steps >= 2\nmodified >= 2026-01-01\nmodified <= 2026-06-30"
    assert parse_session_sql_filters(filters_text) == [
        {"field": "steps", "operator": ">=", "value": 2},
        {"field": "modified", "operator": ">=", "value": "2026-01-01"},
        {"field": "modified", "operator": "<=", "value": "2026-06-30"},
    ]


def test_build_session_sql_filters_text_can_skip_all_filters():
    assert build_session_sql_filters_text(skip_first_step_sessions=False) == ""
    assert parse_session_sql_filters("") == []
