import os
import sys
import sqlite3
import json
import re
from typing import Optional, Any, Dict, List
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from pathlib import Path

# Make sure we can import vim_deepl from ./python
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.join(BASE_DIR, "python")
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from vim_deepl.cli.dispatcher import load_config, build_services, parse_dt, now_str, dispatch
from vim_deepl.utils.config import load_config
from vim_deepl.repos.dict_repo import resolve_db_path
from vim_deepl.repos.sqlite_repo import SQLiteRepo
from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.translation_repo import TranslationRepo
from vim_deepl.services.trainer_service import TrainerConfig
from vim_deepl.api.routes.mw_audio import router as mw_audio_router
from vim_deepl.api.routes.bookmarks import router as bookmarks_router
from vim_deepl.repos.book_marks_repo import BookMarksRepo
from vim_deepl.services.bookmarks_service import BookmarksService


app = FastAPI(title="Local Dict API")

app.include_router(mw_audio_router)
app.include_router(bookmarks_router)

DICT_BASE = os.path.expanduser("~/.local/share/vim-deepl")
_MW_TAG_RE = re.compile(r"\{[^}]+\}")  # strips {it} {/it} {bc} {ldquo} {sx|...} etc.

def _trainer_ctx_list(db_path: Path, term: str, src_lang: str, dst_lang: str, limit: int = 3) -> List[str]:
    term = (term or "").strip()
    src_lang = (src_lang or "").strip().upper()
    dst_lang = (dst_lang or "").strip().upper()
    if not term or not src_lang or not dst_lang:
        return []

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 10000;")
        rows = conn.execute(
            """
            SELECT ctx_text
            FROM entries_ctx
            WHERE term = ?
              AND src_lang = ?
              AND dst_lang = ?
              AND COALESCE(TRIM(ctx_text), '') != ''
            ORDER BY COALESCE(last_used, created_at) DESC, id DESC
            LIMIT ?
            """,
            (term, src_lang, dst_lang, limit),
        ).fetchall()
        return [r["ctx_text"].strip() for r in rows if r["ctx_text"]]
    finally:
        conn.close()


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

    # Bookmarks service (reading highlights)
    app.state.bookmarks = BookmarksService(repo=BookMarksRepo(sqlite))



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
    src_filter: str
    word: Optional[str] = None
    entry_id: Optional[int] = None

class SelectionRequest(BaseModel):
    text: str
    target_lang: str
    src_hint: Optional[str] = ""

class TrainReviewReq(BaseModel):
    card_id: int
    grade: int
    src_filter: Optional[str] = "EN"

class TrainNextRequest(BaseModel):
    src_filter: str | None = None
    exclude_card_ids: list[int] = Field(default_factory=list)


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
def api_train_next(payload: TrainNextRequest):
    cfg = load_config()
    recent_days = getattr(cfg, "trainer_recent_days", 7)
    mastery_count = getattr(cfg, "trainer_mastery_count", 7)
    recent_ratio = getattr(cfg, "trainer_recent_ratio", 0.7)

    services = build_services(
        DICT_BASE,
        cfg=cfg,
        recent_days=recent_days,
        mastery_count=mastery_count,
    )

    now = datetime.now(timezone.utc)

    # NOTE: pick_training_word must accept exclude_card_ids and pass it into repo queries
    result = services.trainer.pick_training_word(
        src_filter=payload.src_filter or None,
        now=now,
        now_s=now_str(),
        parse_dt=parse_dt,
        exclude_card_ids=payload.exclude_card_ids,
    )

    # Attach deck stats (mastery) if possible
    if isinstance(result, dict) and not result.get("error"):
        try:
            db_path = _guess_vocab_db_path(DICT_BASE)

            term = (result.get("term") or "").strip()
            src = (payload.src_filter or result.get("src_lang") or "EN").strip().upper()
            dst = (result.get("dst_lang") or "RU").strip().upper()  # если у тебя другое — подставь
            if term:
                result["ctx_list"] = _trainer_ctx_list(db_path, term, src, dst, limit=3)
                result["variants"] = _entry_translations_list(db_path, term, src, dst, limit=10)
            else:
                result["ctx_list"] = []
                result["variants"] = []

            _attach_ctx_and_detected(db_path, result, payload.src_filter)

            # Attach SRS fields (reps/lapses/wrong_streak/...)
            cid = result.get("card_id")
            if cid is not None:
                srs = _trainer_card_srs_fields(db_path, int(cid))
                for k, v in srs.items():
                    # Do not overwrite if backend already returned the field
                    if k not in result or result.get(k) is None:
                        result[k] = v

            # Attach stats (mastery)
            stats_src = payload.src_filter or result.get("src_lang") or None
            result["stats"] = _trainer_stats(db_path, stats_src, mastery_count)

            # Attach grammar (MW)
            term = (result.get("term") or "").strip()
            src_lang = (result.get("src_lang") or payload.src_filter or "EN").strip().upper()

            # trainer needs raw_json + audio_ids from mw_definitions table
            result["mw_definitions"] = _mw_definitions_from_db(db_path, term, src_lang) if term else None

            # optional: existing pretty grammar dict (can stay)
            if term:
                g = _mw_attach_grammar(db_path, term, src_lang)
                if g:
                    result["grammar"] = g
                    try:
                        ...
                        result["grammar_line"] = " / ".join(parts)
                    except Exception:
                        result["grammar_line"] = ""
            else:
                result["grammar_line"] = ""

        except Exception:
            pass

    # Reduce noise: if we already have a real context, detected_raw is redundant
    if isinstance(result, dict) and result.get("context_raw"):
        result.pop("detected_raw", None)

    return result

