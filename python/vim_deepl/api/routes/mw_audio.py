from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from vim_deepl.services.mw_audio_service import ensure_mw_audio_cached, play_audio_twice_in_background
from fastapi.responses import FileResponse

router = APIRouter()

class MWPlayReq(BaseModel):
    audio_id: str
    play_server: bool = False  # default: do NOT play on server (VM). Only cache.


@router.post("/mw/audio/play")
def mw_audio_play(req: MWPlayReq):
    """Ensure MW audio is cached. Optionally play on server if explicitly requested."""
    audio_id = (req.audio_id or "").strip()
    if not audio_id:
        raise HTTPException(status_code=400, detail="audio_id is required")

    # Basic sanity: audio ids are expected to be simple tokens like 'lovesi01'.
    if any(c in audio_id for c in ("/", "\\", " ", "\t", "\n")):
        raise HTTPException(status_code=400, detail="invalid audio_id")

    try:
        path = ensure_mw_audio_cached(audio_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch audio: {e}")

    if req.play_server:
        ok, msg = play_audio_twice_in_background(path, delay_sec=2.0)
        status = "ok" if ok else "cached_only"
        return {"status": status, "audio_id": audio_id, "cached_path": str(path), "playback": msg}

    # cache-only mode
    return {"status": "cached_only", "audio_id": audio_id, "cached_path": str(path), "playback": "cache_only"}


@router.get("/mw/audio/file/{audio_id}")
def mw_audio_file(audio_id: str):
    audio_id = (audio_id or "").strip()
    if not audio_id:
        raise HTTPException(status_code=400, detail="audio_id is required")
    if any(c in audio_id for c in ("/", "\\", " ", "\t", "\n")):
        raise HTTPException(status_code=400, detail="invalid audio_id")

    path = ensure_mw_audio_cached(audio_id)
    return FileResponse(
        path=str(path),
        media_type="audio/mpeg",
        filename=f"{audio_id}.mp3",
    )

