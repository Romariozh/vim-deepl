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
from datetime import datetime


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
    """Create tables/indexes if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            term         TEXT NOT NULL,
            translation  TEXT NOT NULL,
            src_lang     TEXT NOT NULL,
            dst_lang     TEXT NOT NULL,
            detected_raw TEXT,
            created_at   TEXT NOT NULL,
            last_used    TEXT,
            count        INTEGER NOT NULL DEFAULT 0,
            hard         INTEGER NOT NULL DEFAULT 0,
            ignore       INTEGER NOT NULL DEFAULT 0,
            UNIQUE(term, src_lang, dst_lang)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entries_src_ignore
            ON entries(src_lang, ignore)
        """
    )
    conn.commit()


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

def deepl_call(text: str, target_lang: str):
    """Perform a DeepL API call."""
    api_key = os.environ.get("DEEPL_API_KEY", "")
    if not api_key:
        return None, "", "DEEPL_API_KEY is not set."

    url = "https://api-free.deepl.com/v2/translate"
    params = {"auth_key": api_key, "text": text, "target_lang": target_lang}
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
):
    """
    Translation of a single word with caching in SQLite and repetition counters.
    """
    target_lang = (target_lang or "RU").upper()
    conn = get_conn(dict_base_path)
    now = now_str()

    # 1) Try to retrieve from the database (any src_lang, but the required dst_lang)
    row = db_get_entry_any_src(conn, word, target_lang)
    if row is not None:
        db_touch_usage(conn, row["id"], now)
        return {
            "type": "word",
            "source": word,
            "text": row["translation"],
            "target_lang": row["dst_lang"],
            "detected_source_lang": row["src_lang"],
            "from_cache": True,
            "timestamp": row["created_at"],
            "last_used": now,
            "count": row["count"] + 1,      # we have just increased
            "error": None,
        }

    # 2) If it is not in the database, we refer to DeepL.
    tr, detected, err = deepl_call(word, target_lang)
    if err:
        return {
            "type": "word",
            "source": word,
            "text": "",
            "target_lang": target_lang,
            "detected_source_lang": "",
            "from_cache": False,
            "timestamp": "",
            "last_used": "",
            "count": 0,
            "error": err,
        }

    src = normalize_src_lang(detected, src_hint)
    db_upsert_entry(conn, word, tr, src, target_lang, detected, now)

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

def main():
    """CLI entry point called asynchronously from Vim."""
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Not enough arguments"}, ensure_ascii=False))
        sys.exit(1)

    mode = sys.argv[1]
    text = sys.argv[2]

    if mode == "word":
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Missing dict path"}, ensure_ascii=False))
            sys.exit(1)
        dict_base_path = sys.argv[3]
        target_lang = sys.argv[4] if len(sys.argv) >= 5 else "RU"
        src_hint = sys.argv[5] if len(sys.argv) >= 6 else ""
        result = translate_word(text, dict_base_path, target_lang, src_hint)

    elif mode == "selection":
    # python3 deepl_helper.py selection "long text" ~/.local/.../dict RU EN
        if len(sys.argv) < 4:
            raise ValueError("selection mode: not enough arguments")
        text = sys.argv[2]
        dict_base = sys.argv[3]
        target = sys.argv[4] if len(sys.argv) > 4 else "RU"
        src_hint = sys.argv[5] if len(sys.argv) > 5 else ""
        result = translate_selection(text, dict_base, target, src_hint)


    elif mode == "train":
        dict_base_path = text
        src_filter = sys.argv[3] if len(sys.argv) >= 4 else ""
        result = pick_training_word(dict_base_path, src_filter or None)

    elif mode == "mark_hard":
        if len(sys.argv) < 5:
            result = {"type": "mark_hard", "error": "Not enough arguments"}
        else:
            dict_base_path = text
            src_filter = sys.argv[3]
            word = sys.argv[4]
            result = mark_hard(dict_base_path, src_filter, word)

    elif mode == "ignore":
        if len(sys.argv) < 5:
            result = {"type": "ignore", "error": "Not enough arguments"}
        else:
            dict_base_path = text
            src_filter = sys.argv[3]
            word = sys.argv[4]
            result = mark_ignore(dict_base_path, src_filter, word)

    else:
        result = {"error": f"Unknown mode: {mode}"}

    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
