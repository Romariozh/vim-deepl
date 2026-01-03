from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Body
from pydantic import BaseModel, Field

from vim_deepl.services.bookmarks_service import BookmarksService

router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])


class MarkRequest(BaseModel):
    path: str = Field(..., description="Absolute/relative path to the book file")
    lnum: int = Field(..., ge=1)
    col: int = Field(..., ge=1)
    length: int = Field(..., ge=1)
    term: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)  # 'f2' | 'mw' (or 'seen')


def _svc(req: Request) -> BookmarksService:
    svc = getattr(req.app.state, "bookmarks", None)
    if svc is None:
        raise RuntimeError("BookmarksService is not initialized. Check dict_api.py startup handler.")
    return svc


@router.post("/mark")
def mark(req: Request, body: MarkRequest = Body(...)):
    try:
        return _svc(req).upsert_mark(
            path=body.path,
            lnum=body.lnum,
            col=body.col,
            length=body.length,
            term=body.term,
            kind=body.kind,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
def list_marks(req: Request, path: str):
    try:
        return _svc(req).list_marks_for_path(path=path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

