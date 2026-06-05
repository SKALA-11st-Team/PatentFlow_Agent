import json
from pathlib import Path

import pytest

from agents.valuation import run_axis_valuation_agent, run_valuation_agent
from agents.valuation_axes.business_fit import select_evidence as select_business_fit_evidence
from agents.writing.final_report import run_final_report_agent
from app.main import build_parser, build_user_input, save_outputs
from workflow.supervisor import check_valuation_result
from workflow.state import PatentWorkflowState


def test_valuation_axes_are_split_into_axis_modules():
    from agents.valuation_axes import AXIS_MODULES

    assert list(AXIS_MODULES) == ["legal", "technology", "market", "business_fit"]
    assert AXIS_MODULES["legal"].LABEL == "권리성"
    assert AXIS_MODULES["technology"].LABEL == "기술성"
    assert AXIS_MODULES["market"].LABEL == "시장성"
    assert AXIS_MODULES["business_fit"].LABEL == "사업 연계성"
    assert AXIS_MODULES["legal"].PROMPT_PATH == "valuation/valuation_legal.md"
    assert callable(AXIS_MODULES["legal"].run)


def test_business_fit_prompt_matches_official_evidence_criteria():
    prompt = "prompts/valuation/valuation_business_fit.md"
    text = __import__("pathlib").Path(prompt).read_text(encoding="utf-8")

    assert "사업 연계성 축을 평가한다" in text
    assert "공식 근거 존재성: 30점" in text
    assert "제품·기능 직접 매칭도: 45점" in text
    assert "사업 문맥 적합성: 25점" in text
    assert "direct/plausible/broad/weak/none" not in text
    assert "context_fit_label" not in text
    assert '"axis": "business_fit"' in text
    assert '"score": 0' in text
    assert '"subscores"' in text
    assert "patent_description" in text
    assert "skax_official_evidence" in text
    assert "사업 연결성 40" not in text
    assert "포트폴리오 필요성 35" not in text
    assert "실제 활용 가능성 15" not in text
    assert "근거 신뢰도 10" not in text
    assert "SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다" in text


def test_legal_prompt_does_not_treat_missing_foreign_claims_as_weakness():
    text = Path("prompts/valuation/valuation_legal.md").read_text(encoding="utf-8")

    assert "해외 출원 청구항 상세가 제공되지 않은 경우에도 이를 권리 확장성 부족, 권리 약화, 감점 사유로 작성하지 않는다." in text
    assert "해외 패밀리 또는 해외 등록 정보는 확인된 경우에만 보조 긍정 근거로 사용한다." in text


def test_final_report_prompt_does_not_require_benchmark_data():
    final_report = Path("prompts/writing/final_report.md").read_text(encoding="utf-8")

    assert "정량 성능 검증, benchmark, 학습 데이터 세부 부재를 기술성 약점처럼 쓰지 않습니다." in final_report


def test_final_report_prompt_uses_rights_scope_explanation_for_legal_axis():
    final_report = Path("prompts/writing/final_report.md").read_text(encoding="utf-8")

    assert "**권리범위 참고도 및 이해**" in final_report
    assert "| 권리범위 참고도 | 권리범위 이해 |" in final_report
    assert "### 4.1 권리성" in final_report
    legal_section = final_report.split("### 4.1 권리성", 1)[1].split("### 4.2 기술성", 1)[0]
    assert "**주요 근거**" in legal_section
    assert "patent.rights_scope_context.representative_drawing.image_markdown" in final_report
    assert "patent.rights_scope_context.representative_figure_detail" in final_report
    assert "도면에 나타난 주요 구성 또는 처리 단계가 청구항의 어느 필수 구성과 연결되는지 매칭하세요." in final_report
    assert "도면상 구성/흐름 → 청구항 필수 구성 → 권리범위 형성 의미" in final_report
    assert "왼쪽 열에는 도면 이미지와 도면 설명만 작성하고 권리범위 판단을 쓰지 마세요." in final_report
    assert "아이디어 자체보다" not in final_report


def test_market_prompt_uses_40_40_20_market_structure():
    prompt = Path("prompts/valuation/valuation_market.md").read_text(encoding="utf-8")

    assert "시장성 점수(100) = 산업 시장성(40) + 시장 성장성(40) + 글로벌 사업성(20)" in prompt
    assert "- `industry_growth_evidence`: 0 또는 15" in prompt
    assert "- `corporate_investment_entry`: 0 또는 10" in prompt
    assert "- `news_market_diffusion`: 0 또는 10" in prompt
    assert "직접 기능 시장성(60)" not in prompt
    assert "specific_function_demand" not in prompt


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
    assert result.valuation_result["average_score"] == round(result.valuation_result["total_score"] / 4, 1)
    assert "평균 점수는" in result.valuation_result["decision_rationale"][0]
    assert axes["market"]["subscores"]["market_growth"]["score"] is None
    assert "final_report_markdown" not in result.valuation_result


def test_run_axis_valuation_agent_sets_only_legal_axis(monkeypatch, tmp_path):
    captured_prompts = []

    def fake_call_llm(prompt):
        captured_prompts.append(prompt)
        return json.dumps(
            {
                "score": 54,
                "subscores": {
                    "right_stability": {
                        "label": "권리안정성",
                        "score": 22,
                        "max_score": 35,
                        "details": {
                            "prior_art_overlap": {
                                "label": "선행문헌 대비 청구항 중복도",
                                "score": 18,
                                "max_score": 25,
                                "rationale": "일부 핵심 구성이 겹치지만 차별 구성이 남아 있다.",
                            },
                            "claim_structure_stability": {
                                "label": "청구항 구조 안정성",
                                "score": 4,
                                "max_score": 10,
                                "rationale": "독립항은 있으나 종속항은 1개 수준이다.",
                            },
                        },
                        "rationale": "등록상태는 유효하나 유사 인용문헌이 일부 존재한다.",
                    },
                    "claim_protection": {
                        "label": "청구항 보호력",
                        "score": 21,
                        "max_score": 40,
                        "details": {
                            "core_solution_coverage": {
                                "label": "핵심 해결수단의 독립항 반영도",
                                "score": 8,
                                "max_score": 12,
                                "rationale": "핵심 흐름은 반영되나 일부 조건이 약하다.",
                            },
                            "independent_claim_scope": {
                                "label": "독립항 보호범위 적정성",
                                "score": 8,
                                "max_score": 12,
                                "rationale": "일부 구현 조건이 있으나 핵심 범위는 유지된다.",
                            },
                            "dependent_claim_support": {
                                "label": "종속항 보완성",
                                "score": 3,
                                "max_score": 10,
                                "rationale": "종속항은 단순 구체화에 가깝다.",
                            },
                            "claim_type_diversity": {
                                "label": "청구항 보호형태 다양성",
                                "score": 2,
                                "max_score": 6,
                                "rationale": "단일 유형 중심이다.",
                            },
                        },
                        "rationale": "핵심 기능은 보호하지만 일부 구현 한정이 있다.",
                    },
                    "portfolio_defensive_value": {
                        "label": "포트폴리오·해외 권리 가치",
                        "score": 11,
                        "max_score": 25,
                        "details": {
                            "portfolio_connection_coverage": {
                                "label": "관련 특허군 연계·커버리지",
                                "score": 11,
                                "max_score": 15,
                                "rationale": "관련 특허군과 연결되고 새로운 보호 포인트가 일부 확인된다.",
                            },
                            "overseas_right_coverage": {
                                "label": "해외 권리 확보 범위",
                                "score": 0,
                                "max_score": 6,
                                "rationale": "현재 입력 기준에서 해외 권리 확보 가점 근거는 확인되지 않는다.",
                            },
                            "follow_on_right_signal": {
                                "label": "후속 권리화 신호",
                                "score": 0,
                                "max_score": 4,
                                "rationale": "피인용 신호는 확인되지 않는다.",
                            },
                        },
                        "rationale": "동일 관리번호 패밀리와 보완 관계가 있다.",
                    },
                },
                "grade": "C",
                "rationale": "권리성 단독 평가",
                "evidence_ids": ["portfolio_001"],
                "risk_factors": ["유사 인용문헌 비교 필요"],
                "missing_information": [],
                "confidence": 0.7,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("agents.valuation.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        patent_structured={"status": "등록", "related_product": "ESS"},
        kipris_api_data={"claim_stats": {"total_claim_count": 3}},
        preprocessed_patent={
            "claims": [
                {"claim_no": 1, "text": "독립항", "is_independent": True, "dependency": None},
                {"claim_no": 2, "text": "종속항", "is_independent": False, "dependency": 1},
            ]
        },
        citation_evidence={
            "kr_citation_documents": [
                {
                    "application_number": "1020200012345",
                    "representative_claims": [{"claim_no": 1, "text": "선행 청구항"}],
                }
            ]
        },
        evidence_bundle=[
            {
                "evidence_id": "portfolio_001",
                "source_type": "portfolio_context",
                "compressed_summary": "동일 관리번호 패밀리 특허가 있다.",
            }
        ],
    )

    result = run_axis_valuation_agent("legal", state)

    axes = result.valuation_result["axes"]
    assert list(axes) == ["legal"]
    assert axes["legal"]["subscores"]["right_stability"]["score"] == 22
    assert axes["legal"]["score"] == 54
    assert axes["legal"]["evidence_ids"] == ["portfolio_001"]
    assert axes["legal"]["legal_context"]["claim_count_context"]["evidence"]["total_claim_count"] == 2
    assert "Valuation Legal Axis Prompt" in captured_prompts[0]
    assert "citation_evidence" in captured_prompts[0]


def test_business_fit_selects_only_news_with_sk_ax_or_cnc_context():
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
                "title": "SK AX 문서변환 SW 사업 확대",
                "compressed_summary": "SK AX 문서변환 SW가 업무 자동화에 적용되고 있다.",
            },
            {
                "evidence_id": "news_002",
                "source": "naver_news",
                "source_type": "news",
                "title": "에스케이 문서변환 SW 사업 확대",
                "compressed_summary": "문서변환 SW가 업무 자동화에 적용되고 있다.",
            },
        ],
    )

    selected = select_business_fit_evidence(state.evidence_bundle, state)

    assert [item["evidence_id"] for item in selected] == ["news_001"]


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
                "title": "SK AX 로보어드바이저 자산배분 시장 확대",
                "compressed_summary": "SK AX 데이터분석 서비스 수요 확대",
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

    selected = select_business_fit_evidence(state.evidence_bundle, state)

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

    selected = select_business_fit_evidence(state.evidence_bundle, state)

    assert selected[0]["evidence_id"] == "skax_meta"


