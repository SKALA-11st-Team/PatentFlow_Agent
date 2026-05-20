import json

import pytest

from agents.valuation import run_valuation_agent, select_axis_evidence
from agents.valuation_axes.business_fit import append_skax_official_evidence_to_state, build_patent_context_from_state
from agents.writing.final_report import run_final_report_agent
from app.main import build_parser, build_user_input, save_outputs
from workflow.supervisor import check_valuation_result
from workflow.state import PatentWorkflowState


@pytest.fixture(autouse=True)
def disable_skax_site_search(monkeypatch):
    monkeypatch.setattr(
        "agents.valuation_axes.business_fit.collect_skax_site_evidence",
        lambda patent_context: {"items": [], "stats": {}},
    )


def test_valuation_axes_are_split_into_axis_modules():
    from agents.valuation_axes import AXIS_MODULES

    assert list(AXIS_MODULES) == ["legal", "technology", "market", "business_fit"]
    assert AXIS_MODULES["legal"].LABEL == "권리성"
    assert AXIS_MODULES["technology"].LABEL == "기술성"
    assert AXIS_MODULES["market"].LABEL == "시장성"
    assert AXIS_MODULES["business_fit"].LABEL == "사업 연계성"
    assert AXIS_MODULES["legal"].PROMPT_PATH == "valuation/valuation_legal.md"
    assert callable(AXIS_MODULES["legal"].run)


def test_run_valuation_agent_sets_result():
    def fake_call_llm(prompt):
        if "Return ONLY Markdown" in prompt:
            return "# LLM 최종 보고서"
        return '{"score":70,"grade":"B","rationale":"LLM 평가","evidence_ids":[],"risk_factors":["추가 확인"],"missing_information":[],"confidence":0.7}'

    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={
            "id": 1,
            "title_final": "문서변환 특허",
            "related_product": "문서변환 SW",
            "business_area": "기존사업",
            "technology_area": "AI 문서처리",
            "status": "등록",
            "expected_expiration_date": "2032-01-01",
        },
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source": "naver_news",
                "source_type": "news",
                "related_axes": ["market"],
                "compressed_summary": "문서변환 SW 시장 수요가 확대되고 있다.",
            },
            {
                "evidence_id": "portfolio_001",
                "source": "kipris_api",
                "source_type": "portfolio_context",
                "compressed_summary": "문서변환 포트폴리오 맥락",
            },
        ],
    )

    from agents import valuation

    original_call_llm = valuation.call_llm
    valuation.call_llm = fake_call_llm
    try:
        result = run_valuation_agent(state)
    finally:
        valuation.call_llm = original_call_llm

    assert result.valuation_result is not None
    axes = result.valuation_result["axes"]
    assert set(axes) == {"legal", "technology", "market", "business_fit"}
    assert "strategy" not in axes
    assert axes["business_fit"]["label"] == "사업 연계성"
    assert result.valuation_result["total_score"] == sum(axis["score"] for axis in axes.values())
    assert result.valuation_result["average_score"] == 70
    assert "평균 점수는 70/100점" in result.valuation_result["decision_rationale"][0]
    assert "final_report_markdown" not in result.valuation_result


def test_business_fit_selects_news_with_company_or_product_context():
    state = PatentWorkflowState(
        user_input={"use_llm_valuation": False, "use_llm_final_report": False},
        patent_structured={
            "related_product": "문서변환 SW",
            "title_final": "문서변환 특허",
        },
        kipris_api_data={"metadata": {"assignee": ["에스케이"]}},
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source": "naver_news",
                "source_type": "news",
                "title": "에스케이 문서변환 SW 사업 확대",
                "compressed_summary": "문서변환 SW가 업무 자동화에 적용되고 있다.",
            },
            {
                "evidence_id": "news_002",
                "source": "naver_news",
                "source_type": "news",
                "title": "무관한 산업 뉴스",
                "compressed_summary": "다른 시장 이야기",
            },
        ],
    )

    selected = select_axis_evidence("business_fit", state)

    assert selected[0]["evidence_id"] == "news_001"


