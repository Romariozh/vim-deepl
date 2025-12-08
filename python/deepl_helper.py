#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import urllib.parse
import random
from datetime import datetime


def load_dict(path: str):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, TypeError):
        return {}

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def save_dict(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

RECENT_DAYS = 7          # последние 7 дней считаем "новыми"
MASTERY_COUNT = 7        # сколько повторений нужно, чтобы считать слово "освоенным"

def parse_dt(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime(1970, 1, 1)

def pick_training_word(dict_base_path: str, src_filter: str | None = None):
    """
    Выбор слова для тренажёра.

    Логика:
    - фильтр по языку словаря: EN/DA (src_filter) или оба;
    - делим слова на 2 корзины:
        recent  -> добавлены за последние RECENT_DAYS дней
        old     -> старше RECENT_DAYS
    - выбираем корзину:
        70% -> recent, 30% -> old (если обе не пустые)
    - внутри корзины сортируем:
        hard DESC, count ASC, last_used ASC
      => "трудные", мало повторённые и давно не трогали — в приоритете
    - обновляем last_used и count для выбранного слова
    - считаем статистику прогресса:
        сколько слов имеют count >= MASTERY_COUNT
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
            # NEW: skip words marked as ignored
            if entry.get("ignore"):
                continue

            cnt = entry.get("count", 0)
            hard = entry.get("hard", 0)

            last_str = entry.get("last_used") or entry.get("date") or "1970-01-01 00:00:00"
            last_dt = parse_dt(last_str)

            date_str = entry.get("date") or last_str
            date_dt = parse_dt(date_str)

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
            "error": f"No words found in dictionaries for filter={src_filter or 'ALL'}",
        }

    # --- Статистика прогресса ---
    total = len(entries)
    mastered = sum(1 for e in entries if e["count"] >= MASTERY_COUNT)
    mastery_percent = int(round(mastered * 100 / total)) if total else 0

    # --- Split into recent/old ---
    recents = [e for e in entries if e["bucket"] == "recent"]
    olds = [e for e in entries if e["bucket"] == "old"]

    if not recents:
        pool = olds
    elif not olds:
        pool = recents
    else:
        # 70% new words, 30% older words
        if random.random() < 0.7:
            pool = recents
        else:
            pool = olds

    # ----------------------------------------------------------------
    # Prefer not-mastered words (count < MASTERY_COUNT).
    # Among them: lower count first, then "hard" words, then by last_used.
    # ----------------------------------------------------------------
    not_mastered = [e for e in pool if e["count"] < MASTERY_COUNT]
    if not_mastered:
        pool = not_mastered

    # Now: words with smaller count first,
    # and within same count – those with higher "hard" first.
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
    """
    Mark a word as "ignored":
    - set 'ignore' = True in EN/DA dictionary
    - trainer will completely skip such words
    """
    src = (src_filter or "").upper()
    if src not in ("EN", "DA"):
        return {
            "type": "ignore",
            "error": f"Unsupported src_filter={src_filter}",
        }

    path = f"{dict_base_path}_{src.lower()}.json"
    data = load_dict(path)

    if word not in data:
        return {
            "type": "ignore",
            "error": f"Word '{word}' not found in {path}",
        }

    entry = data[word]
    entry["ignore"] = True
    save_dict(path, data)

    return {
        "type": "ignore",
        "word": word,
        "src_lang": src,
        "ignore": True,
        "error": None,
    }

def mark_hard(dict_base_path: str, src_filter: str, word: str):
    """
    Отметить слово как 'трудное':
    - увеличиваем поле 'hard' на 1 в словаре EN/DA
    """

    src = (src_filter or "").upper()
    if src not in ("EN", "DA"):
        return {
            "type": "mark_hard",
            "error": f"Unsupported src_filter={src_filter}",
        }

    path = f"{dict_base_path}_{src.lower()}.json"
    data = load_dict(path)

    if word not in data:
        return {
            "type": "mark_hard",
            "error": f"Word '{word}' not found in {path}",
        }

    entry = data[word]
    entry["hard"] = entry.get("hard", 0) + 1
    save_dict(path, data)

    return {
        "type": "mark_hard",
        "word": word,
        "src_lang": src,
        "hard": entry["hard"],
        "error": None,
    }

def deepl_call(text: str, target_lang: str):
    api_key = os.environ.get("DEEPL_API_KEY", "")
    if not api_key:
        return None, "", "DEEPL_API_KEY is not set in environment."

    url = "https://api-free.deepl.com/v2/translate"

    # Можно через form-encoded (как у тебя было) – ответ такой же, как в curl.
    params = {
        "auth_key": api_key,
        "text": text,
        "target_lang": target_lang,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")

    try:
        with urllib.request.urlopen(req) as resp:
            response_text = resp.read().decode("utf-8")
            response = json.loads(response_text)
    except Exception as e:
        return None, "", f"DeepL request error: {e}"

    translations = response.get("translations") or []
    if not translations:
        return None, "", "Empty response from DeepL API."

    tr_obj = translations[0]
    translated_text = tr_obj.get("text", "")
    detected_lang = tr_obj.get("detected_source_language", "")

    return translated_text, detected_lang, None

def oneline(text: str) -> str:
    parts = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(parts)

def normalize_src_lang(detected: str, src_hint: str) -> str:
    """
    detected  - что вернул DeepL (EN, DA, NL, SV, EN-GB, ...)
    src_hint  - EN или DA от Vim (что ты сам выбрал)
    """
    code = (detected or "").upper()
    hint = (src_hint or "").upper()

    # 1) Сначала доверяем DeepL, если это EN/DA
    if code.startswith("EN"):
        return "EN"
    if code.startswith("DA"):
        return "DA"

    # 2) Если DeepL что-то странное (NL, SV, DE, ...),
    #    используем то, что выбрал пользователь в Vim
    if hint in ("EN", "DA"):
        return hint

    # 3) На всякий случай дефолт
    return "EN"

def translate_word(word: str, dict_base_path: str, target_lang: str, src_hint: str = ""):
    key = word  # при желании можно сделать word.lower()

    # 1) Пробуем кэш в обоих словарях EN/DA
    src_langs = ["EN", "DA"]
    dicts = {}

    for src in src_langs:
        path = f"{dict_base_path}_{src.lower()}.json"
        data = load_dict(path)
        dicts[src] = (path, data)

        if key in data:
            entry = data[key]
            entry.setdefault("date", now_str())
            entry["last_used"] = now_str()
            entry["count"] = entry.get("count", 0) + 1
            entry.setdefault("lang", target_lang)
            entry.setdefault("src_lang", src)

            save_dict(path, data)

            tr = entry.get("text", "N/A")
            return {
                "type": "word",
                "source": word,
                "text": tr,
                "target_lang": target_lang,
                "detected_source_lang": entry.get("src_lang", src),
                "from_cache": True,
                "timestamp": entry.get("date", ""),
                "last_used": entry.get("last_used", ""),
                "count": entry.get("count", 0),
                "error": None,
            }

    # 2) В кэше нет → спрашиваем DeepL
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
            "error": err,
        }

    # 3) Определяем, в какой словарь класть (EN/DA)
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
        "lang": target_lang,       # RU
        "src_lang": src,           # EN или DA
        "detected_raw": detected,  # то, что реально сказал DeepL (EN/NL/SV/...)
    }
    save_dict(path, data)

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

def translate_selection(text: str, target_lang: str):
    text_clean = oneline(text)
    tr, detected, err = deepl_call(text_clean, target_lang)
    if err:
        return {
            "type": "selection",
            "source": text_clean,
            "text": "",
            "target_lang": target_lang,
            "detected_source_lang": detected,
            "timestamp": "",
            "error": err,
        }

    now = now_str()
    return {
        "type": "selection",
        "source": text_clean,
        "text": oneline(tr),
        "target_lang": target_lang,
        "detected_source_lang": detected,
        "timestamp": now,
        "error": None,
    }

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Not enough arguments"}, ensure_ascii=False))
        sys.exit(1)

    mode = sys.argv[1]
    text = sys.argv[2]

    if mode == "word":
        # usage: script.py word <WORD> <DICT_BASE_PATH> [TARGET_LANG] [SRC_HINT]
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Dictionary base path is required for word mode"}, ensure_ascii=False))
            sys.exit(1)

        dict_base_path = sys.argv[3]
        target_lang = sys.argv[4] if len(sys.argv) >= 5 else "RU"
        src_hint = sys.argv[5] if len(sys.argv) >= 6 else ""

        result = translate_word(text, dict_base_path, target_lang, src_hint)

    elif mode == "selection":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "Text is required for selection mode"}, ensure_ascii=False))
            sys.exit(1)
        target_lang = sys.argv[3] if len(sys.argv) >= 4 else "RU"
        tr, detected, err = deepl_call(oneline(text), target_lang)
        # ... здесь можно оставить твою прежнюю логику translate_selection
        result = translate_selection(text, target_lang)

    elif mode == "train":
        # usage: script.py train <DICT_BASE_PATH> [SRC_FILTER]
        dict_base_path = text  # sys.argv[2]
        src_filter = sys.argv[3] if len(sys.argv) >= 4 else ""
        result = pick_training_word(dict_base_path, src_filter or None)

    elif mode == "mark_hard":
        # usage: script.py mark_hard <DICT_BASE_PATH> <SRC_FILTER> <WORD>
        if len(sys.argv) < 5:
            result = {"type": "mark_hard", "error": "Not enough arguments for mark_hard"}
        else:
            dict_base_path = text  # sys.argv[2]
            src_filter = sys.argv[3]
            word = sys.argv[4]
            result = mark_hard(dict_base_path, src_filter, word)

    elif mode == "ignore":
        # usage: script.py ignore <DICT_BASE_PATH> <SRC_FILTER> <WORD>
        if len(sys.argv) < 5:
            result = {"type": "ignore", "error": "Not enough arguments for ignore"}
        else:
            dict_base_path = text  # sys.argv[2]
            src_filter = sys.argv[3]
            word = sys.argv[4]
            result = mark_ignore(dict_base_path, src_filter, word)

    else:
        result = {"error": f"Unknown mode: {mode}"}

    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
