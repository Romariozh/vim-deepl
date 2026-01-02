from __future__ import annotations

import os
import re
import time
import threading
import subprocess
import shutil
import signal
from pathlib import Path
from typing import Optional, Set

import urllib.request
import logging
log = logging.getLogger("uvicorn.error")
log.info("[mw_audio] LOADED_FROM=%s", __file__)


DEFAULT_PULSE_NATIVE_SOCK = "/home/ro_/.cache/pulse-native"

_AUDIO_LOCK = threading.Lock()
_AUDIO_COND = threading.Condition(_AUDIO_LOCK)

_PLAY_TOKEN = 0
_CURRENT_PROC: subprocess.Popen | None = None
_WORKER_THREAD: threading.Thread | None = None

# Pending request for the worker thread
_PENDING_REQ: tuple[int, Path, float] | None = None
_WORKER_STARTED = False

# Track inflight prefetch to avoid spawning many threads for the same audio_id.
_PREFETCH_LOCK = threading.Lock()
_PREFETCH_INFLIGHT: Set[str] = set()

def mw_audio_subdir(audio_id: str) -> str:
    """
    Determine MW audio subdirectory according to MW docs.
    """
    if audio_id.startswith("bix"):
        return "bix"
    if audio_id.startswith("gg"):
        return "gg"
    if re.match(r"^[0-9_]", audio_id):
        return "number"
    return audio_id[0].lower()


def mw_audio_url(audio_id: str, lang: str = "en", country: str = "us", fmt: str = "mp3") -> str:
    """
    Build MW pronunciation audio URL.
    Docs: https://media.merriam-webster.com/audio/prons/[language]/[country]/[format]/[subdir]/[audio].[format]
    """
    subdir = mw_audio_subdir(audio_id)
    return f"https://media.merriam-webster.com/audio/prons/{lang}/{country}/{fmt}/{subdir}/{audio_id}.{fmt}"


def mw_audio_cache_dir() -> Path:
    """
    Store audio in ~/.local/share/vim-deepl/mw_audio by default (or XDG_DATA_HOME).
    """
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        base = Path(data_home)
    else:
        base = Path.home() / ".local" / "share"

    d = base / "vim-deepl" / "mw_audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_mw_audio_cached(audio_id: str) -> Path:
    """
    Download MW audio to cache if missing. Returns local file path.
    """
    cache_dir = mw_audio_cache_dir()
    dst = cache_dir / f"{audio_id}.mp3"

    # HIT (cache already exists)
    if dst.exists():
        try:
            size = dst.stat().st_size
        except OSError as e:
            # Если stat/exists глюканули — просто попробуем перекачать (редко).
            log.warning("[mw_audio] HIT_CHECK_FAILED audio_id=%r path=%r err=%s", audio_id, str(dst), e)
        else:
            if size > 0:
                log.info("[mw_audio] HIT audio_id=%r path=%r size=%s", audio_id, str(dst), size)
                return dst

    url = mw_audio_url(audio_id, lang="en", country="us", fmt="mp3")
    tmp = cache_dir / f".{audio_id}.mp3.tmp"

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status: Optional[int] = getattr(resp, "status", None)
            ctype = resp.headers.get("Content-Type", "")

            if status is not None and status != 200:
                raise RuntimeError(f"HTTP {status}")

            # Защита от HTML/ошибок вместо mp3
            if ctype and ("audio" not in ctype and "mpeg" not in ctype and "mp3" not in ctype):
                raise RuntimeError(f"unexpected content-type: {ctype}")

            data = resp.read()
            if not data:
                raise RuntimeError("empty response body")

        tmp.write_bytes(data)
        tmp.replace(dst)  # атомарно
        size = dst.stat().st_size
        log.info("[mw_audio] DOWNLOADED audio_id=%r path=%r size=%s", audio_id, str(dst), size)
        return dst

    except Exception as e:
        # best-effort cleanup
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        log.warning("[mw_audio] FAILED audio_id=%r url=%r path=%r err=%s", audio_id, url, str(dst), e)
        raise