def test_business_fit_prioritizes_sk_ax_official_evidence():
    state = PatentWorkflowState(
        patent_structured={
            "related_product": "로보어드바이저",
            "title_final": "강화학습 자산배분 특허",
            "technology_area": "데이터분석",
            "business_area": "Data",
        },
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source": "naver_news",
                "source_type": "news",
                "title": "로보어드바이저 자산배분 시장 확대",
                "compressed_summary": "데이터분석 서비스 수요 확대",
            },
            {
                "evidence_id": "skax_low",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "SK AX 데이터분석",
                "content": "데이터분석 사업 소개",
                "relevance_score": 0.4,
            },
            {
                "evidence_id": "skax_high",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "SK AX 로보어드바이저",
                "content": "로보어드바이저 자산배분 데이터분석 솔루션",
                "relevance_score": 0.9,
            },
        ],
    )

    selected = select_axis_evidence("business_fit", state)

    assert [item["evidence_id"] for item in selected[:3]] == ["skax_high", "skax_low", "news_001"]


def test_business_fit_detects_sk_ax_official_evidence_from_metadata():
    state = PatentWorkflowState(
        patent_structured={"related_product": "로보어드바이저"},
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source": "naver_news",
                "source_type": "news",
                "title": "로보어드바이저 시장 확대",
            },
            {
                "evidence_id": "skax_meta",
                "source": "company_page",
                "source_type": "company_disclosure",
                "title": "SK AX 로보어드바이저",
                "metadata": {"source": "sk_ax_official", "relevance_score": 0.8},
            },
        ],
    )

    selected = select_axis_evidence("business_fit", state)

    assert selected[0]["evidence_id"] == "skax_meta"


def test_business_fit_fallback_without_official_evidence_matches_existing_order():
    state = PatentWorkflowState(
        patent_structured={
            "related_product": "문서변환 SW",
            "title_final": "문서변환 특허",
        },
        kipris_api_data={"metadata": {"assignee": ["에스케이"]}},
        evidence_bundle=[
            {
                "evidence_id": "news_direct",
                "source": "naver_news",
                "source_type": "news",
                "title": "에스케이 문서변환 SW 사업 확대",
            },
            {
                "evidence_id": "portfolio_001",
                "source": "kipris_api",
                "source_type": "portfolio_context",
                "compressed_summary": "문서변환 포트폴리오 맥락",
            },
            {
                "evidence_id": "news_secondary",
                "source": "naver_news",
                "source_type": "news",
                "title": "무관한 산업 뉴스",
            },
        ],
    )

    selected = select_axis_evidence("business_fit", state)

    assert [item["evidence_id"] for item in selected] == ["news_direct", "portfolio_001", "news_secondary"]


def test_business_fit_builds_patent_context_from_state():
    state = PatentWorkflowState(
        user_input={"management_number": "USER-MGMT"},
        patent_structured={
            "management_number": "P202405001-KR0",
            "title_final": "상품 트렌드 예측 자산배분 특허",
            "business_area": "Data",
            "technology_area": "데이터분석",
            "related_product": "로보어드바이저",
        },
        kipris_api_data={"metadata": {"title": "KIPRIS 제목"}},
        preprocessed_patent={"metadata": {"title": "전처리 제목"}},
        summary_result={"title": "요약 제목"},
    )

    context = build_patent_context_from_state(state)

    assert context == {
        "management_number": "P202405001-KR0",
        "title_final": "상품 트렌드 예측 자산배분 특허",
        "title_draft": "",
        "business_area": "Data",
        "technology_area": "데이터분석",
        "related_product": "로보어드바이저",
    }


