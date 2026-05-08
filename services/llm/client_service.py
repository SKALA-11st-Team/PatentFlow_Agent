from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from openai import OpenAI

from app.config import settings


F = TypeVar("F", bound=Callable[..., Any])


def trace_llm_call(func: F) -> F:
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return func
    try:
        from langsmith import traceable
    except ImportError:
        return func
    traced = traceable(name="openai_responses", run_type="llm")(func)

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return traced(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


@trace_llm_call
def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    selected_model = model or settings.openai_chat_model
    request = {
        "model": selected_model,
        "input": prompt,
    }
    if supports_temperature(selected_model):
        request["temperature"] = temperature

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(**request)
    output_text = getattr(response, "output_text", None)
    if not output_text:
        raise RuntimeError("LLM response did not contain output_text.")
    record_langsmith_usage(
        model=selected_model,
        prompt=prompt,
        output_text=output_text,
        response=response,
    )
    return output_text.strip()


def supports_temperature(model: str) -> bool:
    return not model.startswith("gpt-5")


def record_langsmith_usage(
    *,
    model: str,
    prompt: str,
    output_text: str,
    response: Any,
) -> None:
    try:
        from langsmith.run_helpers import get_current_run_tree
    except ImportError:
        return

    run = get_current_run_tree()
    if run is None:
        return

    usage = getattr(response, "usage", None)
    usage_metadata = response_usage_metadata(usage)
    run.set(
        inputs={"messages": [{"role": "user", "content": prompt}]},
        outputs={"message": {"role": "assistant", "content": output_text}},
        metadata={"ls_provider": "openai", "ls_model_name": model},
        usage_metadata=usage_metadata if usage_metadata else None,
    )


def response_usage_metadata(usage: Any) -> dict[str, int] | None:
    if not usage:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    result = {
        key: value
        for key, value in {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }.items()
        if isinstance(value, int)
    }
    return result or None
