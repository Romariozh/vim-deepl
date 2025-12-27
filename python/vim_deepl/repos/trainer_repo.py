# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Dict, Any, Optional

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo

import sqlite3
import time

def _ph(n: int) -> str:
    return ",".join(["?"] * n)

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

    def touch_last_used(self, entry_id: int, now_s: str) -> None:
        """
        Update last_used only (no count increment). Useful for fallback browsing.
        """
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
                """
                UPDATE entries
                SET last_used = ?
                WHERE id = ?
                """,
                (now_s, entry_id),
            )

    def get_training_card(self, card_id: int) -> Optional[Dict[str, Any]]:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT id, entry_id, src_lang,
                       reps, lapses, ef, interval_days, due_at,
                       last_review_at, last_grade, correct_streak, wrong_streak, suspended
                FROM training_cards
                WHERE id = ?
                """,
                (card_id,),
            ).fetchone()
            return dict(row) if row else None

    def insert_training_review(self, card_id: int, ts: int, grade: int, day: str) -> None:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
				"""
				INSERT INTO training_reviews(card_id, ts, grade, day)
				VALUES(?, ?, ?, ?)
				""",
				(card_id, ts, grade, day),
			)

    def update_training_card_srs(self, card_id: int, s: Dict[str, Any]) -> None:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.execute(
                """
                UPDATE training_cards
                SET reps=?,
                    lapses=?,
                    ef=?,
                    interval_days=?,
                    due_at=?,
                    last_review_at=?,
                    last_grade=?,
                    correct_streak=?,
                    wrong_streak=?
                WHERE id=?
                """,
                (
					s["reps"],
					s["lapses"],
					s["ef"],
					s["interval_days"],
					s["due_at"],
					s["last_review_at"],
					s["last_grade"],
					s["correct_streak"],
					s["wrong_streak"],
					card_id,
				),
			)

    def _get_training_card_conn(self, conn, card_id: int) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            """
            SELECT id, reps, lapses, ef, interval_days, due_at,
                last_review_at, last_grade, correct_streak, wrong_streak, suspended
			FROM training_cards
			WHERE id = ?
			""",
			(card_id,),
		).fetchone()
        return dict(row) if row else None

    def _insert_training_review_conn(self, conn, card_id: int, ts: int, grade: int, day: str) -> None:
        # required for some schemas
        row = conn.execute("SELECT entry_id FROM training_cards WHERE id=?", (card_id,)).fetchone()
        entry_id = row["entry_id"] if row else None

        cols = {r[1] for r in conn.execute("PRAGMA table_info(training_reviews)").fetchall()}

        insert_cols = []
        insert_vals = []

        # common
        if "entry_id" in cols:
            if entry_id is None:
                raise ValueError(f"training_cards missing for card_id={card_id}")
            insert_cols.append("entry_id")
            insert_vals.append(entry_id)

        if "card_id" in cols:
            insert_cols.append("card_id")
            insert_vals.append(card_id)

        if "ts" in cols:
            insert_cols.append("ts")
            insert_vals.append(ts)

        if "reviewed_at" in cols:
            # real vocab.db requires this NOT NULL
            insert_cols.append("reviewed_at")
            insert_vals.append(ts)

        if "grade" in cols:
            insert_cols.append("grade")
            insert_vals.append(grade)

        if "day" in cols:
            insert_cols.append("day")
            insert_vals.append(day)

        # fallback: if this is our minimal schema and we didn't detect columns
        if not insert_cols:
            conn.execute(
                "INSERT INTO training_reviews(card_id, ts, grade, day) VALUES(?, ?, ?, ?)",
                (card_id, ts, grade, day),
            )
            return

        cols_sql = ", ".join(insert_cols)
        qs_sql = ", ".join(["?"] * len(insert_cols))
        sql = f"INSERT INTO training_reviews({cols_sql}) VALUES({qs_sql})"
        conn.execute(sql, tuple(insert_vals))


    def _update_training_card_srs_conn(self, conn, card_id: int, s: Dict[str, Any]) -> None:
        # Normalize timestamps: store seconds, not milliseconds.
        now_ts = int(time.time())

        due_at = int(s.get("due_at") or 0)
        last_review_at = int(s.get("last_review_at") or 0)

        # If values look like milliseconds -> convert to seconds.
        if due_at > 10_000_000_000:
            due_at //= 1000
        if last_review_at > 10_000_000_000:
            last_review_at //= 1000

        # Safety clamp: if due_at is absurdly in the past, push it forward (prevents “infinite due” loops).
        if due_at and due_at < now_ts - 86400 * 365:
            due_at = now_ts + 86400

        # Write back normalized values (optional, but nice for debugging)
        s["due_at"] = due_at
        s["last_review_at"] = last_review_at

        conn.execute(
            """
            UPDATE training_cards
            SET reps=?,
                lapses=?,
                ef=?,
                interval_days=?,
                due_at=?,
                last_review_at=?,
                last_grade=?,
                correct_streak=?,
                wrong_streak=?
            WHERE id=?
            """,
            (
                s["reps"],
                s["lapses"],
                s["ef"],
                s["interval_days"],
                due_at,
                last_review_at,
                s["last_grade"],
                s["correct_streak"],
                s["wrong_streak"],
                card_id,
            ),
        )


    def _list_due_entries_conn(
        self,
        conn,
        src_langs: list[str],
        now_ts: int,
        limit: int,
        exclude_card_ids: Optional[list[int]] = None,
    ) -> list[dict[str, Any]]:
        if not src_langs:
            return []

        placeholders = ",".join(["?"] * len(src_langs))

        exclude_sql = ""
        exclude_args: list[Any] = []
        if exclude_card_ids:
            ex_ph = ",".join(["?"] * len(exclude_card_ids))
            exclude_sql = f" AND c.id NOT IN ({ex_ph}) "
            exclude_args = list(exclude_card_ids)

        sql = f"""
        SELECT
            c.id AS card_id,
            c.entry_id AS entry_id,

            -- Normalize due_at: if it's milliseconds (13+ digits) -> seconds
            CASE
              WHEN CAST(c.due_at AS INTEGER) > 100000000000
              THEN CAST(CAST(c.due_at AS INTEGER) / 1000 AS INTEGER)
              ELSE CAST(c.due_at AS INTEGER)
            END AS due_at,

            c.lapses,
            c.wrong_streak,
            e.term,
            e.translation,
            e.src_lang,
            e.dst_lang,
            e.detected_raw AS detected_raw,
            COALESCE(
              (
                SELECT x.ctx_text
                FROM entries_ctx x
                WHERE x.term = e.term
                  AND x.src_lang = e.src_lang
                  AND x.dst_lang = e.dst_lang
                  AND x.ctx_text IS NOT NULL
                  AND x.ctx_text != ''
                ORDER BY
                  COALESCE(x.last_used, x.created_at) DESC,
                  x.count DESC,
                  x.id DESC
                LIMIT 1
              ),
            '') AS context_raw
        FROM training_cards c
        JOIN entries e ON e.id = c.entry_id
        WHERE c.suspended = 0
          AND c.entry_id IS NOT NULL
          AND e.ignore = 0
          AND c.due_at IS NOT NULL
          {exclude_sql}
          AND (
            CASE
              WHEN CAST(c.due_at AS INTEGER) > 100000000000
              THEN CAST(CAST(c.due_at AS INTEGER) / 1000 AS INTEGER)
              ELSE CAST(c.due_at AS INTEGER)
            END
          ) <= ?
          AND e.src_lang IN ({placeholders})
        ORDER BY
          due_at ASC,
          c.lapses DESC,
          c.wrong_streak DESC
        LIMIT ?
        """

        args: list[Any] = []
        args.extend(exclude_args)
        args.append(now_ts)
        args.extend(src_langs)
        args.append(limit)

        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


    def _list_new_entries_conn(
        self,
        conn,
        src_langs: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not src_langs:
            return []
        placeholders = ",".join(["?"] * len(src_langs))

        # "New" = entries without any training card yet
        sql = f"""
        SELECT
            NULL AS card_id,
            e.id AS entry_id,
            e.term,
            e.translation,
            e.src_lang,
            e.dst_lang,
            e.detected_raw AS detected_raw,
            COALESCE(
              (
                SELECT x.ctx_text
                FROM entries_ctx x
                WHERE x.term = e.term
                  AND x.src_lang = e.src_lang
                  AND x.dst_lang = e.dst_lang
                  AND x.ctx_text IS NOT NULL
                  AND x.ctx_text != ''
                ORDER BY
                  COALESCE(x.last_used, x.created_at) DESC,
                  x.count DESC,
                  x.id DESC
                LIMIT 1
              ),
            '') AS context_raw,
            NULL AS due_at,
            0 AS lapses,
            0 AS wrong_streak
        FROM entries e
        LEFT JOIN training_cards c
          ON c.entry_id = e.id
        WHERE e.ignore = 0
          AND e.src_lang IN ({placeholders})
          AND c.id IS NULL
        ORDER BY RANDOM()
        LIMIT ?
        """

        args: list[Any] = [*src_langs, limit]
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def _list_hard_entries_conn(
        self,
        conn,
        src_langs: list[str],
        now_ts: int,
        limit: int,
        exclude_card_ids: Optional[list[int]] = None,
    ) -> list[dict[str, Any]]:
        if not src_langs:
            return []

        placeholders = ",".join(["?"] * len(src_langs))

        exclude_sql = ""
        exclude_args: list[Any] = []
        if exclude_card_ids:
            ex_ph = ",".join(["?"] * len(exclude_card_ids))
            exclude_sql = f" AND c.id NOT IN ({ex_ph}) "
            exclude_args = list(exclude_card_ids)

        sql = f"""
        SELECT
            c.id AS card_id,
            c.entry_id AS entry_id,

            -- Normalize due_at: if it's milliseconds (13+ digits) -> seconds
            CASE
              WHEN CAST(c.due_at AS INTEGER) > 100000000000
              THEN CAST(CAST(c.due_at AS INTEGER) / 1000 AS INTEGER)
              ELSE CAST(c.due_at AS INTEGER)
            END AS due_at,

            c.lapses,
            c.wrong_streak,

            e.term,
            e.translation,
            e.src_lang,
            e.dst_lang,
            e.detected_raw AS detected_raw,
            COALESCE(
              (
                SELECT x.ctx_text
                FROM entries_ctx x
                WHERE x.term = e.term
                  AND x.src_lang = e.src_lang
                  AND x.dst_lang = e.dst_lang
                  AND x.ctx_text IS NOT NULL
                  AND x.ctx_text != ''
                ORDER BY
                  COALESCE(x.last_used, x.created_at) DESC,
                  x.count DESC,
                  x.id DESC
                LIMIT 1
              ),
            '') AS context_raw
        FROM training_cards c
        JOIN entries e ON e.id = c.entry_id
        WHERE c.suspended = 0
          AND c.entry_id IS NOT NULL
          AND e.ignore = 0
          AND c.due_at IS NOT NULL
          {exclude_sql}
          AND (
            CASE
              WHEN CAST(c.due_at AS INTEGER) > 100000000000
              THEN CAST(CAST(c.due_at AS INTEGER) / 1000 AS INTEGER)
              ELSE CAST(c.due_at AS INTEGER)
            END
          ) <= ?
          AND e.src_lang IN ({placeholders})
        ORDER BY
          c.lapses DESC,
          c.wrong_streak DESC,
          due_at ASC,
          COALESCE(CAST(c.last_review_at AS INTEGER), 0) ASC
        LIMIT ?
        """

        args: list[Any] = []
        # Param order: (exclude args...) then now_ts then src_langs then limit
        args.extend(exclude_args)
        args.append(now_ts)
        args.extend(src_langs)
        args.append(limit)

        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


    def _ensure_card_for_entry_conn(self, conn, entry_id: int, now_ts: int) -> int:
        row = conn.execute("SELECT id FROM training_cards WHERE entry_id=?", (entry_id,)).fetchone()
        if row:
            return row["id"]

        cols = {r[1] for r in conn.execute("PRAGMA table_info(training_cards)").fetchall()}

        needs_src = "src_lang" in cols
        has_created = "created_at" in cols
        has_updated = "updated_at" in cols

        src_lang = None
        if needs_src:
            e = conn.execute("SELECT src_lang FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not e:
                raise ValueError(f"entry not found: id={entry_id}")
            src_lang = e["src_lang"]

        # собрать INSERT динамически
        insert_cols = ["entry_id", "due_at"]
        insert_vals = [entry_id, now_ts]

        if needs_src:
            insert_cols.insert(1, "src_lang")
            insert_vals.insert(1, src_lang)

        if has_created:
            insert_cols.append("created_at")
            insert_vals.append(now_ts)
        if has_updated:
            insert_cols.append("updated_at")
            insert_vals.append(now_ts)

        cols_sql = ", ".join(insert_cols)
        qs_sql = ", ".join(["?"] * len(insert_cols))
        sql = f"INSERT INTO training_cards({cols_sql}) VALUES({qs_sql})"

        try:
            conn.execute(sql, tuple(insert_vals))
        except Exception:
            # на случай гонки/unique
            row2 = conn.execute("SELECT id FROM training_cards WHERE entry_id=?", (entry_id,)).fetchone()
            if row2:
                return row2["id"]
            raise

        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def _count_reviews_for_day_conn(self, conn, day: str) -> int:
        row = conn.execute(
			"SELECT COUNT(*) AS c FROM training_reviews WHERE day = ?",
			(day,),
		).fetchone()
        return int(row["c"] or 0)

    def _list_active_days_desc_conn(self, conn, limit: int = 400) -> list[str]:
        rows = conn.execute(
			"""
			SELECT day
			FROM training_reviews
			WHERE day IS NOT NULL
			GROUP BY day
			HAVING COUNT(*) > 0
			ORDER BY day DESC
			LIMIT ?
			""",
			(limit,),
		).fetchall()
        return [r["day"] for r in rows]