def test_business_fit_prioritizes_owned_media_before_sk_mentioned_news_and_secondary():
    state = PatentWorkflowState(
        patent_structured={
            "related_product": "문서변환 SW",
            "title_final": "문서변환 특허",
        },
        kipris_api_data={"metadata": {"assignee": ["에스케이"]}},
        evidence_bundle=[
            {
                "evidence_id": "general_news",
                "source": "naver_news",
                "source_type": "news",
                "title": "에스케이 문서변환 SW 사업 확대",
            },
            {
                "evidence_id": "sk_news",
                "source": "naver_news",
                "source_type": "news",
                "title": "SK C&C 문서변환 SW 사업 확대",
            },
            {
                "evidence_id": "owned_media",
                "source": "sk_group_owned_media",
                "source_type": "company_disclosure",
                "source_domain": "openapi.sk.com",
                "title": "SK주식회사 AX 문서변환 SW",
                "content": "SK주식회사 AX 문서변환 SW",
                "relevance_score": 0.5,
            },
            {
                "evidence_id": "portfolio_001",
                "source": "kipris_api",
                "source_type": "portfolio_context",
                "compressed_summary": "문서변환 포트폴리오 맥락",
            },
            {
                "evidence_id": "irrelevant_news",
                "source": "naver_news",
                "source_type": "news",
                "title": "무관한 산업 뉴스",
            },
        ],
    )

    selected = select_business_fit_evidence(state.evidence_bundle, state)

    assert [item["evidence_id"] for item in selected] == ["owned_media", "sk_news", "portfolio_001"]


def test_business_fit_input_context_uses_summary_and_limited_official_evidence():
    from agents.valuation_axes.business_fit import build_input_payload

    long_content = "SK AX 공식 사업 근거 " * 120
    state = PatentWorkflowState(
        patent_structured={
            "관리번호": "P202405001-KR0",
            "발명의 명칭(최종)": "강화학습 자산배분 특허",
            "관련제품": "로보어드바이저",
            "관련 사업 분야": "Data",
            "관련 기술 분야": "데이터분석",
        },
        summary_result={
            "plain_summary": "요약 우선 사용",
            "key_points": ["핵심 1", "핵심 2"],
        },
        preprocessed_patent={
            "sections": {
                "abstract": "초록 fallback",
                "problem": "해결 과제",
                "solution": "핵심 해결수단",
                "effect": "기대 효과",
                "claims_text": "청구항 전체 원문은 들어가면 안 됨",
            },
            "cleaned_markdown": "정제된 전체 원문은 들어가면 안 됨",
        },
        parsed_pdf={"markdown_text": "PDF 전체 원문은 들어가면 안 됨"},
    )
    evidence = [
        {
            "evidence_id": f"skax_{score}",
            "source": "sk_ax_official",
            "source_type": "company_disclosure",
            "title": f"SK AX 공식 근거 {score}",
            "url": f"https://www.skax.co.kr/finance/{score}",
            "content": long_content,
            "relevance_score": score / 10,
            "matched_keywords": ["로보어드바이저"],
            "candidate_results": [{"url": "debug"}],
            "search_request_url": "https://api.tavily.com/search",
        }
        for score in [1, 5, 9, 7, 3]
    ]

    payload = build_input_payload(state=state, evidence=evidence)
    context = payload["business_fit_context"]
    description = context["patent_description"]
    official = context["skax_official_evidence"]
    serialized = json.dumps(context, ensure_ascii=False)

    assert description["management_number"] == "P202405001-KR0"
    assert description["title_final"] == "강화학습 자산배분 특허"
    assert description["related_product"] == "로보어드바이저"
    assert description["business_area"] == "Data"
    assert description["technology_area"] == "데이터분석"
    assert description["summary"] == "요약 우선 사용"
    assert description["key_points"] == ["핵심 1", "핵심 2"]
    assert description["problem_or_purpose"] == "해결 과제"
    assert description["solution_or_core_technology"] == "핵심 해결수단"
    assert description["effect_or_expected_benefit"] == "기대 효과"
    assert [item["evidence_id"] for item in official] == ["skax_9", "skax_7", "skax_5"]
    assert all(len(item["content_excerpt"]) <= 1500 for item in official)
    assert official[0]["matched_keywords"] == ["로보어드바이저"]
    assert official[0]["score_reasons"] == []
    metrics = context["quantitative_metrics"]
    assert metrics["official_evidence_count"] == 5
    assert metrics["official_business_evidence"]["score"] == 30
    assert metrics["official_business_evidence"]["max_score"] == 30
    assert metrics["product_function_direct_match"]["max_score"] == 45
    assert "candidate_results" not in serialized
    assert "search_request_url" not in serialized
    assert "청구항 전체 원문은 들어가면 안 됨" not in serialized
    assert "정제된 전체 원문은 들어가면 안 됨" not in serialized
    assert "PDF 전체 원문은 들어가면 안 됨" not in serialized


def test_business_fit_patent_description_falls_back_to_sections_and_kipris():
    from agents.valuation_axes.business_fit import build_business_fit_patent_description

    state = PatentWorkflowState(
        patent_structured={"title_final": "fallback 특허"},
        preprocessed_patent={"sections": {"abstract": "", "solution": "섹션 해결수단"}},
        kipris_api_data={"sections": {"abstract": "KIPRIS 초록"}},
    )

    description = build_business_fit_patent_description(state)

    assert description["title_final"] == "fallback 특허"
    assert description["summary"] == "KIPRIS 초록"
    assert description["solution_or_core_technology"] == "섹션 해결수단"


def test_business_fit_quantitative_metrics_scores_rule_based_subscores_from_official_evidence():
    from agents.valuation_axes.business_fit import build_business_fit_quantitative_metrics

    state = PatentWorkflowState(
        patent_structured={
            "title_final": "강화학습 자산배분 시스템",
            "related_product": "로보어드바이저",
            "business_area": "Data",
            "technology_area": "데이터분석",
        }
    )
    evidence = [
        {
            "evidence_id": "skax_finance_001",
            "source": "sk_ax_official",
            "source_type": "company_disclosure",
            "title": "로보어드바이저 자산관리 금융 서비스",
            "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
            "content": "로보어드바이저 데이터분석 기반 자산배분 서비스와 금융 고객 업무 적용 시나리오를 설명한다.",
            "candidate_relevance_score": 0.72,
        },
        {
            "evidence_id": "skax_insight_001",
            "source": "sk_ax_official",
            "source_type": "company_disclosure",
            "title": "AI 트렌드",
            "url": "https://www.skax.co.kr/insight/trend/23",
            "content": "AI 데이터 서비스 트렌드",
            "candidate_relevance_score": 0.2,
        },
    ]

    metrics = build_business_fit_quantitative_metrics(state=state, evidence=evidence)

    assert metrics["official_evidence_count"] == 2
    assert metrics["best_relevance_score"] == 0.72
    assert metrics["official_business_evidence"]["score"] == 24
    assert metrics["product_function_direct_match"]["score"] == 36
    assert metrics["product_function_direct_match"]["product_match_level"] == "direct"
    assert "자산배분" in metrics["product_function_direct_match"]["matched_strong_core_terms"]


