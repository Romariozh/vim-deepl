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

    def get_training_card(self, card_id: int) -> Optional[Dict[str, Any]]:
        with self.db.tx() as conn:
            ensure_schema(conn)
            conn.row_factory = sqlite3.Row
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
        conn.execute(
			"""
			INSERT INTO training_reviews(card_id, ts, grade, day)
			VALUES(?, ?, ?, ?)
			""",
			(card_id, ts, grade, day),
		)

    def _update_training_card_srs_conn(self, conn, card_id: int, s: Dict[str, Any]) -> None:
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

    def _list_due_entries_conn(self, conn, src_langs: list[str], now_ts: int, limit: int) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in src_langs)
        sql = f"""
        SELECT c.id AS card_id, c.entry_id,
            c.due_at, c.lapses, c.wrong_streak,
            e.word, e.translation, e.src_lang
        FROM training_cards c
        JOIN entries e ON e.id = c.entry_id
        WHERE c.suspended = 0
        AND c.entry_id IS NOT NULL
        AND c.due_at IS NOT NULL AND c.due_at <= ?
        AND e.src_lang IN ({placeholders})
        ORDER BY c.due_at ASC, c.lapses DESC, c.wrong_streak DESC
        LIMIT ?
        """
        args = [now_ts, *src_langs, limit]
        rows = conn.execute(sql, args).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def _list_new_entries_conn(self, conn, src_langs: list[str], limit: int) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in src_langs)
        sql = f"""
		SELECT e.id AS entry_id, e.word, e.translation, e.src_lang
		FROM entries e
		LEFT JOIN training_cards c ON c.entry_id = e.id
		WHERE e.src_lang IN ({placeholders})
		  AND c.id IS NULL
		ORDER BY RANDOM()
		LIMIT ?
		"""
        args = [*src_langs, limit]
        rows = conn.execute(sql, args).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def _list_hard_entries_conn(self, conn, src_langs: list[str], limit: int) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in src_langs)
        sql = f"""
        SELECT c.id AS card_id, c.entry_id,
		       c.lapses, c.wrong_streak, c.last_review_at,
		       e.word, e.translation, e.src_lang
		FROM training_cards c
		JOIN entries e ON e.id = c.entry_id
		WHERE c.suspended = 0
		  AND c.entry_id IS NOT NULL
		  AND e.src_lang IN ({placeholders})
		ORDER BY c.lapses DESC, c.wrong_streak DESC, COALESCE(c.last_review_at, 0) ASC
		LIMIT ?
		"""
        args = [*src_langs, limit]
        rows = conn.execute(sql, args).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def _ensure_card_for_entry_conn(self, conn, entry_id: int, now_ts: int) -> int:
        row = conn.execute("SELECT id FROM training_cards WHERE entry_id=?", (entry_id,)).fetchone()
        if row:
            return row["id"]

        conn.execute(
            "INSERT INTO training_cards(entry_id, due_at) VALUES(?, ?)",
            (entry_id, now_ts),
        )
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def _list_due_entries_conn(self, conn, src_langs: list[str], now_ts: int, limit: int) -> list[dict[str, Any]]:
        ph = ",".join("?" for _ in src_langs)
        sql = f"""
        SELECT
			c.id   AS card_id,
			e.id   AS entry_id,
			e.term,
			e.translation,
			e.src_lang,
			e.dst_lang,
			c.due_at,
			c.lapses,
			c.wrong_streak
		FROM training_cards c
		JOIN entries e ON e.id = c.entry_id
		WHERE c.suspended = 0
		  AND c.entry_id IS NOT NULL
		  AND c.due_at IS NOT NULL AND c.due_at <= ?
		  AND e.ignore = 0
		  AND e.src_lang IN ({ph})
		ORDER BY c.due_at ASC, c.lapses DESC, c.wrong_streak DESC
		LIMIT ?
		"""
        rows = conn.execute(sql, (now_ts, *src_langs, limit)).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def _list_new_entries_conn(self, conn, src_langs: list[str], limit: int) -> list[dict[str, Any]]:
        ph = ",".join("?" for _ in src_langs)
        sql = f"""
        SELECT
			e.id AS entry_id,
			e.term,
			e.translation,
			e.src_lang,
			e.dst_lang
		FROM entries e
		LEFT JOIN training_cards c ON c.entry_id = e.id
		WHERE e.ignore = 0
		  AND e.src_lang IN ({ph})
		  AND c.id IS NULL
		ORDER BY RANDOM()
		LIMIT ?
		"""
        rows = conn.execute(sql, (*src_langs, limit)).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def _list_hard_entries_conn(self, conn, src_langs: list[str], limit: int) -> list[dict[str, Any]]:
        ph = ",".join("?" for _ in src_langs)
        sql = f"""
        SELECT
			c.id AS card_id,
			e.id AS entry_id,
			e.term,
			e.translation,
			e.src_lang,
			e.dst_lang,
			c.lapses,
			c.wrong_streak,
			c.last_review_at
		FROM training_cards c
		JOIN entries e ON e.id = c.entry_id
		WHERE c.suspended = 0
		  AND c.entry_id IS NOT NULL
		  AND e.ignore = 0
		  AND e.src_lang IN ({ph})
		ORDER BY c.lapses DESC, c.wrong_streak DESC, COALESCE(c.last_review_at, 0) ASC
		LIMIT ?
		"""
        rows = conn.execute(sql, (*src_langs, limit)).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def _ensure_card_for_entry_conn(self, conn, entry_id: int, now_ts: int) -> int:
        row = conn.execute("SELECT id FROM training_cards WHERE entry_id=?", (entry_id,)).fetchone()
        if row:
            return row["id"]

        try:
            conn.execute("INSERT INTO training_cards(entry_id, due_at) VALUES(?, ?)", (entry_id, now_ts))
        except Exception:
			# на случай гонки/unique
            row2 = conn.execute("SELECT id FROM training_cards WHERE entry_id=?", (entry_id,)).fetchone()
            if row2:
                return row2["id"]
            raise

        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

