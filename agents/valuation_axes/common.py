from __future__ import annotations

from typing import Any


def select_by_types_or_axes(
    items: list[dict[str, Any]],
    *,
    source_types: set[str],
    axes: set[str],
) -> list[dict[str, Any]]:
    selected = []
    for item in items:
        item_axes = set(item.get("related_axes") or item.get("related_axis") or [])
        if item.get("source_type") in source_types or item_axes.intersection(axes):
            selected.append(item)
    return selected[:5]


def normalize_text(value: Any) -> str:
    return str(value or "").strip()
