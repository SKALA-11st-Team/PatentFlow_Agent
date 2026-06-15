"""valuationConfig(운영 설정 가치평가 기준) 적용 검증.

계약 C1: BE가 evaluate 요청에 valuationConfig를 실으면 축 가중치/등급 컷오프/유지 임계/
subscore 배점이 재정의되고, 누락 시 기존 하드코딩 값과 동일하게 동작해야 한다.
"""

import pytest

from agents.valuation import (
    build_axis_prompt,
    build_final_valuation_result,
    score_to_final_recommendation,
    valuation_config_from_state,
)
from agents.valuation_axes.business_fit import reconcile_business_fit_scores
from agents.valuation_axes.common import grade_for_score
from agents.valuation_axes.legal import reconcile_legal_scores
from schemas.valuation import (
    DEFAULT_AXIS_WEIGHTS,
    DEFAULT_GRADE_CUTOFFS,
    DEFAULT_SUBSCORE_WEIGHTS,
    is_default_valuation_config,
    resolve_valuation_config,
)
from workflow.state import PatentWorkflowState


def axis(name: str, label: str, score: int) -> dict:
    return {
        "axis": name,
        "label": label,
        "score": score,
        "grade": "B",
        "rationale": f"{label} 근거",
        "confidence": 0.7,
    }


def four_axes(legal=80, technology=60, market=40, business_fit=20) -> dict:
    return {
        "legal": axis("legal", "권리성", legal),
        "technology": axis("technology", "기술성", technology),
        "market": axis("market", "시장성", market),
        "business_fit": axis("business_fit", "사업 연계성", business_fit),
    }


def test_resolve_valuation_config_defaults_when_absent():
    resolved = resolve_valuation_config(None)

    assert resolved["axisWeights"] == DEFAULT_AXIS_WEIGHTS
    assert resolved["gradeCutoffs"] == DEFAULT_GRADE_CUTOFFS
    assert resolved["gradeCutoffs"] == {"A": 70.0, "B": 50.0}
    assert resolved["subscoreWeights"] == DEFAULT_SUBSCORE_WEIGHTS
    assert resolved["source"] == "default"
    assert is_default_valuation_config(resolved)


def test_resolve_valuation_config_accepts_partial_request():
    resolved = resolve_valuation_config({"axisWeights": {"market": 40}})

    assert resolved["axisWeights"]["market"] == 40.0
    assert resolved["axisWeights"]["legal"] == 25.0
    assert resolved["source"] == "request"
    assert not is_default_valuation_config(resolved)


def test_resolve_valuation_config_rejects_invalid_values():
    resolved = resolve_valuation_config(
        {
            "axisWeights": {"legal": -5, "unknown_axis": 50},
            "gradeCutoffs": {"A": 30, "B": 60},  # A<B → 순서 깨짐 → 기본 컷오프 폴백
            "businessFitOverrideThreshold": 250,  # 0~100 클램프
            "subscoreWeights": {"legal": {"right_stability": -1}},
        }
    )

    assert resolved["axisWeights"]["legal"] == 25.0
    assert "unknown_axis" not in resolved["axisWeights"]
    assert resolved["gradeCutoffs"] == DEFAULT_GRADE_CUTOFFS
    assert resolved["businessFitOverrideThreshold"] == 100.0
    assert resolved["subscoreWeights"]["legal"]["right_stability"] == 40


def test_grade_for_score_uses_custom_cutoffs():
    cutoffs = {"A": 90, "B": 70}

    assert grade_for_score(85, cutoffs) == "B"
    assert grade_for_score(85) == "A"
    assert grade_for_score(49, cutoffs) == "C"  # B컷 미만은 모두 C(D 없음)


def test_score_to_final_recommendation_three_tier_cutoffs():
    # 권고는 등급에서 1:1 파생된다(기본 A≥70 / B≥50 / C<50 → 유지/조건부/포기).
    assert score_to_final_recommendation(70) == "유지 권고"
    assert score_to_final_recommendation(50) == "조건부 유지"
    assert score_to_final_recommendation(49) == "포기 검토"
    # 등급 컷오프(gradeCutoffs)를 바꾸면 권고 경계도 따라 움직인다(단일 기준).
    assert score_to_final_recommendation(60, cutoffs={"A": 55, "B": 50}) == "유지 권고"
    assert score_to_final_recommendation(52, cutoffs={"A": 70, "B": 55}) == "포기 검토"


