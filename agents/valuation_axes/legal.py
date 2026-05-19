from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import select_by_types_or_axes
from workflow.state import PatentWorkflowState


AXIS = "legal"
LABEL = "권리성"


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    return select_by_types_or_axes(
        items,
        source_types={"portfolio_context", "competitor_patent", "patent_api"},
        axes={AXIS},
    )
