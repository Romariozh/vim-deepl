# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
import sqlite3
from typing import Any, Dict, List, Optional
from vim_deepl.repos.schema import ensure_schema
import time
from datetime import timezone

from vim_deepl.repos.trainer_repo import TrainerRepo

def _update_ef(ef: float, grade: int) -> float:
    ef = ef + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    return max(1.3, ef)

def _next_interval_days(reps: int, prev_interval: int, ef: float) -> int:
    if reps <= 1:
        return 1
    if reps == 2:
        return 3
    return max(1, int(round(prev_interval * ef)))

def compute_srs(card: Dict[str, Any], grade: int, now_ts: int) -> Dict[str, Any]:
    reps = int(card.get("reps") or 0)
    lapses = int(card.get("lapses") or 0)
    ef = float(card.get("ef") or 2.5)
    interval_days = int(card.get("interval_days") or 0)
    correct_streak = int(card.get("correct_streak") or 0)
    wrong_streak = int(card.get("wrong_streak") or 0)

    if grade < 3:
        lapses += 1
        reps = 0
        interval_days = 1
        ef = _update_ef(ef, grade)
        due_at = now_ts + 86400
        wrong_streak += 1
        correct_streak = 0
    else:
        reps += 1
        ef = _update_ef(ef, grade)
        interval_days = _next_interval_days(reps, max(1, interval_days), ef)
        due_at = now_ts + interval_days * 86400
        correct_streak += 1
        wrong_streak = 0

    return {
        "reps": reps,
        "lapses": lapses,
        "ef": ef,
        "interval_days": interval_days,
        "due_at": due_at,
        "last_review_at": now_ts,
        "last_grade": grade,
        "correct_streak": correct_streak,
        "wrong_streak": wrong_streak,
    }

@dataclass(frozen=True)
class TrainerConfig:
    recent_days: int
    mastery_count: int
    recent_ratio: float = 0.7
    srs_new_ratio: float = 0.2


