from __future__ import annotations

from openai import OpenAI

from app.config import settings


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
    return output_text.strip()


def supports_temperature(model: str) -> bool:
    return not model.startswith("gpt-5")

