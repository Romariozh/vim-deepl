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

            hard = self.repo._list_hard_entries_conn(conn, src_langs, now_ts=now_ts, limit=1, exclude_card_ids=exclude_card_ids)
            if hard:
                item = hard[0]
                item["mode"] = "srs_hard"
                return finalize(item)

        # --- fallback to existing logic (your current implementation) ---

        src_filter_u = (src_filter or "").upper()
        if src_filter_u in ("EN", "DA"):
            src_langs = [src_filter_u]
        else:
            src_langs = ["EN", "DA"]

        rows = self.repo.list_entries_for_training(src_langs)
        if not rows:
            return {"type": "train", "error": f"No entries for filter={src_filter_u or 'ALL'}"}

        entries: List[Dict[str, Any]] = []
        for row in rows:
            last_str = row.get("last_used") or row.get("created_at") or "1970-01-01 00:00:00"
            date_dt = parse_dt(row["created_at"])
            last_dt = parse_dt(last_str)

            age_days = (now.date() - date_dt.date()).days
            bucket = "recent" if age_days <= self.cfg.recent_days else "old"

            entries.append(
                {
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
            last_dt = parse_dt(it["last_used"]) if it.get("last_used") else None
            if last_dt and last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            it["last_ts"] = int(last_dt.timestamp()) if last_dt else 0

        pool.sort(key=lambda e: (e["count"], -e["hard"], e["last_ts"]))
        chosen = pool[0]

        self.repo.touch_usage(chosen["id"], now_s)

        # ensure card exists so UI/review can work uniformly
        with self.repo.db.tx() as conn:
            ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            card_id = self.repo._ensure_card_for_entry_conn(conn, chosen["id"], now_ts)

        item = {
            "mode": "fallback",
            "card_id": card_id,
            "entry_id": chosen["id"],
            "term": chosen["word"],
            "translation": chosen["translation"],
            "src_lang": chosen["src"],
            "dst_lang": chosen["target_lang"],
            "timestamp": now_s,
            "count": chosen["count"] + 1,
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
