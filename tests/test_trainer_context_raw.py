from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig


def test_pick_returns_context_raw_if_present(tmp_path: Path):
	db_path = tmp_path / "t.db"
	db = SQLiteRepo(db_path)

	now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
	now_ts = int(now.timestamp())

	with db.tx() as conn:
		ensure_schema(conn)
		conn.row_factory = sqlite3.Row

		conn.execute("""
			INSERT INTO entries(term, translation, src_lang, dst_lang, detected_raw, created_at, ignore)
			VALUES(?, ?, ?, ?, ?, ?, 0)
		""", ("one", "один", "EN", "UK", "I have one apple.", now.isoformat()))
		entry_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

		conn.execute("INSERT INTO training_cards(entry_id, due_at) VALUES(?, ?)", (entry_id, now_ts - 10))

	svc = TrainerService(repo=TrainerRepo(db=db), cfg=TrainerConfig(recent_days=7, mastery_count=5, recent_ratio=0.7))
	item = svc.pick_training_word("EN", now=now, now_s=now.isoformat(), parse_dt=lambda s: datetime.fromisoformat(s))

	assert item["entry_id"] == entry_id
	assert item.get("context_raw") == "I have one apple."

