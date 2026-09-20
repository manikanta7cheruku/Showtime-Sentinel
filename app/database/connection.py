"""SQLite plumbing.

Decision: one short-lived connection per operation instead of a shared pool.
SQLite handles this fine at our scale (a few queries a minute), and it removes
every thread-affinity bug that bites people when a Telegram handler and the
scheduler touch the DB at the same time. Simplicity > micro-optimisation.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Transactional connection. Commits on success, rolls back on error."""
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15.0, isolation_level="DEFERRED")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            # WAL lets a reader (dashboard) run while the scheduler writes.
            if str(self.path) != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialise(self) -> None:
        """Create tables/indexes if missing. Safe to call on every start."""
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(sql)
        logger.info("database ready at %s", self.path)

    def healthcheck(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1 FROM watches LIMIT 1")
            return True
        except sqlite3.Error as exc:
            logger.error("database healthcheck failed: %s", exc)
            return False
