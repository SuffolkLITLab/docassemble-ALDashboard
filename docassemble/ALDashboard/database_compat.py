"""Database-session compatibility for docassemble 1.9 and 1.10."""

from contextlib import contextmanager
from typing import Any, Iterator

try:
    from docassemble.webapp.db import (
        get_session as get_database_session,
        session_scope as database_session_scope,
    )
except ModuleNotFoundError as err:
    if err.name != "docassemble.webapp.db":
        raise
    # docassemble < 1.10 exposes a configured SQLAlchemy session directly.
    from docassemble.webapp.db_object import init_sqlalchemy

    _legacy_db = init_sqlalchemy()

    @contextmanager
    def get_database_session() -> Iterator[Any]:
        yield _legacy_db.session

    @contextmanager
    def database_session_scope() -> Iterator[Any]:
        try:
            yield _legacy_db.session
            _legacy_db.session.commit()
        except BaseException:
            _legacy_db.session.rollback()
            raise


__all__ = ["database_session_scope", "get_database_session"]
