"""Short, independent usage transactions. Accounting never repeats an AI call."""

from contextlib import closing
import logging
import sqlite3
import uuid
from pathlib import Path
from threading import Lock

from agent_runtime.models.usage import normalize_usage
from storage.token_store import TokenStore

logger = logging.getLogger(__name__)
_recorders = {}
_lock = Lock()


class UsageRecorder:
    def __init__(self, db_path, *, connection_factory=None):
        self.db_path = str(db_path)
        self.boot_id = uuid.uuid4().hex
        self.warning = False
        self.connection_factory = connection_factory or self._connect

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=0.25)
        db.row_factory = sqlite3.Row
        return db

    def _write(self, operation):
        try:
            with closing(self.connection_factory()) as db:
                return operation(TokenStore(db))
        except (sqlite3.Error, OSError):
            self.warning = True
            logger.warning("Model usage accounting unavailable", exc_info=False)
            return None

    def activate(self):
        self._write(lambda store: (store.activate(), store.recover(self.boot_id)))

    def begin(self, identity, *, run_id="", execution_id="", project_id=""):
        call_id = uuid.uuid4().hex
        self._write(
            lambda store: store.begin_call(
                call_id,
                identity,
                boot_id=self.boot_id,
                run_id=run_id,
                execution_id=execution_id,
                project_id=project_id,
            )
        )
        return call_id

    def finish(
        self,
        call_id,
        *,
        state,
        raw_usage=None,
        provider_request_id=None,
        duration_ms=None
    ):
        value = normalize_usage(raw_usage)
        self._write(
            lambda store: store.finish_call(
                call_id,
                state,
                value,
                provider_request_id=provider_request_id,
                duration_ms=duration_ms,
            )
        )

    def interrupt_execution(self, execution_id, state="interrupted"):
        self._write(lambda store: store.interrupt_execution(execution_id, state))


def get_usage_recorder(db_path=None):
    from config.settings import DB_PATH

    key = str(Path(db_path or DB_PATH).resolve())
    with _lock:
        if key not in _recorders:
            recorder = UsageRecorder(key)
            recorder.activate()
            _recorders[key] = recorder
        return _recorders[key]


def recorder_for_connection(db):
    """In-memory test connections have no durable usage destination."""
    path = db.execute("PRAGMA database_list").fetchone()[2]
    return get_usage_recorder(path) if path else None


def usage_warning_for_connection(db):
    """Read collector health without creating a writer during a dashboard GET."""
    path = db.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        return False
    with _lock:
        recorder = _recorders.get(str(Path(path).resolve()))
        return recorder.warning if recorder else False
