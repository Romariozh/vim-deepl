# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

import sqlite3
from vim_deepl.utils.logging import get_logger

log = get_logger("repos.schema")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Put all CREATE TABLE / CREATE INDEX / PRAGMA-related schema setup here.
    Must be idempotent (safe to call multiple times).
    """
    # IMPORTANT: do not set PRAGMAs here that must be per-connection in SQLiteRepo.connect()
    # Only schema DDL (CREATE TABLE/INDEX) should live here.

    # TODO: paste your conn.execute("""CREATE TABLE ...""") blocks here
    # Example:
    # conn.execute("""CREATE TABLE IF NOT EXISTS ...""")
    # conn.execute("""CREATE INDEX IF NOT EXISTS ...""")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            term         TEXT NOT NULL,
            translation  TEXT NOT NULL,
            src_lang     TEXT NOT NULL,
            dst_lang     TEXT NOT NULL,
            detected_raw TEXT,
            created_at   TEXT NOT NULL,
            last_used    TEXT,
            count        INTEGER NOT NULL DEFAULT 0,
            hard         INTEGER NOT NULL DEFAULT 0,
            ignore       INTEGER NOT NULL DEFAULT 0,
            UNIQUE(term, src_lang, dst_lang)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entries_src_ignore
            ON entries(src_lang, ignore)
        """
    )

    # --- Merriam-Webster definitions table (per term, per src_lang) ---
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mw_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            src_lang TEXT NOT NULL,        -- "EN" for now
            defs_noun TEXT,                -- JSON list of strings
            defs_verb TEXT,
            defs_adj TEXT,
            defs_adv TEXT,
            defs_other TEXT,
            raw_json TEXT,                 -- raw MW JSON (for debugging / future use)
            created_at TEXT NOT NULL,
            UNIQUE(term, src_lang)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mw_def_term_src
        ON mw_definitions(term, src_lang)
        """
    )

    conn.execute(
         """
         CREATE TABLE IF NOT EXISTS entries_ctx (
             id           INTEGER PRIMARY KEY AUTOINCREMENT,
             term         TEXT NOT NULL,
             translation  TEXT NOT NULL,
             src_lang     TEXT NOT NULL,
             dst_lang     TEXT NOT NULL,
             ctx_hash     TEXT NOT NULL,
             created_at   TEXT NOT NULL,
             last_used    TEXT,
             count        INTEGER NOT NULL DEFAULT 0,
             UNIQUE(term, src_lang, dst_lang, ctx_hash)
         )
         """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entries_ctx_lookup
        ON entries_ctx(term, src_lang, dst_lang, ctx_hash)
        """
    )

    # --- Trainer SRS extensions (v3) ---
    ensure_columns(conn, "training_cards", {
        "reps": "INTEGER DEFAULT 0",
        "lapses": "INTEGER DEFAULT 0",
        "ef": "REAL DEFAULT 2.5",
        "interval_days": "INTEGER DEFAULT 0",
        "due_at": "INTEGER",
        "last_review_at": "INTEGER",
        "last_grade": "INTEGER",
        "correct_streak": "INTEGER DEFAULT 0",
        "wrong_streak": "INTEGER DEFAULT 0",
        "suspended": "INTEGER DEFAULT 0",
    })

    ensure_columns(conn, "training_reviews", {
        "day": "TEXT",
    })

    conn.execute("""
        CREATE TABLE IF NOT EXISTS training_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS training_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER,
            ts INTEGER,
            grade INTEGER,
            day TEXT
        )
    """)


def table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    if not table_exists(conn, table):
        return

    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}  # row[1] = column name

    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_cards_due ON training_cards(due_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_cards_hard ON training_cards(lapses, wrong_streak)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_cards_last_review ON training_cards(last_review_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_reviews_card_ts ON training_reviews(card_id, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_reviews_day ON training_reviews(day)")