def test_business_fit_builds_patent_context_from_korean_patent_fields():
    state = PatentWorkflowState(
        patent_structured={
            "관리번호": "P202405001-KR0",
            "발명의 명칭(가제)": "상품 트렌드 예측을 반영한 강화학습 모델",
            "발명의 명칭(최종)": "강화학습 모델을 적용한 자산배분 시스템 및 방법",
            "관련사업 분야": "Data",
            "관련기술 분야": "데이터분석",
            "관련제품": "로보어드바이저",
        },
        evidence_bundle=[],
    )

    context = build_patent_context_from_state(state)

    assert context == {
        "management_number": "P202405001-KR0",
        "title_final": "강화학습 모델을 적용한 자산배분 시스템 및 방법",
        "title_draft": "상품 트렌드 예측을 반영한 강화학습 모델",
        "business_area": "Data",
        "technology_area": "데이터분석",
        "related_product": "로보어드바이저",
    }


def test_business_fit_builds_patent_context_from_spaced_korean_patent_fields():
    state = PatentWorkflowState(
        patent_structured={
            "관리번호": "P202405001-KR0",
            "발명의 명칭(최종)": "상품 트렌드 예측을 반영한 강화학습 모델을 적용한 자산배분 시스템 및 방법",
            "관련 사업 분야": "Data",
            "관련 기술 분야": "데이터분석",
            "관련제품": "로보어드바이저",
        },
        evidence_bundle=[],
    )

    context = build_patent_context_from_state(state)

    assert context["business_area"] == "Data"
    assert context["technology_area"] == "데이터분석"
    assert context["related_product"] == "로보어드바이저"


def test_business_fit_appends_skax_official_evidence_to_state(monkeypatch):
    def fake_collect(patent_context):
        assert patent_context["related_product"] == "로보어드바이저"
        return {
            "items": [
                {
                    "evidence_id": "skax_001",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "title": "SK AX 로보어드바이저",
                    "url": "https://www.skax.co.kr/financial/robo-advisor",
                    "content": "로보어드바이저 사업 근거",
                    "related_axes": ["business_fit"],
                    "relevance_score": 0.9,
                }
            ],
            "stats": {"collected_evidence_count": 1},
        }

    monkeypatch.setattr("agents.valuation_axes.business_fit.collect_skax_site_evidence", fake_collect)
    state = PatentWorkflowState(
        patent_structured={
            "title_final": "강화학습 자산배분 특허",
            "related_product": "로보어드바이저",
        },
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source": "naver_news",
                "source_type": "news",
                "title": "로보어드바이저 시장 확대",
            }
        ],
    )

    result = append_skax_official_evidence_to_state(state)
    selected = select_axis_evidence("business_fit", result)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["news_001", "skax_001"]
    assert selected[0]["evidence_id"] == "skax_001"


def test_business_fit_keeps_evidence_bundle_when_skax_result_is_empty(monkeypatch):
    monkeypatch.setattr(
        "agents.valuation_axes.business_fit.collect_skax_site_evidence",
        lambda patent_context: {"items": [], "stats": {"collected_evidence_count": 0}},
    )
    state = PatentWorkflowState(
        patent_structured={"title_final": "문서변환 특허", "related_product": "문서변환 SW"},
        evidence_bundle=[{"evidence_id": "news_001", "source_type": "news"}],
    )

    result = append_skax_official_evidence_to_state(state)

    assert result.evidence_bundle == [{"evidence_id": "news_001", "source_type": "news"}]


def test_business_fit_keeps_evidence_bundle_when_skax_service_fails(monkeypatch):
    def failing_collect(patent_context):
        raise RuntimeError("search failed")

    monkeypatch.setattr("agents.valuation_axes.business_fit.collect_skax_site_evidence", failing_collect)
    state = PatentWorkflowState(
        patent_structured={"title_final": "문서변환 특허", "related_product": "문서변환 SW"},
        evidence_bundle=[{"evidence_id": "news_001", "source_type": "news"}],
    )

    result = append_skax_official_evidence_to_state(state)

    assert result.evidence_bundle == [{"evidence_id": "news_001", "source_type": "news"}]


