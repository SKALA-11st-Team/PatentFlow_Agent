from typing import Literal

from pydantic import BaseModel, Field


ValuationAxis = Literal["legal", "technology", "market", "economic", "business_fit"]


class AxisValuationResult(BaseModel):
    axis: ValuationAxis
    label: str
    score: int = Field(ge=0, le=100)
    grade: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class ValuationResult(BaseModel):
    axes: dict[ValuationAxis, AxisValuationResult] = Field(default_factory=dict)
    total_score: int | None = None
    final_indicator: str | None = None
    recommendation: str | None = None
    decision_rationale: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
