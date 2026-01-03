from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List

from vim_deepl.repos.sqlite_repo import SQLiteRepo


@dataclass(frozen=True)
class BookMark:
    id: int
    path: str
    fingerprint: str
    lnum: int
    col: int
    length: int
    term: str
    kind: str


class BookMarksRepo:
    """
    Persistence for reading highlights (book marks).
    Independent from entries / entries_ctx tables.
    """

    def __init__(self, sqlite: SQLiteRepo) -> None:
        self._sqlite = sqlite

    @staticmethod
    def canon_path(path: str) -> str:
        return os.path.realpath(path)

    @staticmethod
    def sha256_file(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def upsert_mark(
        self,
        *,
        path: str,
        fingerprint: str,
        lnum: int,
        col: int,
        length: int,
        term: str,
        kind: str,
    ) -> int:
        sql = """
        INSERT INTO book_marks(path, fingerprint, lnum, col, length, term, kind, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(path, lnum, col, kind) DO UPDATE SET
          fingerprint = excluded.fingerprint,
          length      = excluded.length,
          term        = excluded.term,
          updated_at  = datetime('now')
        ;
        """
        path = self.canon_path(path)
        with self._sqlite.connect() as conn:
            conn.execute(sql, (path, fingerprint, lnum, col, length, term, kind))
            row = conn.execute(
                "SELECT id FROM book_marks WHERE path=? AND lnum=? AND col=? AND kind=?",
                (path, lnum, col, kind),
            ).fetchone()
            return int(row[0])

    def list_by_path(self, *, path: str) -> List[BookMark]:
        path = self.canon_path(path)
        with self._sqlite.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path, fingerprint, lnum, col, length, term, kind
                FROM book_marks
                WHERE path=?
                ORDER BY lnum, col
                """,
                (path,),
            ).fetchall()
        return [BookMark(*row) for row in rows]

    def list_by_fingerprint(self, *, fingerprint: str) -> List[BookMark]:
        with self._sqlite.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path, fingerprint, lnum, col, length, term, kind
                FROM book_marks
                WHERE fingerprint=?
                ORDER BY path, lnum, col
                """,
                (fingerprint,),
            ).fetchall()
        return [BookMark(*row) for row in rows]

    def relink_path_for_fingerprint(self, *, fingerprint: str, new_path: str) -> None:
        new_path = self.canon_path(new_path)
        with self._sqlite.connect() as conn:
            conn.execute(
                "UPDATE book_marks SET path=?, updated_at=datetime('now') WHERE fingerprint=?",
                (new_path, fingerprint),
            )