def test_valuation_fails_when_llm_valuation_is_disabled():
    state = PatentWorkflowState(
        user_input={"use_llm_valuation": False, "use_llm_final_report": False},
        patent_structured={"related_product": "문서변환 SW"},
        evidence_bundle=[],
    )

    with pytest.raises(RuntimeError, match="use_llm_valuation is disabled"):
        run_valuation_agent(state)


def test_supervisor_requires_business_fit_axis():
    state = PatentWorkflowState(
        evidence_bundle=[],
        valuation_result={
            "axes": {
                "legal": {"score": 70, "grade": "B", "rationale": "r", "evidence_ids": [], "risk_factors": ["r"], "confidence": 0.6},
                "technology": {"score": 70, "grade": "B", "rationale": "r", "evidence_ids": [], "risk_factors": ["r"], "confidence": 0.6},
                "market": {"score": 70, "grade": "B", "rationale": "r", "evidence_ids": [], "risk_factors": ["r"], "confidence": 0.6},
                "strategy": {"score": 70, "grade": "B", "rationale": "r", "evidence_ids": [], "risk_factors": ["r"], "confidence": 0.6},
            }
        },
    )

    decision = check_valuation_result(state)

    assert decision.passed is False
    assert "Missing valuation axis: business_fit" in decision.issues
    assert "Deprecated valuation axis present: strategy" in decision.issues


def test_llm_final_report_markdown_is_used_when_enabled(monkeypatch):
    captured_prompts = []

    def fake_call_llm(prompt):
        captured_prompts.append(prompt)
        if "Return ONLY Markdown" not in prompt:
            return '{"score":70,"grade":"B","rationale":"r","evidence_ids":[],"risk_factors":["r"],"missing_information":[],"confidence":0.7}'
        return "## 1. 의사결정 요약\n\n- AI 권고: 추가 정보 필요"

    monkeypatch.setattr("agents.valuation.call_llm", fake_call_llm)
    monkeypatch.setattr("agents.writing.final_report.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_valuation": True, "use_llm_final_report": True},
        patent_structured={"title_final": "문서변환 특허", "related_product": "문서변환 SW"},
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source_type": "news",
                "source": "naver_news",
                "title": "문서변환 SW 시장 확대",
                "url": "https://example.com/news",
            }
        ],
    )

    result = run_valuation_agent(state)
    result = run_final_report_agent(result)

    markdown = result.valuation_result["final_report_markdown"]
    assert markdown.startswith("# 특허 가치판단 종합 보고서")
    assert "### 문서변환 특허" in markdown
    assert "## 1. 의사결정 요약" in markdown
    assert "## 사용된 외부 근거" not in markdown
    final_prompt = captured_prompts[-1]
    assert "Return ONLY Markdown" in final_prompt
    assert "본문에는 작성하지 마세요" in final_prompt
    assert "citation_title" in final_prompt
    assert "valuation_result" in final_prompt


def test_axis_valuation_prompt_includes_common_rules(monkeypatch):
    captured_prompts = []

    def fake_call_llm(prompt):
        captured_prompts.append(prompt)
        if "Return ONLY Markdown" in prompt:
            return "# LLM 최종 보고서"
        return '{"score":70,"grade":"B","rationale":"r","evidence_ids":[],"risk_factors":["r"],"missing_information":[],"confidence":0.7}'

    monkeypatch.setattr("agents.valuation.call_llm", fake_call_llm)
    monkeypatch.setattr("agents.writing.final_report.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_valuation": True, "use_llm_final_report": True, "no_save": True},
        patent_structured={"related_product": "문서변환 SW"},
    )

    state = run_valuation_agent(state)
    run_final_report_agent(state)

    axis_prompts = [prompt for prompt in captured_prompts if "Return ONLY one JSON object" in prompt]
    assert len(axis_prompts) == 4
    assert all(prompt.index("# Common Valuation Axis Rules") < prompt.index("# Valuation") for prompt in axis_prompts)


