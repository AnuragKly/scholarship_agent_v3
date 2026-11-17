"""SQLite connection handling.

All access goes through :class:`Database`, which opens short-lived connections with
foreign keys on, and offers a ``transaction()`` context manager so multi-step writes
commit or roll back together. All SQL elsewhere uses ``?`` parameters.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .migrations import run_migrations

logger = logging.getLogger("scholarship_agent.database")


class Database:
    """A SQLite database that is migrated to the latest schema on creation.

    Attributes:
        path: Location of the database file.
    """

    def __init__(self, path: Path, auto_migrate: bool = True):
        """Open (and optionally migrate) the database.

        Args:
            path: SQLite file path.
            auto_migrate: Run pending migrations immediately.
        """
        self.path = Path(path)
        if auto_migrate:
            applied = run_migrations(self.path)
            if applied:
                logger.info("Applied migrations: %s", applied)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a transaction.

        Commits on success and rolls back on any exception (which is re-raised).
        """
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list:
        """Run a read-only parameterised query and return all rows as dicts."""
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
