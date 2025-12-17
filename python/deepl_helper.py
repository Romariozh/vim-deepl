#!/usr/bin/env python3
# vim-deepl - DeepL translation and vocabulary trainer for Vim
# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

import sys
import os
import json
import sqlite3
import random
import hashlib
from datetime import datetime

from vim_deepl.utils.logging import setup_logging
from vim_deepl.utils.logging import get_logger
from vim_deepl.utils.config import load_config
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.dict_repo import DictRepo, resolve_db_path
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.transport.vim_stdio import run
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig
from vim_deepl.services.dict_service import DictService
from vim_deepl.services.container import build_services, TranslationHooks
from vim_deepl.services.translation_service import TranslationDeps

from pathlib import Path


LOGGER = get_logger("deepl_helper")

MW_SD3_ENDPOINT = "https://www.dictionaryapi.com/api/v3/references/sd3/json/"
MW_SD3_ENV_VAR = "MW_SD3_API_KEY"

def now_str() -> str:
    """Get current datetime as a formatted string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Recent/learning parameters
RECENT_DAYS = 7      # Words added within the last X days are considered "recent"
MASTERY_COUNT = 7    # How many repetitions to consider a word mastered

def parse_dt(s: str) -> datetime:
    """Parse datetime string or return epoch default."""
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime(1970, 1, 1)

# --- SQLite storage ---

DB_FILENAME = "vocab.db"


def ctx_hash(context: str) -> str:
    ctx = (context or "").strip()
    return hashlib.sha256(ctx.encode("utf-8")).hexdigest()


def pick_training_word(dict_base_path: str, src_filter: str | None = None):
    services = build_services(dict_base_path, recent_days=RECENT_DAYS, mastery_count=MASTERY_COUNT)

    now = datetime.now()
    now_s = now_str()

    return services.trainer.pick_training_word(
        src_filter=src_filter,
        now=now,
        now_s=now_s,
        parse_dt=parse_dt,
    )

def mark_ignore(dict_base_path: str, src_filter: str, word: str):
    services = build_services(dict_base_path, recent_days=RECENT_DAYS, mastery_count=MASTERY_COUNT)
    return services.dict.mark_ignore(word=word, src_filter=src_filter)

def mark_hard(dict_base_path: str, src_filter: str, word: str):
    services = build_services(dict_base_path, recent_days=RECENT_DAYS, mastery_count=MASTERY_COUNT)
    return services.dict.mark_hard(word=word, src_filter=src_filter)


def oneline(text: str) -> str:
    """Convert text into a single whitespace-normalized line."""
    parts = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(parts)

def normalize_src_lang(detected: str, src_hint: str) -> str:
    """Normalize DeepL language code using fallback to hint."""
    code = (detected or "").upper()
    hint = (src_hint or "").upper()

    if code.startswith("EN"):
        return "EN"
    if code.startswith("DA"):
        return "DA"
    if hint in ("EN", "DA"):
        return hint
    return "EN"

def build_translation_hooks() -> TranslationHooks:
    return TranslationHooks(
        normalize_src_lang=normalize_src_lang,
        ctx_hash=ctx_hash,
    )

def translate_word(word: str, dict_base_path: str, target_lang: str, src_hint: str = "", context: str = ""):
    hooks = build_translation_hooks()
    services = build_services(
        dict_base_path,
        recent_days=RECENT_DAYS,
        mastery_count=MASTERY_COUNT,
        translation_hooks=hooks,
    )
    now_s = now_str()
    return services.translation.translate_word(
        word=word,
        target_lang=target_lang,
        src_hint=src_hint,
        now_s=now_s,
        context=context,
    )


def translate_selection(text: str, dict_base_path: str, target_lang: str, src_hint: str = ""):
    hooks = build_translation_hooks()
    services = build_services(
        dict_base_path,
        recent_days=RECENT_DAYS,
        mastery_count=MASTERY_COUNT,
        translation_hooks=hooks,
    )
    return services.translation.translate_selection(text=text, target_lang=target_lang, src_hint=src_hint)


def _ok(data):
    return {"ok": True, "data": data}

def _fail(message, code="ERROR", details=None):
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err}

def _wrap_result(result: dict):
    err = result.get("error")
    if err:
        return _fail(str(err), code="ERROR", details=result)
    return _ok(result)

def dispatch(argv):

    """Business dispatcher: returns JSON-ready dict (ok:true/false)."""
    if len(argv) < 3:
        LOGGER.warning("Not enough arguments: argv=%s", argv)
        return _fail("Not enough arguments", code="ARGS", details={"argv": argv})

    mode = argv[1]
    text = argv[2]

    if mode == "word":
        if len(argv) < 4:
            LOGGER.warning("Missing dict path: argv=%s", argv)
            return _fail("Missing dict path", code="ARGS", details={"argv": argv})

        dict_base_path = argv[3]
        target_lang = argv[4] if len(argv) >= 5 else "RU"
        src_hint = argv[5] if len(argv) >= 6 else ""
        result = translate_word(text, dict_base_path, target_lang, src_hint)
        return _wrap_result(result)

    elif mode == "selection":
        # python3 deepl_helper.py selection "long text" ~/.local/.../dict RU EN
        if len(argv) < 4:
            LOGGER.warning("selection mode: not enough arguments argv=%s", argv)
            return _fail("selection mode: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base = argv[3]
        target = argv[4] if len(argv) > 4 else "RU"
        src_hint = argv[5] if len(argv) > 5 else ""
        result = translate_selection(text, dict_base, target, src_hint)
        return _wrap_result(result)

    elif mode == "train":
        dict_base_path = text
        src_filter = argv[3] if len(argv) >= 4 else ""
        result = pick_training_word(dict_base_path, src_filter or None)
        return _wrap_result(result)

    elif mode == "mark_hard":
        if len(argv) < 5:
            LOGGER.warning("mark_hard: not enough arguments argv=%s", argv)
            return _fail("mark_hard: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base_path = text
        src_filter = argv[3]
        word = argv[4]
        result = mark_hard(dict_base_path, src_filter, word)
        return _wrap_result(result)

    elif mode == "ignore":
        if len(argv) < 5:
            LOGGER.warning("ignore: not enough arguments argv=%s", argv)
            return _fail("ignore: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base_path = text
        src_filter = argv[3]
        word = argv[4]
        result = mark_ignore(dict_base_path, src_filter, word)
        return _wrap_result(result)

    else:
        LOGGER.warning("Unknown mode: %s argv=%s", mode, argv)
        return _fail(f"Unknown mode: {mode}", code="ARGS", details={"argv": argv})

if __name__ == "__main__":
    run(dispatch)
