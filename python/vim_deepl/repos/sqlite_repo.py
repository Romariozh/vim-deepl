# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from vim_deepl.utils.logging import get_logger

log = get_logger("repos.sqlite")


class SQLiteRepo:
    """
    Single, explicit place for:
      - opening sqlite connection
      - PRAGMAs
      - transactions

    Notes:
      - Use WAL to reduce reader/writer blocking.
      - Use connect(timeout=...) + PRAGMA busy_timeout for lock waits.
      - Use BEGIN IMMEDIATE for write transactions to acquire a write lock up-front.
    """

    def __init__(self, db_path: Path, *, timeout_s: float = 10.0, busy_timeout_ms: int = 10000):
        self.db_path = Path(db_path)
        self.timeout_s = float(timeout_s)
        self.busy_timeout_ms = int(busy_timeout_ms)

    def connect(self) -> sqlite3.Connection:
        # timeout here is important: sqlite3 will wait for locks before raising OperationalError
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.timeout_s,
            check_same_thread=False,  # ok for FastAPI threadpool; each tx opens its own connection
        )
        conn.row_factory = sqlite3.Row

        # safer defaults
        conn.execute("PRAGMA foreign_keys = ON;")

        # WAL: readers don't block writers and vice versa (mostly)
        conn.execute("PRAGMA journal_mode = WAL;")

        # good default for WAL
        conn.execute("PRAGMA synchronous = NORMAL;")

        # wait for locks (ms). works together with connect(timeout=...)
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")

        # Optional but often helpful in WAL mode:
        # conn.execute("PRAGMA temp_store = MEMORY;")
        # conn.execute("PRAGMA mmap_size = 268435456;")  # 256MB

        return conn

    @contextmanager
    def tx_write(self) -> Iterator[sqlite3.Connection]:
        """
        Write transaction. Uses BEGIN IMMEDIATE to acquire a RESERVED lock up-front.
        This reduces random 'database is locked' in the middle of a write sequence.
        """
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                # in rare cases connection itself can be broken/closed
                pass
            log.exception("SQLite write transaction rolled back")
            raise
        finally:
            conn.close()

    @contextmanager
    def tx_read(self) -> Iterator[sqlite3.Connection]:
        """
        Explicit read transaction (optional). Useful when you need consistent snapshot across multiple SELECTs.
        """
        conn = self.connect()
        try:
            conn.execute("BEGIN;")
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            log.exception("SQLite read transaction rolled back")
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """
        Autocommit read connection for simple single SELECT usage.
        """
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    # Backward compatibility: keep old tx() name as write tx
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        # default (deferred) transaction – does NOT take write lock up-front
        conn = self.connect()
        try:
            conn.execute("BEGIN;")
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            log.exception("SQLite transaction rolled back")
            raise
        finally:
            conn.close()
