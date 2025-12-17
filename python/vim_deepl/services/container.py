from __future__ import annotations

from dataclasses import dataclass

from vim_deepl.utils.config import load_config
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.dict_repo import DictRepo, resolve_db_path
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.services.dict_service import DictService
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig


@dataclass(frozen=True)
class Services:
    dict: DictService
    trainer: TrainerService


def build_services(dict_base_path: str, *, recent_days: int, mastery_count: int) -> Services:
    """
    Central wiring point:
      - load config
      - resolve db path
      - create repos
      - create services
    """
    cfg = load_config()
    db_path = resolve_db_path(dict_base_path, cfg.db_path)

    sqlite = SQLiteRepo(db_path)

    dict_service = DictService(DictRepo(sqlite))
    trainer_service = TrainerService(
        repo=TrainerRepo(sqlite),
        cfg=TrainerConfig(
            recent_days=recent_days,
            mastery_count=mastery_count,
            recent_ratio=0.7,
        ),
    )

    return Services(dict=dict_service, trainer=trainer_service)
