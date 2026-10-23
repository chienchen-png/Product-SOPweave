"""Persist audit decisions in the snapshot that they describe."""
from collections.abc import MutableMapping


class SnapshotDecisions(MutableMapping):
    def __init__(self, db, lock):
        self.db, self.lock = db, lock
        db.execute('CREATE TABLE IF NOT EXISTS audit_decisions(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.commit()

    def __getitem__(self, key):
        with self.lock:
            row = self.db.execute('SELECT value FROM audit_decisions WHERE key=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]

    def __setitem__(self, key, value):
        with self.lock:
            self.db.execute('INSERT INTO audit_decisions VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
            self.db.commit()

    def __delitem__(self, key):
        with self.lock:
            cursor = self.db.execute('DELETE FROM audit_decisions WHERE key=?', (key,))
            self.db.commit()
        if not cursor.rowcount:
            raise KeyError(key)

    def __iter__(self):
        with self.lock:
            return iter([row[0] for row in self.db.execute('SELECT key FROM audit_decisions ORDER BY key')])

    def __len__(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM audit_decisions').fetchone()[0]
