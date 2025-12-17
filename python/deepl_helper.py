#!/usr/bin/env python3
# vim-deepl - DeepL translation and vocabulary trainer for Vim
# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

import sys
import os
import json
import sqlite3
import urllib.request
import urllib.parse
import random
import hashlib
from datetime import datetime

from vim_deepl.utils.logging import setup_logging
from vim_deepl.utils.logging import get_logger
from vim_deepl.utils.config import load_config
from vim_deepl.utils.config import load_config
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.dict_repo import DictRepo, resolve_db_path
from vim_deepl.repos.trainer_repo import TrainerRepo
from vim_deepl.transport.vim_stdio import run, _ok, _fail
from vim_deepl.services.trainer_service import TrainerService, TrainerConfig
from vim_deepl.services.dict_service import DictService
from vim_deepl.services.container import build_services, TranslationHooks
from vim_deepl.services.translation_service import TranslationDeps

from pathlib import Path


LOGGER = get_logger("deepl_helper")

MW_SD3_ENDPOINT = "https://www.dictionaryapi.com/api/v3/references/sd3/json/"
MW_SD3_ENV_VAR = "MW_SD3_API_KEY"

def load_dict(path: str):
    """Load a JSON dictionary file. Return empty dict if missing or broken."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, TypeError):
        return {}

def now_str() -> str:
    """Get current datetime as a formatted string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def save_dict(path: str, data: dict) -> None:
    """Save dictionary JSON ensuring folder exists."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

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


def get_db_path(dict_base_path: str) -> str:
    """
    Build path to SQLite DB near the old JSON dictionaries.

    Example:
    dict_base_path=~/.local/share/vim-deepl/dict
    -> ~/.local/share/vim-deepl/vocab.db
    """
    base_dir = os.path.dirname(dict_base_path)
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, DB_FILENAME)

def get_conn(dict_base_path: str) -> sqlite3.Connection:
    """
    Backward-compatible: returns sqlite3.Connection,
    but connection is created via SQLiteRepo and schema is ensured.
    """
    cfg = load_config()
    db_path = resolve_db_path(dict_base_path, cfg.db_path)
    LOGGER.info("DB open: %s", db_path)

    repo = SQLiteRepo(db_path)
    conn = repo.connect()

    # Safe to call; idempotent. No commit inside ensure_schema.
    ensure_schema(conn)
    conn.commit()  # commit DDL if any

    return conn

def init_db(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)

def ctx_hash(context: str) -> str:
    ctx = (context or "").strip()
    return hashlib.sha256(ctx.encode("utf-8")).hexdigest()

def mw_call(word: str):
    """Call Merriam-Webster Intermediate (sd3) API for an English word.

    Returns (data, error) where:
      - data is parsed JSON (Python object) or None on error
      - error is None or error message
    """
    api_key = os.environ.get(MW_SD3_ENV_VAR, "")
    if not api_key:
        return None, f"{MW_SD3_ENV_VAR} is not set."

    url = MW_SD3_ENDPOINT + urllib.parse.quote(word) + f"?key={api_key}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except Exception as e:
        return None, f"MW request error: {e}"

    if not isinstance(data, list):
        return None, "MW response is not a list."
    return data, None

def mw_extract_definitions(entries: list) -> dict:
    """Group Merriam-Webster shortdef strings by part of speech.

    Returns dict with keys: noun, verb, adjective, adverb, other.
    """
    result: dict[str, list[str]] = {
        "noun": [],
        "verb": [],
        "adjective": [],
        "adverb": [],
        "other": [],
    }

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fl = (entry.get("fl") or "").lower()  # function label / part of speech
        shortdefs = entry.get("shortdef") or []
        if not isinstance(shortdefs, list):
            continue

        # Decide bucket
        if fl == "noun":
            bucket = "noun"
        elif fl == "verb":
            bucket = "verb"
        elif fl in ("adjective", "adj."):
            bucket = "adjective"
        elif fl in ("adverb", "adv."):
            bucket = "adverb"
        else:
            bucket = "other"

        for d in shortdefs:
            if isinstance(d, str) and d.strip():
                result[bucket].append(d.strip())

    return result


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

def deepl_call(text: str, target_lang: str, context: str = ""):
    """Perform a DeepL API call."""
    api_key = os.environ.get("DEEPL_API_KEY", "")
    if not api_key:
        return None, "", "DEEPL_API_KEY is not set."

    url = "https://api-free.deepl.com/v2/translate"
    params = {
    "auth_key": api_key,
    "text": text,
    "target_lang": target_lang,
    }

    if context:
        params["context"] = context

    data = urllib.parse.urlencode(params).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")

    try:
        with urllib.request.urlopen(req) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None, "", f"DeepL request error: {e}"

    translations = response.get("translations") or []
    if not translations:
        return None, "", "DeepL empty response."

    tr_obj = translations[0]
    translated_text = tr_obj.get("text", "")
    detected_lang = tr_obj.get("detected_source_language", "")

    return translated_text, detected_lang, None

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

def translate_word(word: str, dict_base_path: str, target_lang: str, src_hint: str = "", context: str = ""):
    hooks = TranslationHooks(
        deepl_call=deepl_call,
        normalize_src_lang=normalize_src_lang,
        ctx_hash=ctx_hash,
    )
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
    hooks = TranslationHooks(
        deepl_call=deepl_call,
        normalize_src_lang=normalize_src_lang,
        ctx_hash=ctx_hash,
    )
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
    cfg = load_config()
    db = SQLiteRepo(cfg.db_path)

    # ensure schema once per run (cheap, idempotent)
    with db.tx() as conn:
        ensure_schema(conn)

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
