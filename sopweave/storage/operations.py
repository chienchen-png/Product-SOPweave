"""Cooperative cancellation for database and atomic file operations."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event

_cancelled: ContextVar[Event | None] = ContextVar("operation_cancelled", default=None)


class OperationCancelled(Exception):
    pass


def check_cancelled():
    event = _cancelled.get()
    if event and event.is_set():
        raise OperationCancelled("操作已取消")


@contextmanager
def operation_scope(event):
    token = _cancelled.set(event)
    try:
        check_cancelled()
        yield
        check_cancelled()
    finally:
        _cancelled.reset(token)
