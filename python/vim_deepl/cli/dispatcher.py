from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib

from vim_deepl.utils.logging import get_logger
from vim_deepl.services.container import build_services, TranslationHooks

log = get_logger("vim_deepl.cli")


RECENT_DAYS = 7
MASTERY_COUNT = 7


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime(1970, 1, 1)


def ctx_hash(context: str) -> str:
    ctx = (context or "").strip()
    return hashlib.sha256(ctx.encode("utf-8")).hexdigest()


def normalize_src_lang(detected: str, src_hint: str) -> str:
    code = (detected or "").upper()
    hint = (src_hint or "").upper()

    if code.startswith("EN"):
        return "EN"
    if code.startswith("DA"):
        return "DA"
    if hint in ("EN", "DA"):
        return hint
    return "EN"


def build_translation_hooks() -> TranslationHooks:
    return TranslationHooks(
        normalize_src_lang=normalize_src_lang,
        ctx_hash=ctx_hash,
    )


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _fail(message: str, *, code: str = "ERROR", details: dict | None = None) -> dict:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err}


def _wrap_result(result: dict) -> dict:
    err = result.get("error")
    if err:
        return _fail(str(err), code="ERROR", details=result)
    return _ok(result)


def pick_training_word(dict_base_path: str, src_filter: str | None) -> dict:
    services = build_services(dict_base_path, recent_days=RECENT_DAYS, mastery_count=MASTERY_COUNT)
    now = datetime.now()
    return services.trainer.pick_training_word(
        src_filter=src_filter,
        now=now,
        now_s=now_str(),
        parse_dt=parse_dt,
    )


def mark_ignore(dict_base_path: str, src_filter: str, word: str) -> dict:
    services = build_services(dict_base_path, recent_days=RECENT_DAYS, mastery_count=MASTERY_COUNT)
    return services.dict.mark_ignore(word=word, src_filter=src_filter)


def mark_hard(dict_base_path: str, src_filter: str, word: str) -> dict:
    services = build_services(dict_base_path, recent_days=RECENT_DAYS, mastery_count=MASTERY_COUNT)
    return services.dict.mark_hard(word=word, src_filter=src_filter)


def translate_word(word: str, dict_base_path: str, target_lang: str, src_hint: str = "", context: str = "") -> dict:
    hooks = build_translation_hooks()
    services = build_services(
        dict_base_path,
        recent_days=RECENT_DAYS,
        mastery_count=MASTERY_COUNT,
        translation_hooks=hooks,
    )
    return services.translation.translate_word(
        word=word,
        target_lang=target_lang,
        src_hint=src_hint,
        now_s=now_str(),
        context=context,
    )


def translate_selection(text: str, dict_base_path: str, target_lang: str, src_hint: str = "") -> dict:
    hooks = build_translation_hooks()
    services = build_services(
        dict_base_path,
        recent_days=RECENT_DAYS,
        mastery_count=MASTERY_COUNT,
        translation_hooks=hooks,
    )
    return services.translation.translate_selection(text=text, target_lang=target_lang, src_hint=src_hint)


def dispatch(argv: list[str]) -> dict:
    """Business dispatcher: returns JSON-ready dict (ok:true/false)."""
    if len(argv) < 3:
        log.warning("Not enough arguments: argv=%s", argv)
        return _fail("Not enough arguments", code="ARGS", details={"argv": argv})

    mode = argv[1]
    text = argv[2]

    if mode == "word":
        if len(argv) < 4:
            log.warning("Missing dict path: argv=%s", argv)
            return _fail("Missing dict path", code="ARGS", details={"argv": argv})

        dict_base_path = argv[3]
        target_lang = argv[4] if len(argv) >= 5 else "RU"
        src_hint = argv[5] if len(argv) >= 6 else ""
        result = translate_word(text, dict_base_path, target_lang, src_hint)
        return _wrap_result(result)

    if mode == "selection":
        if len(argv) < 4:
            log.warning("selection mode: not enough arguments argv=%s", argv)
            return _fail("selection mode: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base_path = argv[3]
        target_lang = argv[4] if len(argv) >= 5 else "RU"
        src_hint = argv[5] if len(argv) >= 6 else ""
        result = translate_selection(text, dict_base_path, target_lang, src_hint)
        return _wrap_result(result)

    if mode == "train":
        dict_base_path = text
        src_filter = argv[3] if len(argv) >= 4 else ""
        result = pick_training_word(dict_base_path, src_filter or None)
        return _wrap_result(result)

    if mode == "mark_hard":
        if len(argv) < 5:
            log.warning("mark_hard: not enough arguments argv=%s", argv)
            return _fail("mark_hard: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base_path = text
        src_filter = argv[3]
        word = argv[4]
        result = mark_hard(dict_base_path, src_filter, word)
        return _wrap_result(result)

    if mode == "ignore":
        if len(argv) < 5:
            log.warning("ignore: not enough arguments argv=%s", argv)
            return _fail("ignore: not enough arguments", code="ARGS", details={"argv": argv})

        dict_base_path = text
        src_filter = argv[3]
        word = argv[4]
        result = mark_ignore(dict_base_path, src_filter, word)
        return _wrap_result(result)

    log.warning("Unknown mode: %s argv=%s", mode, argv)
    return _fail(f"Unknown mode: {mode}", code="ARGS", details={"argv": argv})

