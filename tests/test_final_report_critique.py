import json

import pytest

import agents.writing.final_report as final_report_module
from agents.writing.final_report import apply_report_self_critique
from workflow.state import PatentWorkflowState


VALUATION_RESULT = {
    "total_score": 280,
    "average_score": 70.0,
    "recommendation": "유지 권고",
    "axes": {
        "legal": {"axis": "legal", "label": "권리성", "score": 70, "grade": "B", "rationale": "권리성 근거"},
        "technology": {"axis": "technology", "label": "기술성", "score": 75, "grade": "B", "rationale": "기술성 근거"},
        "market": {"axis": "market", "label": "시장성", "score": 65, "grade": "B", "rationale": "시장성 근거"},
        "business_fit": {"axis": "business_fit", "label": "사업 연계성", "score": 70, "grade": "B", "rationale": "사업 연계성 근거"},
    },
}
REPORT_MARKDOWN = "# 특허 가치판단 종합 보고서\n\n## 1. 종합 의견\n\n유지 권고 보고서 본문"


def make_state() -> PatentWorkflowState:
    return PatentWorkflowState(user_input={"no_save": True})


def test_consistent_report_keeps_original_without_revision(monkeypatch):
    calls: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps({"consistent": True, "issues": []})

    monkeypatch.setattr(final_report_module, "call_llm", fake_call_llm)

    markdown, meta = apply_report_self_critique(
        state=make_state(), valuation_result=dict(VALUATION_RESULT), report_markdown=REPORT_MARKDOWN
    )

    assert markdown == REPORT_MARKDOWN
    assert meta == {"checked": True, "consistent": True, "issues": [], "revised": False}
    # critique 1회만 호출되고 교정 호출은 없어야 한다.
    assert len(calls) == 1


def test_inconsistent_report_is_revised_once(monkeypatch):
    revised_markdown = "# 특허 가치판단 종합 보고서\n\n## 1. 종합 의견\n\n점수와 일치하도록 교정된 본문"
    responses = [
        json.dumps({"consistent": False, "issues": ["시장성 서술이 점수(65점)보다 과도하게 긍정적"]}),
        revised_markdown,
    ]
    calls: list[dict] = []

    def fake_call_llm(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return responses[len(calls) - 1]

    monkeypatch.setattr(final_report_module, "call_llm", fake_call_llm)

    markdown, meta = apply_report_self_critique(
        state=make_state(), valuation_result=dict(VALUATION_RESULT), report_markdown=REPORT_MARKDOWN
    )

    assert markdown == revised_markdown
    assert meta["consistent"] is False
    assert meta["revised"] is True
    assert meta["issues"] == ["시장성 서술이 점수(65점)보다 과도하게 긍정적"]
    assert len(calls) == 2
    # 교정 프롬프트에는 critique가 찾은 issues와 원본 보고서가 포함된다.
    assert "시장성 서술이 점수(65점)보다 과도하게 긍정적" in calls[1]["prompt"]
    assert "report_markdown" in calls[1]["prompt"]


def test_critique_exception_keeps_original_report(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(final_report_module, "call_llm", fake_call_llm)

    markdown, meta = apply_report_self_critique(
        state=make_state(), valuation_result=dict(VALUATION_RESULT), report_markdown=REPORT_MARKDOWN
    )

    assert markdown == REPORT_MARKDOWN
    assert meta["checked"] is False
    assert meta["revised"] is False
    assert meta["warning"].startswith("report_critique_failed:RuntimeError")


def test_critique_non_json_response_keeps_original_report(monkeypatch):
    monkeypatch.setattr(final_report_module, "call_llm", lambda prompt, **kwargs: "JSON이 아닌 응답")

    markdown, meta = apply_report_self_critique(
        state=make_state(), valuation_result=dict(VALUATION_RESULT), report_markdown=REPORT_MARKDOWN
    )

    assert markdown == REPORT_MARKDOWN
    assert meta["checked"] is False
    assert "report_critique_failed:ValueError" in meta["warning"]


def test_revision_exception_keeps_original_report(monkeypatch):
    calls: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({"consistent": False, "issues": ["점수-결론 불일치"]})
        raise RuntimeError("revision failed")

    monkeypatch.setattr(final_report_module, "call_llm", fake_call_llm)

    markdown, meta = apply_report_self_critique(
        state=make_state(), valuation_result=dict(VALUATION_RESULT), report_markdown=REPORT_MARKDOWN
    )

    assert markdown == REPORT_MARKDOWN
    assert meta["consistent"] is False
    assert meta["revised"] is False
    assert meta["warning"].startswith("report_revision_failed:RuntimeError")


def test_empty_revision_keeps_original_report(monkeypatch):
    responses = [json.dumps({"consistent": False, "issues": ["점수-결론 불일치"]}), "   "]
    calls: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        calls.append(prompt)
        return responses[len(calls) - 1]

    monkeypatch.setattr(final_report_module, "call_llm", fake_call_llm)

    markdown, meta = apply_report_self_critique(
        state=make_state(), valuation_result=dict(VALUATION_RESULT), report_markdown=REPORT_MARKDOWN
    )

    assert markdown == REPORT_MARKDOWN
    assert meta["revised"] is False
    assert meta["warning"] == "report_revision_empty"


def test_self_critique_disabled_skips_llm(monkeypatch):
    monkeypatch.setattr(final_report_module.settings, "report_self_critique_enabled", False)

    def fail_call_llm(prompt, **kwargs):
        raise AssertionError("self-critique가 꺼져 있으면 LLM을 호출하면 안 된다")

    monkeypatch.setattr(final_report_module, "call_llm", fail_call_llm)

    markdown, meta = apply_report_self_critique(
        state=make_state(), valuation_result=dict(VALUATION_RESULT), report_markdown=REPORT_MARKDOWN
    )

    assert markdown == REPORT_MARKDOWN
    assert meta is None
