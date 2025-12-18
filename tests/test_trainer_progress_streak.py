from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig


def test_progress_streak(tmp_path: Path):
    db_path = tmp_path / "t.db"
    db = SQLiteRepo(db_path)

    with db.tx() as conn:
        ensure_schema(conn)
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO training_reviews(card_id, ts, grade, day) VALUES(1, 1, 5, '2025-01-01')")
        conn.execute("INSERT INTO training_reviews(card_id, ts, grade, day) VALUES(1, 2, 5, '2025-01-02')")
        conn.execute("INSERT INTO training_reviews(card_id, ts, grade, day) VALUES(1, 3, 5, '2025-01-04')")

    svc = TrainerService(repo=TrainerRepo(db=db), cfg=TrainerConfig(recent_days=7, mastery_count=5, recent_ratio=0.7))

    p = svc.get_progress(datetime(2025, 1, 4, 12, 0, tzinfo=timezone.utc))
    assert p["day"] == "2025-01-04"
    assert p["today_done"] == 1
    assert p["streak_days"] == 1

    p2 = svc.get_progress(datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc))
    assert p2["today_done"] == 1
    assert p2["streak_days"] == 2
