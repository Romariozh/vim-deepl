from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
from typing import Any, Dict, List, Optional

from vim_deepl.repos.trainer_repo import TrainerRepo


@dataclass(frozen=True)
class TrainerConfig:
    recent_days: int
    mastery_count: int
    recent_ratio: float = 0.7


@dataclass(frozen=True)
class TrainerService:
    repo: TrainerRepo
    cfg: TrainerConfig

    def pick_training_word(self, src_filter: Optional[str], now: datetime, now_s: str, parse_dt) -> Dict[str, Any]:
        """
        Pure business logic + repo calls:
          - fetch candidates
          - choose a word
          - touch usage
          - return response dict (without ok/fail wrapper)
        parse_dt is injected from existing helper to preserve date parsing behavior.
        """
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
            date_dt = parse_dt(row.get("created_at") or last_str)
            last_dt = parse_dt(last_str)

            age_days = (now - date_dt).days
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

        pool.sort(key=lambda e: (e["count"], -e["hard"], e["last"]))
        chosen = pool[0]

        self.repo.touch_usage(chosen["id"], now_s)

        return {
            "type": "train",
            "word": chosen["word"],
            "translation": chosen["translation"],
            "src_lang": chosen["src"],
            "target_lang": chosen["target_lang"],
            "timestamp": now_s,
            "count": chosen["count"] + 1,
            "hard": chosen["hard"],
            "stats": {
                "total": total,
                "mastered": mastered,
                "mastery_threshold": self.cfg.mastery_count,
                "mastery_percent": mastery_percent,
            },
            "error": None,
        }
