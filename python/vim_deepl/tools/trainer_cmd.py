# SPDX-License-Identifier: LGPL-3.0-only

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig


def _default_db_path() -> Path:
    p = os.environ.get("VIM_DEEPL_DB")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".local/share/vim-deepl/vocab.db"


def _make_service(db_path: Path) -> TrainerService:
    db = SQLiteRepo(db_path)
    repo = TrainerRepo(db=db)
    cfg = TrainerConfig(recent_days=7, mastery_count=5, recent_ratio=0.7)
    return TrainerService(repo=repo, cfg=cfg)


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def cmd_next(args: argparse.Namespace) -> int:
    svc = _make_service(args.db)
    now = datetime.now(timezone.utc)
    item = svc.pick_training_word(
        args.src,
        now=now,
        now_s=now.isoformat(),
        parse_dt=datetime.fromisoformat,
    )
    _print_json(item)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    svc = _make_service(args.db)
    now = datetime.now(timezone.utc)

    svc.review_training_card(args.card_id, grade=args.grade, now=now)

    item = svc.pick_training_word(
        args.src,
        now=now,
        now_s=now.isoformat(),
        parse_dt=datetime.fromisoformat,
    )
    _print_json(item)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="vim-deepl-trainer")
    p.add_argument("--db", type=Path, default=_default_db_path(), help="Path to vocab.db")

    sub = p.add_subparsers(dest="cmd", required=True)

    p_next = sub.add_parser("next", help="Pick next training item")
    p_next.add_argument("--src", default="EN", help="Source language filter (e.g. EN)")
    p_next.set_defaults(fn=cmd_next)

    p_rev = sub.add_parser("review", help="Review a card (0..5) and return next item")
    p_rev.add_argument("--src", default="EN", help="Source language filter (e.g. EN)")
    p_rev.add_argument("--card-id", type=int, required=True)
    p_rev.add_argument("--grade", type=int, required=True, choices=range(0, 6))
    p_rev.set_defaults(fn=cmd_review)

    args = p.parse_args()
    args.db = args.db.expanduser()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
