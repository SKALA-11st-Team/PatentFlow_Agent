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
from agents.valuation_axes.legal import (
    legal_detail_max_map,
    legal_subscore_max_map,
    reconcile_legal_scores,
)
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
    assert resolved["maintainThreshold"] == 60.0
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
            "gradeCutoffs": {"A": 30, "B": 60, "C": 40},  # A<B → 순서 깨짐 → 기본 컷오프 폴백
            "maintainThreshold": 250,
            "subscoreWeights": {"legal": {"right_stability": -1}},
        }
    )

    assert resolved["axisWeights"]["legal"] == 25.0
    assert "unknown_axis" not in resolved["axisWeights"]
    assert resolved["gradeCutoffs"] == DEFAULT_GRADE_CUTOFFS
    assert resolved["maintainThreshold"] == 100.0
    assert resolved["subscoreWeights"]["legal"]["right_stability"] == 35


def test_grade_for_score_uses_custom_cutoffs():
    cutoffs = {"A": 90, "B": 70, "C": 50}

    assert grade_for_score(85, cutoffs) == "B"
    assert grade_for_score(85) == "A"
    assert grade_for_score(49, cutoffs) == "D"


def test_score_to_final_recommendation_uses_threshold():
    assert score_to_final_recommendation(55, threshold=50) == "유지 권고"
    assert score_to_final_recommendation(55) == "포기 검토"


def test_build_final_valuation_result_defaults_match_legacy_equal_average():
    result = build_final_valuation_result(four_axes(70, 70, 70, 70))

    # 종합 점수는 권리성·기술성·시장성 3축 합산(0~300)·평균이다.
    assert result["total_score"] == 210
    assert result["average_score"] == 70.0
    assert result["final_grade"] == "B"
    assert result["recommendation"] == "유지 권고"
    assert result["applied_config"]["source"] == "default"
    assert "평균 점수는" in result["decision_rationale"][0]
    assert "가중치" not in result["decision_rationale"][0]


def test_build_final_valuation_result_applies_weighted_average_and_cutoffs():
    config = resolve_valuation_config(
        {
            "axisWeights": {"legal": 70, "technology": 10, "market": 10, "business_fit": 10},
            "gradeCutoffs": {"A": 75, "B": 60, "C": 40},
            "maintainThreshold": 65,
        }
    )

    result = build_final_valuation_result(four_axes(80, 60, 40, 20), config=config)

    # 가중 평균은 core 3축만 반영 = (70*80 + 10*60 + 10*40) / 90 = 73.3 (균등 평균이면 60.0)
    assert result["average_score"] == 73.3
    # total_score는 가중치와 무관하게 core 3축 단순 합을 유지한다.
    assert result["total_score"] == 180
    assert result["final_grade"] == "B"
    assert result["recommendation"] == "유지 권고"  # 73.3 >= 65
    assert result["applied_config"]["source"] == "request"
    assert "가중 평균" in result["decision_rationale"][0]
    # 축 등급도 설정 컷오프로 재산정된다(80점, A컷 75 → A).
    assert result["axes"]["legal"]["grade"] == "A"


def test_build_final_valuation_result_threshold_flips_recommendation():
    config = resolve_valuation_config({"maintainThreshold": 75})

    # business_fit < 60(오버라이드 없음)에서 임계만으로 권고가 뒤집히는지 본다.
    result = build_final_valuation_result(four_axes(70, 70, 70, 50), config=config)

    assert result["average_score"] == 70.0
    assert result["recommendation"] == "포기 검토"


def test_build_final_valuation_result_business_fit_override_wins_over_threshold():
    config = resolve_valuation_config({"maintainThreshold": 75})

    # business_fit ≥ 60이면 임계 미달이어도 AI 권고를 유지 권고로 본다(제품 정책 오버라이드).
    result = build_final_valuation_result(four_axes(70, 70, 70, 70), config=config)

    assert result["business_fit_override"] is True
    assert "final_indicator" not in result
    assert result["recommendation"] == "유지 권고"


def test_legal_subscore_max_map_reads_state_config():
    state = PatentWorkflowState(
        user_input={
            "valuation_config": resolve_valuation_config(
                {"subscoreWeights": {"legal": {"right_stability": 50, "claim_protection": 30, "portfolio_defensive_value": 20}}}
            )
        }
    )

    assert legal_subscore_max_map(state) == {
        "right_stability": 50,
        "claim_protection": 30,
        "portfolio_defensive_value": 20,
    }
    assert legal_subscore_max_map(PatentWorkflowState(user_input={})) == DEFAULT_SUBSCORE_WEIGHTS["legal"]