@dataclass(frozen=True)
class TrainerService:
    repo: TrainerRepo
    cfg: TrainerConfig


    def pick_training_word(self, src_filter: Optional[str], now: datetime, now_s: str, parse_dt, exclude_card_ids: Optional[list[int]] = None) -> Dict[str, Any]:
        """
        Pure business logic + repo calls:
          - fetch candidates
          - choose a word
          - touch usage
          - return response dict (without ok/fail wrapper)
        parse_dt is injected from existing helper to preserve date parsing behavior.
        """

        # --- SRS picker (v3) ---
        src_langs = [src_filter] if src_filter else ["EN"]  # подстрой: как у тебя сейчас формируется список
        now_ts = int(now.timestamp())

        def finalize(item: Dict[str, Any]) -> Dict[str, Any]:
            progress = self.get_progress(now)
            item.update(progress)

            # Keep both keys for backward compatibility
            ctx = (item.get("context_raw") or "").strip()
            det = (item.get("detected_raw") or "").strip()

            if not ctx and det:
                item["context_raw"] = det
                ctx = det

            if not det and ctx:
                item["detected_raw"] = ctx

            return item

        with self.repo.db.tx() as conn:
            ensure_schema(conn)
            conn.row_factory = sqlite3.Row

            due = self.repo._list_due_entries_conn(conn, src_langs, now_ts, limit=1, exclude_card_ids=exclude_card_ids)
            if due:
                item = due[0]
                item["mode"] = "srs_due"
                return finalize(item)

            import random
            pick_new = random.random() < float(getattr(self.cfg, "srs_new_ratio", 0.2))

            if pick_new:
                new_items = self.repo._list_new_entries_conn(conn, src_langs, limit=1)
                if new_items:
                    item = new_items[0]
                    card_id = self.repo._ensure_card_for_entry_conn(conn, item["entry_id"], now_ts)
                    item["card_id"] = card_id
                    item["mode"] = "srs_new"
                    return finalize(item)

            hard_n = int(getattr(self.cfg, "hard_random_top_n", 5))
            hard = self.repo._list_hard_entries_conn(
                conn,
                src_langs,
                now_ts=now_ts,
                limit=max(1, hard_n),
                exclude_card_ids=exclude_card_ids,
                allow_future=True,
            )
            # Удалить после проверки
            print("DBG hard_cnt=", len(hard), "allow_future=True", flush=True)

            if hard:
                top = hard[: max(1, min(len(hard), hard_n))]
                if len(top) == 1:
                    item = top[0]
                else:
                    idx = int(random.triangular(0, len(top) - 1, 0))
                    item = top[idx]

                item["mode"] = "srs_hard"
                return finalize(item)
        # --- fallback to existing logic (your current implementation) ---

        src_filter_u = (src_filter or "").upper()
        if src_filter_u in ("EN", "DA"):
            src_langs = [src_filter_u]
        else:
            src_langs = ["EN", "DA"]

        # Map exclude_card_ids -> exclude_entry_ids for fallback picker
        exclude_entry_ids: set[int] = set()
        if exclude_card_ids:
            with self.repo.db.tx() as conn:
                ensure_schema(conn)
                conn.row_factory = sqlite3.Row
                ph = ",".join(["?"] * len(exclude_card_ids))
                sql = f"SELECT entry_id FROM training_cards WHERE id IN ({ph})"
                for r in conn.execute(sql, list(exclude_card_ids)).fetchall():
                    exclude_entry_ids.add(int(r["entry_id"]))

        rows = self.repo.list_entries_for_training(src_langs)
        if not rows:
            return {"type": "train", "error": f"No entries for filter={src_filter_u or 'ALL'}"}

         # If the client excluded everything in this session, ignore exclusions (backend-side reset).
        ignore_exclusions = False
        if exclude_entry_ids and len(exclude_entry_ids) >= len(rows):
            exclude_entry_ids.clear()
            ignore_exclusions = True

        entries: List[Dict[str, Any]] = []
        for row in rows:
            # IMPORTANT: normalize the real entries.id (some queries may return joined "id")
            row_entry_id = int(row.get("entry_id") or row["id"])
            if row_entry_id in exclude_entry_ids:
                continue

            last_str = row.get("last_used") or row.get("created_at") or "1970-01-01 00:00:00"
            date_dt = parse_dt(row["created_at"])
            last_dt = parse_dt(last_str)

            age_days = (now.date() - date_dt.date()).days
            bucket = "recent" if age_days <= self.cfg.recent_days else "old"

            entries.append(
                {
                    "entry_id": row_entry_id,
                    "id": row["id"],  # keep for debugging/back-compat
                    "src": row["src_lang"],
                    "word": row["term"],
                    "translation": row["translation"],
                    "target_lang": row["dst_lang"],
                    "count": row["count"],
                    "hard": row["hard"],
                    "last": last_dt,
                    "bucket": bucket,
                }
            )

        # ---- FALLBACK SAFETY: if we excluded everything in this session, ignore exclusions ----
        if not entries:
            exclude_entry_ids.clear()
            for row in rows:
                row_entry_id = int(row.get("entry_id") or row["id"])

                last_str = row.get("last_used") or row.get("created_at") or "1970-01-01 00:00:00"
                date_dt = parse_dt(row["created_at"])
                last_dt = parse_dt(last_str)

                age_days = (now.date() - date_dt.date()).days
                bucket = "recent" if age_days <= self.cfg.recent_days else "old"

                entries.append(
                    {
                        "entry_id": row_entry_id,
                        "id": row["id"],
                        "src": row["src_lang"],
                        "word": row["term"],
                        "translation": row["translation"],
                        "target_lang": row["dst_lang"],
                        "count": row["count"],
                        "hard": row["hard"],
                        "last": last_dt,
                        "bucket": bucket,
                    }
                )

        # ------------------------------------------------------------------------------

        total = len(entries)
        mastered = sum(1 for e in entries if e["count"] >= self.cfg.mastery_count)
        mastery_percent = int(round(mastered * 100 / total)) if total else 0

        recents = [e for e in entries if e["bucket"] == "recent"]
        olds = [e for e in entries if e["bucket"] == "old"]

        if not recents:
            pool = olds
        elif not olds:
            pool = recents
        else:
            pool = recents if random.random() < self.cfg.recent_ratio else olds

        not_mastered = [e for e in pool if e["count"] < self.cfg.mastery_count]
        if not_mastered:
            pool = not_mastered

        for it in pool:
            last_dt = it.get("last")
            if isinstance(last_dt, datetime):
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                it["last_ts"] = int(last_dt.timestamp())
            else:
                it["last_ts"] = 0

        pool.sort(key=lambda e: (e["count"], -e["hard"], e["last_ts"]))

        # Randomize inside the "best" slice to avoid repeating the same deterministic order.
        if len(pool) == 1:
            chosen = pool[0]
        else:
            mode = max(0, int(len(pool) * 0.2))
            idx = int(random.triangular(0, len(pool) - 1, mode))
            chosen = pool[idx]

        # If we had to ignore exclusions (exclude covered everything), try not to return the last-excluded card again.
        if ignore_exclusions and exclude_card_ids and len(pool) > 1:
            try:
                last_cid = int(exclude_card_ids[-1])
                with self.repo.db.tx() as conn:
                    ensure_schema(conn)
                    conn.row_factory = sqlite3.Row
                    r = conn.execute("SELECT entry_id FROM training_cards WHERE id=?", (last_cid,)).fetchone()
                last_eid = int(r["entry_id"]) if r else None
            except Exception:
                last_eid = None

            if last_eid is not None:
                # re-roll a few times to avoid immediate repeat
                for _ in range(5):
                    if int(chosen.get("entry_id") or chosen.get("id") or 0) != last_eid:
                        break
                    idx = int(random.triangular(0, len(pool) - 1, 0))
                    chosen = pool[idx]

        # IMPORTANT: fallback browsing (key 'n' / skip) must NOT change count/last_used.
        # Count should increase only when the user grades a card (review 0..5).

        # ensure card exists so UI/review can work uniformly
        with self.repo.db.tx() as conn:
            ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            chosen_entry_id = int(chosen.get("entry_id") or chosen["id"])
            card_id = self.repo._ensure_card_for_entry_conn(conn, chosen_entry_id, now_ts)

        item = {
            "mode": "fallback",
            "card_id": card_id,
            "entry_id": chosen_entry_id,
            "term": chosen["word"],
            "translation": chosen["translation"],
            "src_lang": chosen["src"],
            "dst_lang": chosen["target_lang"],
            "timestamp": now_s,
            "count": chosen["count"],
            "hard": chosen["hard"],
            "stats": {
                "total": total,
                "mastered": mastered,
                "mastery_threshold": self.cfg.mastery_count,
                "mastery_percent": mastery_percent,
            },
        }

        return finalize(item)

    def review_training_card(self, card_id: int, grade: int, now: datetime) -> Dict[str, Any]:
        if not (0 <= grade <= 5):
            raise ValueError("grade must be in range 0..5")

        now_ts = int(now.timestamp())
        day = now.date().isoformat()

        with self.repo.db.tx() as conn:
            ensure_schema(conn)
            conn.row_factory = sqlite3.Row

            card = self.repo._get_training_card_conn(conn, card_id)
            if not card:
                raise ValueError(f"training_card not found: id={card_id}")
            if int(card.get("suspended") or 0) == 1:
                raise ValueError(f"training_card suspended: id={card_id}")

            srs = compute_srs(card, grade, now_ts)

            self.repo._insert_training_review_conn(conn, card_id, now_ts, grade, day)
            self.repo._update_training_card_srs_conn(conn, card_id, srs)

            # Count only graded answers (0-5). Views/fallback must NOT call this block.
            now_s = now.strftime("%Y-%m-%d %H:%M:%S")
            entry_id = int(card.get("entry_id") or 0)
            if entry_id:
                conn.execute(
                    """
                    UPDATE entries
                    SET last_used = ?,
                        count = count + 1
                    WHERE id = ?
                    """,
                    (now_s, entry_id),
                )

            return srs

    def get_progress(self, now: datetime) -> Dict[str, Any]:
        day = now.date().isoformat()

        with self.repo.db.tx() as conn:
            ensure_schema(conn)
            conn.row_factory = sqlite3.Row

            today_done = self.repo._count_reviews_for_day_conn(conn, day)
            days = self.repo._list_active_days_desc_conn(conn)

        # streak: сколько подряд дней до today включительно, где были reviews
        from datetime import date as _date
        active = set(days)
        streak = 0
        cur = _date.fromisoformat(day)
        while cur.isoformat() in active:
            streak += 1
            cur = cur.fromordinal(cur.toordinal() - 1)

        return {"day": day, "today_done": today_done, "streak_days": streak}