@app.post("/train/review")
def api_train_review(payload: TrainReviewRequest):
    cfg = load_config()
    mastery_count = getattr(cfg, "trainer_mastery_count", 7)

    argv = ["dict_api", "review", DICT_BASE, payload.src_filter or "", str(payload.card_id), str(payload.grade)]
    result = _dispatch_data(argv)

    # Attach deck stats/grammar/SRS just like /train/next
    if isinstance(result, dict) and not result.get("error"):
        try:
            db_path = _guess_vocab_db_path(DICT_BASE)

            term = (result.get("term") or "").strip()
            src = (payload.src_filter or result.get("src_lang") or "EN").strip().upper()
            dst = (result.get("dst_lang") or "RU").strip().upper()

            # ✅ mw_definitions for trainer (raw_json + audio_ids)
            result["mw_definitions"] = _mw_definitions_from_db(db_path, term, src) if term else None

            # ✅ ctx_list for trainer
            result["ctx_list"] = _trainer_ctx_list(db_path, term, src, dst, limit=3) if term else []

            result["variants"] = _entry_translations_list(db_path, term, src, dst, limit=10) if term else []

            # Keep old fields too (if you still need them elsewhere)
            _attach_ctx_and_detected(db_path, result, payload.src_filter)

            # Attach SRS fields (reps/lapses/wrong_streak/...)
            cid = result.get("card_id")
            if cid is not None:
                srs = _trainer_card_srs_fields(db_path, int(cid))
                for k, v in srs.items():
                    if k not in result or result.get(k) is None:
                        result[k] = v

            # Attach stats (mastery)
            stats_src = payload.src_filter or result.get("src_lang") or None
            result["stats"] = _trainer_stats(db_path, stats_src, mastery_count)

            # Attach grammar (MW) — один раз
            if term:
                g = _mw_attach_grammar(db_path, term, src)
                if g:
                    result["grammar"] = g

        except Exception:
            pass

    # Reduce noise: if we already have a real context, detected_raw is redundant
    if isinstance(result, dict) and result.get("context_raw"):
        result.pop("detected_raw", None)

    return result

@app.post("/train/mark_hard")
def api_mark_hard(payload: MarkRequest):
    argv = ["dict_api", "mark_hard", DICT_BASE, payload.src_filter, payload.word]
    return _dispatch_data(argv)

@app.post("/train/mark_ignore")
def api_mark_ignore(payload: MarkRequest):
    # Prefer exact ignore by entry_id when available.
    if payload.entry_id is not None:
        db_path = _guess_vocab_db_path(DICT_BASE)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE entries SET ignore=1 WHERE id=?", (int(payload.entry_id),))
            # Optional: also suspend related training card(s) to be safe.
            conn.execute("UPDATE training_cards SET suspended=1 WHERE entry_id=?", (int(payload.entry_id),))
            conn.commit()
        return {"ignored": True, "entry_id": int(payload.entry_id)}

    # Backward-compatible fallback: ignore by (src_filter, word)
    argv = ["dict_api", "ignore", DICT_BASE, payload.src_filter, payload.word or ""]
    return _dispatch_data(argv)

def _dispatch_data(argv: list[str]) -> dict:
    resp = dispatch(argv)
    if not resp.get("ok"):
        err = resp.get("error") or {"message": "Unknown error"}
        raise HTTPException(status_code=500, detail=err)
    data = resp["data"]
    data = _maybe_attach_trainer_stats(argv, data)
    return data

