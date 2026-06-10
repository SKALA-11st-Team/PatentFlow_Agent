from __future__ import annotations

from typing import Any


def select_by_types_or_axes(
    items: list[dict[str, Any]],
    *,
    source_types: set[str],
    axes: set[str],
    limit: int | None = 5,
) -> list[dict[str, Any]]:
    selected = []
    for item in items:
        item_axes = set(item.get("related_axes") or item.get("related_axis") or [])
        if item.get("source_type") in source_types or item_axes.intersection(axes):
            selected.append(item)
    if limit is None:
        return selected
    return selected[: max(0, int(limit))]


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def grade_for_score(score: int | float, cutoffs: dict[str, float] | None = None) -> str:
    # cutoffs는 운영 설정(valuationConfig.gradeCutoffs)으로 재정의 가능. 미지정 시 기존 80/60/40.
    resolved = cutoffs or {"A": 80, "B": 60, "C": 40}
    if score >= resolved.get("A", 80):
        return "A"
    if score >= resolved.get("B", 60):
        return "B"
    if score >= resolved.get("C", 40):
        return "C"
    return "D"
