"""Database ownership for business operations outside a web request."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator
from storage.operations import check_cancelled

ConnectionFactory = Callable[[], sqlite3.Connection]
_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
    "business_connection", default=None
)


@contextmanager
def connection_scope(factory: ConnectionFactory) -> Iterator[sqlite3.Connection]:
    """Own a connection for one operation, including rollback and cleanup."""
    connection = factory()
    token = _connection.set(connection)
    try:
        yield connection
        check_cancelled()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        _connection.reset(token)
        connection.close()


def get_connection() -> sqlite3.Connection:
    connection = _connection.get()
    if connection is not None:
        return connection
    # Request ownership remains in the Flask adapter; business functions use
    # the same accessor in both entry points.
    from storage.db import get_db

    return get_db()
