#!/usr/bin/env python3
# vim-deepl - DeepL translation and vocabulary trainer for Vim
# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

import sys
import os
import json
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

def pick_training_word(dict_base_path: str, src_filter: str | None = None):
    """
    Pick a word/phrase for training.

    Selection strategy:
    - Filter by source: EN / DA or both
    - Split entries into 2 buckets:
        recent  -> added in last RECENT_DAYS
        old     -> older entries
    - Bucket selection:
        - If both exist: 70% pick from recent, 30% from old
    - Within bucket prioritize:
        1. Not mastered (count < MASTERY_COUNT)
        2. Lower count first
        3. Higher "hard" score first
        4. Least recently used first

    On choice:
    - Update last_used and count
    - Return progress statistics
    """

    now = datetime.now()
    entries = []

    src_filter = (src_filter or "").upper()
    if src_filter in ("EN", "DA"):
        src_langs = [src_filter]
    else:
        src_langs = ["EN", "DA"]

    for src in src_langs:
        path = f"{dict_base_path}_{src.lower()}.json"
        data = load_dict(path)
        if not data:
            continue

        for word, entry in data.items():

            # Skip words the user explicitly ignored
            if entry.get("ignore"):
                continue

            cnt = entry.get("count", 0)
            hard = entry.get("hard", 0)

            last_str = entry.get("last_used") or entry.get("date") or "1970-01-01 00:00:00"
            last_dt = parse_dt(last_str)
            date_dt = parse_dt(entry.get("date") or last_str)

            age_days = (now - date_dt).days
            bucket = "recent" if age_days <= RECENT_DAYS else "old"

            entries.append({
                "src": src,
                "word": word,
                "entry": entry,
                "path": path,
                "data": data,
                "count": cnt,
                "last": last_dt,
                "date_dt": date_dt,
                "bucket": bucket,
                "hard": hard,
            })

    if not entries:
        return {
            "type": "train",
            "error": f"No entries for filter={src_filter or 'ALL'}",
        }

    # --- Progress statistics ---
    total = len(entries)
    mastered = sum(1 for e in entries if e["count"] >= MASTERY_COUNT)
    mastery_percent = int(round(mastered * 100 / total)) if total else 0

    # --- select bucket ---
    recents = [e for e in entries if e["bucket"] == "recent"]
    olds = [e for e in entries if e["bucket"] == "old"]

    if not recents:
        pool = olds
    elif not olds:
        pool = recents
    else:
        pool = recents if random.random() < 0.7 else olds

    # Prefer not-mastered first
    not_mastered = [e for e in pool if e["count"] < MASTERY_COUNT]
    if not_mastered:
        pool = not_mastered

    # Sorting priority
    pool.sort(key=lambda e: (e["count"], -e["hard"], e["last"]))

    chosen = pool[0]
    entry = chosen["entry"]

    now_s = now_str()
    entry["last_used"] = now_s
    entry["count"] = entry.get("count", 0) + 1
    save_dict(chosen["path"], chosen["data"])

    return {
        "type": "train",
        "word": chosen["word"],
        "translation": entry.get("text", ""),
        "src_lang": chosen["src"],
        "target_lang": entry.get("lang", "RU"),
        "timestamp": now_s,
        "count": entry.get("count", 0),
        "hard": entry.get("hard", 0),
        "stats": {
            "total": total,
            "mastered": mastered,
            "mastery_threshold": MASTERY_COUNT,
            "mastery_percent": mastery_percent,
        },
        "error": None,
    }

def mark_ignore(dict_base_path: str, src_filter: str, word: str):
    """Mark a vocabulary entry as permanently ignored (trainer skips it)."""
    src = (src_filter or "").upper()
    if src not in ("EN", "DA"):
        return {"type": "ignore", "error": f"Unsupported src_filter={src_filter}"}

    path = f"{dict_base_path}_{src.lower()}.json"
    data = load_dict(path)

    if word not in data:
        return {"type": "ignore", "error": f"Word '{word}' not found in {path}"}

    entry = data[word]
    entry["ignore"] = True
    save_dict(path, data)

    return {"type": "ignore", "word": word, "src_lang": src, "ignore": True, "error": None}

def mark_hard(dict_base_path: str, src_filter: str, word: str):
    """Mark word as difficult by incrementing 'hard' counter."""
    src = (src_filter or "").upper()
    if src not in ("EN", "DA"):
        return {"type": "mark_hard", "error": f"Unsupported src_filter={src_filter}"}

    path = f"{dict_base_path}_{src.lower()}.json"
    data = load_dict(path)

    if word not in data:
        return {"type": "mark_hard", "error": f"Word '{word}' not found in {path}"}

    entry = data[word]
    entry["hard"] = entry.get("hard", 0) + 1
    save_dict(path, data)

    return {"type": "mark_hard", "word": word, "src_lang": src, "hard": entry["hard"], "error": None}

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

def translate_word(word: str, dict_base_path: str, target_lang: str, src_hint: str = ""):
    """Full async word translation with caching and training counters."""
    key = word
    src_langs = ["EN", "DA"]
    dicts = {}

    # Try both dictionaries first
    for src in src_langs:
        path = f"{dict_base_path}_{src.lower()}.json"
        data = load_dict(path)
        dicts[src] = (path, data)

        if key in data:
            entry = data[key]
            now = now_str()
            entry.setdefault("date", now)
            entry["last_used"] = now
            entry["count"] = entry.get("count", 0) + 1
            entry.setdefault("lang", target_lang)
            entry.setdefault("src_lang", src)
            save_dict(path, data)

            return {
                "type": "word",
                "source": word,
                "text": entry.get("text", "N/A"),
                "target_lang": target_lang,
                "detected_source_lang": entry.get("src_lang", src),
                "from_cache": True,
                "timestamp": entry.get("date", ""),
                "last_used": entry.get("last_used", ""),
                "count": entry.get("count", 0),
                "error": None,
            }

    # Not in cache: ask DeepL
    tr, detected, err = deepl_call(word, target_lang)
    if err:
        return {"type": "word", "source": word, "text": "", "target_lang": target_lang,
                "detected_source_lang": "", "from_cache": False, "timestamp": "", "error": err}

    src = normalize_src_lang(detected, src_hint)
    path, data = dicts.get(src, (f"{dict_base_path}_{src.lower()}.json", None))
    if data is None:
        data = load_dict(path)

    now = now_str()
    data[key] = {
        "text": tr,
        "date": now,
        "last_used": now,
        "count": 1,
        "lang": target_lang,
        "src_lang": src,
        "detected_raw": detected,
    }
    save_dict(path, data)

    return {"type": "word", "source": word, "text": tr, "target_lang": target_lang,
            "detected_source_lang": src, "from_cache": False, "timestamp": now,
            "last_used": now, "count": 1, "error": None}

def translate_selection(text: str, target_lang: str):
    """Translate a multi-word text block without caching."""
    text_clean = oneline(text)
    tr, detected, err = deepl_call(text_clean, target_lang)
    if err:
        return {"type": "selection", "source": text_clean, "text": "",
                "target_lang": target_lang, "detected_source_lang": detected,
                "timestamp": "", "error": err}

    now = now_str()
    return {"type": "selection", "source": text_clean, "text": oneline(tr),
            "target_lang": target_lang, "detected_source_lang": detected,
            "timestamp": now, "error": None}

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
        target_lang = sys.argv[3] if len(sys.argv) >= 4 else "RU"
        result = translate_selection(text, target_lang)

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