def test_business_fit_quantitative_metrics_uses_sk_owned_media_as_lower_tier_evidence():
    from agents.valuation_axes.business_fit import build_business_fit_quantitative_metrics, build_input_payload

    state = PatentWorkflowState(
        patent_structured={
            "title_final": "AI 영상 분석 시스템",
            "related_product": "AIDEN VAS",
            "business_area": "AI",
            "technology_area": "Vision AI",
        },
    )
    evidence = [
        {
            "evidence_id": "sk_owned_001",
            "source": "sk_group_owned_media",
            "source_domain": "skcareersjournal.com",
            "source_tier": "sk_related_owned_media",
            "source_type": "company_disclosure",
            "title": "SK AX AIDEN VAS",
            "url": "https://www.skcareersjournal.com/2827",
            "content": "SK AX AIDEN VAS AI 영상 분석 서비스",
            "relevance_score": 0.8,
        }
    ]

    payload = build_input_payload(state=state, evidence=evidence)
    metrics = build_business_fit_quantitative_metrics(state=state, evidence=evidence)

    assert payload["business_fit_context"]["skax_official_evidence"] == []
    assert payload["business_fit_context"]["sk_owned_media_evidence"][0]["source_domain"] == "skcareersjournal.com"


def test_business_fit_quantitative_metrics_limits_match_when_core_terms_are_missing():
    from agents.valuation_axes.business_fit import build_business_fit_quantitative_metrics, title_keyword_terms

    title = "상품 트렌드 예측을 반영한 강화학습 모델을 적용한 자산배분 시스템 및 방법"

    assert title_keyword_terms(title, limit=6) == ["강화학습", "자산배분", "트렌드 예측", "상품 트렌드", "트렌드", "예측"]

    state = PatentWorkflowState(
        patent_structured={
            "title_final": title,
            "related_product": "로보어드바이저",
            "business_area": "Data",
            "technology_area": "데이터분석",
        }
    )
    evidence = [
        {
            "evidence_id": f"skax_finance_{index}",
            "source": "sk_ax_official",
            "source_type": "company_disclosure",
            "title": "로보어드바이저 금융 서비스",
            "url": f"https://www.skax.co.kr/finance/{index}",
            "content": "로보어드바이저 고객 업무 자동화 플랫폼 서비스",
            "candidate_relevance_score": 0.92,
        }
        for index in range(3)
    ]

    metrics = build_business_fit_quantitative_metrics(state=state, evidence=evidence)

    assert metrics["product_function_direct_match"]["score"] == 24
    assert metrics["product_function_direct_match"]["missing_core_terms"]
    assert metrics["product_function_direct_match"]["strong_core_match_ratio"] == 0.0


def test_business_fit_run_uses_llm_for_final_axis_json():
    from agents.valuation import AxisRuntime
    from agents.valuation_axes import business_fit

    captured_payloads = []
    captured_llm_calls = []

    def fake_build_prompt(**kwargs):
        captured_payloads.append(kwargs["payload"])
        return "saved artifact prompt"

    def fake_run_llm_required(**kwargs):
        captured_llm_calls.append(kwargs)
        return {
            "axis": "business_fit",
            "label": "사업 연계성",
            "score": 70,
            "grade": "C",
            "rationale": "공식 근거와 제품 문맥이 일부 연결된다.",
            "evidence_ids": ["skax_finance_001"],
            "risk_factors": [],
            "missing_information": [],
            "confidence": 0.75,
            "subscores": {
                "official_business_evidence": {
                    "label": "공식 근거 존재성",
                    "score": 16,
                    "max_score": 30,
                    "rationale": "공식 근거 1건이 확인된다.",
                },
                "product_function_direct_match": {
                    "label": "제품·기능 직접 매칭도",
                    "score": 36,
                    "max_score": 45,
                    "rationale": "관련제품과 핵심 기능 일부가 확인된다.",
                },
                "business_context_fit": {
                    "label": "사업 문맥 적합성",
                    "score": 18,
                    "max_score": 25,
                    "rationale": "사업 문맥이 자연스럽게 연결된다.",
                },
            },
        }

    state = PatentWorkflowState(
        patent_structured={
            "title_final": "강화학습 자산배분 시스템",
            "related_product": "로보어드바이저",
            "business_area": "Data",
            "technology_area": "데이터분석",
        },
        evidence_bundle=[
            {
                "evidence_id": "skax_finance_001",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "로보어드바이저 자산관리 금융 서비스",
                "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                "content": "로보어드바이저 데이터분석 기반 자산배분 서비스",
                "candidate_relevance_score": 0.7,
            }
        ],
    )

    result = business_fit.run(
        state,
        AxisRuntime(build_prompt=fake_build_prompt, run_llm_required=fake_run_llm_required),
    )

    assert captured_payloads[0]["business_fit_context"]["quantitative_metrics"]
    assert captured_llm_calls[0]["axis"] == "business_fit"
    assert captured_llm_calls[0]["prompt"] == "saved artifact prompt"
    assert captured_llm_calls[0]["evidence"][0]["evidence_id"] == "skax_finance_001"
    assert result["subscores"]["official_business_evidence"]["score"] == 16
    assert result["subscores"]["product_function_direct_match"]["score"] == 36
    assert result["subscores"]["business_context_fit"]["score"] == 18
    assert "details" not in result["subscores"]["business_context_fit"]
    assert result["score"] == 70
    assert result["grade"] == "B"


def test_business_fit_patent_description_uses_metadata_and_agent_summary_fallbacks():
    from agents.valuation_axes.business_fit import build_business_fit_patent_description

    state = PatentWorkflowState(
        patent_structured={"related_product": "ChainZ", "business_area": "Blockchain", "technology_area": "인증"},
        summary_result={
            "title": "summary 제목",
            "key_points": ["서명 검증 핵심", "블록체인 합의"],
        },
        preprocessed_patent={
            "metadata": {"title": "preprocessed 제목"},
            "sections": {},
            "agent_inputs": {
                "summary": {
                    "abstract": "agent 초록",
                    "problem": "agent 문제",
                    "solution": "agent 해결수단",
                    "technical_field": "agent 기술분야",
                    "effect": "agent 효과",
                },
                "valuation": {
                    "claims": [{"text": "valuation claims 전체는 들어가면 안 됨"}],
                    "detailed_description": "valuation 상세설명 전체는 들어가면 안 됨",
                },
            },
            "cleaned_markdown": "cleaned_markdown 전체는 들어가면 안 됨",
        },
        kipris_api_data={
            "metadata": {"title": "kipris 제목"},
            "sections": {
                "abstract": "kipris 초록",
                "problem": "kipris 문제",
                "solution": "kipris 해결",
                "technical_field": "kipris 기술분야",
            },
            "raw": {"secret": "kipris raw 전체는 들어가면 안 됨"},
        },
    )

    description = build_business_fit_patent_description(state)
    serialized = json.dumps(description, ensure_ascii=False)

    assert description["title"] == "summary 제목"
    assert description["title_final"] == "preprocessed 제목"
    assert description["summary"] == "agent 초록"
    assert description["problem_or_purpose"] == "agent 문제"
    assert description["solution_or_core_technology"] == "agent 해결수단"
    assert description["effect_or_expected_benefit"] == "agent 효과"
    assert "ChainZ" in description["key_terms"]
    assert "Blockchain" in description["key_terms"]
    assert "인증" in description["key_terms"]
    assert "서명 검증 핵심" in description["key_terms"]
    assert "블록체인 합의" in description["key_terms"]
    assert "valuation claims 전체는 들어가면 안 됨" not in serialized
    assert "valuation 상세설명 전체는 들어가면 안 됨" not in serialized
    assert "cleaned_markdown 전체는 들어가면 안 됨" not in serialized
    assert "kipris raw 전체는 들어가면 안 됨" not in serialized


def test_business_fit_patent_description_uses_kipris_section_fallbacks():
    from agents.valuation_axes.business_fit import build_business_fit_patent_description

    state = PatentWorkflowState(
        kipris_api_data={
            "metadata": {"title": "KIPRIS 제목"},
            "sections": {
                "abstract": "KIPRIS 초록",
                "problem": "KIPRIS 문제",
                "solution": "KIPRIS 해결",
                "technical_field": "KIPRIS 기술분야",
            },
        },
    )

    description = build_business_fit_patent_description(state)

    assert description["title"] == "KIPRIS 제목"
    assert description["title_final"] == "KIPRIS 제목"
    assert description["summary"] == "KIPRIS 초록"
    assert description["problem_or_purpose"] == "KIPRIS 문제"
    assert description["solution_or_core_technology"] == "KIPRIS 해결"


