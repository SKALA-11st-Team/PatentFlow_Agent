from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.config import settings


_CLIENT: OpenAI | None = None


def _get_client() -> OpenAI:
    """Return a shared OpenAI client.

    When LangSmith tracing is enabled the client is wrapped with
    ``wrap_openai`` so every LLM call (Responses API included) becomes an LLM
    run with token usage/cost, nested under the current LangGraph node run.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    if settings.langsmith_tracing and settings.langsmith_api_key:
        try:
            from langsmith.wrappers import wrap_openai

            client = wrap_openai(client)
        except ImportError:
            pass
    _CLIENT = client
    return client


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

    response = _get_client().responses.create(**request)
    output_text = getattr(response, "output_text", None)
    if not output_text:
        raise RuntimeError("LLM response did not contain output_text.")
    return output_text.strip()


def supports_temperature(model: str) -> bool:
    return not model.startswith("gpt-5")


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
