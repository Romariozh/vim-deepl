# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
import json
import re

from vim_deepl.repos.translation_repo import TranslationRepo
from vim_deepl.integrations.mw_parse import extract_audio_main_and_ids
from vim_deepl.services.mw_audio_service import prefetch_mw_audio_in_background


# deepl_call(text, target_lang, context="") -> (translation, detected_src, err_string_or_None)
DeeplCall = Callable[[str, str], Tuple[str, str, Optional[str]]]

_RE_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")

@dataclass(frozen=True)
class TranslationDeps:
    """
    External dependencies (callables) injected from deepl_helper.py for now.
    Later you can move these into separate modules/classes.
    """
    deepl_call: Callable[..., Tuple[str, str, Optional[str]]]
    normalize_src_lang: Callable[[str, str], str]
    ctx_hash: Callable[[str], str]
    mw_fetch: Optional[Callable[[str, str], Optional[dict]]] = None  # (term, src_lang) -> defs dict

def _mw_src_lang(term: str, src_hint: str | None, detected_lang: str | None) -> str:
    """
    Decide source language for MW lookup.
    Priority:
      1) explicit src_hint from Vim (F3 cycle)
      2) detected_lang (if you have it)
      3) fallback heuristic: latin word -> EN
    """
    if src_hint:
        return src_hint.upper()

    if detected_lang:
        return detected_lang.upper()

    if term and _RE_LATIN_WORD.match(term):
        return "EN"

    return ""

