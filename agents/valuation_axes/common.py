from __future__ import annotations

from typing import Any


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


def grade_for_score(score: int | float) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"
