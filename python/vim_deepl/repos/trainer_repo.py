# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Dict, Any

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo


@dataclass(frozen=True)
class TrainerRepo:
    db: SQLiteRepo

    def list_entries_for_training(self, src_langs: Iterable[str]) -> List[Dict[str, Any]]:
        """
        Fetch entries for training (ignore=0, filtered by src_lang IN (...)).
        Returns list of dicts (row-like).
        """
        src_langs = list(src_langs)
        if not src_langs:
            return []

        placeholders = ",".join("?" for _ in src_langs)

        with self.db.read() as conn:
            ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT
                    id,
                    term,
                    translation,
                    src_lang,
                    dst_lang,
                    detected_raw,
                    created_at,
                    last_used,
                    count,
                    hard,
                    ignore
                FROM entries
                WHERE ignore = 0
                  AND src_lang IN ({placeholders})
                """,
                src_langs,
            ).fetchall()

            return [dict(r) for r in rows]

    def touch_usage(self, entry_id: int, now_s: str) -> None:
        """
        Update last_used and increment count for the chosen entry.
        """
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
