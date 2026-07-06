"""Search saved docassemble sessions by values in their interview dictionaries."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

__all__ = [
    "SessionSearchCriteriaError",
    "build_session_search_criteria_text",
    "build_session_sql_filters_text",
    "parse_session_search_criteria",
    "parse_session_sql_filters",
    "resolve_session_variable",
    "search_interview_sessions",
]


class SessionSearchCriteriaError(ValueError):
    """Raised when a session-search criterion is malformed or unsafe."""


@dataclass(frozen=True)
class _PathPart:
    kind: str
    value: Any


def _split_criterion(line: str) -> tuple[str, str]:
    """Split ``path = value`` at an equals sign outside quotes and brackets."""
    quote: Optional[str] = None
    escaped = False
    depth = 0
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quote:
            if character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "=" and depth == 0:
            return line[:index].strip(), line[index + 1 :].strip()
    raise SessionSearchCriteriaError(
        "Each filter must use the format variable_name = partial text."
    )


def _parse_path(path: str) -> tuple[_PathPart, ...]:
    try:
        node = ast.parse(path, mode="eval").body
    except (SyntaxError, ValueError) as error:
        raise SessionSearchCriteriaError(f"Invalid variable path: {path}") from error

    parts: list[_PathPart] = []

    def visit(current: ast.AST) -> None:
        if isinstance(current, ast.Name):
            if current.id.startswith("_"):
                raise SessionSearchCriteriaError(f"Unsafe variable path: {path}")
            parts.append(_PathPart("name", current.id))
            return
        if isinstance(current, ast.Attribute):
            visit(current.value)
            if current.attr.startswith("_"):
                raise SessionSearchCriteriaError(f"Unsafe variable path: {path}")
            parts.append(_PathPart("attribute", current.attr))
            return
        if isinstance(current, ast.Subscript):
            visit(current.value)
            slice_node = current.slice
            if isinstance(slice_node, ast.Constant) and isinstance(
                slice_node.value, (str, int)
            ):
                parts.append(_PathPart("item", slice_node.value))
                return
            raise SessionSearchCriteriaError(
                f"Only quoted keys and integer indexes are allowed in: {path}"
            )
        raise SessionSearchCriteriaError(
            f"Only names, attributes, quoted keys, and integer indexes are allowed in: {path}"
        )

    visit(node)
    return tuple(parts)


def parse_session_search_criteria(criteria_text: str) -> list[dict[str, str]]:
    """Parse one case-insensitive partial-match criterion per nonblank line."""
    criteria: list[dict[str, str]] = []
    for line_number, raw_line in enumerate(str(criteria_text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            path, query = _split_criterion(line)
            if not path or not query:
                raise SessionSearchCriteriaError(
                    "Both the variable path and partial text are required."
                )
            _parse_path(path)
        except SessionSearchCriteriaError as error:
            raise SessionSearchCriteriaError(f"Line {line_number}: {error}") from error
        criteria.append({"path": path, "query": query})
    if not criteria:
        raise SessionSearchCriteriaError("Enter at least one filter.")
    return criteria


def build_session_search_criteria_text(
    variable_name: str,
    variable_value: str,
    *,
    use_advanced_filters: bool = False,
    advanced_criteria_text: str = "",
) -> str:
    """Return the criteria text submitted to the session-search parser."""
    if use_advanced_filters:
        return str(advanced_criteria_text or "").strip()
    return f"{str(variable_name or '').strip()} = {str(variable_value or '').strip()}"


_SQL_FILTER_PATTERN = re.compile(
    r"^(created|creation_date|modified|modified_date|steps|num_steps|number_of_steps|user_id)"
    r"\s*(<=|>=|!=|=|<|>)\s*(.+)$",
    re.IGNORECASE,
)
_SQL_FILTER_NAMES = {
    "created": "created",
    "creation_date": "created",
    "modified": "modified",
    "modified_date": "modified",
    "steps": "steps",
    "num_steps": "steps",
    "number_of_steps": "steps",
    "user_id": "user_id",
}


def parse_session_sql_filters(filters_text: str) -> list[dict[str, Any]]:
    """Parse allowlisted filters that can be applied by the session SQL query."""
    filters: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(str(filters_text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        match = _SQL_FILTER_PATTERN.fullmatch(line)
        if not match:
            raise SessionSearchCriteriaError(
                f"SQL filter line {line_number}: use field operator value; for example, "
                "modified >= 2026-01-01."
            )
        supplied_field, operator, supplied_value = match.groups()
        field = _SQL_FILTER_NAMES[supplied_field.casefold()]
        value_text = supplied_value.strip()
        try:
            if field in {"steps", "user_id"}:
                value: Any = int(value_text)
                if value < 0 or (field == "steps" and value < 1):
                    raise ValueError
            else:
                value = date.fromisoformat(value_text).isoformat()
        except ValueError as error:
            expected = "a whole number" if field in {"steps", "user_id"} else "YYYY-MM-DD"
            raise SessionSearchCriteriaError(
                f"SQL filter line {line_number}: {field} requires {expected}."
            ) from error
        filters.append({"field": field, "operator": operator, "value": value})
    return filters


def _iso_date_text(value: Any) -> str:
    if not value:
        return ""
    if hasattr(value, "format_date"):
        return value.format_date("yyyy-MM-dd")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def build_session_sql_filters_text(
    *,
    skip_first_step_sessions: bool = True,
    start_date: Any = None,
    end_date: Any = None,
) -> str:
    """Build SQL-filter text from the simple interview fields."""
    filters: list[str] = []
    if skip_first_step_sessions:
        filters.append("steps >= 2")
    start_date_text = _iso_date_text(start_date)
    if start_date_text:
        filters.append(f"modified >= {start_date_text}")
    end_date_text = _iso_date_text(end_date)
    if end_date_text:
        filters.append(f"modified <= {end_date_text}")
    return "\n".join(filters)


def resolve_session_variable(variables: Mapping[str, Any], path: str) -> Any:
    """Resolve a restricted Python-style path without using ``eval``."""
    value: Any = variables
    for part in _parse_path(path):
        if part.kind in {"name", "item"}:
            value = value[part.value]
        else:
            value = getattr(value, part.value)
    return value


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _display_value(value: Any, limit: int = 500) -> str:
    try:
        display = str(value)
    except Exception:
        display = f"<{type(value).__name__}>"
    if len(display) > limit:
        return display[: limit - 1] + "…"
    return display


def _default_session_lister(
    filename: str,
    *,
    limit: int,
    offset: int,
    sql_filters: Sequence[Mapping[str, Any]],
) -> Sequence[Any]:
    """List sessions using parameterized SQL so filters run before loading answers."""
    from docassemble.base.util import user_has_privilege
    from sqlalchemy.sql import text

    if not user_has_privilege(["admin", "developer"]):
        raise PermissionError("Only an administrator or developer may search all sessions.")

    try:
        from docassemble.webapp.db import get_session
    except ModuleNotFoundError as error:
        if error.name != "docassemble.webapp.db":
            raise
        from docassemble.webapp.db_object import init_sqlalchemy

        get_session = init_sqlalchemy().connect

    clauses = ["summary.filename = :filename"]
    parameters: dict[str, Any] = {
        "filename": filename,
        "metadata": "metadata",
        "limit": limit,
        "offset": offset,
    }
    sql_columns = {
        "created": "DATE(summary.created)",
        "modified": "DATE(summary.modified)",
        "steps": "summary.num_keys",
    }
    for index, sql_filter in enumerate(sql_filters):
        parameter_name = f"filter_{index}"
        if sql_filter["field"] == "user_id":
            clauses.append(
                "EXISTS (SELECT 1 FROM userdictkeys AS filtered_users "
                "WHERE filtered_users.key = summary.key "
                f"AND filtered_users.user_id {sql_filter['operator']} :{parameter_name})"
            )
        else:
            clauses.append(
                f"{sql_columns[sql_filter['field']]} {sql_filter['operator']} :{parameter_name}"
            )
        parameters[parameter_name] = (
            date.fromisoformat(sql_filter["value"])
            if sql_filter["field"] in {"created", "modified"}
            else sql_filter["value"]
        )

    statement = text(
        """
        SELECT summary.filename AS filename,
               summary.num_keys AS num_keys,
               joined_users.user_id AS user_id,
               summary.modified AS modtime,
               summary.created AS created,
               summary.key AS key,
               jsonstorage.data->>'auto_title' AS auto_title,
               jsonstorage.data->>'title' AS title
          FROM (
                SELECT filename,
                       key,
                       MIN(modtime) AS created,
                       MAX(modtime) AS modified,
                       COUNT(*) AS num_keys
                  FROM userdict
                 GROUP BY filename, key
               ) AS summary
          LEFT JOIN (
                SELECT key, MIN(user_id) AS user_id
                  FROM userdictkeys
                 GROUP BY key
               ) AS joined_users ON joined_users.key = summary.key
          LEFT JOIN jsonstorage
                 ON jsonstorage.key = summary.key
                AND jsonstorage.tags = :metadata
         WHERE """
        + " AND ".join(clauses)
        + """
         ORDER BY summary.modified DESC, summary.key
         LIMIT :limit OFFSET :offset
        """
    )
    with get_session() as database_session:
        return [
            dict(row._mapping)
            for row in database_session.execute(statement, parameters)
        ]


def _default_variable_loader(filename: str, session_id: str) -> Mapping[str, Any]:
    from docassemble.base.util import get_session_variables

    return get_session_variables(filename, session_id, secret=None, simplify=False)


def search_interview_sessions(
    filename: str,
    criteria_text: str,
    *,
    sql_filters_text: str = "",
    max_results: int = 100,
    batch_size: int = 200,
    session_lister: Optional[Callable[..., Sequence[Any]]] = None,
    variable_loader: Optional[Callable[[str, str], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Search every saved session for ``filename`` and return bounded results.

    Sessions are listed in pages and each raw interview dictionary is discarded
    immediately after the requested paths are checked.
    """
    filename = str(filename or "").strip()
    if not filename:
        raise ValueError("An interview filename is required.")
    max_results = max(1, min(int(max_results), 1000))
    batch_size = max(1, min(int(batch_size), 1000))
    criteria = parse_session_search_criteria(criteria_text)
    sql_filters = parse_session_sql_filters(sql_filters_text)
    list_sessions = session_lister or _default_session_lister
    load_variables = variable_loader or _default_variable_loader

    results: list[dict[str, Any]] = []
    sessions_examined = 0
    matching_sessions = 0
    load_errors = 0
    offset = 0
    seen_session_ids: set[str] = set()

    while True:
        batch = list_sessions(
            filename,
            limit=batch_size,
            offset=offset,
            sql_filters=sql_filters,
        )
        if not batch:
            break
        for session in batch:
            session_id = str(_row_value(session, "key", ""))
            if not session_id:
                load_errors += 1
                continue
            if session_id in seen_session_ids:
                continue
            seen_session_ids.add(session_id)
            sessions_examined += 1
            try:
                variables = load_variables(filename, session_id)
                found_values: dict[str, str] = {}
                is_match = True
                for criterion in criteria:
                    value = resolve_session_variable(variables, criterion["path"])
                    display_value = _display_value(value)
                    if criterion["query"].casefold() not in display_value.casefold():
                        is_match = False
                        break
                    found_values[criterion["path"]] = display_value
            except (AttributeError, IndexError, KeyError, TypeError):
                is_match = False
            except Exception:
                load_errors += 1
                continue

            if is_match:
                matching_sessions += 1
                if len(results) < max_results:
                    results.append(
                        {
                            "session_id": session_id,
                            "filename": filename,
                            "user_id": _row_value(session, "user_id"),
                            "modified": _display_value(_row_value(session, "modtime", "")),
                            "title": _display_value(
                                _row_value(session, "title")
                                or _row_value(session, "auto_title")
                                or ""
                            ),
                            "values": found_values,
                        }
                    )
        offset += len(batch)
        if len(batch) < batch_size:
            break

    return {
        "filename": filename,
        "criteria": criteria,
        "sql_filters": sql_filters,
        "sessions_examined": sessions_examined,
        "matching_sessions": matching_sessions,
        "load_errors": load_errors,
        "max_results": max_results,
        "results": results,
        "truncated": matching_sessions > len(results),
    }
