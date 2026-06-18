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


# @author 배세은
# @date 2026-05-06
# @relatedFR FR-005, FR-006, FR-008
# @relatedUI UI-005
# @description 공용 OpenAI 호출 래퍼. 요약·4축 평가·최종 보고서·supervisor 판정 등 모든 LLM 호출이 거치는
# 단일 진입점이다. seed 지정 시 재현성을 위해 Chat Completions 경로를 쓰고, 그 외엔 Responses API를 사용하며
# gpt-5 계열 reasoning effort/verbosity와 타임아웃을 작업별로 적용한다.
def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
    seed: int | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    timeout: float | None = None,
) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    selected_model = model or settings.openai_chat_model
    # seed는 Responses API가 받지 않는 Chat Completions 전용 파라미터다. seed가 지정되면
    # Chat Completions 경로를 사용한다(재현성은 모델이 seed를 실제로 존중할 때만 보장됨).
    if seed is not None:
        chat_request = {
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "seed": seed,
        }
        if supports_temperature(selected_model):
            chat_request["temperature"] = temperature
        # 호출별 timeout 오버라이드(전역 클라이언트 timeout보다 우선). Chat Completions도
        # per-request timeout을 받으므로, seed 경로에서 호출자가 넘긴 timeout을 잃지 않는다.
        if timeout is not None:
            chat_request["timeout"] = timeout
        chat_response = _get_client().chat.completions.create(**chat_request)
        content = chat_response.choices[0].message.content if chat_response.choices else None
        if not content:
            raise RuntimeError("LLM response did not contain content.")
        return content.strip()

    request = {
        "model": selected_model,
        "input": prompt,
    }
    if supports_temperature(selected_model):
        request["temperature"] = temperature
    # reasoning_effort/verbosity는 gpt-5 계열에만 적용된다. 호출에서 주어지지 않으면
    # 전역 기본값(settings)을 쓰고, 그래도 없으면 보내지 않아 API 기본값을 따른다.
    if supports_reasoning_controls(selected_model):
        effort = reasoning_effort or settings.openai_reasoning_effort
        if effort:
            request["reasoning"] = {"effort": effort}
        level = verbosity or settings.openai_verbosity
        if level:
            request["text"] = {"verbosity": level}

    # 호출별 timeout 오버라이드(전역 클라이언트 timeout보다 우선). 긴 출력의 작성
    # 단계처럼 일반 호출보다 시간이 더 필요한 경우에 사용한다.
    if timeout is not None:
        request["timeout"] = timeout

    response = _get_client().responses.create(**request)
    output_text = getattr(response, "output_text", None)
    if not output_text:
        raise RuntimeError("LLM response did not contain output_text.")
    return output_text.strip()


def supports_reasoning_controls(model: str) -> bool:
    """gpt-5 계열만 reasoning effort/verbosity를 지원한다."""
    return model.startswith("gpt-5")


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
