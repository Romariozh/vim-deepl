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
from vim_deepl.transport.vim_stdio import run, _ok, _fail
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.schema import ensure_schema

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
    """Open SQLite connection (and initialize schema on first use)."""
    db_path = get_db_path(dict_base_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)

def db_get_entry_any_src(
    conn: sqlite3.Connection,
    term: str,
    dst_lang: str,
) -> sqlite3.Row | None:
    """
    Найти слово по term и целевому языку (dst_lang), не зная src_lang заранее.
    Берем одну самую «используемую» запись.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM entries
        WHERE term = ?
          AND dst_lang = ?
          AND ignore = 0
        ORDER BY count DESC, last_used DESC
        LIMIT 1
        """,
        (term, dst_lang),
    )
    return cur.fetchone()


def db_upsert_entry(
    conn: sqlite3.Connection,
    term: str,
    translation: str,
    src_lang: str,
    dst_lang: str,
    detected_raw: str,
    now_s: str,
) -> None:
    """Вставить или обновить запись о слове."""
    conn.execute(
        """
        INSERT INTO entries (
            term, translation, src_lang, dst_lang,
            detected_raw, created_at, last_used, count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(term, src_lang, dst_lang)
        DO UPDATE SET
            translation  = excluded.translation,
            detected_raw = excluded.detected_raw
        """,
        (term, translation, src_lang, dst_lang, detected_raw, now_s, now_s),
    )
    conn.commit()

def ctx_hash(context: str) -> str:
    ctx = (context or "").strip()
    return hashlib.sha256(ctx.encode("utf-8")).hexdigest()


def db_get_entry_ctx(conn: sqlite3.Connection, term: str, src_lang: str, dst_lang: str, h: str):
    row = conn.execute(
        """
        SELECT term, translation, src_lang, dst_lang, created_at, last_used, count
        FROM entries_ctx
        WHERE term=? AND src_lang=? AND dst_lang=? AND ctx_hash=?
        """,
        (term, src_lang, dst_lang, h),
    ).fetchone()
    if not row:
        return None
    return {
        "term": row[0],
        "translation": row[1],
        "src_lang": row[2],
        "dst_lang": row[3],
        "created_at": row[4],
        "last_used": row[5],
        "count": row[6],
    }


def db_upsert_entry_ctx(conn: sqlite3.Connection, term: str, translation: str, src_lang: str, dst_lang: str, h: str):
    now = now_str()
    conn.execute(
        """
        INSERT INTO entries_ctx(term, translation, src_lang, dst_lang, ctx_hash, created_at, last_used, count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(term, src_lang, dst_lang, ctx_hash)
        DO UPDATE SET
            translation = excluded.translation,
            last_used   = excluded.last_used,
            count       = entries_ctx.count + 1
        """,
        (term, translation, src_lang, dst_lang, h, now, now),
    )
    conn.commit()


def db_touch_usage_ctx(conn: sqlite3.Connection, term: str, src_lang: str, dst_lang: str, h: str):
    now = now_str()
    conn.execute(
        """
        UPDATE entries_ctx
        SET last_used=?, count=count+1
        WHERE term=? AND src_lang=? AND dst_lang=? AND ctx_hash=?
        """,
        (now, term, src_lang, dst_lang, h),
    )
    conn.commit()

def db_touch_usage(
    conn: sqlite3.Connection,
    entry_id: int,
    now_s: str,
) -> None:
    """Обновить last_used и увеличить count."""
    conn.execute(
        """
        UPDATE entries
        SET last_used = ?,
            count     = count + 1
        WHERE id = ?
        """,
        (now_s, entry_id),
    )
    conn.commit()

def db_get_mw_definitions(
    conn: sqlite3.Connection,
    term: str,
    src_lang: str = "EN",
) -> dict | None:
    """Return MW definitions for term/src_lang from mw_definitions, or None."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT term, src_lang,
               defs_noun, defs_verb, defs_adj, defs_adv, defs_other,
               raw_json, created_at
        FROM mw_definitions
        WHERE term = ? AND src_lang = ?
        """,
        (term, src_lang),
    )
    row = cur.fetchone()
    if not row:
        return None

    def _load(s: str | None) -> list[str]:
        if not s:
            return []
        try:
            return json.loads(s)
        except Exception:
            return []

    return {
        "term": row["term"],
        "src_lang": row["src_lang"],
        "noun": _load(row["defs_noun"]),
        "verb": _load(row["defs_verb"]),
        "adjective": _load(row["defs_adj"]),
        "adverb": _load(row["defs_adv"]),
        "other": _load(row["defs_other"]),
        "raw": row["raw_json"],  # raw JSON string
        "created_at": row["created_at"],
    }


