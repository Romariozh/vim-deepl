# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2025 Romariozh

from __future__ import annotations

import json
import sys
from typing import Callable, Dict, Any

from vim_deepl.utils.config import load_config
from vim_deepl.utils.logging import setup_logging, get_logger


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _fail(message: str, code: str = "ERROR", details: Any = None) -> Dict[str, Any]:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err}


def run(dispatch: Callable[[list[str]], Dict[str, Any]]) -> None:
    """
    Generic Vim stdio transport:
      - reads argv (sys.argv)
      - logs start/exceptions
      - prints strict JSON to stdout
      - exits with code 0/1
    """
    cfg = load_config()
    setup_logging(cfg.log_path, cfg.log_level)
    log = get_logger("transport")

    argv = sys.argv
    try:
        log.info("transport started argv=%s", argv)

        resp = dispatch(argv)

        # Backward compatibility: if dispatch returned plain dict without ok,
        # wrap it as success.
        if not isinstance(resp, dict) or "ok" not in resp:
            resp = _ok(resp)

        print(json.dumps(resp, ensure_ascii=False))
        sys.exit(0 if resp.get("ok") else 1)

    except SystemExit:
        # Respect exit codes from dispatch if any.
        raise

    except Exception as e:
        log.exception("Unhandled error in transport")
        print(json.dumps(_fail(str(e), code="EXCEPTION"), ensure_ascii=False))
        sys.exit(1)
