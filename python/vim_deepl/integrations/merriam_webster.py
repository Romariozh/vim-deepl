# python/vim_deepl/integrations/merriam_webster.py
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Optional, Tuple, Dict, Any

MW_SD3_ENDPOINT = "https://www.dictionaryapi.com/api/v3/references/sd3/json/"
MW_SD3_ENV_VAR = "MW_SD3_API_KEY"

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


def mw_fetch(term: str, src_lang: str) -> Optional[dict]:
    """
    Fetch MW definitions from API and return defs dict for caching.
    Returns keys: noun, verb, adjective, adverb, other, raw_json
    """
    if (src_lang or "").upper() != "EN":
        return None

    data, err = mw_call(term)
    if err or not data:
        return None

    defs_by_pos = mw_extract_definitions(data)
    if not defs_by_pos or not any(defs_by_pos.values()):
        return None

    defs_by_pos = dict(defs_by_pos)
    defs_by_pos["raw_json"] = json.dumps(data, ensure_ascii=False)
    return defs_by_pos

