# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

from dataclasses import dataclass

from typing import Optional, Callable

from vim_deepl.utils.config import load_config
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.dict_repo import DictRepo, resolve_db_path
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.repos.translation_repo import TranslationRepo
from vim_deepl.services.dict_service import DictService
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig
from vim_deepl.services.translation_service import TranslationService, TranslationDeps
from vim_deepl.integrations.merriam_webster import mw_fetch
from vim_deepl.integrations.deepl import deepl_call


@dataclass(frozen=True)
class TranslationHooks:
    normalize_src_lang: Callable
    ctx_hash: Callable

@dataclass(frozen=True)
class Services:
    dict: DictService
    trainer: TrainerService
    translation: Optional[TranslationService] = None

def build_services(
    dict_base_path: str,
    *,
    cfg,
    recent_days: int,
    mastery_count: int,
    translation_hooks: Optional[TranslationHooks] = None,
) -> Services:

    """
    Central wiring point:
      - load config
      - resolve db path
      - create repos
      - create services
    """
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

    translation_service = None
    if translation_hooks is not None:
        translation_deps = TranslationDeps(
            deepl_call=deepl_call,
            normalize_src_lang=translation_hooks.normalize_src_lang,
            ctx_hash=translation_hooks.ctx_hash,
            mw_fetch=mw_fetch,
        )
        translation_service = TranslationService(
            repo=TranslationRepo(sqlite),
            deps=translation_deps,
        )

    return Services(dict=dict_service, trainer=trainer_service, translation=translation_service)