def test_build_final_valuation_result_defaults_match_legacy_equal_average():
    result = build_final_valuation_result(four_axes(70, 70, 70, 70))

    # 종합 점수는 권리성·기술성·시장성 3축 합산(0~300)·평균이다.
    assert result["total_score"] == 210
    assert result["average_score"] == 70.0
    assert result["final_grade"] == "A"
    assert result["recommendation"] == "유지 권고"
    assert result["applied_config"]["source"] == "default"
    assert "종합환산점수는" in result["decision_rationale"][0]
    assert "가중치" not in result["decision_rationale"][0]
    assert "/300" not in result["decision_rationale"][0]


def test_build_final_valuation_result_applies_weighted_average_and_cutoffs():
    config = resolve_valuation_config(
        {
            "axisWeights": {"legal": 70, "technology": 10, "market": 10, "business_fit": 10},
            "gradeCutoffs": {"A": 75, "B": 60},
        }
    )

    result = build_final_valuation_result(four_axes(80, 60, 40, 20), config=config)

    # 가중 평균은 core 3축만 반영 = (70*80 + 10*60 + 10*40) / 90 = 73.3 (균등 평균이면 60.0)
    assert result["average_score"] == 73.3
    # total_score는 가중치와 무관하게 core 3축 단순 합을 유지한다.
    assert result["total_score"] == 180
    assert result["final_grade"] == "B"  # 73.3 < A컷 75, ≥ B컷 60 → B
    assert result["recommendation"] == "조건부 유지"  # 등급 B에서 1:1 파생
    assert result["applied_config"]["source"] == "request"
    assert "종합환산점수는" in result["decision_rationale"][0]
    assert "가중치" in result["decision_rationale"][0]
    # 축 등급도 설정 컷오프로 재산정된다(80점, A컷 75 → A).
    assert result["axes"]["legal"]["grade"] == "A"


def test_grade_cutoffs_drive_recommendation_boundary():
    # 등급 컷오프가 권고 경계를 결정한다(단일 기준). A컷을 65로 낮추면 평균 70이 A→유지 권고.
    config = resolve_valuation_config({"gradeCutoffs": {"A": 65, "B": 40}})
    result = build_final_valuation_result(four_axes(70, 70, 70, 30), config=config)
    assert result["final_grade"] == "A"
    assert result["business_fit_override"] is False  # business_fit 30 < 60
    assert result["recommendation"] == "유지 권고"


def test_build_final_valuation_result_business_fit_override_wins_over_grade():
    # 3축 평균은 B(조건부)지만 사업 연계성이 기준(60) 이상이면 권고를 유지로 끌어올린다.
    result = build_final_valuation_result(four_axes(55, 55, 55, 70))

    assert result["final_grade"] == "B"  # 평균 55 → B
    assert result["business_fit_override"] is True  # business_fit 70 ≥ 60
    assert "final_indicator" not in result
    assert result["recommendation"] == "유지 권고"  # 등급 파생(조건부)을 오버라이드


def test_business_fit_override_threshold_is_configurable():
    # 오버라이드 기준점은 운영 설정(FE)에서 조정 가능하다. 90으로 올리면 business_fit 70은 미발동.
    config = resolve_valuation_config({"businessFitOverrideThreshold": 90})
    result = build_final_valuation_result(four_axes(55, 55, 55, 70), config=config)

    assert result["business_fit_override"] is False
    assert result["recommendation"] == "조건부 유지"  # 등급 B 그대로


def test_reconcile_legal_scores_uses_configured_subscore_max():
    from agents.valuation_axes.legal import reconcile_legal_scores

    state = PatentWorkflowState(
        user_input={
            "valuation_config": resolve_valuation_config(
                {"subscoreWeights": {"legal": {"right_stability": 50, "claim_protection": 30, "portfolio_defensive_value": 20}}}
            )
        },
        patent_structured={"country": "KR"},
    )
    # 세부지표(details)는 프롬프트 본문 기본 배점(40/40/20) 스케일로 보고된다.
    perfect = {
        "subscores": {
            "right_stability": {
                "score": 40,
                "details": {
                    "prior_art_overlap": {"score": 20},
                    "independent_claim_clarity": {"score": 20},
                },
            },
            "claim_protection": {
                "score": 40,
                "details": {
                    "core_solution_coverage": {"score": 15},
                    "independent_claim_scope": {"score": 15},
                    "dependent_claim_support": {"score": 7},
                    "infringement_detectability": {"score": 3},
                },
            },
            "portfolio_defensive_value": {
                "score": 20,
                "details": {
                    "portfolio_connection_coverage": {"score": 12},
                    "follow_on_right_signal": {"score": 4},
                    "overseas_right_coverage": {"score": 4},
                },
            },
        }
    }

    reconciled = reconcile_legal_scores(perfect, state=state)

    # 만점 특허는 설정 배점으로 비례 변환되어 50+30+20=100점이어야 한다.
    assert reconciled["subscores"]["right_stability"]["score"] == 50
    assert reconciled["subscores"]["right_stability"]["max_score"] == 50
    assert reconciled["subscores"]["claim_protection"]["score"] == 30
    assert reconciled["subscores"]["portfolio_defensive_value"]["score"] == 20
    assert reconciled["score"] == 100

    # 기본 배점(40/40/20)에서는 변환이 일어나지 않는다.
    default_reconciled = reconcile_legal_scores(
        perfect, state=PatentWorkflowState(patent_structured={"country": "KR"})
    )
    assert default_reconciled["subscores"]["right_stability"]["score"] == 40
    assert default_reconciled["score"] == 100


