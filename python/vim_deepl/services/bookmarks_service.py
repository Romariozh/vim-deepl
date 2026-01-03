from __future__ import annotations

import os
from typing import List, Dict, Any

from vim_deepl.repos.book_marks_repo import BookMarksRepo, BookMark


class BookmarksService:
    """
    Bookmarks/highlights API for reading mode.
    """

    def __init__(self, repo: BookMarksRepo) -> None:
        self._repo = repo

    @staticmethod
    def _canon_path(path: str) -> str:
        return os.path.realpath(path)

    def upsert_mark(
        self,
        *,
        path: str,
        lnum: int,
        col: int,
        length: int,
        term: str,
        kind: str,
    ) -> Dict[str, Any]:
        canon = self._canon_path(path)
        fingerprint = self._repo.sha256_file(canon)
        mark_id = self._repo.upsert_mark(
            path=canon,
            fingerprint=fingerprint,
            lnum=lnum,
            col=col,
            length=length,
            term=term,
            kind=kind,
        )
        return {"id": mark_id, "path": canon, "fingerprint": fingerprint}

    def list_marks_for_path(self, *, path: str) -> Dict[str, Any]:
        canon = self._canon_path(path)

        # Fast path: try by path (no sha256)
        marks = self._repo.list_by_path(path=canon)
        if marks:
            # fingerprint is stored per mark; but may vary if you ever rehash.
            # We return fingerprint of the first row for convenience.
            fp = marks[0].fingerprint
            return {"path": canon, "fingerprint": fp, "marks": [self._to_item(m) for m in marks]}

        # Fallback: compute sha256 and try by fingerprint (for moved/renamed file)
        fingerprint = self._repo.sha256_file(canon)
        marks = self._repo.list_by_fingerprint(fingerprint=fingerprint)
        if marks:
            self._repo.relink_path_for_fingerprint(fingerprint=fingerprint, new_path=canon)

        return {"path": canon, "fingerprint": fingerprint, "marks": [self._to_item(m) for m in marks]}

    @staticmethod
    def _to_item(m: BookMark) -> Dict[str, Any]:
        return {
            "id": m.id,
            "lnum": m.lnum,
            "col": m.col,
            "length": m.length,
            "term": m.term,
            "kind": m.kind,
        }

