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

DEFAULT_PULSE_NATIVE_SOCK = "/tmp/pulse-native"

_AUDIO_LOCK = threading.Lock()
_AUDIO_COND = threading.Condition(_AUDIO_LOCK)

_PLAY_TOKEN = 0
_CURRENT_PROC: subprocess.Popen | None = None

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
    if dst.exists() and dst.stat().st_size > 0:
        return dst

    url = mw_audio_url(audio_id, lang="en", country="us", fmt="mp3")
    tmp = cache_dir / f".{audio_id}.mp3.tmp"

    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        tmp.write_bytes(resp.read())

    tmp.replace(dst)
    return dst


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

    We default to PulseAudio-over-SSH socket at /tmp/pulse-native, so
    audio goes to the host when user connected with:
      ssh -R /tmp/pulse-native:/run/user/1000/pulse/native vm_fedora
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
        with _AUDIO_COND:
            while _PENDING_REQ is None:
                _AUDIO_COND.wait()

            token, file_path, delay_sec = _PENDING_REQ
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
                    stderr=subprocess.DEVNULL,
                    env=env,
                    start_new_session=True,
                )
            except Exception:
                break

            with _AUDIO_LOCK:
                if token != _PLAY_TOKEN:
                    _stop_proc(p)
                    break
                _CURRENT_PROC = p

            # Best-effort: set stream volume to 100%
            _set_sink_input_volume_for_pid(p.pid, env, volume="100%")

            # Wait for playback to finish (prevents overlap inside the worker)
            try:
                p.wait(timeout=10.0)
            except Exception:
                _stop_proc(p)

            with _AUDIO_LOCK:
                if token != _PLAY_TOKEN:
                    break

            if i == 0:
                # Sleep in small steps so cancellation reacts fast
                end_at = time.time() + float(delay_sec)
                while time.time() < end_at:
                    with _AUDIO_LOCK:
                        if token != _PLAY_TOKEN:
                            break
                    time.sleep(0.05)

        with _AUDIO_LOCK:
            # Clear proc pointer if still ours
            if token == _PLAY_TOKEN:
                _CURRENT_PROC = None


def play_audio_twice_in_background(file_path: Path, delay_sec: float = 1.0) -> tuple[bool, str]:
    """Queue audio playback on a single worker thread. Cancels previous playback if any."""
    global _PLAY_TOKEN, _PENDING_REQ, _WORKER_STARTED, _CURRENT_PROC

    player = pick_player()
    if not player:
        return False, "no player found"

    env = _build_audio_env()

    with _AUDIO_COND:
        _PLAY_TOKEN += 1
        token = _PLAY_TOKEN

        # Start the single worker once
        if not _WORKER_STARTED:
            _WORKER_STARTED = True
            t = threading.Thread(
                target=_audio_worker_loop,
                args=(player, env),
                daemon=True,
            )
            t.start()

        # Queue/replace pending request
        _PENDING_REQ = (token, file_path, float(delay_sec))
        _AUDIO_COND.notify()

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
            # Uncomment for debug:
            print(f"[mw_audio] prefetch cached audio_id={aid!r}", flush=True)
        except Exception as e:
            # Uncomment for debug:
            print(f"[mw_audio] prefetch failed audio_id={aid!r}: {e}", flush=True)
            pass
        finally:
            with _PREFETCH_LOCK:
                _PREFETCH_INFLIGHT.discard(aid)

    threading.Thread(target=_run, daemon=True).start()
