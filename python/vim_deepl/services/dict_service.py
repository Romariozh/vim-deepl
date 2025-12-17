from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from vim_deepl.repos.dict_repo import DictRepo


@dataclass(frozen=True)
class DictService:
    repo: DictRepo

    @staticmethod
    def _normalize_src(src_filter: str) -> str:
        return (src_filter or "").strip().upper()

    def mark_ignore(self, word: str, src_filter: str) -> Dict[str, Any]:
        """
        Mark a word as ignored (trainer will skip it).
        """
        src = self._normalize_src(src_filter)
        if src not in ("EN", "DA"):
            return {"type": "ignore", "error": f"Unsupported src_filter={src_filter}"}

        changed = self.repo.set_ignore(term=word, src_lang=src)
        if changed == 0:
            return {"type": "ignore", "error": f"Word '{word}' not found for src_lang={src}"}

        return {"type": "ignore", "word": word, "src_lang": src, "ignore": True, "error": None}

    def mark_hard(self, word: str, src_filter: str) -> Dict[str, Any]:
        """
        Increase 'hard' counter for a word (treated as more difficult).
        """
        src = self._normalize_src(src_filter)
        if src not in ("EN", "DA"):
            return {"type": "mark_hard", "error": f"Unsupported src_filter={src_filter}"}

        hard_val = self.repo.inc_hard_and_get(term=word, src_lang=src)
        if hard_val is None:
            return {"type": "mark_hard", "error": f"Word '{word}' not found for src_lang={src}"}

        return {"type": "mark_hard", "word": word, "src_lang": src, "hard": hard_val, "error": None}
