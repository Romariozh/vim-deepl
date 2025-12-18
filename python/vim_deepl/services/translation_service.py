# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from vim_deepl.repos.translation_repo import TranslationRepo


# deepl_call(text, target_lang, context="") -> (translation, detected_src, err_string_or_None)
DeeplCall = Callable[[str, str], Tuple[str, str, Optional[str]]]


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


@dataclass(frozen=True)
class TranslationService:
    repo: TranslationRepo
    deps: TranslationDeps

    def _ensure_mw_definitions(self, term: str, src_lang: str, now_s: str) -> Optional[dict]:
        if src_lang != "EN":
            return None

        cached = self.repo.get_mw_definitions(term, src_lang)
        if cached is not None:
            return cached

        if self.deps.mw_fetch is None:
            return None

        try:
            defs = self.deps.mw_fetch(term, src_lang)
        except Exception:
            return None

        if not defs:
            return None

        self.repo.upsert_mw_definitions(term, src_lang, defs, now_s)
        return self.repo.get_mw_definitions(term, src_lang)

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
        ctx = (context or "").strip()

        # 1) CONTEXT MODE (separate cache: entries_ctx)
        if ctx:
            src_expected = (src_hint or "").upper() or "EN"
            h = self.deps.ctx_hash(ctx)

            cached = self.repo.get_ctx_entry(word, src_expected, target_lang, h)
            if cached:
                self.repo.touch_ctx_usage(word, src_expected, target_lang, h, now_s)
                if self.repo.get_base_entry_any_src(word, target_lang) is None:
                    self.repo.upsert_base_entry(word, cached["translation"], cached["src_lang"], target_lang, "", now_s)

                mw_defs = self._ensure_mw_definitions(word, cached["src_lang"], now_s)

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
                }

            tr, detected, err = self.deps.deepl_call(word, target_lang, context=ctx)
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
                }

            src = self.deps.normalize_src_lang(detected, src_hint)

            # write base entry too (so context words are always searchable in base cache)
            self.repo.upsert_base_entry(word, tr, src, target_lang, detected, now_s)

            # write context entry
            self.repo.upsert_ctx_entry(word, tr, src, target_lang, h, now_s)

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
            }

        # 2) BASE MODE (original cache: entries)
        row = self.repo.get_base_entry_any_src(word, target_lang)
        if row is not None:
            self.repo.touch_base_usage(row["id"], now_s)

            mw_defs = self._ensure_mw_definitions(word, row["src_lang"], now_s)

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
        self.repo.upsert_base_entry(word, tr, src, target_lang, detected, now_s)

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
