import json
from pathlib import Path

import pytest

from agents.summary import (
    build_summary_brief_markdown,
    build_summary_input_payload,
    run_summary_agent,
    validate_summary_brief,
)
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
        if "Summary Brief Prompt" in prompt:
            return json.dumps(
                {
                    "one_line_summary": "문서를 자동 생성하는 기술입니다.",
                    "problem": "수작업 문서 작성의 비효율을 해결합니다.",
                    "core_idea": "입력 데이터를 분석해 문서로 자동 변환합니다.",
                    "key_components": ["입력 분석", "문서 변환", "결과 출력"],
                    "operation_steps": ["입력 수신", "데이터 분석", "문서 생성"],
                    "expected_effect": "문서 작성 시간과 오류를 줄입니다.",
                },
                ensure_ascii=False,
            )
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
    assert result.summary_result["summary_brief"]["key_components"] == [
        "입력 분석",
        "문서 변환",
        "결과 출력",
    ]
    assert "### 문서 자동 생성 특허" in result.summary_result["summary_markdown"]
    assert "## 1. 한 줄 요약" in result.summary_result["summary_markdown"]
    assert "`# 특허 요약`, 특허명, `기본 정보` 섹션은 작성하지 마세요." in captured_prompts[0]
    assert "프론트 화면의 작은 카드 6개" in captured_prompts[1]


def test_summary_input_uses_compact_patent_structures():
    state = PatentWorkflowState(
        preprocessed_patent={"metadata": {"title": "구조화 특허"}, "claims": []},
        target_structure={
            "doc_id": "TARGET",
            "key_elements": [
                {
                    "key_element_id": "K1",
                    "key_element_name": "입력 분석부",
                    "why_essential": "입력 데이터를 분석합니다.",
                    "core_role": "essential",
                    "in_independent_claim": True,
                    "spec_support": [{"mapped_spec_content": "긴 원문"}],
                    "drawing_support": [{"mapped_drawing_content": "도면 원문"}],
                    "claim_clarity": "self_clear",
                }
            ],
            "key_flow": [
                {
                    "key_element_id": "K1",
                    "next_key_element_id": "K2",
                    "relation_summary": "분석 결과를 변환부로 전달합니다.",
                    "coupling_strength": "strong",
                }
            ],
            "claims": [{"claim_no": "1", "claim_elements": [{"claim_element_text": "전문"}]}],
        },
        comparison_structures=[
            {
                "doc_id": "PRIOR-1",
                "comparison_source": "prior_art",
                "key_elements": [
                    {
                        "key_element_id": "K1",
                        "key_element_name": "비교 분석부",
                        "why_essential": "비교문헌의 분석 기능입니다.",
                        "core_role": "essential",
                        "in_independent_claim": True,
                    }
                ],
                "key_flow": [],
                "claims": [{"claim_no": "1", "claim_elements": [{"claim_element_text": "비교 전문"}]}],
            }
        ],
    )

    payload = build_summary_input_payload(
        state=state,
        summary_result={"title": "구조화 특허", "plain_summary": "요약"},
    )
    structures = payload["patent_structures"]
    serialized = json.dumps(structures, ensure_ascii=False)

    assert structures["target"]["key_elements"][0]["key_element_name"] == "입력 분석부"
    assert structures["target"]["key_flow"][0]["relation_summary"] == "분석 결과를 변환부로 전달합니다."
    assert "comparisons" not in structures
    assert "spec_support" not in serialized
    assert "drawing_support" not in serialized
    assert "claim_elements" not in serialized


def test_summary_prompt_requires_numbered_plain_language_flow():
    prompt = Path("prompts/summary/summary.md").read_text(encoding="utf-8")

    assert "## 3. 핵심 구성과 작동 방식" not in prompt
    assert "### 작동 방식" in prompt
    assert "실제 단계 수를 반영해 `### 작동 방식 4단계`" in prompt
    assert "`1. 단계 이름: 설명` 형식의 번호 목록" in prompt
    assert "화살표로 흐름을 한 줄에 압축하지 마세요." in prompt
    assert "## 4. 비교문헌 대비 기술적 차이" not in prompt
    assert "괄호는 꼭 필요한 영문 명칭이나 짧은 예시" in prompt
    core_section = prompt.split("## 2. 핵심 내용", 1)[1]
    assert core_section.index("### 주요 기능/구성") < core_section.index("### 작동 방식")
    assert core_section.index("### 작동 방식") < core_section.index("### 기대 효과")


def test_save_outputs_writes_summary_markdown(tmp_path):
    brief = {
        "one_line_summary": "문서를 자동 생성하는 기술입니다.",
        "problem": "수작업 작성의 비효율을 해결합니다.",
        "core_idea": "입력 데이터를 분석해 문서로 변환합니다.",
        "key_components": ["입력 분석", "문서 변환", "결과 출력"],
        "operation_steps": ["입력 수신", "데이터 분석", "문서 생성"],
        "expected_effect": "문서 작성 시간과 오류를 줄입니다.",
    }
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path)},
        preprocessed_patent={"patent_id": "P1"},
        summary_result={
            "title": "문서 자동 생성 특허",
            "plain_summary": "요약",
            "key_points": ["핵심"],
            "summary_markdown": "# 특허 요약\n\n본문",
            "summary_brief": brief,
        },
    )

    saved = save_outputs(state)

    assert saved["summary_json"].name == "P1_summary.json"
    assert saved["summary_markdown"].name == "P1_summary.md"
    assert saved["summary_markdown"].read_text(encoding="utf-8").startswith("# 특허 요약")
    assert "summary_brief" not in json.loads(saved["summary_json"].read_text(encoding="utf-8"))
    assert saved["summary_brief_json"].name == "P1_summary_brief.json"
    assert json.loads(saved["summary_brief_json"].read_text(encoding="utf-8")) == brief
    assert saved["summary_brief_markdown"].name == "P1_summary_brief.md"
    brief_markdown = saved["summary_brief_markdown"].read_text(encoding="utf-8")
    assert "## 작동 방식 3단계" in brief_markdown
    assert "1. 입력 수신" in brief_markdown


def test_validate_summary_brief_enforces_card_shape():
    brief = validate_summary_brief(
        {
            "one_line_summary": "한글 숫자를 표준 숫자로 추출하는 기술입니다.",
            "problem": "다양한 한글 숫자 표현의 인식 오류를 해결합니다.",
            "core_idea": "문자 분해와 규칙 기반 변환을 결합합니다.",
            "key_components": [
                "문자 분해",
                "숫자 단위 인식",
                "규칙 기반 변환",
                "품사 구분",
                "길이 기반 보정",
                "연산자 인식",
                "초과 구성",
            ],
            "operation_steps": [
                "입력 수신",
                "문자 분해",
                "규칙 변환",
                "길이 보정",
                "결과 출력",
                "초과 단계",
            ],
            "expected_effect": "숫자 추출 정확도와 업무 자동화 효율을 높입니다.",
        }
    )

    assert len(brief["key_components"]) == 6
    assert len(brief["operation_steps"]) == 5
    assert build_summary_brief_markdown(brief).startswith("# 특허 이해 요약")