def pick_player() -> Optional[list[str]]:
    """
    Prefer mplayer; fallback to mpv/ffplay if needed.
    """
    for cmd in (
        ["mplayer", "-really-quiet", "-nolirc", "-noconsolecontrols"],
        ["mpv", "--no-terminal"],
        ["ffplay", "-nodisp", "-autoexit"],
    ):
        try:
            subprocess.run([cmd[0], "-h"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return cmd
        except Exception:
            continue
    return None

def _build_audio_env() -> dict[str, str]:
    """
    Build environment for audio playback.

    We default to PulseAudio-over-SSH socket at /home/ro_/.cache/pulse-native, so
    audio goes to the host when user connected with:
      ssh -R /home/ro_/.cache/pulse-native:/run/user/1000/pulse/native vm_fedora
    """
    env = dict(os.environ)
    env.setdefault("PULSE_SERVER", f"unix:{DEFAULT_PULSE_NATIVE_SOCK}")

    # Tag the stream so it is easy to find in PipeWire/WirePlumber.
    # PulseAudio reads client properties from PULSE_PROP.
    env.setdefault("PULSE_PROP", "application.name=vim-deepl")
    return env

def _set_sink_input_volume_for_pid(pid: int, env: dict[str, str], volume: str = "100%") -> None:
    """
    Best-effort: find sink-input created by this pid and set its volume.
    Works with PulseAudio and PipeWire (PulseAudio compatibility layer).
    """
    if not shutil.which("pactl"):
        return

    # PulseAudio text output usually contains:
    #   Sink Input #287
    #   application.process.id = "1176"
    pid_str = str(pid)
    re_sink = re.compile(r"^Sink Input #(\d+)\s*$", re.M)
    re_pid  = re.compile(r'^\s*application\.process\.id\s*=\s*"' + re.escape(pid_str) + r'"\s*$', re.M)

    deadline = time.time() + 2.0
    sink_id: Optional[str] = None

    while time.time() < deadline and sink_id is None:
        try:
            out = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=env,
                check=False,
            ).stdout or ""
        except Exception:
            return

        # Split into blocks by "Sink Input #"
        # Keep the header line in each block.
        parts = re.split(r"(?=^Sink Input #\d+\s*$)", out, flags=re.M)
        for block in parts:
            m_id = re_sink.search(block)
            if not m_id:
                continue
            if re_pid.search(block):
                sink_id = m_id.group(1)
                break

        if sink_id is None:
            time.sleep(0.05)

    if not sink_id:
        return

    try:
        subprocess.run(
            ["pactl", "set-sink-input-volume", sink_id, volume],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            check=False,
        )
        subprocess.run(
            ["pactl", "set-sink-input-mute", sink_id, "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            check=False,
        )
    except Exception:
        return

def _stop_proc(p: subprocess.Popen | None) -> None:
    """Stop a process group best-effort (we spawn with start_new_session=True)."""
    if not p:
        return
    if p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=0.5)
        except Exception:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        pass


def _audio_worker_loop(player: list[str], env: dict[str, str]) -> None:
    """Single worker responsible for all audio playback (prevents overlaps)."""
    global _CURRENT_PROC, _PENDING_REQ, _PLAY_TOKEN

    while True:
        try:
            with _AUDIO_COND:
                while _PENDING_REQ is None:
                    _AUDIO_COND.wait()

                token, file_path, delay_sec = _PENDING_REQ
                log.debug("[mw_audio] WORKER_GOT token=%s play_token=%s file=%r delay=%s", token, _PLAY_TOKEN, str(file_path), delay_sec)
                _PENDING_REQ = None

                # Cancel any current playback immediately
                p = _CURRENT_PROC
                _CURRENT_PROC = None

            _stop_proc(p)

            # Play twice sequentially. At any moment a newer token may arrive -> cancel.
            for i in range(2):
                with _AUDIO_LOCK:
                    if token != _PLAY_TOKEN:
                        break

                try:
                    p = subprocess.Popen(
                        player + [str(file_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,   # <-- чтобы видеть ошибки
                        text=True,
                        env=env,
                        start_new_session=True,
                    )
                except Exception as e:
                    log.info("[mw_audio] WORKER_GOT token=%s play_token=%s file=%r delay=%s", token, _PLAY_TOKEN, str(file_path), delay_sec)
                    break

                with _AUDIO_LOCK:
                    if token != _PLAY_TOKEN:
                        _stop_proc(p)
                        break
                    _CURRENT_PROC = p

                # Best-effort: set stream volume to 100%
                try:
                    _set_sink_input_volume_for_pid(p.pid, env, volume="100%")
                except Exception as e:
                    log.warning("[mw_audio] VOLUME_SET_FAILED pid=%s err=%s", p.pid, e)

                # Wait for playback to finish
                try:
                    p.wait(timeout=10.0)
                except Exception:
                    _stop_proc(p)

                # <-- логируем rc/stderr чтобы видеть, почему “тишина”
                try:
                    err = (p.stderr.read() if p.stderr else "")  # type: ignore[union-attr]
                    err = (err or "")[-800:]
                    log.info("[mw_audio] WORKER_GOT token=%s play_token=%s file=%r delay=%s", token, _PLAY_TOKEN, str(file_path), delay_sec)
                except Exception:
                    pass

                with _AUDIO_LOCK:
                    if token != _PLAY_TOKEN:
                        break

                if i == 0:
                    end_at = time.time() + float(delay_sec)
                    while time.time() < end_at:
                        with _AUDIO_LOCK:
                            if token != _PLAY_TOKEN:
                                break
                        time.sleep(0.05)

            with _AUDIO_LOCK:
                if token == _PLAY_TOKEN:
                    _CURRENT_PROC = None

        except Exception as e:
            log.debug("[mw_audio] PLAY_EXIT rc=%s path=%r stderr=%r", p.returncode, str(file_path), err)
            with _AUDIO_LOCK:
                _CURRENT_PROC = None
            time.sleep(0.1)
            continue

def play_audio_twice_in_background(file_path: Path, delay_sec: float = 1.0) -> tuple[bool, str]:
    """Queue audio playback on a single worker thread. Cancels previous playback if any."""
    global _PLAY_TOKEN, _PENDING_REQ, _WORKER_THREAD

    player = pick_player()
    if not player:
        return False, "no player found"

    env = _build_audio_env()

    with _AUDIO_COND:
        _PLAY_TOKEN += 1
        token = _PLAY_TOKEN

        # Start/restart the worker if needed
        if _WORKER_THREAD is None or not _WORKER_THREAD.is_alive():
            _WORKER_THREAD = threading.Thread(
                target=_audio_worker_loop,
                args=(player, env),
                daemon=True,
            )
            _WORKER_THREAD.start()
            print("[mw_audio] WORKER_STARTED", flush=True)

        _PENDING_REQ = (token, file_path, float(delay_sec))
        alive = (_WORKER_THREAD is not None and _WORKER_THREAD.is_alive())
        log.info("[mw_audio] QUEUE token=%s file=%r worker_alive=%s", token, str(file_path), alive)
        _AUDIO_COND.notify()

        print(f"[mw_audio] QUEUE token={token} file={str(file_path)!r} delay={delay_sec} "
              f"worker_alive={_WORKER_THREAD is not None and _WORKER_THREAD.is_alive()}",
              flush=True)

    return True, f"queued: {' '.join(player)}"

def prefetch_mw_audio_in_background(audio_id: Optional[str]) -> None:
    """
    Prefetch MW audio in background (best-effort).

    IMPORTANT: prefetch must ONLY cache/download, never play audio.
    Playing is triggered only by explicit user action (F4).
    """
    aid = (audio_id or "").strip()
    if not aid:
        return

    with _PREFETCH_LOCK:
        if aid in _PREFETCH_INFLIGHT:
            return
        _PREFETCH_INFLIGHT.add(aid)

    def _run() -> None:
        try:
            ensure_mw_audio_cached(aid)
        except Exception:
            # ensure_mw_audio_cached already printed FAILED with details
            pass
        finally:
            with _PREFETCH_LOCK:
                _PREFETCH_INFLIGHT.discard(aid)

    threading.Thread(target=_run, daemon=True).start()