def db_upsert_mw_definitions(
    conn: sqlite3.Connection,
    term: str,
    src_lang: str,
    defs_by_pos: dict,
    raw_json: str,
    now_s: str,
) -> None:
    """Insert or update MW definitions for a term."""
    # defs_by_pos keys: "noun", "verb", "adjective", "adverb", "other"
    noun = json.dumps(defs_by_pos.get("noun", []), ensure_ascii=False)
    verb = json.dumps(defs_by_pos.get("verb", []), ensure_ascii=False)
    adj = json.dumps(defs_by_pos.get("adjective", []), ensure_ascii=False)
    adv = json.dumps(defs_by_pos.get("adverb", []), ensure_ascii=False)
    other = json.dumps(defs_by_pos.get("other", []), ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO mw_definitions (
            term, src_lang,
            defs_noun, defs_verb, defs_adj, defs_adv, defs_other,
            raw_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(term, src_lang) DO UPDATE SET
            defs_noun = excluded.defs_noun,
            defs_verb = excluded.defs_verb,
            defs_adj  = excluded.defs_adj,
            defs_adv  = excluded.defs_adv,
            defs_other = excluded.defs_other,
            raw_json  = excluded.raw_json
        """,
        (term, src_lang, noun, verb, adj, adv, other, raw_json, now_s),
    )
    conn.commit()

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

def ensure_mw_definitions(
    conn: sqlite3.Connection,
    term: str,
    src_lang: str,
) -> dict | None:
    """Get MW definitions from DB if present, otherwise fetch from API and store.

    Returns dict like mw_extract_definitions(), or None on error.
    """
    src_lang = (src_lang or "").upper()
    if src_lang != "EN":
        return None  # MW only for English source words

    # 1) Check cache
    cached = db_get_mw_definitions(conn, term, src_lang)
    if cached:
        return {
            "noun": cached["noun"],
            "verb": cached["verb"],
            "adjective": cached["adjective"],
            "adverb": cached["adverb"],
            "other": cached["other"],
        }

    # 2) Call API
    data, err = mw_call(term)
    if err or not data:
        return None

    data, err = mw_call(term)
    if err or not data:
    # временный дебаг
        print(f"[MW DEBUG] term={term!r} err={err!r}", file=sys.stderr)
        return None


    defs_by_pos = mw_extract_definitions(data)
    # If everything is empty, don't store
    if not any(defs_by_pos.values()):
        return None

    now = now_str()
    raw_json = json.dumps(data, ensure_ascii=False)
    db_upsert_mw_definitions(conn, term, src_lang, defs_by_pos, raw_json, now)
    return defs_by_pos

def pick_training_word(dict_base_path: str, src_filter: str | None = None):
    """
    Выбрать слово/фразу для тренировки из SQLite.

    Логика полностью повторяет старую версию:
    - фильтрация по src_lang (EN/DA или оба),
    - деление на "recent" / "old" по RECENT_DAYS,
    - 70% recent / 30% old при наличии обоих,
    - приоритет:
        1. count < MASTERY_COUNT
        2. меньший count
        3. больший hard
        4. давнее использование (LRU)
    """
    now = datetime.now()
    src_filter = (src_filter or "").upper()

    if src_filter in ("EN", "DA"):
        src_langs = [src_filter]
    else:
        src_langs = ["EN", "DA"]

    conn = get_conn(dict_base_path)
    placeholders = ",".join("?" for _ in src_langs)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            id,
            term,
            translation,
            src_lang,
            dst_lang,
            detected_raw,
            created_at,
            last_used,
            count,
            hard,
            ignore
        FROM entries
        WHERE ignore = 0
          AND src_lang IN ({placeholders})
        """,
        src_langs,
    )
    rows = cur.fetchall()

    if not rows:
        return {
            "type": "train",
            "error": f"No entries for filter={src_filter or 'ALL'}",
        }

    entries = []
    for row in rows:
        last_str = (
            row["last_used"]
            or row["created_at"]
            or "1970-01-01 00:00:00"
        )
        date_dt = parse_dt(row["created_at"] or last_str)
        last_dt = parse_dt(last_str)
        age_days = (now - date_dt).days
        bucket = "recent" if age_days <= RECENT_DAYS else "old"

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
                "date_dt": date_dt,
                "bucket": bucket,
            }
        )

    # --- Статистика прогресса ---
    total = len(entries)
    mastered = sum(1 for e in entries if e["count"] >= MASTERY_COUNT)
    mastery_percent = int(round(mastered * 100 / total)) if total else 0

    # --- делим по "recent"/"old" ---
    recents = [e for e in entries if e["bucket"] == "recent"]
    olds = [e for e in entries if e["bucket"] == "old"]

    if not recents:
        pool = olds
    elif not olds:
        pool = recents
    else:
        pool = recents if random.random() < 0.7 else olds

    # сначала те, кто ещё не "mastered"
    not_mastered = [e for e in pool if e["count"] < MASTERY_COUNT]
    if not_mastered:
        pool = not_mastered

    # сортировка по приоритету
    pool.sort(key=lambda e: (e["count"], -e["hard"], e["last"]))
    chosen = pool[0]

    now_s = now_str()
    db_touch_usage(conn, chosen["id"], now_s)

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
            "mastery_threshold": MASTERY_COUNT,
            "mastery_percent": mastery_percent,
        },
        "error": None,
    }

