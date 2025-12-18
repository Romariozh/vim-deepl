# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional


def setup_logging(log_path: Path, level: str = "INFO") -> logging.Logger:
    """
    Sets up app-level logging once.
    Writes logs to file and keeps format stable.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("vim_deepl")
    logger.setLevel(level.upper())

    # Avoid duplicate handlers if setup is called multiple times
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setLevel(level.upper())
        fmt = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    # Don't propagate to root (prevents double output in some environments)
    logger.propagate = False
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"vim_deepl.{name}")
    return logging.getLogger("vim_deepl")
