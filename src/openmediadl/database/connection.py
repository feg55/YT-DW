"""SQLite connection lifecycle and required pragmas."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

from openmediadl.database.migrations import apply_migrations


class Database:
    """Creates short-lived configured connections to an application database."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._migration_lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # Fail early if the path is unusable and ensure the schema exists.
        connection = self.connect()
        if self.path != ":memory:":
            connection.close()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def connect(self) -> sqlite3.Connection:
        """Return a configured connection with the latest schema."""

        with self._migration_lock:
            if self.path == ":memory:":
                if self._memory_connection is None:
                    self._memory_connection = self._new_connection()
                    apply_migrations(self._memory_connection)
                return self._memory_connection
            connection = self._new_connection()
            apply_migrations(connection)
            return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a transaction and reliably commit, roll back, and close it."""

        connection = self.connect()
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if self.path != ":memory:":
                connection.close()

    def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def __enter__(self) -> Database:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


SQLiteDatabase = Database


def initialize_database(path: str | Path) -> Database:
    return Database(path)
