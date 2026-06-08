from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


ValuationAxis = Literal["legal", "technology", "market", "business_fit"]
VALUATION_AXES: tuple[ValuationAxis, ...] = ("legal", "technology", "market", "business_fit")
FinalRecommendation = Literal["유지 권고", "포기 검토", "추가 정보 필요"]
FinalGrade = Literal["A", "B", "C", "D"]


class AxisValuationResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    axis: ValuationAxis
    label: str
    score: int = Field(ge=0, le=100)
    grade: FinalGrade
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @field_validator("label", "rationale")
    @classmethod
    def require_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ValuationResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    axes: dict[ValuationAxis, AxisValuationResult]
    total_score: int = Field(ge=0, le=400)
    average_score: float = Field(ge=0, le=100)
    final_grade: FinalGrade
    final_indicator: FinalRecommendation
    recommendation: FinalRecommendation
    decision_rationale: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_current_axes_and_consistent_totals(self) -> "ValuationResult":
        missing = [axis for axis in VALUATION_AXES if axis not in self.axes]
        extra = [axis for axis in self.axes if axis not in VALUATION_AXES]
        if missing:
            raise ValueError(f"missing valuation axes: {', '.join(missing)}")
        if extra:
            raise ValueError(f"unsupported valuation axes: {', '.join(extra)}")
        total = sum(axis.score for axis in self.axes.values())
        if self.total_score != total:
            raise ValueError(f"total_score {self.total_score} does not match axis sum {total}")
        expected_average = round(total / len(VALUATION_AXES), 1)
        if round(float(self.average_score), 1) != expected_average:
            raise ValueError(f"average_score {self.average_score} does not match {expected_average}")
        return self


def validate_axis_result(axis: str, value: dict[str, Any]) -> dict[str, Any]:
    payload = {**value, "axis": value.get("axis") or axis}
    try:
        return AxisValuationResult.model_validate(payload).model_dump()
    except ValidationError as exc:
        raise RuntimeError(f"Valuation axis schema invalid for {axis}: {exc}") from exc


def validate_valuation_result(value: dict[str, Any]) -> dict[str, Any]:
    try:
        return ValuationResult.model_validate(value).model_dump()
    except ValidationError as exc:
        raise RuntimeError(f"Valuation result schema invalid: {exc}") from exc
