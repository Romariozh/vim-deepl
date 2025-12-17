from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AppError(Exception):
    code: str
    message: str
    details: Optional[Any] = None

    def to_dict(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.details is not None:
            err["details"] = self.details
        return err


class ConfigError(AppError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(code="CONFIG_ERROR", message=message, details=details)


class BackendError(AppError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(code="BACKEND_ERROR", message=message, details=details)
