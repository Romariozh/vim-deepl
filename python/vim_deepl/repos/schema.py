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

