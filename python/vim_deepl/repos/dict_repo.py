from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from vim_deepl.repos.schema import ensure_schema
from vim_deepl.repos.sqlite_repo import SQLiteRepo


def resolve_db_path(dict_base_path: str, fallback_db_path: Path) -> Path:
    """
    Backward-compatible resolver:
    - if dict_base_path is a directory: try common sqlite filenames inside it
    - if dict_base_path is a sqlite file: use it
    - else fallback to config db
    """
    try:
        base = Path(dict_base_path).expanduser()
    except Exception:
        return fallback_db_path

    if base.is_dir():
        candidates = [
            base / "vocab.db",
            base / "vim_deepl.sqlite3",
            base / "deepl.sqlite3",
            base / "dict.sqlite3",
        ]
        for p in candidates:
            if p.exists():
                return p

    if base.is_file() and base.suffix in (".db", ".sqlite", ".sqlite3"):
        return base

    return fallback_db_path


@dataclass(frozen=True)
class DictRepo:
    db: SQLiteRepo

    def set_ignore(self, term: str, src_lang: str) -> int:
        """SET ignore=1; return affected rows."""
        with self.db.tx() as conn:
            ensure_schema(conn)
            cur = conn.execute(
                """
                UPDATE entries
                SET ignore = 1
                WHERE term = ?
                  AND src_lang = ?
                """,
                (term, src_lang),
            )
            return cur.rowcount

    def inc_hard_and_get(self, term: str, src_lang: str) -> Optional[int]:
        """
        hard = hard + 1; return new hard value, or None if not found.
        """
        with self.db.tx() as conn:
            ensure_schema(conn)

            cur = conn.execute(
                """
                UPDATE entries
                SET hard = hard + 1
                WHERE term = ?
                  AND src_lang = ?
                """,
                (term, src_lang),
            )
            if cur.rowcount == 0:
                return None

            row = conn.execute(
                """
                SELECT hard
                FROM entries
                WHERE term = ?
                  AND src_lang = ?
                """,
                (term, src_lang),
            ).fetchone()

            return int(row["hard"]) if row and row["hard"] is not None else None