def _guess_vocab_db_path(dict_base: str) -> str:
    """
    Try to locate vocab.db.
    Priority:
      1) <DICT_BASE>/vocab.db
      2) ~/.local/share/vim-deepl/vocab.db
    """
    p1 = os.path.join(dict_base, "vocab.db")
    if os.path.isfile(p1):
        return p1
    p2 = os.path.expanduser("~/.local/share/vim-deepl/vocab.db")
    if os.path.isfile(p2):
        return p2
    return p1  # fallback (may not exist)

def _trainer_stats(db_path: str, src_filter: str | None, mastery_count: int) -> dict:
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()

        where = "suspended=0"
        args: list = []
        if src_filter:
            where += " AND src_lang=?"
            args.append(src_filter)

        cur.execute(f"SELECT COUNT(*) FROM training_cards WHERE {where}", args)
        total = int(cur.fetchone()[0] or 0)

        cur.execute(
            f"SELECT COUNT(*) FROM training_cards WHERE {where} AND correct_streak >= ?",
            args + [int(mastery_count)],
        )
        mastered = int(cur.fetchone()[0] or 0)

        percent = int(round((mastered * 100.0) / total)) if total > 0 else 0

        return {
            "total": total,
            "mastered": mastered,
            "mastery_threshold": int(mastery_count),
            "mastery_percent": percent,
        }
    finally:
        con.close()

def _trainer_card_srs_fields(db_path: str, card_id: int) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """
            SELECT reps, lapses, wrong_streak, correct_streak, interval_days, ef, due_at, last_grade
            FROM training_cards
            WHERE id = ?
            """,
            (int(card_id),),
        ).fetchone()
        if not row:
            return {}
        return {
            "reps": int(row["reps"] or 0),
            "lapses": int(row["lapses"] or 0),
            "wrong_streak": int(row["wrong_streak"] or 0),
            "correct_streak": int(row["correct_streak"] or 0),
            "interval_days": float(row["interval_days"] or 0.0),
            "ef": float(row["ef"] or 2.5),
            "due_at": row["due_at"],
            "last_grade": int(row["last_grade"] or 0) if row["last_grade"] is not None else None,
        }
    finally:
        con.close()

def _attach_stats_if_possible(result: object, src_filter: str | None, mastery_count: int) -> object:
    if not isinstance(result, dict) or result.get("error"):
        return result
    try:
        db_path = _guess_vocab_db_path(DICT_BASE)
        src = src_filter or result.get("src_lang") or None
        result["stats"] = _trainer_stats(db_path, src, mastery_count)
    except Exception:
        pass
    return result

def _maybe_attach_trainer_stats(argv: list[str], data: Any) -> Any:
    """
    Attach trainer deck stats to responses returned via CLI dispatcher.
    This is intentionally conservative: we only attach stats for known trainer ops
    and only when the returned payload looks like a trainer card.
    """
    if not isinstance(data, dict):
        return data

    # Only for trainer-related dispatched commands
    op = argv[1] if len(argv) > 1 else ""
    trainer_ops = {
        "review",
        "mark_hard",
        "ignore",
        "skip",
        "suspend",
        "unsuspend",
        "mark_known",
    }
    if op not in trainer_ops:
        return data

    # Only attach when response resembles a trainer card payload
    looks_like_card = (
        "card_id" in data
        or "entry_id" in data
        or "term" in data
        or "translation" in data
        or "src_lang" in data
    )
    if not looks_like_card:
        return data

    try:
        cfg = load_config()
        mastery_count = getattr(cfg, "trainer_mastery_count", 7)

        # argv shape for trainer ops is typically:
        # ["dict_api", <op>, DICT_BASE, <src_filter>, ...]
        src_filter = argv[3] if len(argv) > 3 else ""
        src_filter = (src_filter or data.get("src_lang") or "").strip() or None

        db_path = _guess_vocab_db_path(DICT_BASE)

        # Attach SRS fields from SQLite (reps/lapses/wrong_streak/...)
        cid = data.get("card_id")
        if cid is not None:
            srs = _trainer_card_srs_fields(db_path, int(cid))
            for k, v in srs.items():
                if k not in data or data.get(k) is None:
                    data[k] = v

        # Attach mastery stats
        data["stats"] = _trainer_stats(db_path, src_filter, int(mastery_count))

    except Exception:
        # Never break trainer flow because of stats
        pass

    return data


def _mw_clean(s: str) -> str:
    if not s:
        return ""
    s = _MW_TAG_RE.sub("", s)
    s = " ".join(s.split())
    return s.strip()

