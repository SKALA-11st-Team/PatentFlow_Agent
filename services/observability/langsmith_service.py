from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from app.config import settings


F = TypeVar("F", bound=Callable[..., Any])


def trace(name: str | None = None, run_type: str = "chain") -> Callable[[F], F]:
    def decorator(func: F) -> F:
        if not settings.langsmith_tracing or not settings.langsmith_api_key:
            return func

        try:
            from langsmith import traceable
        except ImportError:
            return func

        traced = traceable(name=name or func.__name__, run_type=run_type)(func)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return traced(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