def test_valuation_llm_inputs_are_saved(monkeypatch, tmp_path):
    def fake_call_llm(prompt):
        if "Return ONLY Markdown" in prompt:
            return "# LLM 최종 보고서"
        return """
        {
          "score": 70,
          "grade": "B",
          "rationale": "LLM 평가",
          "evidence_ids": ["news_001"],
          "risk_factors": ["추가 확인 필요"],
          "missing_information": [],
          "confidence": 0.7
        }
        """

    monkeypatch.setattr("agents.valuation.call_llm", fake_call_llm)
    monkeypatch.setattr("agents.writing.final_report.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path)},
        patent_structured={
            "management_number": "P1",
            "application_number": "10-2024-0000001",
            "registration_number": "10-3000001",
            "title_final": "문서변환 특허",
            "related_product": "문서변환 SW",
        },
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source": "naver_news",
                "source_type": "news",
                "title": "문서변환 SW 시장 확대",
                "url": "https://example.com/news",
                "published_at": "2026-01-01",
                "related_axes": ["market"],
                "compressed_summary": "문서변환 SW 시장 수요 확대",
            }
        ],
    )

    state = run_valuation_agent(state)
    run_final_report_agent(state)

    input_dir = tmp_path / "valuation_inputs"
    assert (input_dir / "legal_input.json").exists()
    assert (input_dir / "technology_input.json").exists()
    assert (input_dir / "market_input.json").exists()
    assert not (input_dir / "economic_input.json").exists()
    assert (input_dir / "business_fit_input.json").exists()
    assert (input_dir / "final_report_input.json").exists()
    market_input = json.loads((input_dir / "market_input.json").read_text(encoding="utf-8"))
    assert market_input["evidence"][0]["url"] == "https://example.com/news"
    business_fit_input = json.loads((input_dir / "business_fit_input.json").read_text(encoding="utf-8"))
    rubric = business_fit_input["business_fit_scoring_rubric"]
    assert rubric["components"]["business_connection"] == 40
    assert rubric["components"]["portfolio_necessity"] == 35
    assert rubric["components"]["practical_applicability"] == 15
    assert rubric["components"]["evidence_reliability"] == 10
    assert "sub_scores" in rubric["rationale_instruction"]
    final_input = json.loads((input_dir / "final_report_input.json").read_text(encoding="utf-8"))
    assert final_input["patent"]["metadata"]["title"] == "문서변환 특허"
    assert final_input["evidence_references"][0]["title"] == "문서변환 SW 시장 확대"
    assert final_input["evidence_references"][0]["citation_title"] == "문서변환 SW 시장 확대"
    assert final_input["evidence_references"][0]["url"] == "https://example.com/news"


def test_axis_input_includes_representative_claims(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        kipris_api_data={
            "claim_stats": {"active_claim_count": 2},
        },
        preprocessed_patent={
            "claims": [
                {"claim_no": 1, "text": "대표 청구항 내용", "is_independent": True, "dependency": None},
                {"claim_no": 2, "text": "종속 청구항 내용", "is_independent": False, "dependency": 1},
            ]
        },
    )

    from agents.valuation import build_axis_input_payload

    payload = build_axis_input_payload(state=state, evidence=[])

    assert payload["patent"]["claim_availability"]["representative_claims_provided"] is True
    assert payload["patent"]["representative_claims"][0]["claim_no"] == 1
    assert payload["patent"]["representative_claims"][0]["text"] == "대표 청구항 내용"
    assert payload["patent"]["claim_availability"]["full_claims_provided"] is False
    assert payload["patent"]["claims"] == []


