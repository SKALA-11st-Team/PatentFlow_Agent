from __future__ import annotations

from typing import Any

from schemas.valuation import DEFAULT_GRADE_CUTOFFS


# @author 배세은
# @date 2026-05-19
# @relatedFR FR-006, FR-007
# @relatedUI UI-005
# @description 4축 평가 공용 유틸. source_type 기반 축별 근거 라우팅과 점수→등급(A/B/C) 변환을 제공한다.
# 등급 컷오프는 운영 설정(valuationConfig.gradeCutoffs)으로 재정의 가능하며 기본 70/50이다.
def select_by_source_types(
    items: list[dict[str, Any]],
    *,
    source_types: set[str],
    limit: int | None = 5,
) -> list[dict[str, Any]]:
    """축별 근거 선택은 source_type만으로 결정한다.

    예전에는 압축 단계가 붙인 related_axes 태그로도 끌어왔는데, 그 때문에
    뉴스가 기술성/권리성 축으로 새는 문제가 있었다. 라우팅은 source_type만
    본다: 뉴스(source_type="news")는 시장성 축에만, SK AX 공식 콘텐츠
    (company_disclosure)는 사업연계성 축에서만 자체 선택한다.
    """
    selected = [item for item in items if item.get("source_type") in source_types]
    if limit is None:
        return selected
    return selected[: max(0, int(limit))]


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def grade_for_score(score: int | float, cutoffs: dict[str, float] | None = None) -> str:
    # 등급은 A·B 두 경계로만 나뉘고 그 밑은 모두 C다(D 없음). cutoffs는 운영 설정
    # (valuationConfig.gradeCutoffs)으로 재정의 가능, 미지정 시 기본 70/50.
    # 구(舊) 3키 컷오프({A,B,C})가 들어와도 C 키는 무시되어 안전하다.
    resolved = cutoffs or DEFAULT_GRADE_CUTOFFS
    if score >= resolved.get("A", DEFAULT_GRADE_CUTOFFS["A"]):
        return "A"
    if score >= resolved.get("B", DEFAULT_GRADE_CUTOFFS["B"]):
        return "B"
    return "C"