def _mw_guess_raw_payload(row_dict: dict) -> Optional[str]:
    # Try known names first
    for k in ("raw", "mw_raw", "raw_json", "payload", "mw_payload"):
        v = row_dict.get(k)
        if isinstance(v, str) and v.lstrip().startswith("["):
            return v
    # Fallback: detect “MW-like” json in any column
    for v in row_dict.values():
        if isinstance(v, str) and v.lstrip().startswith("[") and '"meta"' in v and '"stems"' in v:
            return v
    return None

import json, re, sqlite3

def _mw_clean(s: str) -> str:
    if not s:
        return ""
    # базовая чистка mw-разметки
    s = s.replace("{bc}", "").replace("{ldquo}", '"').replace("{rdquo}", '"')
    s = s.replace("{it}", "").replace("{/it}", "")
    # всё вида {sx|embrace:1||1} и т.п.
    s = re.sub(r"\{[^}]+\}", "", s)
    return " ".join(s.split()).strip()

def _mw_definitions_from_db(db_path, term: str, src_lang: str) -> Optional[Dict[str, Any]]:
    term = (term or "").strip()
    src_lang = (src_lang or "").strip().upper()
    if not term or not src_lang:
        return None

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 10000;")

        cols = [r["name"] for r in conn.execute("PRAGMA table_info(mw_definitions)").fetchall()]

        # raw MW json column name (support older schemas)
        raw_col = None
        for c in ("raw_json", "raw", "defs_json", "defs", "payload_json"):
            if c in cols:
                raw_col = c
                break
        if raw_col is None:
            return None

        audio_ids_col = "audio_ids" if "audio_ids" in cols else None
        audio_mark_col = "audio_mark" if "audio_mark" in cols else None

        select_cols = [f"{raw_col} AS raw_json"]
        if audio_ids_col:
            select_cols.append(f"{audio_ids_col} AS audio_ids")
        if audio_mark_col:
            select_cols.append(f"{audio_mark_col} AS audio_mark")

        sql = f"""
        SELECT {", ".join(select_cols)}
        FROM mw_definitions
        WHERE term=? AND src_lang=?
        ORDER BY id DESC
        LIMIT 1
        """
        row = conn.execute(sql, (term, src_lang)).fetchone()
        if not row:
            return None

        raw_json = row["raw_json"] or ""

        audio_ids = []
        if audio_ids_col:
            ai = row["audio_ids"]
            # audio_ids stored as JSON string like '["travel01"]'
            if isinstance(ai, str) and ai.strip():
                try:
                    audio_ids = json.loads(ai)
                except Exception:
                    audio_ids = []
            elif isinstance(ai, (list, tuple)):
                audio_ids = list(ai)

        audio_mark = ""
        if audio_mark_col:
            audio_mark = row["audio_mark"] or ""

        return {
            "raw_json": raw_json,      # <-- string, Vim будет json_decode()
            "audio_ids": audio_ids,    # <-- list
            "audio_mark": audio_mark,  # <-- string (не обязателен, но пусть будет)
        }
    finally:
        conn.close()

