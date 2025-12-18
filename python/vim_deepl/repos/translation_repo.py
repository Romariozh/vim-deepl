# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo


@dataclass(frozen=True)
class TranslationRepo:
    db: SQLiteRepo

    # -------------------------
    # Base cache: entries
    # -------------------------
    def get_base_entry_any_src(self, term: str, dst_lang: str) -> Optional[dict]:
        with self.db.read() as conn:
            ensure_schema(conn)
            row = conn.execute(
                """
                SELECT *
                FROM entries
                WHERE trim(term) = trim(?) COLLATE NOCASE
                AND upper(trim(dst_lang)) = upper(trim(?))
                ORDER BY
                    COALESCE(last_used, created_at) DESC,
                    created_at DESC
                LIMIT 1
                """,
                (term, dst_lang),
            ).fetchone()
            return dict(row) if row else None

    def touch_base_usage(self, entry_id: int, now_s: str) -> None:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
                """
                UPDATE entries
                SET last_used = ?,
                    count = count + 1
                WHERE id = ?
                """,
                (now_s, entry_id),
            )

    def upsert_base_entry(
        self,
        term: str,
        translation: str,
        src_lang: str,
        dst_lang: str,
        detected_raw: str,
        now_s: str,
    ) -> None:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO entries (
                    term, translation, src_lang, dst_lang, detected_raw,
                    created_at, last_used, count, hard, ignore
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 0)
                ON CONFLICT(term, src_lang, dst_lang) DO UPDATE SET
                    translation  = excluded.translation,
                    detected_raw = excluded.detected_raw,
                    last_used    = excluded.last_used,
                    count        = entries.count + 1
                """,
                (term, translation, src_lang, dst_lang, detected_raw, now_s, now_s),
            )

    # -------------------------
    # Context cache: entries_ctx
    # -------------------------
    def get_ctx_entry(self, term: str, src_lang: str, dst_lang: str, ctx_hash: str) -> Optional[dict]:
        with self.db.read() as conn:
            ensure_schema(conn)
            row = conn.execute(
                """
                SELECT *
                FROM entries_ctx
                WHERE term = ?
                  AND src_lang = ?
                  AND dst_lang = ?
                  AND ctx_hash = ?
                LIMIT 1
                """,
                (term, src_lang, dst_lang, ctx_hash),
            ).fetchone()
            return dict(row) if row else None

    def touch_ctx_usage(self, term: str, src_lang: str, dst_lang: str, ctx_hash: str, now_s: str) -> None:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
                """
                UPDATE entries_ctx
                SET last_used = ?,
                    count = count + 1
                WHERE term = ?
                  AND src_lang = ?
                  AND dst_lang = ?
                  AND ctx_hash = ?
                """,
                (now_s, term, src_lang, dst_lang, ctx_hash),
            )

    def upsert_ctx_entry(
        self,
        term: str,
        translation: str,
        src_lang: str,
        dst_lang: str,
        ctx_hash: str,
        now_s: str,
    ) -> None:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO entries_ctx (
                    term, translation, src_lang, dst_lang, ctx_hash,
                    created_at, last_used, count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(term, src_lang, dst_lang, ctx_hash) DO UPDATE SET
                    translation = excluded.translation,
                    last_used   = excluded.last_used,
                    count       = entries_ctx.count + 1
                """,
                (term, translation, src_lang, dst_lang, ctx_hash, now_s, now_s),
            )

    # -------------------------
    # MW definitions cache: mw_definitions
    # -------------------------
    def get_mw_definitions(self, term: str, src_lang: str) -> Optional[dict]:
        with self.db.read() as conn:
            ensure_schema(conn)
            row = conn.execute(
                """
                SELECT *
                FROM mw_definitions
                WHERE term = ?
                  AND src_lang = ?
                LIMIT 1
                """,
                (term, src_lang),
            ).fetchone()
            if not row:
                return None

            # Stored as JSON strings per part-of-speech
            def _loads(x: Any) -> Any:
                if x is None:
                    return None
                try:
                    return json.loads(x)
                except Exception:
                    return x
            """
            return {
                "term": row["term"],
                "src_lang": row["src_lang"],
                "noun": _loads(row["defs_noun"]),
                "verb": _loads(row["defs_verb"]),
                "adj": _loads(row["defs_adj"]),
                "adv": _loads(row["defs_adv"]),
                "other": _loads(row["defs_other"]),
                "raw_json": row["raw_json"],
                "created_at": row["created_at"],
            }
            """
            return {
                "noun": _loads(row["defs_noun"]),
                "verb": _loads(row["defs_verb"]),
                "adjective": _loads(row["defs_adj"]),
                "adverb": _loads(row["defs_adv"]),
                "other": _loads(row["defs_other"]),
                "raw_json": row["raw_json"],
                "created_at": row["created_at"],
            }


    def upsert_mw_definitions(self, term: str, src_lang: str, defs: dict, now_s: str) -> None:
        def _dumps(x: Any) -> Optional[str]:
            if x is None:
                return None
            return json.dumps(x, ensure_ascii=False)

        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO mw_definitions (
                    term, src_lang,
                    defs_noun, defs_verb, defs_adj, defs_adv, defs_other,
                    raw_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(term, src_lang) DO UPDATE SET
                    defs_noun  = excluded.defs_noun,
                    defs_verb  = excluded.defs_verb,
                    defs_adj   = excluded.defs_adj,
                    defs_adv   = excluded.defs_adv,
                    defs_other = excluded.defs_other,
                    raw_json   = excluded.raw_json
                """,
                (
                    term,
                    src_lang,
                    _dumps(defs.get("noun")),
                    _dumps(defs.get("verb")),
                    _dumps(defs.get("adjective")),
                    _dumps(defs.get("adverb")),
                    _dumps(defs.get("other")),
                    defs.get("raw_json"),
                    now_s,
                ),
            )
