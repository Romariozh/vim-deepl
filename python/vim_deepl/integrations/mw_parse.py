# python/vim_deepl/integrations/mw_parse.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_non_alnum = re.compile(r"[^a-z0-9]+")

def norm(s: Any) -> str:
    """Normalize MW strings for matching (safe for non-strings)."""
    if not isinstance(s, str):
        return ""
    return s.strip().lower()

def _norm_token(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("*", "")          # MW syllable markers
    s = _non_alnum.sub("", s)       # strip punctuation/spaces
    return s

def pick_main_entry(entries: list[dict[str, Any]], term: str) -> Optional[dict[str, Any]]:
    t = _norm_token(term)

    # 1) meta.id matches (ignore ":1")
    for e in entries:
        meta = e.get("meta") or {}
        mid = (meta.get("id") or "")
        mid0 = mid.split(":")[0]
        if _norm_token(mid0) == t:
            return e

    # 2) hwi.hw matches (be*side -> beside)
    for e in entries:
        hwi = e.get("hwi") or {}
        hw = hwi.get("hw") or ""
        if _norm_token(hw) == t:
            return e

    # 3) stems contain term
    for e in entries:
        meta = e.get("meta") or {}
        stems = meta.get("stems") or []
        if isinstance(stems, list) and any(_norm_token(s) == t for s in stems if isinstance(s, str)):
            return e

    return None

def _collect_audio_from_prs(prs: Any, out: List[str]) -> None:
    """Collect sound.audio from a MW pronunciation list (prs)."""
    if not isinstance(prs, list):
        return

    for p in prs:
        if not isinstance(p, dict):
            continue
        snd = p.get("sound") or {}
        if isinstance(snd, dict):
            aid = snd.get("audio")
            if isinstance(aid, str):
                aid = aid.strip()
                if aid:
                    out.append(aid)


def collect_audio_ids_from_entry(entry: Dict[str, Any]) -> List[str]:
    """
    Collect audio IDs from a single MW entry:
    - entry.hwi.prs[].sound.audio
    - entry.uros[].prs[].sound.audio (derived forms)
    Deduplicates while preserving order.
    """
    found: List[str] = []

    # Main headword pronunciations
    hwi = entry.get("hwi") or {}
    if isinstance(hwi, dict):
        _collect_audio_from_prs(hwi.get("prs"), found)

    # Derived forms (uros)
    uros = entry.get("uros") or []
    if isinstance(uros, list):
        for u in uros:
            if isinstance(u, dict):
                _collect_audio_from_prs(u.get("prs"), found)

    # Deduplicate in-order
    seen = set()
    result: List[str] = []
    for aid in found:
        if aid not in seen:
            seen.add(aid)
            result.append(aid)

    return result


def extract_audio_main_and_ids(entries: List[Any], term: str) -> Tuple[Optional[str], List[str]]:
    """
    Extract audio from the main entry only.
    Returns: (audio_main, audio_ids)
    """
    main = pick_main_entry(entries, term)
    if not main:
        return None, []

    audio_ids = collect_audio_ids_from_entry(main)
    audio_main = audio_ids[0] if audio_ids else None
    return audio_main, audio_ids