def test_legal_detail_max_scales_with_right_stability_weight():
    scaled = legal_detail_max_map({"right_stability": 50})

    # 25/35 비율 유지 + right_stability 하위 두 지표의 합계가 정확히 만점.
    # follow_on_right_signal은 portfolio_defensive_value 하위라 스케일하지 않는다.
    assert scaled["prior_art_overlap"] == 36
    assert scaled["claim_structure_stability"] == 14
    assert scaled["prior_art_overlap"] + scaled["claim_structure_stability"] == 50
    assert scaled["follow_on_right_signal"] == 4
    assert legal_detail_max_map({"right_stability": 35}) == {
        "prior_art_overlap": 25,
        "claim_structure_stability": 10,
        "follow_on_right_signal": 4,
    }


def test_reconcile_legal_scores_rescales_detail_sums_to_configured_max():
    """세부지표(details)는 프롬프트 본문(기본 배점 35/40/25) 기준으로 보고되므로,
    설정 배점이 다르면 detail 부분합을 비례 변환해야 한다(리뷰 HIGH 회귀 테스트).
    만점 특허 + 50/30/20 설정 → 변환 없으면 (35+40+25)/100=100이 아니라 85가 됐다."""
    state = PatentWorkflowState(
        user_input={
            "valuation_config": resolve_valuation_config(
                {"subscoreWeights": {"legal": {"right_stability": 50, "claim_protection": 30, "portfolio_defensive_value": 20}}}
            )
        },
        patent_structured={"country": "KR"},
    )
    perfect = {
        "subscores": {
            "right_stability": {
                "score": 35,
                "details": {
                    "prior_art_overlap": {"score": 25},
                    "claim_structure_stability": {"score": 10},
                },
            },
            "claim_protection": {
                "score": 40,
                "details": {
                    "core_solution_coverage": {"score": 12},
                    "independent_claim_scope": {"score": 12},
                    "dependent_claim_support": {"score": 10},
                    "claim_type_diversity": {"score": 6},
                },
            },
            "portfolio_defensive_value": {
                "score": 25,
                "details": {
                    "portfolio_connection_coverage": {"score": 15},
                    "overseas_right_coverage": {"score": 6},
                    "follow_on_right_signal": {"score": 4},
                },
            },
        }
    }

    reconciled = reconcile_legal_scores(perfect, state=state)

    # 만점은 설정 배점으로 변환되어 50+30+20=100점이어야 한다.
    assert reconciled["subscores"]["right_stability"]["score"] == 50
    assert reconciled["subscores"]["claim_protection"]["score"] == 30
    assert reconciled["subscores"]["portfolio_defensive_value"]["score"] == 20
    assert reconciled["score"] == 100

    # 기본 배점에서는 변환이 일어나지 않는다(기존 동작 보존).
    default_state = PatentWorkflowState(user_input={}, patent_structured={"country": "KR"})
    legacy = reconcile_legal_scores(perfect, state=default_state)
    assert legacy["score"] == 100
    assert legacy["subscores"]["right_stability"]["score"] == 35


def test_build_final_valuation_result_honors_zero_maintain_threshold():
    """maintainThreshold=0(항상 유지 권고)이 `or 60`으로 무시되던 버그 회귀 테스트."""
    config = resolve_valuation_config({"maintainThreshold": 0})

    result = build_final_valuation_result(four_axes(10, 10, 10, 10), config=config)

    assert result["recommendation"] == "유지 권고"


def test_reconcile_legal_scores_uses_configured_subscore_max():
    state = PatentWorkflowState(
        user_input={
            "valuation_config": resolve_valuation_config(
                {"subscoreWeights": {"legal": {"right_stability": 20, "claim_protection": 60, "portfolio_defensive_value": 20}}}
            )
        },
        patent_structured={"country": "KR"},
    )
    result = {
        "subscores": {
            "right_stability": {"score": 35},  # 만점 20으로 캡
            "claim_protection": {"score": 60},
            "portfolio_defensive_value": {"score": 10},
        }
    }

    reconciled = reconcile_legal_scores(result, state=state)

    assert reconciled["subscores"]["right_stability"]["score"] == 20
    assert reconciled["subscores"]["right_stability"]["max_score"] == 20
    assert reconciled["subscores"]["claim_protection"]["max_score"] == 60
    # (20+60+10)/100 = 90
    assert reconciled["score"] == 90


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
    assert "권리안정성(right_stability): 35점 → 50점" in configured_prompt
    assert "subscore 만점 합계: 100점" in configured_prompt


def test_valuation_config_from_state_falls_back_to_default():
    assert valuation_config_from_state(PatentWorkflowState(user_input={}))["source"] == "default"

    configured = PatentWorkflowState(
        user_input={"valuation_config": resolve_valuation_config({"maintainThreshold": 70})}
    )
    assert valuation_config_from_state(configured)["maintainThreshold"] == 70.0


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
    assert final_grade_for_average(70.0) == "B"
    assert final_grade_for_average(70.0, {"gradeCutoffs": {"A": 70, "B": 50, "C": 30}}) == "A"
