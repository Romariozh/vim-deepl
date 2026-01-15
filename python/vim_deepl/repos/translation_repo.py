# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, List

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo

def _ctx_for_storage(context: str | None) -> str | None:
    """
    Normalize and keep context only if it looks like a sentence.
    We store it into entries.detected_raw as a lightweight solution.
    """
    if not context:
        return None
    ctx = " ".join(context.split())
    if re.search(r"\s", ctx) or re.search(r"[.!?,;:]", ctx):
        return ctx
    return None

def _norm_translation(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s.strip(" \t\r\n.,;:!?")

def _should_store_variant(term: str, translation: str) -> bool:
    t = (term or "").strip().casefold()
    tr = (translation or "").strip().casefold()
    if not t or not tr:
        return False
    # filter obvious "not translated" case (ought -> ought)
    if tr == t:
        return False
    return True

@dataclass(frozen=True)
class TranslationRepo:
    db: SQLiteRepo

    # -------------------------
    # Base cache: entries
    # -------------------------
    def get_base_entry_any_src(self, term: str, dst_lang: str, src_hint: str | None = None) -> Optional[dict]:
        """
        Return the best cached base entry for (term, dst_lang).

        If src_hint is provided (e.g. from Vim F3 cycle), try that src_lang first.
        If not found, fall back to any src_lang.
        """
        with self.db.read() as conn:
            ensure_schema(conn)

            # 1) Prefer explicit src_hint (EN/DA) if available.
            if src_hint:
                row = conn.execute(
                    """
                    SELECT *
                    FROM entries
                    WHERE trim(term) = trim(?) COLLATE NOCASE
                      AND upper(trim(dst_lang)) = upper(trim(?))
                      AND upper(trim(src_lang)) = upper(trim(?))
                    ORDER BY
                        COALESCE(last_used, created_at) DESC,
                        created_at DESC
                    LIMIT 1
                    """,
                    (term, dst_lang, src_hint),
                ).fetchone()
                if row:
                    return dict(row)

            # 2) Fallback: any src_lang.
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
            # Keep translation variant stats in sync with base cache hits
            conn.execute(
                """
                INSERT INTO entry_translations(term, translation, src_lang, dst_lang, created_at, last_used, count)
                SELECT term, translation, src_lang, dst_lang, created_at, ?, 1
                FROM entries
                WHERE id = ?
                ON CONFLICT(term, src_lang, dst_lang, translation) DO UPDATE SET
                    last_used = excluded.last_used,
                    count     = entry_translations.count + 1
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
        context: str | None = None,
    ) -> None:
        ctx = _ctx_for_storage(context)
        detected_raw_to_store = ctx if ctx else detected_raw

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
                    last_used    = excluded.last_used,
                    count        = entries.count + 1
                """,
                (term, translation, src_lang, dst_lang, detected_raw_to_store, now_s, now_s),
            )
            # Accumulate translation variants (multiple meanings per term)
            conn.execute(
                """
                INSERT INTO entry_translations (
                    term, translation, src_lang, dst_lang,
                    created_at, last_used, count
                )
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(term, src_lang, dst_lang, translation) DO UPDATE SET
                    last_used = excluded.last_used,
                    count     = entry_translations.count + 1
                """,
                (term, translation, src_lang, dst_lang, now_s, now_s),
            )

    def list_entry_translations(self, term: str, src_lang: str, dst_lang: str, limit: int = 20) -> list[dict]:
        with self.db.read() as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT translation, count, last_used, created_at
                FROM entry_translations
                WHERE trim(term) = trim(?) COLLATE NOCASE
                  AND upper(trim(src_lang)) = upper(trim(?))
                  AND upper(trim(dst_lang)) = upper(trim(?))
                ORDER BY
                  COALESCE(last_used, created_at) DESC,
                  count DESC
                LIMIT ?
                """,
                (term, src_lang, dst_lang, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # -------------------------
    # Context cache: entries_ctx
    # -------------------------
    def get_ctx_entry(
        self,
        term: str,
        src_lang: str | None,
        dst_lang: str,
        ctx_hash: str,
    ) -> Optional[dict]:
        """
        Return cached context entry for (term, src_lang, dst_lang, ctx_hash).

        Notes:
        - term matching is case-insensitive and trimmed
        - language codes are compared as UPPER(TRIM(...))
        - if src_lang is empty/None, fall back to any src_lang
        """
        with self.db.read() as conn:
            ensure_schema(conn)

            term_n = (term or "").strip()
            dst_n = (dst_lang or "").strip()
            src_n = (src_lang or "").strip()

            # 1) Prefer exact src_lang if provided
            if src_n:
                row = conn.execute(
                    """
                    SELECT *
                    FROM entries_ctx
                    WHERE trim(term) = trim(?) COLLATE NOCASE
                      AND upper(trim(src_lang)) = upper(trim(?))
                      AND upper(trim(dst_lang)) = upper(trim(?))
                      AND ctx_hash = ?
                    LIMIT 1
                    """,
                    (term_n, src_n, dst_n, ctx_hash),
                ).fetchone()
                if row:
                    return dict(row)

            # 2) Fallback: any src_lang (helps when src_hint/detection was missing)
            row = conn.execute(
                """
                SELECT *
                FROM entries_ctx
                WHERE trim(term) = trim(?) COLLATE NOCASE
                  AND upper(trim(dst_lang)) = upper(trim(?))
                  AND ctx_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (term_n, dst_n, ctx_hash),
            ).fetchone()

            return dict(row) if row else None

    def touch_ctx_usage(self, term: str, src_lang: str, dst_lang: str, ctx_hash: str, now_s: str) -> None:
        with self.db.tx_write() as conn:
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

    def list_ctx_translations(self, term: str, src_lang: str, dst_lang: str, limit: int = 10) -> List[str]:
        with self.db.tx() as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT translation, MAX(COALESCE(last_used, created_at)) AS lu
                FROM entries_ctx
                WHERE term = ?
                  AND src_lang = ?
                  AND dst_lang = ?
                GROUP BY translation
                ORDER BY lu DESC
                LIMIT ?
                """,
                (term, src_lang, dst_lang, limit),
            ).fetchall()

        return [r[0] for r in rows if r and r[0]]

    def upsert_ctx_entry(
        self,
        term: str,
        translation: str,
        src_lang: str,
        dst_lang: str,
        ctx_hash: str,
        now_s: str,
        ctx_text: str = "",
    ) -> None:
        MAX_CTX = 3

        with self.db.tx() as conn:
            ensure_schema(conn)

            # Normalize context text for storage
            ctx_text = " ".join((ctx_text or "").split())

            # Upsert the (term, src, dst, ctx_hash) context entry
            conn.execute(
                """
                INSERT INTO entries_ctx (
                    term, translation, src_lang, dst_lang, ctx_hash,
                    ctx_text,
                    created_at, last_used, count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(term, src_lang, dst_lang, ctx_hash) DO UPDATE SET
                    translation = excluded.translation,
                    last_used   = excluded.last_used,
                    count       = entries_ctx.count + 1,
                    ctx_text    = CASE
                                    WHEN excluded.ctx_text IS NOT NULL AND excluded.ctx_text != ''
                                    THEN excluded.ctx_text
                                    ELSE entries_ctx.ctx_text
                                  END
                """,
                (term, translation, src_lang, dst_lang, ctx_hash, ctx_text, now_s, now_s),
            )

            # Also accumulate translation variants from contexts
            tr_norm = _norm_translation(translation)
            if _should_store_variant(term, tr_norm):
                conn.execute(
                    """
                    INSERT INTO entry_translations (
                        term, translation, src_lang, dst_lang,
                        created_at, last_used, count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(term, src_lang, dst_lang, translation) DO UPDATE SET
                        last_used = excluded.last_used,
                        count     = entry_translations.count + 1
                    """,
                    (term, tr_norm, src_lang, dst_lang, now_s, now_s),
                )

            # Keep only MAX_CTX contexts per (term, src_lang, dst_lang),
            # prefer most recently used; never delete the current ctx_hash.
            conn.execute(
                """
                DELETE FROM entries_ctx
                WHERE id IN (
                    SELECT id
                    FROM entries_ctx
                    WHERE term = ?
                      AND src_lang = ?
                      AND dst_lang = ?
                      AND ctx_hash != ?
                    ORDER BY
                      COALESCE(last_used, created_at) ASC,
                      id ASC
                    LIMIT (
                        SELECT CASE
                                 WHEN COUNT(*) > ? THEN COUNT(*) - ?
                                 ELSE 0
                               END
                        FROM entries_ctx
                        WHERE term = ?
                          AND src_lang = ?
                          AND dst_lang = ?
                    )
                )
                """,
                (
                    term, src_lang, dst_lang, ctx_hash,
                    MAX_CTX, MAX_CTX,
                    term, src_lang, dst_lang,
                ),
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

            return {
                "noun": _loads(row["defs_noun"]),
                "verb": _loads(row["defs_verb"]),
                "adjective": _loads(row["defs_adj"]),
                "adverb": _loads(row["defs_adv"]),
                "other": _loads(row["defs_other"]),
                "raw_json": row["raw_json"],
                "audio_main": row["audio_main"],
                "audio_ids": _loads(row["audio_ids"]),
                "created_at": row["created_at"],
            }

    def upsert_mw_definitions(self, term: str, src_lang: str, defs: dict, now_s: str) -> None:
        """
        Store MW definitions into SQLite.

        Notes:
        - POS columns remain JSON-encoded lists for backward compatibility.
        - raw_json is stored as-is (string). It can be either:
            * original MW response JSON (list)
            * our parsed v2 object JSON (dict)
        """
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
                    raw_json,
                    audio_main, audio_ids,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(term, src_lang) DO UPDATE SET
                    defs_noun  = excluded.defs_noun,
                    defs_verb  = excluded.defs_verb,
                    defs_adj   = excluded.defs_adj,
                    defs_adv   = excluded.defs_adv,
                    defs_other = excluded.defs_other,
                    raw_json   = excluded.raw_json,
                    audio_main = excluded.audio_main,
                    audio_ids  = excluded.audio_ids
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
                    defs.get("audio_main"),
                    _dumps(defs.get("audio_ids")),
                    now_s,
                ),
            )