def test_business_fit_context_is_safe_with_latest_workflow_state_fields():
    from agents.valuation_axes.business_fit import build_input_payload

    state = PatentWorkflowState(
        user_input={"company_context": {"company_name": "SK AX"}},
        patent_structured={"title_final": "최신 State 특허", "related_product": "ChainZ"},
        kipris_api_data=None,
        kipris_family_patents=[{"application_number": "10-1"}],
        citation_evidence={"kr_citation_documents": [{"application_number": "10-2"}]},
        parsed_pdf={"markdown_text": "parsed_pdf 전체 원문은 들어가면 안 됨"},
        preprocessed_patent={
            "sections": {
                "claims_text": "claims_text 전체 원문은 들어가면 안 됨",
            },
            "agent_inputs": {
                "valuation": {
                    "claims": [{"text": "agent_inputs valuation claims 전체는 들어가면 안 됨"}],
                    "detailed_description": "agent_inputs valuation 상세설명은 들어가면 안 됨",
                }
            },
            "cleaned_markdown": "cleaned_markdown 전체 원문은 들어가면 안 됨",
        },
        summary_result=None,
        portfolio_evidence=[{"evidence_id": "portfolio_external"}],
        portfolio_result={"summary": "포트폴리오 결과는 business_fit_context에 넣지 않음"},
        query_plan={"queries": ["검색 계획"]},
        search_queries=["검색어"],
        evidence_bundle=[],
        valuation_result={"axes": {}},
        final_report={"summary": "final"},
        validation_result={"ok": True},
        summary_validation_result={"ok": True},
        report_validation_result={"ok": True},
        supervisor_decision={"decision": "PASS"},
        missing_evidence=["missing"],
    )

    payload = build_input_payload(state=state, evidence=[])
    context = payload["business_fit_context"]
    serialized_context = json.dumps(context, ensure_ascii=False)

    assert context["patent_description"]["title_final"] == "최신 State 특허"
    assert context["patent_description"]["related_product"] == "ChainZ"
    assert context["skax_official_evidence"] == []
    assert "citation_evidence" not in serialized_context
    assert "portfolio_external" not in serialized_context
    assert "포트폴리오 결과는 business_fit_context에 넣지 않음" not in serialized_context
    assert "summary_validation_result" not in serialized_context
    assert "report_validation_result" not in serialized_context
    assert "parsed_pdf 전체 원문은 들어가면 안 됨" not in serialized_context
    assert "cleaned_markdown 전체 원문은 들어가면 안 됨" not in serialized_context
    assert "claims_text 전체 원문은 들어가면 안 됨" not in serialized_context
    assert "agent_inputs valuation claims 전체는 들어가면 안 됨" not in serialized_context
    assert "agent_inputs valuation 상세설명은 들어가면 안 됨" not in serialized_context


def test_business_fit_context_is_safe_with_minimal_empty_state():
    from agents.valuation_axes.business_fit import build_input_payload

    payload = build_input_payload(state=PatentWorkflowState(), evidence=[])

    assert payload["business_fit_context"]["patent_description"]["title_final"] == ""
    assert payload["business_fit_context"]["patent_description"]["summary"] == ""
    assert payload["business_fit_context"]["skax_official_evidence"] == []
    assert payload["business_fit_context"]["quantitative_metrics"]["official_evidence_count"] == 0
    assert payload["business_fit_context"]["quantitative_metrics"]["official_business_evidence"]["score"] == 0


def test_business_fit_rubric_is_externalized_to_prompt_md():
    from agents.valuation import normalize_axis_llm_result

    text = __import__("pathlib").Path("prompts/valuation/valuation_business_fit.md").read_text(encoding="utf-8")

    assert "사업 연계성 축을 평가한다" in text
    assert "공식 근거 존재성: 30점" in text
    assert "제품·기능 직접 매칭도: 45점" in text
    assert "사업 문맥 적합성: 25점" in text
    assert '"axis": "business_fit"' in text
    assert '"subscores"' in text
    assert "context_fit_label" not in text
    assert "business_connection" not in text
    assert "portfolio_necessity" not in text
    assert "practical_applicability" not in text
    assert "evidence_reliability" not in text
    assert "SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다." in text

    result = normalize_axis_llm_result(
        "business_fit",
        {
            "score": 80,
            "grade": "B",
            "subscores": {
                "official_business_evidence": {
                    "label": "공식 사업 근거 발견도",
                    "score": 24,
                    "max_score": 30,
                    "rationale": "공식 근거 확인",
                }
            },
            "rationale": "공식 근거 기반 평가",
            "evidence_ids": ["skax_001"],
            "risk_factors": [],
            "missing_information": [],
            "confidence": 0.8,
        },
        evidence=[{"evidence_id": "skax_001"}],
    )

    assert set(result) == {
        "axis",
        "label",
        "score",
        "grade",
        "rationale",
        "evidence_ids",
        "risk_factors",
        "missing_information",
        "confidence",
        "subscores",
    }
    assert result["subscores"]["official_business_evidence"]["score"] == 24


def test_market_score_helpers_apply_40_40_20_structure():
    from datetime import date, datetime

    from agents.valuation_axes.market import (
        apply_marketability_scores,
        build_global_business_metrics,
        market_growth_reference_date,
        market_growth_windows,
        score_cagr,
        score_recent_trend,
    )

    assert market_growth_reference_date(datetime(2026, 5, 29)) == date(2024, 11, 29)
    assert market_growth_windows(date(2024, 11, 29)) == [
        {"label": "2021-11-30~2022-11-29", "start_date": date(2021, 11, 30), "end_date": date(2022, 11, 29)},
        {"label": "2022-11-30~2023-11-29", "start_date": date(2022, 11, 30), "end_date": date(2023, 11, 29)},
        {"label": "2023-11-30~2024-11-29", "start_date": date(2023, 11, 30), "end_date": date(2024, 11, 29)},
    ]
    assert score_cagr(0.16) == 25
    assert score_cagr(0.1) == 20
    assert score_cagr(0.05) == 15
    assert score_cagr(0.01) == 10
    assert score_cagr(-0.01) == 0
    assert score_recent_trend([10, 12, 15]) == ("continuous_increase", 15)
    assert score_recent_trend([10, 8, 12]) == ("partial_increase", 8)
    assert score_recent_trend([15, 12, 10]) == ("continuous_decrease", 0)

    result = apply_marketability_scores(
        {
            "score": 70,
            "industry_marketability_score": 40,
            "grade": "B",
            "missing_information": [],
            "confidence": 0.8,
        },
        {
            "market_growth_score": 32,
            "global_business_score": 20,
        },
    )

    assert result["score"] == 92
    assert result["grade"] == "A"
    assert result["subscores"] == {
        "industry_marketability": {
            "label": "산업 시장성",
            "score": 40,
            "max_score": 40,
            "rationale": "",
        },
        "market_growth": {
            "label": "시장 성장성",
            "score": 32,
            "max_score": 40,
            "details": {
                "cagr_score": None,
                "trend_score": None,
            },
            "rationale": "대표 CPC 기준 18개월 전 종료 3개 1년 구간 공개 특허 수 증가율 및 추세로 산정된 코드 계산값입니다.",
        },
        "global_business": {
            "label": "글로벌 사업성",
            "score": 20,
            "max_score": 20,
            "details": {
                "gnews_evidence_count": 0,
                "gnews_quality_evidence_count": 0,
                "global_business_status": "",
            },
            "rationale": "GNews 해외 뉴스 근거로 산정된 코드 계산값입니다.",
        },
    }

    result = apply_marketability_scores(
        {
            "score": 70,
            "subscores": {
                "industry_marketability": {
                    "details": {
                        "industry_growth_evidence": 15,
                        "corporate_investment_entry": 10,
                        "news_market_diffusion": 10,
                        "source_reliability": 5,
                    },
                },
            },
            "grade": "B",
            "missing_information": [],
            "confidence": 0.8,
        },
        {
            "market_growth_score": 40,
            "global_business_score": 20,
        },
    )

    assert result["subscores"]["industry_marketability"]["score"] == 40
    assert result["subscores"]["industry_marketability"]["max_score"] == 40
    assert result["subscores"]["industry_marketability"]["details"] == {
        "industry_growth_evidence": 15,
        "corporate_investment_entry": 10,
        "news_market_diffusion": 10,
        "source_reliability": 5,
    }
    assert result["subscores"]["market_growth"]["details"] == {
        "cagr_score": None,
        "trend_score": None,
    }
    assert result["subscores"]["global_business"]["details"]["global_business_status"] == ""
    assert "industry_marketability_breakdown" not in result
    assert result["score"] == 100
    assert "market_score_cap_reason" not in result["marketability_metrics"]

    assert build_global_business_metrics(
        [
            {"evidence_id": "g0", "source": "gnews", "compressed_summary": "글로벌 AI 투자 흐름"},
            {"evidence_id": "g1", "source": "gnews", "compressed_summary": "해외 고객사의 서비스 도입 사례"},
            {"evidence_id": "g2", "source": "gnews", "key_facts": ["글로벌 시장에서 관련 제품 출시"]},
            {"evidence_id": "n1", "source": "naver_news", "compressed_summary": "국내 뉴스"},
        ]
    ) == {
        "gnews_evidence_count": 3,
        "gnews_quality_evidence_count": 2,
        "gnews_evidence_ids": ["g1", "g2"],
        "global_business_status": "limited_gnews_global_signal",
        "global_business_score": 10,
    }


def test_market_prompt_uses_18_month_lagged_three_window_activity():
    prompt = Path("prompts/valuation/valuation_market.md").read_text(encoding="utf-8")

    assert "18개월 전을 마지막 시점으로 하는 3개 1년 구간" in prompt
    assert "최근 3개년 공개 활동성" not in prompt
    assert "`source`가 `gnews`인 해외 뉴스는 산업 시장성이 아니라 글로벌 사업성 근거로만 사용한다." in prompt
    assert "시장성 점수(100) = 산업 시장성(40) + 시장 성장성(40) + 글로벌 사업성(20)" in prompt
    assert "시장성 총점은 원칙적으로 55점을 넘기지 않는다" not in prompt