@dataclass(frozen=True)
class TranslationService:
    repo: TranslationRepo
    deps: TranslationDeps

    def _ensure_mw_definitions(self, term: str, src_lang: str, now_s: str) -> Optional[dict]:
        src_u = (src_lang or "").upper().strip()
        if src_u != "EN":
            return None

        # IMPORTANT: use normalized src_u everywhere below
        cached = self.repo.get_mw_definitions(term, src_u)
        if cached is not None:
            # Backfill audio_main/audio_ids for old rows (no MW refetch).
            # Only do it if raw_json looks like MW list[dict].
            try:
                if isinstance(cached, dict) and not cached.get("audio_ids"):
                    raw = cached.get("raw_json")
                    if isinstance(raw, str) and raw:
                        parsed = json.loads(raw)
                        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                            audio_main, audio_ids = extract_audio_main_and_ids(parsed, term)
                            if audio_main or audio_ids:
                                patched = dict(cached)
                                patched["audio_main"] = audio_main
                                patched["audio_ids"] = audio_ids
                                # Upsert will update audio_main/audio_ids columns.
                                self.repo.upsert_mw_definitions(term, src_u, patched, now_s)
                                cached = self.repo.get_mw_definitions(term, src_u)
            except Exception:
                # Backfill is best-effort.
                pass

            # Prefetch audio in background (best-effort).
            if isinstance(cached, dict):
                prefetch_mw_audio_in_background(cached.get("audio_main"))
            return cached

        if self.deps.mw_fetch is None:
            return None

        try:
            defs = self.deps.mw_fetch(term, src_u)
        except Exception as e:
            # Do not swallow MW errors silently; helps debugging systemd/env issues.
            print(f"[mw] fetch failed for term={term} src_lang={src_u}: {e}", flush=True)
            return None

        if not defs:
            return None

        self.repo.upsert_mw_definitions(term, src_u, defs, now_s)

        # Prefetch audio in background (best-effort).
        if isinstance(defs, dict):
            prefetch_mw_audio_in_background(defs.get("audio_main"))

        return self.repo.get_mw_definitions(term, src_u)

    def translate_word(
        self,
        word: str,
        target_lang: str,
        src_hint: str,
        now_s: str,
        context: str = "",
    ) -> Dict[str, Any]:
        """
        Translate a single word with caching:
          - if context provided: use entries_ctx only
          - else: use base cache entries
          - MW definitions returned for EN
        """
        target_lang = (target_lang or "RU").upper()
        ctx_text = " ".join((context or "").split())  # canonical normalized context

        # 1) CONTEXT MODE (separate cache: entries_ctx)
        if ctx_text:
            src_expected = (src_hint or "").upper() or "EN"
            src_for_mw = _mw_src_lang(word, src_hint, src_expected)  # detected_lang unknown here; use src_expected
            h = self.deps.ctx_hash(ctx_text)

            cached = self.repo.get_ctx_entry(word, src_expected, target_lang, h)
            if cached:
                self.repo.touch_ctx_usage(word, src_expected, target_lang, h, now_s)
                if self.repo.get_base_entry_any_src(word, target_lang) is None:
                    self.repo.upsert_base_entry(word, cached["translation"], cached["src_lang"], target_lang, "", now_s, context=context)

                mw_defs = self._ensure_mw_definitions(word, src_for_mw, now_s)
                alts = self.repo.list_ctx_translations(word, cached["src_lang"], target_lang, limit=10)

                return {
                    "type": "word",
                    "source": word,
                    "text": cached["translation"],
                    "target_lang": target_lang,
                    "detected_source_lang": cached["src_lang"],
                    "from_cache": True,
                    "timestamp": cached["created_at"],
                    "last_used": now_s,
                    "count": cached["count"] + 1,
                    "error": None,
                    "mw_definitions": mw_defs,
                    "context_used": True,
                    "cache_source": "context",
                    "context_raw": cached.get("ctx_text", ctx_text),
                    "ctx_translations": alts,
                }

            tr, detected, err = self.deps.deepl_call(word, target_lang, context=ctx_text)
            if err:
                return {
                    "type": "word",
                    "source": word,
                    "text": "",
                    "target_lang": target_lang,
                    "detected_source_lang": "",
                    "from_cache": False,
                    "timestamp": now_s,
                    "last_used": now_s,
                    "count": 0,
                    "error": err,
                    "mw_definitions": None,
                    "context_used": True,
                    "cache_source": None,
                    "context_raw": ctx_text,
                }

            src = self.deps.normalize_src_lang(detected, src_hint)

            # write base entry too (so context words are always searchable in base cache)
            self.repo.upsert_base_entry(word, tr, src, target_lang, detected, now_s, context=context)

            # write context entry
            if ctx_text:
                self.repo.upsert_ctx_entry(word, tr, src, target_lang, h, now_s, ctx_text=ctx_text)

            alts = self.repo.list_ctx_translations(word, src, target_lang, limit=10)
            mw_defs = self._ensure_mw_definitions(word, src, now_s)

            return {
                "type": "word",
                "source": word,
                "text": tr,
                "target_lang": target_lang,
                "detected_source_lang": src,
                "from_cache": False,
                "timestamp": now_s,
                "last_used": now_s,
                "count": 1,
                "error": None,
                "mw_definitions": mw_defs,
                "context_used": True,
                "cache_source": None,
                "context_raw": ctx_text,
                "ctx_translations": alts,
            }

        # 2) BASE MODE (original cache: entries)
        row = self.repo.get_base_entry_any_src(word, target_lang, src_hint)
        if row is not None:
            self.repo.touch_base_usage(row["id"], now_s)

            src_for_mw = _mw_src_lang(word, src_hint, row["src_lang"])
            mw_defs = self._ensure_mw_definitions(word, src_for_mw, now_s)
            alts = self.repo.list_ctx_translations(word, row["src_lang"], target_lang, limit=10)


            return {
                "type": "word",
                "source": word,
                "text": row["translation"],
                "target_lang": target_lang,
                "detected_source_lang": row["src_lang"],
                "from_cache": True,
                "timestamp": row["created_at"],
                "last_used": now_s,
                "count": row["count"] + 1,
                "error": None,
                "mw_definitions": mw_defs,
                "context_used": False,
                "cache_source": "base",
                "ctx_translations": alts,
            }

        tr, detected, err = self.deps.deepl_call(word, target_lang, context="")
        if err:
            return {
                "type": "word",
                "source": word,
                "text": "",
                "target_lang": target_lang,
                "detected_source_lang": "",
                "from_cache": False,
                "timestamp": now_s,
                "last_used": now_s,
                "count": 0,
                "error": err,
                "mw_definitions": None,
                "context_used": False,
                "cache_source": None,
            }

        src = self.deps.normalize_src_lang(detected, src_hint)
        self.repo.upsert_base_entry(word, tr, src, target_lang, detected, now_s, context=context)

        mw_defs = self._ensure_mw_definitions(word, src, now_s)

        return {
            "type": "word",
            "source": word,
            "text": tr,
            "target_lang": target_lang,
            "detected_source_lang": src,
            "from_cache": False,
            "timestamp": now_s,
            "last_used": now_s,
            "count": 1,
            "error": None,
            "mw_definitions": mw_defs,
            "context_used": False,
            "cache_source": None,
        }

    def translate_selection(self, text: str, target_lang: str, src_hint: str = "") -> Dict[str, Any]:
        """
        Translate any text fragment. No SQLite is used here – we just proxy DeepL.
        """
        target_lang = (target_lang or "RU").upper()

        tr, detected, err = self.deps.deepl_call(text, target_lang)
        if err:
            return {
                "type": "selection",
                "source": text,
                "text": "",
                "target_lang": target_lang,
                "detected_source_lang": "",
                "error": err,
            }

        src = self.deps.normalize_src_lang(detected, src_hint)

        return {
            "type": "selection",
            "source": text,
            "text": tr,
            "target_lang": target_lang,
            "detected_source_lang": src,
            "error": None,
        }
