from types import SimpleNamespace

from services.llm.client_service import response_usage_metadata


def test_response_usage_metadata_extracts_openai_response_tokens():
    usage = SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150)

    assert response_usage_metadata(usage) == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
    }