def _mw_attach_grammar(db_path: str, term: str, src_lang: str) -> dict | None:
    term = (term or "").strip()
    src_lang = (src_lang or "EN").upper()
    if not term:
        return None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(mw_definitions)")]
        raw_col = next((c for c in ["raw_json", "raw", "payload", "data", "json_data"] if c in cols), None)
        if not raw_col:
            return None

        row = conn.execute(
            f"SELECT {raw_col} AS raw FROM mw_definitions WHERE term=? AND src_lang=? ORDER BY id DESC LIMIT 1",
            (term, src_lang),
        ).fetchone()
        if not row or not row["raw"]:
            return None

        items = json.loads(row["raw"])
        if not isinstance(items, list) or not items:
            return None

        term_l = term.lower()

        def _base_word(it: dict) -> str:
            meta_id = ((it.get("meta", {}) or {}).get("id", "") or "")
            if isinstance(meta_id, str) and meta_id:
                return meta_id.split(":")[0].strip()
            return ""

        def _stems(it: dict) -> list[str]:
            stems = ((it.get("meta", {}) or {}).get("stems", []) or [])
            return [s for s in stems if isinstance(s, str)]

        # 1) pick lemma/base word
        lemma = ""
        for it in items:
            stems = [s.lower() for s in _stems(it)]
            bw = _base_word(it).lower()
            if term_l in stems or term_l == bw:
                lemma = _base_word(it)
                break
        if not lemma:
            lemma = _base_word(items[0]) or term

        lemma_l = lemma.lower()

        # 2) keep only relevant items (same lemma)
        rel = [it for it in items if _base_word(it).lower() == lemma_l]
        if not rel:
            return None

        # 3) stems: best from first relevant item
        stems = _stems(rel[0])

        # 4) group definitions by POS (fl)
        pos_map: dict[str, list[str]] = {}
        for it in rel:
            fl = it.get("fl", "")
            if not isinstance(fl, str) or not fl.strip():
                continue
            pos = fl.strip().capitalize()

            sd = it.get("shortdef") or []
            if not isinstance(sd, list):
                continue

            for x in sd:
                if isinstance(x, str):
                    d = _mw_clean(x)
                    if d:
                        pos_map.setdefault(pos, []).append(d)

        # unique defs per pos (preserve order)
        pos_defs = []
        for pos, defs in pos_map.items():
            seen = set()
            uniq = []
            for d in defs:
                if d not in seen:
                    seen.add(d)
                    uniq.append(d)
            pos_defs.append((pos, uniq))

        # stable order: Noun, Verb, Adjective... then others
        pref = {"Noun": 0, "Verb": 1, "Adjective": 2, "Adverb": 3}
        pos_defs.sort(key=lambda t: (pref.get(t[0], 99), t[0]))

        # limits
        MAX_PER_POS = 3
        pos_blocks = []
        for pos, defs in pos_defs:
            shown = defs[:MAX_PER_POS]
            more = max(0, len(defs) - len(shown))
            pos_blocks.append({"pos": pos, "defs": shown, "more": more})

        # 5) etymology: first one within relevant items
        ety = ""
        for it in rel:
            et = it.get("et")
            if isinstance(et, list):
                parts = []
                for chunk in et:
                    if isinstance(chunk, list) and len(chunk) >= 2 and chunk[0] == "text":
                        parts.append(_mw_clean(str(chunk[1])))
                ety = " ".join([p for p in parts if p]).strip()
                if ety:
                    break

        out = {
            "word": lemma,
            "stems": stems,
            "pos_blocks": pos_blocks,  # <-- NEW
            "etymology": ety,
        }

        if not (out["stems"] or out["pos_blocks"] or out["etymology"]):
            return None
        return out
    finally:
        conn.close()

def _attach_ctx_and_detected(db_path: str, result: dict, src_filter: str | None = None) -> None:
    term = (result.get("term") or "").strip()
    if not term:
        return

    src_lang = (result.get("src_lang") or src_filter or "EN").strip().upper()
    dst_lang = (result.get("dst_lang") or result.get("target_lang") or "RU").strip().upper()

    need_detected = result.get("detected_raw") in (None, "")
    need_ctx = result.get("context_raw") in (None, "")

    if not (need_detected or need_ctx):
        return

    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row

        detected_raw = None
        if need_detected or need_ctx:
            row = con.execute(
                """
                SELECT detected_raw
                FROM entries
                WHERE term = ? AND src_lang = ? AND dst_lang = ?
                LIMIT 1
                """,
                (term, src_lang, dst_lang),
            ).fetchone()
            if row:
                detected_raw = row["detected_raw"]

        ctx_text = None
        if need_ctx:
            row = con.execute(
                """
                SELECT x.ctx_text
                FROM entries_ctx x
                WHERE x.term = ?
                  AND x.src_lang = ?
                  AND x.dst_lang = ?
                  AND x.ctx_text IS NOT NULL
                  AND x.ctx_text != ''
                ORDER BY COALESCE(x.last_used, x.created_at) DESC,
                         x.count DESC,
                         x.id DESC
                LIMIT 1
                """,
                (term, src_lang, dst_lang),
            ).fetchone()
            if row:
                ctx_text = row["ctx_text"]

        # Fill gaps:
        if need_detected and detected_raw:
            result["detected_raw"] = detected_raw

        if need_ctx:
            # prefer ctx_text, else fall back to detected_raw
            if ctx_text:
                result["context_raw"] = ctx_text
            elif detected_raw:
                result["context_raw"] = detected_raw

    finally:
        con.close()

def _entry_translations_list(db_path: str, term: str, src: str, dst: str, limit: int = 10) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)

        rows = conn.execute(
            """
            SELECT translation, count, last_used, created_at
            FROM entry_translations
            WHERE trim(term) = trim(?) COLLATE NOCASE
              AND upper(trim(src_lang)) = upper(trim(?))
              AND upper(trim(dst_lang)) = upper(trim(?))
            ORDER BY COALESCE(last_used, created_at) ASC
            LIMIT ?
            """,
            (term, src, dst, limit),
        ).fetchall()

        return [dict(r) for r in rows]
