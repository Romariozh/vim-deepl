import os
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Make sure we can import deepl_helper from ./python
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.join(BASE_DIR, "python")
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from deepl_helper import (
    get_conn,
    db_get_entry_any_src,
    db_upsert_entry,
    db_touch_usage,
    now_str,
    translate_word,
    translate_selection,
    pick_training_word,
    mark_hard,
    mark_ignore,
)

# Fixed dict base path (same as used by Vim)
DICT_BASE = os.path.expanduser("~/.local/share/vim-deepl/dict")


class Entry(BaseModel):
    term: str
    translation: str
    src_lang: str
    dst_lang: str
    detected_raw: Optional[str] = None

class WordRequest(BaseModel):
    term: str
    target_lang: str
    src_hint: Optional[str] = ""
    context: Optional[str] = None

class TrainRequest(BaseModel):
    src_filter: Optional[str] = None

class MarkRequest(BaseModel):
    word: str
    src_filter: str

class SelectionRequest(BaseModel):
    text: str
    target_lang: str
    src_hint: Optional[str] = ""


app = FastAPI(title="Local Dict API")


@app.get("/entries")
def get_entry(term: str, dst_lang: str):
    """
    Query a single translation from SQLite dictionary.
    """
    conn = get_conn(DICT_BASE)
    row = db_get_entry_any_src(conn, term, dst_lang.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    now = now_str()
    db_touch_usage(conn, row["id"], now)
    return {
        "term": row["term"],
        "translation": row["translation"],
        "src_lang": row["src_lang"],
        "dst_lang": row["dst_lang"],
        "created_at": row["created_at"],
        "last_used": now,
        "count": row["count"] + 1,
    }


@app.post("/entries")
def create_or_update_entry(entry: Entry):
    """
    Insert or update translation.
    """
    conn = get_conn(DICT_BASE)
    now = now_str()
    db_upsert_entry(
        conn,
        entry.term,
        entry.translation,
        entry.src_lang.upper(),
        entry.dst_lang.upper(),
        entry.detected_raw or entry.src_lang.upper(),
        now,
    )
    return {"status": "ok"}


@app.post("/entries/use")
def mark_used(term: str, src_lang: str, dst_lang: str):
    """
    Increment usage counter.
    """
    conn = get_conn(DICT_BASE)
    now = now_str()
    row = db_get_entry_any_src(conn, term, dst_lang.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db_touch_usage(conn, row["id"], now)
    return {"status": "ok"}

@app.post("/translate/word")
def api_translate_word(payload: WordRequest):
    """
    Translate a single word using DeepL + SQLite dictionary.
    """
    result = translate_word(
        payload.term,
        DICT_BASE,
        payload.target_lang,
        payload.src_hint or "",
        payload.context or "",
    )
    return result

@app.post("/translate/selection")
def api_translate_selection(payload: SelectionRequest):
    """
    Translate an arbitrary text selection using DeepL.
    Dictionary/SQLite is not used here; this is pure DeepL proxy.
    """
    result = translate_selection(
        payload.text,
        DICT_BASE,
        payload.target_lang,
        payload.src_hint or "",
    )
    return result

@app.post("/train/next")
def api_train_next(payload: TrainRequest):
    """
    Pick next training word from dictionary.
    """
    result = pick_training_word(DICT_BASE, payload.src_filter)
    return result


@app.post("/train/mark_hard")
def api_mark_hard(payload: MarkRequest):
    """
    Mark word as hard (increase 'hard' counter).
    """
    result = mark_hard(DICT_BASE, payload.src_filter, payload.word)
    return result


@app.post("/train/mark_ignore")
def api_mark_ignore(payload: MarkRequest):
    """
    Mark word as ignored (trainer will skip it).
    """
    result = mark_ignore(DICT_BASE, payload.src_filter, payload.word)
    return result

