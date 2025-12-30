# python/vim_deepl/integrations/merriam_webster.py
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Optional, Tuple, Dict, Any, List
from vim_deepl.integrations.mw_parse import extract_audio_main_and_ids
from vim_deepl.integrations.mw_parse import pick_main_entry, collect_audio_ids_from_entry



MW_SD3_ENDPOINT = "https://www.dictionaryapi.com/api/v3/references/sd3/json/"
MW_SD3_ENV_VAR = "MW_SD3_API_KEY"


def mw_call(word: str):
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


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _bucket_from_fl(fl: str) -> str:
    fl = (fl or "").strip().lower()
    if fl == "noun":
        return "noun"
    if fl == "verb":
        return "verb"
    if fl in ("adjective", "adj.", "adj"):
        return "adjective"
    if fl in ("adverb", "adv.", "adv"):
        return "adverb"
    return "other"


def _extract_info(entry: dict, term: str) -> dict:
    meta = entry.get("meta") or {}
    stems = meta.get("stems") or []
    if not isinstance(stems, list):
        stems = []
    meta_id = meta.get("id") or None

    hwi = entry.get("hwi") or {}
    headword = hwi.get("hw") or None

    prs = hwi.get("prs") or []
    pron = None
    audio_id = None
    if isinstance(prs, list) and prs:
        p0 = prs[0] if isinstance(prs[0], dict) else {}
        pron = p0.get("mw") or None
        sound = p0.get("sound") or {}
        audio_id = sound.get("audio") or None

    fl = entry.get("fl") or None

    return {
        "term": term,
        "entry_id": meta.get("id"),
        "headword": headword,
        "pronunciation": pron,
        "main_pos": fl,
        "audio_id": audio_id,
        "has_audio": bool(audio_id),
        "stems": [s for s in stems if isinstance(s, str)][:20],
    }

def _filter_entries(entries: list, term: str) -> List[dict]:
    """
    Keep entries relevant to term:
    - meta.id == term
    - meta.id startswith term + ":"  (run:1)
    - OR term is inside meta.stems  (fixes carefully -> careful case)
    """
    t = _norm(term)
    out: List[dict] = []

    for e in entries:
        if not isinstance(e, dict):
            continue
        meta = e.get("meta") or {}
        mid = _norm(meta.get("id") or "")
        stems = meta.get("stems") or []
        stems_n = set(_norm(s) for s in stems if isinstance(s, str))

        if (mid == t) or (mid.startswith(t + ":")) or (t in stems_n):
            out.append(e)

    return out


def mw_extract_definitions(entries: list) -> dict:
    """
    Group shortdef strings by POS buckets.
    Returns dict with keys: noun, verb, adjective, adverb, other.
    """
    result: dict[str, list[str]] = {k: [] for k in ["noun", "verb", "adjective", "adverb", "other"]}
    seen = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fl = entry.get("fl") or ""
        bucket = _bucket_from_fl(fl)

        shortdefs = entry.get("shortdef") or []
        if not isinstance(shortdefs, list):
            continue

        for d in shortdefs:
            if not isinstance(d, str):
                continue
            dd = d.strip()
            if not dd:
                continue
            key = (bucket, dd.lower())
            if key in seen:
                continue
            seen.add(key)
            result[bucket].append(dd)

    # чтобы popup был компактным
    for k in result:
        result[k] = result[k][:7]

    return result

def mw_fetch(term: str, src_lang: str) -> Optional[dict]:
    """
    Fetch MW data and return a dict for caching.

    Rules:
    - Return None only on request/shape errors.
    - If MW returns list[dict], ALWAYS return a dict with raw_json
      (even when shortdef is empty, e.g. inflections like "better").
    - Definitions/audio are extracted ONLY from the chosen main entry.
    """
    if (src_lang or "").upper() != "EN":
        return None

    data, err = mw_call(term)
    if err or not isinstance(data, list):
        return None

    # Suggestions mode: list[str]
    if data and isinstance(data[0], str):
        return {
            "noun": [],
            "verb": [],
            "adjective": [],
            "adverb": [],
            "other": [],
            "raw_json": json.dumps(data, ensure_ascii=False),
            "audio_main": None,
            "audio_ids": [],
        }

    # Normal mode: list[dict]
    if not data:
        # Empty list is unusual, but still cache raw.
        return {
            "noun": [],
            "verb": [],
            "adjective": [],
            "adverb": [],
            "other": [],
            "raw_json": "[]",
            "audio_main": None,
            "audio_ids": [],
        }

    if not isinstance(data[0], dict):
        return None

    # Choose a main entry. If not found, fallback to the first entry.
    main = pick_main_entry(data, term)
    if not main:
        main = data[0]

    # IMPORTANT: definitions only from main entry (avoid unrelated entries like "point:1")
    defs_by_pos = mw_extract_definitions([main]) or {}
    out = {
        "noun": defs_by_pos.get("noun", []),
        "verb": defs_by_pos.get("verb", []),
        "adjective": defs_by_pos.get("adjective", []),
        "adverb": defs_by_pos.get("adverb", []),
        "other": defs_by_pos.get("other", []),
        "raw_json": json.dumps(data, ensure_ascii=False),
    }

    audio_ids = collect_audio_ids_from_entry(main) or []
    audio_main = audio_ids[0] if audio_ids else None
    out["audio_main"] = audio_main
    out["audio_ids"] = audio_ids

    return out

