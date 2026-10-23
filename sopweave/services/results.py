"""Transport-independent application outcomes."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Result:
    data: Any = None
    status_code: int = 200


class ServiceError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def success(data=None, status_code: int = 200) -> Result:
    return Result(data, status_code)


def error(message: str, status_code: int = 400):
    raise ServiceError(message, status_code)
