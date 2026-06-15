from types import SimpleNamespace

from services.llm import client_service
from services.llm.client_service import call_llm, response_usage_metadata


def test_response_usage_metadata_extracts_openai_response_tokens():
    usage = SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150)

    assert response_usage_metadata(usage) == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
    }


def test_get_client_configures_openai_timeout(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(client_service, "_CLIENT", None)
    monkeypatch.setattr(client_service.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(client_service.settings, "openai_request_timeout_seconds", 60, raising=False)
    monkeypatch.setattr(client_service.settings, "langsmith_tracing", False)
    monkeypatch.setattr(client_service, "OpenAI", FakeOpenAI)

    client_service._get_client()

    assert captured["api_key"] == "test-key"
    assert captured["timeout"] == 60


def test_call_llm_seed_branch_forwards_per_call_timeout(monkeypatch):
    captured = {}

    class FakeChatCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions()))

    monkeypatch.setattr(client_service.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(client_service, "_get_client", lambda: fake_client)

    result = call_llm(
        "prompt",
        model="gpt-4o",
        seed=123,
        timeout=300,
    )

    assert result == "ok"
    assert captured["seed"] == 123
    assert captured["timeout"] == 300