def test_final_report_prompt_uses_gnews_for_global_business():
    prompt = Path("prompts/writing/final_report.md").read_text(encoding="utf-8")

    assert "글로벌 적용성은 GNews 등 해외 뉴스 근거에서 확인된 글로벌 시장 관심과 해외 적용 흐름을 중심으로 설명하세요." in prompt
    assert "GNews 기반 글로벌 시장 근거가 없다는 사실만으로 시장성이 낮다고 단정하지 마세요." in prompt
    assert "해외 Patent Family가 확인되지 않아 글로벌 사업성은 국내 단독 출원 기준으로 낮게 반영되었습니다." not in prompt


def test_market_growth_missing_is_not_replaced_with_default_score():
    from agents.valuation_axes.market import MARKET_GROWTH_MISSING_MESSAGE, apply_marketability_scores

    result = apply_marketability_scores(
        {
            "score": 70,
            "industry_marketability_score": 60,
            "grade": "B",
            "missing_information": [],
            "confidence": 0.8,
        },
        {
            "market_growth_score": None,
            "global_business_score": 20,
        },
    )

    assert result["score"] == 60
    assert result["subscores"]["market_growth"]["score"] is None
    assert MARKET_GROWTH_MISSING_MESSAGE in result["missing_information"]
    assert result["confidence"] == 0.49


def test_market_growth_with_zero_start_count_is_unavailable(monkeypatch):
    import agents.valuation_axes.market as market

    monkeypatch.setattr(
        market,
        "collect_cpc_window_application_counts",
        lambda representative_cpc, windows: [
            {"label": "w1", "start_date": "2021-11-30", "end_date": "2022-11-29", "count": 0},
            {"label": "w2", "start_date": "2022-11-30", "end_date": "2023-11-29", "count": 179},
            {"label": "w3", "start_date": "2023-11-30", "end_date": "2024-11-29", "count": 234},
        ],
    )

    metrics = market.build_market_growth_metrics("G06Q 40/06")

    assert metrics["market_growth_available"] is False
    assert metrics["cagr"] is None
    assert metrics["cagr_score"] is None
    assert metrics["trend_status"] is None
    assert metrics["trend_score"] is None
    assert metrics["market_growth_score"] is None
    assert metrics["missing_reason"] == "cagr_start_count_zero"


