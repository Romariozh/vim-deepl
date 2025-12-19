import os
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# Make sure we can import vim_deepl from ./python
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.join(BASE_DIR, "python")
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from vim_deepl.cli.dispatcher import dispatch
from vim_deepl.utils.config import load_config
from vim_deepl.repos.dict_repo import resolve_db_path
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.translation_repo import TranslationRepo

app = FastAPI(title="Local Dict API")

DICT_BASE = os.path.expanduser("~/.local/share/vim-deepl")

@app.on_event("startup")
def _startup() -> None:
    cfg = load_config()
    db_path = resolve_db_path(DICT_BASE, cfg.db_path)

    sqlite = SQLiteRepo(db_path)
    con = sqlite.connect()
    ensure_schema(con)
    con.commit()
    con.close()

    app.state.sqlite = sqlite
    app.state.repo = TranslationRepo(sqlite)


def _repo(req: Request) -> TranslationRepo:
    return req.app.state.repo

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

class TrainReviewRequest(BaseModel):
    card_id: int
    grade: int  # 0..5
    src_filter: str | None = None

class MarkRequest(BaseModel):
    word: str
    src_filter: str

class SelectionRequest(BaseModel):
    text: str
    target_lang: str
    src_hint: Optional[str] = ""

class TrainReviewReq(BaseModel):
    card_id: int
    grade: int
    src_filter: Optional[str] = "EN"


app = FastAPI(title="Local Dict API")


@app.get("/entries")
def get_entry(term: str, dst_lang: str):
    row = _repo.get_base_entry_any_src(term, dst_lang.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    # emulate old behavior: touch usage
    from datetime import datetime
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _repo.touch_base_usage(row["id"], now_s)

    return {
        "term": row["term"],
        "translation": row["translation"],
        "src_lang": row["src_lang"],
        "dst_lang": row["dst_lang"],
        "created_at": row["created_at"],
        "last_used": now_s,
        "count": row["count"] + 1,
    }


@app.post("/entries")
def create_or_update_entry(entry: Entry):
    from datetime import datetime
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _repo.upsert_base_entry(
        entry.term,
        entry.translation,
        entry.src_lang.upper(),
        entry.dst_lang.upper(),
        entry.detected_raw or entry.src_lang.upper(),
        now_s,
    )
    return {"status": "ok"}


@app.post("/entries/use")
def mark_used(term: str, src_lang: str, dst_lang: str):
    row = _repo.get_base_entry_any_src(term, dst_lang.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    from datetime import datetime
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _repo.touch_base_usage(row["id"], now_s)
    return {"status": "ok"}

@app.post("/translate/word")
def api_translate_word(payload: WordRequest):
    argv = [
        "dict_api",
        "word",
        payload.term,
        DICT_BASE,
        payload.target_lang,
        payload.src_hint or "",
        payload.context or "",
    ]
    return _dispatch_data(argv)


@app.post("/translate/selection")
def api_translate_selection(payload: SelectionRequest):
    argv = [
        "dict_api",
        "selection",
        payload.text,
        DICT_BASE,
        payload.target_lang,
        payload.src_hint or "",
    ]
    return _dispatch_data(argv)


@app.post("/train/next")
def api_train_next(payload: TrainRequest):
    argv = ["dict_api", "train", DICT_BASE, payload.src_filter or ""]
    return _dispatch_data(argv)

@app.post("/train/review")
def api_train_review(payload: TrainReviewRequest):
    argv = ["dict_api", "review", DICT_BASE, payload.src_filter or "", str(payload.card_id), str(payload.grade)]
    return _dispatch_data(argv)

@app.post("/train/mark_hard")
def api_mark_hard(payload: MarkRequest):
    argv = ["dict_api", "mark_hard", DICT_BASE, payload.src_filter, payload.word]
    return _dispatch_data(argv)


@app.post("/train/mark_ignore")
def api_mark_ignore(payload: MarkRequest):
    argv = ["dict_api", "ignore", DICT_BASE, payload.src_filter, payload.word]
    return _dispatch_data(argv)

def _dispatch_data(argv: list[str]) -> dict:
    resp = dispatch(argv)
    if not resp.get("ok"):
        err = resp.get("error") or {"message": "Unknown error"}
        raise HTTPException(status_code=500, detail=err)
    return resp["data"]

