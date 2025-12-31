# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json

from vim_deepl.utils.logging import get_logger
from vim_deepl.services.container import build_services, TranslationHooks
from vim_deepl.utils.config import load_config


log = get_logger("vim_deepl.cli")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime(1970, 1, 1)


def ctx_hash(context: str) -> str:
    ctx = " ".join((context or "").split())  # normalize like storage
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


def dispatch(argv: list[str]) -> dict:
    """Business dispatcher: returns JSON-ready dict (ok:true/false)."""
    if len(argv) < 3:
        log.warning("Not enough arguments: argv=%s", argv)
        return _fail("Not enough arguments", code="ARGS", details={"argv": argv})

    cfg = load_config()
    recent_days = getattr(cfg, "trainer_recent_days", 7)
    mastery_count = getattr(cfg, "trainer_mastery_count", 7)

    mode = argv[1]
    text = argv[2]

    # Build services once per run.
    # Note: dict_base_path differs by mode:
    # - word/selection: argv[3] contains dict_base_path
    # - train/mark_hard/ignore: argv[2] ("text") is dict_base_path
    services = None

    if mode in ("word", "selection"):
        if len(argv) < 4:
            log.warning("Missing dict path: argv=%s", argv)
            return _fail("Missing dict path", code="ARGS", details={"argv": argv})

        dict_base_path = argv[3]
        hooks = build_translation_hooks()

        services = build_services(
            dict_base_path,
            cfg=cfg,
            recent_days=recent_days,
            mastery_count=mastery_count,
            translation_hooks=hooks,
        )

    elif mode in ("train", "review", "mark_hard", "ignore"):

        dict_base_path = text
        services = build_services(
            dict_base_path,
            cfg=cfg,
            recent_days=recent_days,
            mastery_count=mastery_count,
        )

    else:
        log.warning("Unknown mode: %s argv=%s", mode, argv)
        return _fail(f"Unknown mode: {mode}", code="ARGS", details={"argv": argv})

    # Now route using the already-built services.
    if mode == "word":
        target_lang = argv[4] if len(argv) >= 5 else "RU"
        src_hint = argv[5] if len(argv) >= 6 else ""
        # context is optional; keep it if you already support it in CLI
        context = argv[6] if len(argv) >= 7 else ""
        #context = ""
        log.info("WORD ctx_len=%s ctx=%r", len(context or ""), context)

        result = services.translation.translate_word(
            word=text,
            target_lang=target_lang,
            src_hint=src_hint,
            now_s=now_str(),
            context=context,
        )
        return _wrap_result(result)

    if mode == "selection":
        target_lang = argv[4] if len(argv) >= 5 else "RU"
        src_hint = argv[5] if len(argv) >= 6 else ""

        result = services.translation.translate_selection(
            text=text,
            target_lang=target_lang,
            src_hint=src_hint,
        )
        return _wrap_result(result)

    if mode == "train":
        src_filter = argv[3] if len(argv) >= 4 else ""

        exclude_card_ids: list[int] = []
        if len(argv) >= 5 and argv[4]:
            try:
                exclude_card_ids = json.loads(argv[4])
                if not isinstance(exclude_card_ids, list):
                    exclude_card_ids = []
            except Exception:
                exclude_card_ids = []

        result = services.trainer.pick_training_word(
            src_filter=src_filter or None,
            now=datetime.now(),
            now_s=now_str(),
            parse_dt=parse_dt,
            exclude_card_ids=exclude_card_ids,
        )
        return _wrap_result(result)

    if mode == "review":
        if len(argv) < 6:
            log.warning("review: not enough arguments argv=%s", argv)
            return _fail("review: not enough arguments", code="ARGS", details={"argv": argv})

        src_filter = argv[3] if len(argv) >= 4 else ""
        try:
            card_id = int(argv[4])
            grade = int(argv[5])
        except Exception:
            return _fail("review: card_id and grade must be integers", code="ARGS", details={"argv": argv})

        if grade < 0 or grade > 5:
            return _fail("review: grade must be in range 0..5", code="ARGS", details={"argv": argv})

        now = datetime.now()

        # Apply review
        services.trainer.review_training_card(card_id=card_id, grade=grade, now=now)

        # Return next item (same shape as /train/next)
        result = services.trainer.pick_training_word(
            src_filter=src_filter or None,
            now=now,
            now_s=now_str(),
            parse_dt=parse_dt,
        )
        return _wrap_result(result)

    if mode == "mark_hard":
        if len(argv) < 5:
            log.warning("mark_hard: not enough arguments argv=%s", argv)
            return _fail("mark_hard: not enough arguments", code="ARGS", details={"argv": argv})
        src_filter = argv[3]
        word = argv[4]
        result = services.dict.mark_hard(word=word, src_filter=src_filter)
        return _wrap_result(result)

    # mode == "ignore"
    if len(argv) < 5:
        log.warning("ignore: not enough arguments argv=%s", argv)
        return _fail("ignore: not enough arguments", code="ARGS", details={"argv": argv})
    src_filter = argv[3]
    word = argv[4]
    result = services.dict.mark_ignore(word=word, src_filter=src_filter)
    return _wrap_result(result)