def test_collect_cpc_window_counts_does_not_make_extra_validation_call(monkeypatch):
    from datetime import date

    from agents.valuation_axes.market import collect_cpc_window_application_counts
    import open_api.kipris_client as kipris_client

    calls = []
    windows = [
        {"label": "w1", "start_date": date(2023, 1, 1), "end_date": date(2023, 12, 31)},
        {"label": "w2", "start_date": date(2024, 1, 1), "end_date": date(2024, 12, 31)},
        {"label": "w3", "start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31)},
    ]

    class FakeKiprisClient:
        def search_by_cpc(self, cpc_number, **params):
            calls.append((cpc_number, params))
            return {
                "items": [
                    {
                        "ApplicationNumber": "1020250000001",
                        "OpeningDate": "20250102",
                        "RegistrationStatus": "공개",
                    },
                    {
                        "ApplicationNumber": "1020240000001",
                        "OpeningDate": "20240102",
                        "RegistrationStatus": "공개",
                    },
                    {
                        "ApplicationNumber": "1020230000001",
                        "OpeningDate": "20230102",
                        "RegistrationStatus": "공개",
                    },
                ]
            }

    monkeypatch.setattr(kipris_client, "KiprisClient", FakeKiprisClient)

    counts = collect_cpc_window_application_counts("G06F 40/00", windows=windows, page_size=20)

    assert counts == [
        {"label": "w1", "start_date": "2023-01-01", "end_date": "2023-12-31", "count": 1},
        {"label": "w2", "start_date": "2024-01-01", "end_date": "2024-12-31", "count": 1},
        {"label": "w3", "start_date": "2025-01-01", "end_date": "2025-12-31", "count": 1},
    ]
    assert len(calls) == 1
    assert calls[0][1] == {
        "patent": True,
        "utility": False,
        "docsCount": 20,
        "docsStart": 1,
        "descSort": True,
        "sortSpec": "OPD",
        "lastvalue": "",
    }


def test_collect_cpc_window_counts_uses_opening_date_and_stops_at_old_window(monkeypatch):
    from datetime import date

    from agents.valuation_axes.market import collect_cpc_window_application_counts
    import open_api.kipris_client as kipris_client

    calls = []
    windows = [
        {"label": "w1", "start_date": date(2023, 1, 1), "end_date": date(2023, 12, 31)},
        {"label": "w2", "start_date": date(2024, 1, 1), "end_date": date(2024, 12, 31)},
        {"label": "w3", "start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31)},
    ]

    class FakeKiprisClient:
        def search_by_cpc(self, cpc_number, **params):
            calls.append((cpc_number, params))
            return {
                "items": [
                    {
                        "ApplicationNumber": "1020250000001",
                        "OpeningDate": "20250102",
                        "RegistrationDate": "20220102",
                        "RegistrationStatus": "등록",
                    },
                    {
                        "ApplicationNumber": "1020220000001",
                        "OpeningDate": "20220102",
                        "RegistrationDate": "20250102",
                        "RegistrationStatus": "등록",
                    },
                ]
            }

    monkeypatch.setattr(kipris_client, "KiprisClient", FakeKiprisClient)

    counts = collect_cpc_window_application_counts(
        "G06F 40/00",
        windows=windows,
        page_size=2,
        max_pages=5,
    )

    assert counts == [
        {"label": "w1", "start_date": "2023-01-01", "end_date": "2023-12-31", "count": 0},
        {"label": "w2", "start_date": "2024-01-01", "end_date": "2024-12-31", "count": 0},
        {"label": "w3", "start_date": "2025-01-01", "end_date": "2025-12-31", "count": 1},
    ]
    assert len(calls) == 1


def test_market_select_evidence_keeps_all_market_evidence():
    from agents.valuation_axes.market import select_evidence

    evidence = [
        {
            "evidence_id": f"news_{index}",
            "source_type": "news",
            "source": "naver_news",
            "title": f"시장 뉴스 {index}",
            "related_axes": ["market"],
        }
        for index in range(1, 9)
    ]
    state = PatentWorkflowState(evidence_bundle=evidence)

    selected = select_evidence(evidence, state)

    assert [item["evidence_id"] for item in selected] == [f"news_{index}" for index in range(1, 9)]


def test_technology_metrics_are_added_to_payload(monkeypatch):
    captured_payloads = []
    captured_prompts = []

    def fake_build_prompt(**kwargs):
        captured_payloads.append(kwargs["payload"])
        captured_prompts.append(kwargs)
        return "prompt"

    def fake_run_llm_required(**kwargs):
        return {
            "axis": "technology",
            "label": "기술성",
            "score": 70,
            "grade": "B",
            "rationale": "r",
            "evidence_ids": [],
            "risk_factors": [],
            "missing_information": [],
            "confidence": 0.7,
        }

    monkeypatch.setattr(
        "agents.valuation_axes.technology.build_similar_patent_context",
        lambda **kwargs: {
            "representative_cpc": "G05B 19/4065",
            "candidate_count": 3,
            "similar_patents": [{"application_number": "1020200000001"}],
            "warnings": [],
        },
    )

    from agents.valuation import AxisRuntime
    from agents.valuation_axes import technology

    state = PatentWorkflowState(
        preprocessed_patent={"metadata": {"cpc": ["G05B 19/4065"], "filing_date": "2024-01-01"}},
    )
    technology.run(
        state,
        AxisRuntime(
            build_prompt=fake_build_prompt,
            run_llm_required=fake_run_llm_required,
        ),
    )

    assert captured_payloads[0]["technology_metrics"]["representative_cpc"] == "G05B 19/4065"
    assert captured_payloads[0]["technology_metrics"]["similar_patents"][0]["application_number"] == "1020200000001"
    assert len(captured_payloads) == 1
    assert captured_prompts[0]["prompt_name"] == "valuation/valuation_technology.md"
    assert captured_prompts[0]["artifact_name"] == "technology_input"


def test_technology_metrics_always_prior_art_first_then_similar(monkeypatch):
    from agents.valuation_axes import technology

    def fake_prior_art_context(**kwargs):
        return {
            "comparison_mode": "prior-art",
            "candidate_count": 2,
            "similar_patents": [
                {"display_number": "KR-A", "source_type": "prior_art"},
                {"display_number": "JP-B", "source_type": "foreign_prior_art"},
            ],
            "prior_art_patents": [
                {"display_number": "KR-A", "source_type": "prior_art"},
                {"display_number": "JP-B", "source_type": "foreign_prior_art"},
            ],
            "warnings": [],
        }

    def fake_similar_context(**kwargs):
        return {
            "comparison_mode": "similar",
            "representative_cpc": "G05B 19/4065",
            "candidate_count": 10,
            "similar_patents": [
                {"application_number": "1020200000001", "source_type": "similar"},
                {"application_number": "1020200000002", "source_type": "similar"},
                {"application_number": "1020200000003", "source_type": "similar"},
                {"application_number": "1020200000004", "source_type": "similar"},
            ],
            "warnings": [],
        }

    monkeypatch.setattr(technology, "build_prior_art_context", fake_prior_art_context)
    monkeypatch.setattr(technology, "build_similar_context", fake_similar_context)

    state = PatentWorkflowState(
        user_input={"technology_comparison_mode": "similar"},
        preprocessed_patent={"metadata": {"cpc": ["G05B 19/4065"], "filing_date": "2024-01-01"}},
    )

    metrics = technology.build_technology_metrics(state)

    assert metrics["comparison_mode"] == "hybrid"
    assert metrics["selection_policy"] == "prior-art-first-then-similar"
    assert [item.get("display_number") or item.get("application_number") for item in metrics["similar_patents"]] == [
        "KR-A",
        "JP-B",
        "1020200000001",
        "1020200000002",
        "1020200000003",
    ]


def test_technology_metrics_payload_removes_duplicate_large_fields(monkeypatch):
    from agents.valuation_axes import technology

    def fake_prior_art_context(**kwargs):
        item = {
            "display_number": "KR-A",
            "source_type": "prior_art",
            "pdf_text": "원문 전체",
            "pdf_text_excerpt": "원문 전체",
            "similarity_text": "유사도 계산용 텍스트",
            "resolved_search_matches": [{"title": "검색 결과"}],
        }
        return {
            "comparison_mode": "prior-art",
            "candidate_count": 1,
            "similar_patents": [item],
            "prior_art_patents": [dict(item)],
            "warnings": [],
        }

    def fake_similar_context(**kwargs):
        return {
            "comparison_mode": "similar",
            "representative_cpc": "G05B 19/4065",
            "candidate_count": 1,
            "similar_patents": [
                {
                    "application_number": "1020200000001",
                    "pdf_text": "유사문헌 원문 전체",
                    "pdf_text_excerpt": "유사문헌 원문 전체",
                    "similarity_text": "유사도 계산용 텍스트",
                    "resolved_search_matches": [],
                }
            ],
            "warnings": [],
        }

    monkeypatch.setattr(technology, "build_prior_art_context", fake_prior_art_context)
    monkeypatch.setattr(technology, "build_similar_context", fake_similar_context)

    metrics = technology.build_technology_metrics(
        PatentWorkflowState(
            preprocessed_patent={"metadata": {"cpc": ["G05B 19/4065"], "filing_date": "2024-01-01"}},
        )
    )

    assert "prior_art_patents" not in metrics
    assert metrics["similar_patents"][0]["pdf_text"] == "원문 전체"
    assert all("pdf_text_excerpt" not in item for item in metrics["similar_patents"])
    assert all("similarity_text" not in item for item in metrics["similar_patents"])
    assert all("resolved_search_matches" not in item for item in metrics["similar_patents"])


def test_technology_metrics_prior_art_only_payload_omits_prior_art_duplicates(monkeypatch):
    from agents.valuation_axes import technology

    prior_items = [
        {
            "display_number": f"KR-{index}",
            "source_type": "prior_art",
            "pdf_text": f"원문 전체 {index}",
            "pdf_text_excerpt": f"원문 전체 {index}",
            "similarity_text": "유사도 계산용 텍스트",
            "resolved_search_matches": [{"title": "검색 결과"}],
        }
        for index in range(6)
    ]

    monkeypatch.setattr(
        technology,
        "build_prior_art_context",
        lambda **kwargs: {
            "comparison_mode": "prior-art",
            "candidate_count": 6,
            "similar_patents": prior_items,
            "prior_art_patents": [dict(item) for item in prior_items],
            "warnings": ["w"],
        },
    )

    metrics = technology.build_technology_metrics(PatentWorkflowState())

    assert metrics["selection_policy"] == "prior-art-only"
    assert len(metrics["similar_patents"]) == 5
    assert "prior_art_patents" not in metrics
    assert all("pdf_text_excerpt" not in item for item in metrics["similar_patents"])


def test_technology_candidate_subscores_use_60_40_structure():
    from agents.valuation_axes.technology import apply_technology_scores

    result = apply_technology_scores(
        {
            "score": 0,
            "grade": "LLM_GRADE",
            "rationale": "r",
            "evidence_ids": [],
            "risk_factors": [],
            "missing_information": [],
            "confidence": 0.8,
            "subscores": {
                "technical_differentiation": {
                    "label": "기술 차별성",
                    "score": 47,
                    "max_score": 60,
                    "details": {
                        "configuration_differentiation": 16,
                        "operation_differentiation": 20,
                        "effect_differentiation": 12,
                    },
                    "rationale": "차별 요소가 확인됨",
                },
                "implementation_specificity": {
                    "label": "구현 구체성",
                    "score": 30,
                    "max_score": 40,
                    "details": {
                        "component_specificity": 15,
                        "procedure_specificity": 15,
                        "implementation_specificity_detail": 0,
                    },
                    "rationale": "구성 요소와 처리 절차는 구체적이나 구현 설명은 제한적임",
                },
            },
        },
        {"similar_patents": [{"application_number": "1020200000001", "pdf_collected": True}]},
    )

    assert result["score"] == 78
    assert result["grade"] == "B"
    assert result["subscores"]["technical_differentiation"]["score"] == 48
    assert result["subscores"]["implementation_specificity"]["score"] == 30
    assert "technical_substantiality" not in result["subscores"]
    assert result["subscores"]["technical_differentiation"]["details"] == {
        "configuration_differentiation": 16,
        "operation_differentiation": 20,
        "effect_differentiation": 12,
    }
    assert result["subscores"]["implementation_specificity"]["details"] == {
        "component_specificity": 15,
        "procedure_specificity": 15,
        "implementation_specificity_detail": 0,
    }


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


def test_final_report_markdown_sanitizes_meta_note_lines():
    from agents.writing.final_report import sanitize_final_report_markdown

    markdown = sanitize_final_report_markdown(
        "\n".join(
            [
                "## 3.3 시장성",
                "",
                "- 대표 CPC 기준 출원 수가 연속 증가했습니다.",
                "(세부 점수 반영: 산업시장성 25점, 시장성 성장성 40점, 글로벌 사업성 0점)",
                "[참고 근거] KPMG 리포트는 산업 성장 근거를 제공합니다.",
                "- 해외 패밀리는 확인되지 않았습니다.",
            ]
        )
    )

    assert "세부 점수 반영" not in markdown
    assert "[참고 근거]" not in markdown
    assert "대표 CPC 기준 출원 수" in markdown
    assert "해외 패밀리는 확인되지 않았습니다" in markdown


def test_final_report_input_payload_is_compact_without_raw_technology_sources():
    from agents.writing.final_report import build_final_report_input_payload

    state = PatentWorkflowState(
        patent_structured={
            "management_number": "P1",
            "application_number": "10-2024-0000001",
            "registration_number": "10-3000001",
            "title_final": "문서변환 특허",
            "related_product": "문서변환 SW",
            "business_area": "AI",
            "technology_area": "문서처리",
            "status": "등록",
        },
        summary_result={"plain_summary": "특허 요약", "key_points": ["요약 포인트"]},
        evidence_bundle=[
            {
                "evidence_id": "news_001",
                "source": "naver_news",
                "source_type": "news",
                "title": "문서변환 SW 시장 확대",
                "url": "https://example.com/news",
                "published_at": "2026-01-01",
                "related_axes": ["market"],
                "compressed_summary": "시장 수요 확대",
                "key_facts": ["사실1", "사실2", "사실3", "사실4", "사실5", "사실6"],
                "content": "원문 본문은 final_report input에 들어가지 않는다.",
            },
            {
                "evidence_id": "news_unused",
                "source": "naver_news",
                "source_type": "news",
                "title": "평가축이 쓰지 않은 뉴스",
                "url": "https://example.com/unused",
                "published_at": "2026-01-02",
                "related_axes": ["market"],
                "compressed_summary": "쓰지 않은 뉴스 요약",
                "key_facts": ["쓰지 않은 사실"],
            }
        ],
    )
    valuation_result = {
        "total_score": 280,
        "average_score": 70,
        "recommendation": "유지 권고",
        "final_indicator": "조건부 유지",
        "final_report_markdown": "# 기존 보고서",
        "axes": {
            "legal": {
                "axis": "legal",
                "label": "권리성",
                "score": 70,
                "grade": "B",
                "rationale": "권리성 근거",
                "risk_factors": [],
                "missing_information": [],
                "confidence": 0.7,
            },
            "technology": {
                "axis": "technology",
                "label": "기술성",
                "score": 75,
                "grade": "B",
                "rationale": "기술성 근거",
                "risk_factors": ["유사특허 비교 필요"],
                "missing_information": [],
                "confidence": 0.7,
                "subscores": {
                    "technical_differentiation": {"score": 35, "max_score": 60},
                },
                "technology_metrics": {
                    "representative_cpc": "G06F 40/00",
                    "similar_patents": [
                        {
                            "application_number": "10-2020-0000001",
                            "pdf_text": "SECRET_FULL_SIMILAR_PATENT_TEXT",
                            "pdf_text_excerpt": "SECRET_EXCERPT",
                            "pdf_path": "/Users/soojeong/Documents/Team11/artifacts/similar.pdf",
                            "markdown_paths": ["/Users/soojeong/Documents/Team11/artifacts/similar.md"],
                        }
                    ],
                },
            },
            "market": {
                "axis": "market",
                "label": "시장성",
                "score": 65,
                "grade": "B",
                "rationale": "시장성 근거",
                "risk_factors": [],
                "missing_information": [],
                "confidence": 0.6,
                "evidence_ids": ["news_001"],
                "subscores": {
                    "industry_marketability": {"score": 30, "max_score": 40},
                },
                "marketability_metrics": {"cpc_application_counts": [{"year": 2025, "raw": "SECRET_RAW"}]},
            },
            "business_fit": {
                "axis": "business_fit",
                "label": "사업 연계성",
                "score": 70,
                "grade": "B",
                "rationale": "사업연계성 근거",
                "risk_factors": [],
                "missing_information": [],
                "confidence": 0.7,
            },
        },
    }

    payload = build_final_report_input_payload(state=state, valuation_result=valuation_result)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["patent"]["metadata"]["title"] == "문서변환 특허"
    assert payload["patent"]["metadata"]["application_number"] == "10-2024-0000001"
    assert payload["patent"]["metadata"]["registration_number"] == "10-3000001"
    assert payload["patent"]["summary_result"]["plain_summary"] == "특허 요약"
    for axis in ["legal", "technology", "market", "business_fit"]:
        assert payload["valuation_result"]["axes"][axis]["score"] == valuation_result["axes"][axis]["score"]
        assert payload["valuation_result"]["axes"][axis]["rationale"] == valuation_result["axes"][axis]["rationale"]
    assert payload["valuation_result"]["axes"]["market"]["evidence_ids"] == ["news_001"]
    assert [item["evidence_id"] for item in payload["evidence_references"]] == ["news_001"]
    assert payload["evidence_references"][0]["citation_title"] == "문서변환 SW 시장 확대"
    assert payload["evidence_references"][0]["url"] == "https://example.com/news"
    assert "compressed_summary" not in payload["evidence_references"][0]
    assert "key_facts" not in payload["evidence_references"][0]

    assert "technology_metrics" not in serialized
    assert "marketability_metrics" not in serialized
    assert "평가축이 쓰지 않은 뉴스" not in serialized
    assert "https://example.com/unused" not in serialized
    assert "쓰지 않은 뉴스 요약" not in serialized
    assert "시장 수요 확대" not in serialized
    assert "사실1" not in serialized
    assert "similar_patents" not in serialized
    assert "pdf_text" not in serialized
    assert "pdf_text_excerpt" not in serialized
    assert "pdf_path" not in serialized
    assert "markdown_paths" not in serialized
    assert "SECRET_FULL_SIMILAR_PATENT_TEXT" not in serialized
    assert "/Users/soojeong/Documents/Team11" not in serialized


def test_final_report_input_payload_ignores_structured_drawing_context(tmp_path):
    from agents.writing.final_report import build_final_report_input_payload

    run_dir = tmp_path / "run"
    markdown_path = run_dir / "patent_markdown" / "10-2021-0131424" / "1020210131424.md"
    image_path = markdown_path.parent / "1020210131424_images" / "imageFile4.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"")

    state = PatentWorkflowState(
        user_input={"artifact_dir": str(run_dir)},
        patent_structured={
            "title_final": "권리범위 설명 특허",
            "drawing_context": {
                "representative_drawing": {
                    "figure_number": "도4",
                    "image_path": "1020210131424_images/imageFile4.png",
                    "markdown_path": str(markdown_path),
                },
                "figure_description": "도 4는 청구항의 처리 흐름을 이해하는 데 참고가 되는 도면이다.",
            },
        },
        summary_result={"plain_summary": "요약"},
    )
    valuation_result = {
        "total_score": 300,
        "average_score": 75,
        "final_indicator": "조건부 유지",
        "axes": {},
    }

    payload = build_final_report_input_payload(state=state, valuation_result=valuation_result)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "rights_scope_context" not in payload["patent"]
    assert str(tmp_path) not in serialized
    assert "markdown_path" not in serialized


def test_final_report_input_payload_uses_preprocessed_drawing_context(tmp_path):
    from agents.writing.final_report import build_final_report_input_payload

    run_dir = tmp_path / "run"
    markdown_path = run_dir / "patent_markdown" / "10-2021-0131424" / "1020210131424.md"
    image_path = markdown_path.parent / "1020210131424_images" / "imageFile8.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"")

    state = PatentWorkflowState(
        user_input={"artifact_dir": str(run_dir)},
        patent_structured={"title_final": "권리범위 설명 특허"},
        preprocessed_patent={
            "drawing_context": {
                "representative_drawing": {
                    "figure_number": "도4",
                    "image_path": "1020210131424_images/imageFile8.png",
                    "markdown_path": str(markdown_path),
                },
                "figure_description": "도 4는 도면 섹션의 원본 이미지이다.",
                "representative_figure_detail": (
                    "도 4를 참조하면, 입력 문장을 토큰화하고(S410), 품사를 판별해 숫자 토큰을 추출한다(S420)."
                ),
            }
        },
        summary_result={"plain_summary": "요약"},
    )

    payload = build_final_report_input_payload(
        state=state,
        valuation_result={"total_score": 300, "average_score": 75, "axes": {}},
    )

    rights_scope = payload["patent"]["rights_scope_context"]
    assert rights_scope["representative_drawing"]["image_markdown"] == (
        "![권리범위 참고도 도4](../patent_markdown/10-2021-0131424/1020210131424_images/imageFile8.png)"
    )
    assert "도면 섹션의 원본 이미지" in rights_scope["figure_description"]
    assert "입력 문장을 토큰화하고(S410)" in rights_scope["representative_figure_detail"]


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
    assert "business_fit_context" in business_fit_input
    assert business_fit_input["business_fit_context"]["patent_description"]["title_final"] == "문서변환 특허"
    assert business_fit_input["business_fit_context"]["skax_official_evidence"] == []
    assert "business_fit_scoring_rubric" not in business_fit_input
    final_input = json.loads((input_dir / "final_report_input.json").read_text(encoding="utf-8"))
    assert final_input["patent"]["metadata"]["title"] == "문서변환 특허"
    assert final_input["evidence_references"][0]["title"] == "문서변환 SW 시장 확대"
    assert final_input["evidence_references"][0]["citation_title"] == "문서변환 SW 시장 확대"
    assert final_input["evidence_references"][0]["url"] == "https://example.com/news"
    assert "technology_metrics" not in final_input["valuation_result"]["axes"]["technology"]


def test_base_axis_input_excludes_claim_context_by_default(tmp_path):
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

    from agents.valuation_axes.payload_common import build_base_input_payload

    payload = build_base_input_payload(state=state, evidence=[])

    assert "representative_claims" not in payload["patent"]
    assert payload["patent"]["claim_context"] == {}
    assert payload["patent"]["claim_availability"]["claim_context_provided"] is False
    assert payload["patent"]["claim_availability"]["full_claims_provided"] is False


def test_legal_axis_input_includes_full_claim_context_without_representative_duplication(tmp_path):
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

    from agents.valuation_axes.legal import build_input_payload as build_legal_input_payload
    from agents.valuation_axes.market import build_input_payload as build_market_input_payload

    legal_payload = build_legal_input_payload(state=state, evidence=[])
    market_payload = build_market_input_payload(state=state, evidence=[])

    assert "representative_claims" not in legal_payload["patent"]
    assert "claims" not in legal_payload["patent"]
    assert [claim["claim_no"] for claim in legal_payload["patent"]["claim_context"]["independent_claims"]] == [1]
    assert [claim["claim_no"] for claim in legal_payload["patent"]["claim_context"]["dependent_claims"]] == [2]
    assert legal_payload["patent"]["claim_context"]["independent_claims"][0]["text"] == "독립항 전체 내용"
    assert legal_payload["patent"]["claim_context"]["dependent_claims"][0]["text"] == "종속항 전체 내용"
    assert legal_payload["patent"]["claim_context"]["all_claims_included"] is True
    assert legal_payload["patent"]["claim_availability"]["claim_context_provided"] is True
    assert legal_payload["patent"]["claim_availability"]["full_claims_provided"] is True
    assert market_payload["patent"]["claim_context"] == {}
    assert market_payload["patent"]["claim_availability"]["claim_context_provided"] is False
    assert market_payload["patent"]["claim_availability"]["full_claims_provided"] is False


def test_technology_axis_input_includes_all_independent_claims_only(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        preprocessed_patent={
            "claims": [
                {"claim_no": 1, "text": "독립항 1 전문", "is_independent": True, "dependency": None},
                {"claim_no": 2, "text": "종속항 2 전문", "is_independent": False, "dependency": 1},
                {"claim_no": 5, "text": "독립항 5 전문", "is_independent": True, "dependency": None},
                {"claim_no": 9, "text": "독립항 9 전문", "is_independent": True, "dependency": None},
                {"claim_no": 11, "text": "독립항 11 전문", "is_independent": True, "dependency": None},
            ]
        },
    )

    from agents.valuation_axes.technology import build_input_payload as build_technology_input_payload

    technology_payload = build_technology_input_payload(state=state, evidence=[])

    claim_context = technology_payload["patent"]["claim_context"]
    assert [claim["claim_no"] for claim in claim_context["independent_claims"]] == [1, 5, 9, 11]
    assert [claim["text"] for claim in claim_context["independent_claims"]] == [
        "독립항 1 전문",
        "독립항 5 전문",
        "독립항 9 전문",
        "독립항 11 전문",
    ]
    assert "dependent_claims" not in claim_context
    assert technology_payload["patent"]["claim_availability"]["claim_context_provided"] is True
    assert technology_payload["patent"]["claim_availability"]["full_claims_provided"] is False


def test_legal_axis_input_includes_prior_art_candidates(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        preprocessed_patent={
            "metadata": {
                "prior_art": ["KR10-1111111", "US2024-0000001A"],
            }
        },
    )

    from agents.valuation_axes.legal import build_input_payload as build_legal_input_payload
    from agents.valuation_axes.market import build_input_payload as build_market_input_payload

    legal_payload = build_legal_input_payload(state=state, evidence=[])
    market_payload = build_market_input_payload(state=state, evidence=[])

    assert legal_payload["patent"]["prior_art_candidates"] == ["KR10-1111111", "US2024-0000001A"]
    assert market_payload["patent"]["prior_art_candidates"] == []


def test_legal_axis_input_includes_citation_evidence(tmp_path):
    citation_evidence = {
        "kr_citation_documents": [
            {
                "direction": "cited_by_target",
                "country_code": "KR",
                "application_number": "1020200012345",
                "title": "선행 KR 특허",
                "abstract": "선행 KR 초록",
                "representative_claims": [
                    {"claim_no": index, "text": f"선행 독립항 {index}", "is_independent": True, "dependency": None}
                    for index in range(1, 8)
                ],
                "lookup_status": "resolved",
                "lookup_source": "kipris_bibliography_detail",
            }
        ],
        "kr_citing_documents": [
            {
                "direction": "citing_target",
                "country_code": "KR",
                "application_number": "1020117007865",
                "title": "후행 KR 특허",
                "abstract": "후행 KR 초록",
                "representative_claims": [{"claim_no": 1, "text": "후행 독립항", "is_independent": True, "dependency": None}],
                "lookup_status": "resolved",
                "lookup_source": "kipris_bibliography_detail",
            }
        ],
        "foreign_claim_lookup_candidates": [
            {
                "direction": "cited_by_target",
                "country_code": "JP",
                "document_number": "29047511",
                "kind_code": "A",
                "original_number": "JP2017047511 A",
                "display_number": "JP29047511 A",
                "lookup_source": "kipris_foreign_demand_paragraph",
            }
        ],
        "foreign_citation_documents": [
            {
                "direction": "cited_by_target",
                "country_code": "JP",
                "publication_number": "JP-2017047511-A",
                "title": "해외 선행 특허",
                "abstract": "해외 선행 초록",
                "representative_claims": [
                    {"claim_no": index, "text": f"foreign claim {index}", "is_independent": None, "dependency": None}
                    for index in range(1, 7)
                ],
                "lookup_status": "resolved",
                "lookup_source": "kipris_foreign_demand_paragraph",
            }
        ],
    }
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        citation_evidence=citation_evidence,
        kipris_api_data={"citing_stats": {"total_count": 2, "standardized_count": 1, "non_standardized_count": 1}},
    )

    from agents.valuation_axes.legal import build_input_payload as build_legal_input_payload
    from agents.valuation_axes.technology import build_input_payload as build_technology_input_payload

    legal_payload = build_legal_input_payload(state=state, evidence=[])
    technology_payload = build_technology_input_payload(state=state, evidence=[])

    citation_evidence = legal_payload["patent"]["citation_evidence"]
    assert citation_evidence["kr_citation_documents"][0]["application_number"] == "1020200012345"
    assert [claim["text"] for claim in citation_evidence["kr_citation_documents"][0]["representative_claims"]] == [
        "선행 독립항 1",
        "선행 독립항 2",
        "선행 독립항 3",
        "선행 독립항 4",
        "선행 독립항 5",
        "선행 독립항 6",
    ]
    assert "kr_citing_documents" not in citation_evidence
    assert citation_evidence["citing_signal"] == {
        "available": True,
        "total_count": 2,
        "standardized_count": 1,
        "non_standardized_count": 1,
        "used_for": "portfolio_defensive_value_only",
    }
    assert citation_evidence["foreign_citation_documents"][0]["publication_number"] == "JP-2017047511-A"
    assert [claim["text"] for claim in citation_evidence["foreign_citation_documents"][0]["representative_claims"]] == [
        "foreign claim 1",
        "foreign claim 2",
        "foreign claim 3",
        "foreign claim 4",
        "foreign claim 5",
    ]
    assert citation_evidence["foreign_claim_lookup_candidates"][0]["lookup_source"] == "kipris_foreign_demand_paragraph"
    assert legal_payload["patent"]["claim_availability"]["citation_evidence_provided"] is True
    assert technology_payload["patent"]["citation_evidence"] == {}


def test_attach_legal_context_preserves_prompt_scores_and_adds_input_context(tmp_path):
    from agents.valuation_axes.legal import attach_legal_context, build_input_payload

    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        patent_structured={"status": "등록"},
        preprocessed_patent={
            "claims": [
                {"claim_no": 1, "text": "핵심 독립항", "is_independent": True, "dependency": None},
                {"claim_no": 2, "text": "보완 종속항", "is_independent": False, "dependency": 1},
            ]
        },
        kipris_api_data={"citing_stats": {"total_count": 3, "standardized_count": 2, "non_standardized_count": 1}},
        kipris_family_patents=[
            {"country_code": "KR", "registration_number": "1020000000000"},
            {"country_code": "US", "registration_number": "1234567", "kind_code": "B2"},
        ],
    )
    payload = build_input_payload(state=state, evidence=[])
    result = {
        "axis": "legal",
        "label": "권리성",
        "score": 90,
        "grade": "B",
        "rationale": "권리성 평가",
        "evidence_ids": [],
        "risk_factors": [],
        "missing_information": [],
        "confidence": 0.9,
        "subscores": {
            "right_stability": {
                "score": 29,
                "max_score": 35,
                "rationale": "차별 구성이 확인됩니다.",
                "details": {
                    "prior_art_overlap": {"score": 25, "rationale": "직접 중복이 낮습니다."},
                    "claim_structure_stability": {"score": 4, "rationale": "독립항과 종속항 1개가 확인됩니다."},
                },
            },
            "claim_protection": {
                "score": 36,
                "max_score": 40,
                "rationale": "핵심 해결수단이 청구항에 반영됩니다.",
                "details": {
                    "core_solution_coverage": {"score": 12, "rationale": "핵심 구성이 반영됩니다."},
                    "independent_claim_scope": {"score": 8, "rationale": "일부 구현 조건이 있습니다."},
                    "dependent_claim_support": {"score": 10, "rationale": "여러 보완 유형이 확인됩니다."},
                    "claim_type_diversity": {"score": 6, "rationale": "여러 보호형태가 확인됩니다."},
                },
            },
            "portfolio_defensive_value": {
                "score": 25,
                "max_score": 25,
                "rationale": "관련 특허군과 보완 관계가 있습니다.",
                "details": {
                    "portfolio_connection_coverage": {"score": 15, "rationale": "구체적 연결성과 별도 보호 포인트가 있습니다."},
                    "overseas_right_coverage": {"score": 6, "rationale": "주요 권역 해외 등록이 확인됩니다."},
                    "follow_on_right_signal": {"score": 4, "rationale": "피인용 3건 이상입니다."},
                },
            },
        },
    }

    scored = attach_legal_context(result, payload=payload, state=state)

    assert scored["score"] == 90
    assert scored["grade"] == "B"
    assert scored["subscores"]["right_stability"]["score"] == 29
    assert scored["legal_context"]["right_status_gate"]["label"] == "registered_or_active"
    assert scored["subscores"]["claim_protection"]["score"] == 36
    assert scored["subscores"]["portfolio_defensive_value"]["score"] == 25
    assert scored["subscores"]["portfolio_defensive_value"]["details"]["follow_on_right_signal"]["score"] == 4


def test_legal_axis_input_falls_back_to_kipris_api_citation_evidence(tmp_path):
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path), "no_save": True},
        kipris_api_data={
            "citation_evidence": {
                "kr_citation_documents": [
                    {
                        "direction": "cited_by_target",
                        "country_code": "KR",
                        "application_number": "1020200012345",
                        "representative_claims": [{"claim_no": 1, "text": "선행 독립항"}],
                    }
                ],
            }
        },
    )

    from agents.valuation_axes.legal import build_input_payload

    legal_payload = build_input_payload(state=state, evidence=[])

    assert legal_payload["patent"]["citation_evidence"]["kr_citation_documents"][0]["application_number"] == "1020200012345"


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
