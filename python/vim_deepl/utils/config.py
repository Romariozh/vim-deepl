from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import ConfigError


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name, default)
    if val is None:
        return None
    val = val.strip()
    return val if val else default


def _env_int(name: str, default: int) -> int:
    val = _env(name, None)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError as e:
        raise ConfigError(f"Env {name} must be int", details={"value": val}) from e


def _env_bool(name: str, default: bool) -> bool:
    val = _env(name, None)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Config:
    # storage
    data_dir: Path
    db_path: Path
    log_path: Path

    # logging
    log_level: str

    # API keys
    deepl_auth_key: Optional[str]
    mw_api_key: Optional[str]

    # timeouts
    http_timeout_sec: int

    # (optional) backend server settings if you use HTTP mode
    http_host: str
    http_port: int

    #
    trainer_recent_days: int = 7
    trainer_mastery_count: int = 7


def load_config() -> Config:
    """
    Config is loaded ONLY here.
    Everywhere else in code should accept Config object (dependency injection).
    """
    # Base data dir (Linux friendly)
    default_data_dir = Path(_env("VIM_DEEPL_DATA_DIR", str(Path.home() / ".local" / "share" / "vim-deepl")))
    data_dir = default_data_dir.expanduser().resolve()

    # DB path
    default_db_path = data_dir / "vocab.db"
    db_path = Path(_env("VIM_DEEPL_DB_PATH", str(default_db_path))).expanduser().resolve()

    # Log path
    default_log_path = Path(_env("VIM_DEEPL_LOG_PATH", str(data_dir / "vim-deepl.log"))).expanduser().resolve()

    # Log level
    log_level = _env("VIM_DEEPL_LOG_LEVEL", "INFO").upper()

    # Keys
    deepl_auth_key = _env("DEEPL_AUTH_KEY", None)
    mw_api_key = _env("MW_API_KEY", None)

    # Timeouts
    http_timeout_sec = _env_int("VIM_DEEPL_HTTP_TIMEOUT_SEC", 25)

    # HTTP server settings (if used)
    http_host = _env("VIM_DEEPL_HTTP_HOST", "127.0.0.1") or "127.0.0.1"
    http_port = _env_int("VIM_DEEPL_HTTP_PORT", 8787)

    # Ensure directories exist
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    default_log_path.parent.mkdir(parents=True, exist_ok=True)

    return Config(
        data_dir=data_dir,
        db_path=db_path,
        log_path=default_log_path,
        log_level=log_level,
        deepl_auth_key=deepl_auth_key,
        mw_api_key=mw_api_key,
        http_timeout_sec=http_timeout_sec,
        http_host=http_host,
        http_port=http_port,
    )