def test_legal_axis_input_includes_full_claims(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        kipris_api_data={
            "claim_stats": {"active_claim_count": 2},
        },
        preprocessed_patent={
            "claims": [
                {"claim_no": 1, "text": "독립항 전체 내용", "is_independent": True, "dependency": None},
                {"claim_no": 2, "text": "종속항 전체 내용", "is_independent": False, "dependency": 1},
            ]
        },
    )

    from agents.valuation import build_axis_input_payload

    legal_payload = build_axis_input_payload(axis="legal", state=state, evidence=[])
    market_payload = build_axis_input_payload(axis="market", state=state, evidence=[])

    assert [claim["claim_no"] for claim in legal_payload["patent"]["claims"]] == [1, 2]
    assert legal_payload["patent"]["claims"][1]["text"] == "종속항 전체 내용"
    assert legal_payload["patent"]["claim_availability"]["full_claims_provided"] is True
    assert market_payload["patent"]["claims"] == []
    assert market_payload["patent"]["claim_availability"]["full_claims_provided"] is False


def test_legal_axis_input_includes_prior_art_candidates(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        preprocessed_patent={
            "metadata": {
                "prior_art": ["KR10-1111111", "US2024-0000001A"],
            }
        },
    )

    from agents.valuation import build_axis_input_payload

    legal_payload = build_axis_input_payload(axis="legal", state=state, evidence=[])
    market_payload = build_axis_input_payload(axis="market", state=state, evidence=[])

    assert legal_payload["patent"]["prior_art_candidates"] == ["KR10-1111111", "US2024-0000001A"]
    assert market_payload["patent"]["prior_art_candidates"] == []


def test_valuation_llm_inputs_respect_no_save(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agents.valuation.call_llm",
        lambda prompt: "# LLM 최종 보고서" if "Return ONLY Markdown" in prompt else '{"score":70,"grade":"B","rationale":"r","evidence_ids":[],"risk_factors":["r"],"confidence":0.7}',
    )
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        patent_structured={"related_product": "문서변환 SW"},
    )

    run_valuation_agent(state)

    assert not (tmp_path / "valuation_inputs").exists()


def test_save_outputs_writes_final_report_markdown(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path)},
        preprocessed_patent={"patent_id": "P1"},
        final_report={
            "summary": {},
            "valuation": {"final_report_markdown": "# 최종 보고서\n\n본문"},
            "evidence": [],
        },
    )

    saved = save_outputs(state)

    assert saved["final_report"].name == "P1_final_report.json"
    assert saved["final_report_markdown"].name == "P1_final_report.md"
    assert saved["final_report_markdown"].read_text(encoding="utf-8").startswith("# 최종 보고서")


def test_cli_user_input_enables_llm_valuation_by_default():
    args = build_parser().parse_args(["--patent-id", "1"])

    user_input = build_user_input(args)

    assert user_input["use_llm_summary"] is True
    assert user_input["use_llm_valuation"] is True
    assert user_input["use_llm_final_report"] is True
    assert user_input["use_llm_supervisor"] is True


def test_cli_positional_identifier_is_management_number():
    args = build_parser().parse_args(["P202012001-US0"])

    user_input = build_user_input(args)

    assert user_input["management_number"] == "P202012001-US0"
    assert "patent_id" not in user_input
    assert "P202012001-US0" in user_input["artifact_dir"]


def test_cli_user_input_can_disable_llm_for_debug():
    args = build_parser().parse_args(
        ["--patent-id", "1", "--no-llm-summary", "--no-llm-valuation", "--no-llm-final-report"]
    )

    user_input = build_user_input(args)

    assert user_input["use_llm_summary"] is False
    assert user_input["use_llm_valuation"] is False
    assert user_input["use_llm_final_report"] is False
    assert user_input["use_llm_supervisor"] is False


def test_cli_user_input_can_disable_only_llm_supervisor():
    args = build_parser().parse_args(["P202405001-KR0", "--no-llm-supervisor"])

    user_input = build_user_input(args)

    assert user_input["management_number"] == "P202405001-KR0"
    assert user_input["use_llm_summary"] is True
    assert user_input["use_llm_valuation"] is True
    assert user_input["use_llm_final_report"] is True
    assert user_input["use_llm_supervisor"] is False
