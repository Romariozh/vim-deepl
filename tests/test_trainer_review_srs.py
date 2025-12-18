from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig


def test_review_writes_review_and_updates_card(tmp_path: Path):
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.commit()
    conn.close()

    db = SQLiteRepo(db_path)  # если твой SQLiteRepo создаётся иначе — подстрой здесь
    repo = TrainerRepo(db=db)
    svc = TrainerService(repo=repo, cfg=TrainerConfig(recent_days=7, mastery_count=5))

    # карточка
    with db.tx() as conn:
        ensure_schema(conn)
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO training_cards DEFAULT VALUES")
        card_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    svc.review_training_card(card_id, grade=5, now=now)

    with db.tx() as conn:
        conn.row_factory = sqlite3.Row
        card = conn.execute("SELECT * FROM training_cards WHERE id=?", (card_id,)).fetchone()
        assert card["reps"] == 1
        assert card["lapses"] == 0
        assert card["interval_days"] == 1
        assert card["last_grade"] == 5

    with db.tx() as conn:
        conn.row_factory = sqlite3.Row
        cnt = conn.execute("SELECT COUNT(*) AS c FROM training_reviews WHERE card_id=?", (card_id,)).fetchone()["c"]
        assert cnt == 1