def test_reconcile_business_fit_scores_uses_configured_subscore_max():
    state = PatentWorkflowState(
        user_input={
            "valuation_config": resolve_valuation_config(
                {
                    "subscoreWeights": {
                        "business_fit": {
                            "official_business_evidence": 50,
                            "product_function_direct_match": 30,
                            "business_context_fit": 20,
                        }
                    }
                }
            )
        }
    )
    result = {
        "subscores": {
            "official_business_evidence": {"score": 50},
            "product_function_direct_match": {"score": 45},  # 만점 30으로 캡
            "business_context_fit": {"score": 10},
        }
    }

    reconciled = reconcile_business_fit_scores(result, state=state)

    assert reconciled["subscores"]["product_function_direct_match"]["score"] == 30
    assert reconciled["score"] == 90  # 50+30+10

    # state 미전달(레거시 호출) 시 기본 배점과 동일하게 동작한다.
    legacy = reconcile_business_fit_scores(
        {
            "subscores": {
                "official_business_evidence": {"score": 30},
                "product_function_direct_match": {"score": 45},
                "business_context_fit": {"score": 25},
            }
        }
    )
    assert legacy["score"] == 100


def test_build_axis_prompt_appends_override_block_only_when_config_differs(tmp_path):
    base_kwargs = {
        "prompt_name": "valuation/valuation_legal.md",
        "payload": {"patent": {}},
        "artifact_name": "legal_input",
        "axis": "legal",
    }
    default_state = PatentWorkflowState(user_input={"no_save": True})
    configured_state = PatentWorkflowState(
        user_input={
            "no_save": True,
            "valuation_config": resolve_valuation_config(
                {"subscoreWeights": {"legal": {"right_stability": 50, "claim_protection": 30, "portfolio_defensive_value": 20}}}
            ),
        }
    )

    default_prompt = build_axis_prompt(state=default_state, **base_kwargs)
    configured_prompt = build_axis_prompt(state=configured_state, **base_kwargs)

    assert "배점 재정의" not in default_prompt
    assert "배점 재정의" in configured_prompt
    assert "권리안정성(right_stability): 40점 → 50점" in configured_prompt
    assert "subscore 만점 합계: 100점" in configured_prompt


def test_valuation_config_from_state_falls_back_to_default():
    assert valuation_config_from_state(PatentWorkflowState(user_input={}))["source"] == "default"

    configured = PatentWorkflowState(
        user_input={"valuation_config": resolve_valuation_config({"businessFitOverrideThreshold": 70})}
    )
    assert valuation_config_from_state(configured)["businessFitOverrideThreshold"] == 70.0


def test_api_request_and_response_carry_valuation_config():
    from app.api import PatentEvaluationRequest, PatentEvaluationResponse, build_api_user_input

    request = PatentEvaluationRequest(
        managementNumber="P2024-001",
        noSave=True,
        valuationConfig={"axisWeights": {"market": 40}},
    )
    user_input = build_api_user_input("PAT-001", request)

    assert user_input["valuation_config"]["axisWeights"]["market"] == 40.0
    assert user_input["valuation_config"]["source"] == "request"

    # valuationConfig 누락(구 BE) → 기본값으로 평가.
    legacy_request = PatentEvaluationRequest(managementNumber="P2024-001", noSave=True)
    legacy_input = build_api_user_input("PAT-001", legacy_request)
    assert legacy_input["valuation_config"]["source"] == "default"
    assert is_default_valuation_config(legacy_input["valuation_config"])

    # 응답 모델은 appliedValuationConfig를 그대로 노출한다.
    fields = PatentEvaluationResponse.model_fields
    assert "appliedValuationConfig" in fields


def test_final_grade_for_average_uses_shared_helper_with_cutoffs():
    from app.api import final_grade_for_average

    assert final_grade_for_average(None) is None
    assert final_grade_for_average(70.0) == "A"
    assert final_grade_for_average(60.0, {"gradeCutoffs": {"A": 80, "B": 50}}) == "B"