def mark_ignore(dict_base_path: str, src_filter: str, word: str):
    """Пометить слово как игнорируемое (тренер его пропускает)."""
    src = (src_filter or "").upper()
    if src not in ("EN", "DA"):
        return {
            "type": "ignore",
            "error": f"Unsupported src_filter={src_filter}",
        }

    conn = get_conn(dict_base_path)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE entries
        SET ignore = 1
        WHERE term = ?
          AND src_lang = ?
        """,
        (word, src),
    )
    changed = cur.rowcount
    conn.commit()

    if changed == 0:
        return {
            "type": "ignore",
            "error": f"Word '{word}' not found for src_lang={src}",
        }

    return {
        "type": "ignore",
        "word": word,
        "src_lang": src,
        "ignore": True,
        "error": None,
    }

def mark_hard(dict_base_path: str, src_filter: str, word: str):
    """Увеличить 'hard' для слова (оно считается более сложным)."""
    src = (src_filter or "").upper()
    if src not in ("EN", "DA"):
        return {
            "type": "mark_hard",
            "error": f"Unsupported src_filter={src_filter}",
        }

    conn = get_conn(dict_base_path)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE entries
        SET hard = hard + 1
        WHERE term = ?
          AND src_lang = ?
        """,
        (word, src),
    )
    if cur.rowcount == 0:
        conn.commit()
        return {
            "type": "mark_hard",
            "error": f"Word '{word}' not found for src_lang={src}",
        }

    # Получим новое значение
    cur.execute(
        """
        SELECT hard
        FROM entries
        WHERE term = ?
          AND src_lang = ?
        """,
        (word, src),
    )
    row = cur.fetchone()
    conn.commit()

    return {
        "type": "mark_hard",
        "word": word,
        "src_lang": src,
        "hard": row["hard"] if row else None,
        "error": None,
    }


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

