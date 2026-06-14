"""ORCH-10 Part A — 순수 산식 골든 회귀 테스트.

LLM/외부 호출 없이 결정적 산식만 단언한다(langgraph 불요).
기대값 출처: 코드 컷오프 — grade_for_score(80/60/40), score_to_final_recommendation(60).
이후 VAL-03/10·VAL-02·VAL-07·VAL-06 정정의 합산·등급·recommendation 회귀를 잡는 안전망.
"""

import pytest

from agents.valuation import build_final_valuation_result, score_to_final_recommendation
from agents.valuation_axes.common import grade_for_score


def _axis(name: str, label: str, score: int, *, missing_information=None) -> dict:
    axis = {
        "axis": name,
        "label": label,
        "score": score,
        "grade": grade_for_score(score),
        "rationale": f"{label} 근거",
        "confidence": 0.7,
    }
    if name == "technology":
        axis["technology_metrics"] = {"target_count": 5, "similar_patents": []}
    if missing_information is not None:
        axis["missing_information"] = missing_information
    return axis


def _axes(legal: int, technology: int, market: int, business_fit: int, *, market_missing=None) -> dict:
    return {
        "legal": _axis("legal", "권리성", legal),
        "technology": _axis("technology", "기술성", technology),
        "market": _axis("market", "시장성", market, missing_information=market_missing),
        "business_fit": _axis("business_fit", "사업 연계성", business_fit),
    }


@pytest.mark.parametrize(
    "score,grade",
    [(0, "D"), (39, "D"), (40, "C"), (59, "C"), (60, "B"), (79, "B"), (80, "A"), (100, "A")],
)
def test_grade_for_score_boundary_cutoffs(score, grade):
    assert grade_for_score(score) == grade


@pytest.mark.parametrize(
    "average,expected",
    [(59.9, "포기 검토"), (60.0, "유지 권고"), (60.1, "유지 권고"), (0.0, "포기 검토"), (100.0, "유지 권고")],
)
def test_score_to_final_recommendation_60_cutoff(average, expected):
    assert score_to_final_recommendation(average) == expected


# 종합 점수는 권리성·기술성·시장성 3축 합산(0~300)·평균이며, 사업 연계성(≥60)은
# AI 권고를 "유지 권고"로 끌어올리는 오버라이드로만 작용한다.
@pytest.mark.parametrize(
    "scores,total,avg,grade,recommendation",
    [
        ((90, 90, 90, 90), 270, 90.0, "A", "유지 권고"),
        ((50, 50, 50, 50), 150, 50.0, "C", "포기 검토"),
        # 평균 70은 본래 임계 미만이지만 business_fit 70(≥60) 오버라이드로 "유지 권고".
        ((70, 75, 65, 70), 210, 70.0, "B", "유지 권고"),
    ],
)
def test_build_final_valuation_result_golden_table(scores, total, avg, grade, recommendation):
    result = build_final_valuation_result(_axes(*scores))
    assert result["total_score"] == total
    assert result["total_score_max"] == 300
    assert result["average_score"] == avg
    assert result["final_grade"] == grade
    assert result["recommendation"] == recommendation


def test_missing_information_overrides_recommendation_to_additional_info():
    # 평균 70(원래 '유지 권고')이라도 한 축에 missing_information이 있으면 recommendation을 '추가 정보 필요'로 오버라이드.
    result = build_final_valuation_result(_axes(70, 70, 70, 70, market_missing=["시장 규모 자료"]))
    assert result["average_score"] == 70.0
    assert result["recommendation"] == "추가 정보 필요"
    assert "시장 규모 자료" in result["missing_information"]
    assert "부족 정보 보완 후 최종 판단 재검토" in result["required_actions"]
