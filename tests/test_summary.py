import pytest

from agents.summary import run_summary_agent
from app.main import save_outputs
from workflow.state import PatentWorkflowState


def test_run_summary_agent_fails_when_llm_summary_is_disabled():
    state = PatentWorkflowState(
        user_input={"use_llm_summary": False},
        preprocessed_patent={
            "metadata": {
                "title": "문서 자동 생성 특허",
                "application_number": "10-2024-0000001",
                "registration_number": "10-3000001",
                "assignee": ["에스케이 주식회사"],
                "ipc": ["G06F"],
                "cpc": ["G06F 40/00"],
                "filing_date": "2024-01-01",
                "registration_date": "2026-01-01",
            },
            "sections": {"abstract": "문서 이미지를 기반으로 웹 문서를 생성한다."},
            "claim_stats": {"active_claim_count": 3},
            "claims": [],
        },
    )

    with pytest.raises(RuntimeError, match="use_llm_summary is disabled"):
        run_summary_agent(state)


def test_run_summary_agent_uses_llm_markdown(monkeypatch):
    captured_prompts = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompts.append(prompt)
        return "## 1. 한 줄 요약\n\n- 본문"

    monkeypatch.setattr("agents.summary.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        preprocessed_patent={
            "metadata": {"title": "문서 자동 생성 특허"},
            "sections": {"abstract": "문서 이미지를 기반으로 웹 문서를 생성한다."},
        },
    )

    result = run_summary_agent(state)

    assert result.summary_result["summary_markdown"].startswith("# 특허 요약")
    assert "### 문서 자동 생성 특허" in result.summary_result["summary_markdown"]
    assert "## 1. 한 줄 요약" in result.summary_result["summary_markdown"]
    assert "`# 특허 요약`, 특허명, `기본 정보` 섹션은 작성하지 마세요." in captured_prompts[0]


def test_save_outputs_writes_summary_markdown(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path)},
        preprocessed_patent={"patent_id": "P1"},
        summary_result={
            "title": "문서 자동 생성 특허",
            "plain_summary": "요약",
            "key_points": ["핵심"],
            "summary_markdown": "# 특허 요약\n\n본문",
        },
    )

    saved = save_outputs(state)

    assert saved["summary_json"].name == "P1_summary.json"
    assert saved["summary_markdown"].name == "P1_summary.md"
    assert saved["summary_markdown"].read_text(encoding="utf-8").startswith("# 특허 요약")