def translate_word(
    word: str,
    dict_base_path: str,
    target_lang: str,
    src_hint: str = "",
    context: str = "",
):
    """Translation of a single word with caching in SQLite and repetition counters."""
    target_lang = (target_lang or "RU").upper()

    conn = get_conn(dict_base_path)
    now = now_str()

    # --- debug/meta flags ---
    ctx = (context or "").strip()
    context_used = bool(ctx)
    cache_source = None  # "context" | "base" | None

    # ------------------------------------------------------------
    # 1) CONTEXT MODE (separate cache: entries_ctx)
    # ------------------------------------------------------------
    if ctx:
        src_expected = (src_hint or "").upper() or "EN"
        h = ctx_hash(ctx)

        cached = db_get_entry_ctx(conn, word, src_expected, target_lang, h)
        if cached:
            # update usage counter in context cache
            db_touch_usage_ctx(conn, word, src_expected, target_lang, h)

            mw_defs = None
            if cached["src_lang"] == "EN":
                mw_defs = ensure_mw_definitions(conn, word, cached["src_lang"])

            return {
                "type": "word",
                "source": word,
                "text": cached["translation"],
                "target_lang": target_lang,
                "detected_source_lang": cached["src_lang"],
                "from_cache": True,
                "timestamp": cached["created_at"],
                "last_used": now,
                "count": cached["count"] + 1,
                "error": None,
                "mw_definitions": mw_defs,
                "context_used": True,
                "cache_source": "context",
            }

        # not in context cache -> call DeepL with context
        tr, detected, err = deepl_call(word, target_lang, context=ctx)
        if err:
            return {
                "type": "word",
                "source": word,
                "text": "",
                "target_lang": target_lang,
                "detected_source_lang": "",
                "from_cache": False,
                "timestamp": now,
                "last_used": now,
                "count": 0,
                "error": err,
                "mw_definitions": None,
                "context_used": True,
                "cache_source": None,
            }

        # normalize detected source
        src = normalize_src_lang(detected, src_hint)

        # store ONLY in context cache (do not touch base entries)
        db_upsert_entry_ctx(conn, word, tr, src, target_lang, h)

        mw_defs = None
        if src == "EN":
            mw_defs = ensure_mw_definitions(conn, word, src)

        return {
            "type": "word",
            "source": word,
            "text": tr,
            "target_lang": target_lang,
            "detected_source_lang": src,
            "from_cache": False,
            "timestamp": now,
            "last_used": now,
            "count": 1,
            "error": None,
            "mw_definitions": mw_defs,
            "context_used": True,
            "cache_source": None,
        }

    # ------------------------------------------------------------
    # 2) BASE MODE (original cache: entries)
    # ------------------------------------------------------------
    row = db_get_entry_any_src(conn, word, target_lang)
    if row is not None:
        db_touch_usage(conn, row["id"], now)

        mw_defs = None
        if row["src_lang"] == "EN":
            mw_defs = ensure_mw_definitions(conn, word, row["src_lang"])

        return {
            "type": "word",
            "source": word,
            "text": row["translation"],
            "target_lang": target_lang,
            "detected_source_lang": row["src_lang"],
            "from_cache": True,
            "timestamp": row["created_at"],
            "last_used": now,
            "count": row["count"] + 1,
            "error": None,
            "mw_definitions": mw_defs,
            "context_used": False,
            "cache_source": "base",
        }

    # fallback: call DeepL (no context)
    tr, detected, err = deepl_call(word, target_lang, context="")
    if err:
        return {
            "type": "word",
            "source": word,
            "text": "",
            "target_lang": target_lang,
            "detected_source_lang": "",
            "from_cache": False,
            "timestamp": now,
            "last_used": now,
            "count": 0,
            "error": err,
            "mw_definitions": None,
            "context_used": False,
            "cache_source": None,
        }

    src = normalize_src_lang(detected, src_hint)

    # store in base cache
    db_upsert_entry(conn, word, tr, src, target_lang, detected, now)

    mw_defs = None
    if src == "EN":
        mw_defs = ensure_mw_definitions(conn, word, src)

    return {
        "type": "word",
        "source": word,
        "text": tr,
        "target_lang": target_lang,
        "detected_source_lang": src,
        "from_cache": False,
        "timestamp": now,
        "last_used": now,
        "count": 1,
        "error": None,
        "mw_definitions": mw_defs,
        "context_used": False,
        "cache_source": None,
    }

def translate_selection(
    text: str,
    dict_base_path: str,
    target_lang: str,
    src_hint: str = "",
):
    """
    Translation of any text fragment (4+ words and more).
    No dictionary/SQLite is used here – we simply proxy DeepL.
    """
    target_lang = (target_lang or "RU").upper()

    tr, detected, err = deepl_call(text, target_lang)
    if err:
        return {
            "type": "selection",
            "source": text,
            "text": "",
            "target_lang": target_lang,
            "detected_source_lang": "",
            "error": err,
        }

    src = normalize_src_lang(detected, src_hint)

    return {
        "type": "selection",
            "source": text,
            "text": tr,
            "target_lang": target_lang,
            "detected_source_lang": src,
            "error": None,
    }

def _ok(data):
    return {"ok": True, "data": data}

def _fail(message, code="ERROR", details=None):
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err}

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
        return _ok(result)

    elif mode == "selection":
        # python3 deepl_helper.py selection "long text" ~/.local/.../dict RU EN
        if len(argv) < 4:
            LOGGER.warning("selection mode: not enough arguments argv=%s", argv)
            return _fail("selection mode: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base = argv[3]
        target = argv[4] if len(argv) > 4 else "RU"
        src_hint = argv[5] if len(argv) > 5 else ""
        result = translate_selection(text, dict_base, target, src_hint)
        return _ok(result)

    elif mode == "train":
        dict_base_path = text
        src_filter = argv[3] if len(argv) >= 4 else ""
        result = pick_training_word(dict_base_path, src_filter or None)
        return _ok(result)

    elif mode == "mark_hard":
        if len(argv) < 5:
            LOGGER.warning("mark_hard: not enough arguments argv=%s", argv)
            return _fail("mark_hard: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base_path = text
        src_filter = argv[3]
        word = argv[4]
        result = mark_hard(dict_base_path, src_filter, word)
        return _ok(result)

    elif mode == "ignore":
        if len(argv) < 5:
            LOGGER.warning("ignore: not enough arguments argv=%s", argv)
            return _fail("ignore: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base_path = text
        src_filter = argv[3]
        word = argv[4]
        result = mark_ignore(dict_base_path, src_filter, word)
        return _ok(result)

    else:
        LOGGER.warning("Unknown mode: %s argv=%s", mode, argv)
        return _fail(f"Unknown mode: {mode}", code="ARGS", details={"argv": argv})

if __name__ == "__main__":
    run(dispatch)
