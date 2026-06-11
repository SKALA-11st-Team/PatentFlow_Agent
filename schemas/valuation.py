from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


ValuationAxis = Literal["legal", "technology", "market", "business_fit"]
VALUATION_AXES: tuple[ValuationAxis, ...] = ("legal", "technology", "market", "business_fit")
# 종합 점수(total_score/average_score)는 권리성·기술성·시장성 3축으로만 산정한다.
# 사업 연계성(business_fit)은 합산 대신 종합 지표 오버라이드로만 작용한다.
CORE_VALUATION_AXES: tuple[ValuationAxis, ...] = ("legal", "technology", "market")
FinalIndicator = Literal["유지", "조건부 유지", "포기 검토", "매각 후보"]
FinalRecommendation = Literal["유지 권고", "포기 검토", "추가 정보 필요"]
FinalGrade = Literal["A", "B", "C", "D"]

# 운영 설정으로 재정의 가능한 가치평가 기준의 기본값. BE가 valuationConfig를 보내지 않으면
# (구 BE ↔ 신 agent 호환) 아래 값이 그대로 적용되어 기존 배포와 동일하게 동작한다.
DEFAULT_AXIS_WEIGHTS: dict[str, float] = {
    "legal": 25.0,
    "technology": 25.0,
    "market": 25.0,
    "business_fit": 25.0,
}
DEFAULT_GRADE_CUTOFFS: dict[str, float] = {"A": 80.0, "B": 60.0, "C": 40.0}
DEFAULT_MAINTAIN_THRESHOLD: float = 60.0
DEFAULT_SUBSCORE_WEIGHTS: dict[str, dict[str, int]] = {
    "legal": {
        "right_stability": 35,
        "claim_protection": 40,
        "portfolio_defensive_value": 25,
    },
    "business_fit": {
        "official_business_evidence": 30,
        "product_function_direct_match": 45,
        "business_context_fit": 25,
    },
}


class ValuationConfig(BaseModel):
    """BE가 evaluate 요청에 실어 보내는 가치평가 기준(계약 C1). 모든 필드는 선택적이며
    누락 시 기본값으로 채워진다. 키는 BE/FE와 공유하는 계약이라 camelCase를 유지한다."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: int | None = None
    axisWeights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_AXIS_WEIGHTS))
    gradeCutoffs: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_GRADE_CUTOFFS))
    # 범위 검증은 resolve_valuation_config에서 클램프로 처리한다(필드 제약으로 두면
    # 한 값의 범위 초과가 유효한 나머지 설정 전체를 기본값으로 폴백시키기 때문).
    maintainThreshold: float = DEFAULT_MAINTAIN_THRESHOLD
    subscoreWeights: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {axis: dict(values) for axis, values in DEFAULT_SUBSCORE_WEIGHTS.items()}
    )


def resolve_valuation_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """요청의 valuationConfig를 검증·보정해 항상 완전한 설정 dict를 돌려준다.

    - 누락/None → 기본값 전체 + source="default"
    - 축 가중치는 알 수 없는 축 제거, 0 이하/비수치 값은 기본값으로 대체
    - 컷오프는 100≥A>B>C≥0이 깨지면 기본 컷오프로 폴백(잘못된 설정으로 등급이 뒤집히는 것 방지)
    - subscore 가중치는 알려진 축/키만 수용하고 음수는 기본값으로 대체
    """
    if not raw:
        config = ValuationConfig()
        source = "default"
    else:
        try:
            config = ValuationConfig.model_validate(raw)
            source = "request"
        except ValidationError:
            config = ValuationConfig()
            source = "default"

    axis_weights: dict[str, float] = {}
    for axis in VALUATION_AXES:
        try:
            value = float(config.axisWeights.get(axis, DEFAULT_AXIS_WEIGHTS[axis]))
        except (TypeError, ValueError):
            value = DEFAULT_AXIS_WEIGHTS[axis]
        axis_weights[axis] = value if value > 0 else DEFAULT_AXIS_WEIGHTS[axis]

    cutoffs: dict[str, float] = {}
    for grade in ("A", "B", "C"):
        try:
            cutoffs[grade] = float(config.gradeCutoffs.get(grade, DEFAULT_GRADE_CUTOFFS[grade]))
        except (TypeError, ValueError):
            cutoffs[grade] = DEFAULT_GRADE_CUTOFFS[grade]
    if not (100 >= cutoffs["A"] > cutoffs["B"] > cutoffs["C"] >= 0):
        cutoffs = dict(DEFAULT_GRADE_CUTOFFS)

    threshold = float(config.maintainThreshold)
    threshold = min(100.0, max(0.0, threshold))

    subscore_weights: dict[str, dict[str, int]] = {}
    for axis, defaults in DEFAULT_SUBSCORE_WEIGHTS.items():
        provided = config.subscoreWeights.get(axis) or {}
        resolved: dict[str, int] = {}
        for key, default_value in defaults.items():
            try:
                value = int(provided.get(key, default_value))
            except (TypeError, ValueError):
                value = default_value
            resolved[key] = value if value >= 0 else default_value
        subscore_weights[axis] = resolved

    return {
        "version": config.version,
        "axisWeights": axis_weights,
        "gradeCutoffs": cutoffs,
        "maintainThreshold": threshold,
        "subscoreWeights": subscore_weights,
        "source": source,
    }


def is_default_valuation_config(config: dict[str, Any] | None) -> bool:
    if not config:
        return True
    return (
        config.get("axisWeights") == DEFAULT_AXIS_WEIGHTS
        and config.get("gradeCutoffs") == DEFAULT_GRADE_CUTOFFS
        and float(config.get("maintainThreshold", DEFAULT_MAINTAIN_THRESHOLD)) == DEFAULT_MAINTAIN_THRESHOLD
        and config.get("subscoreWeights") == DEFAULT_SUBSCORE_WEIGHTS
    )


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
    total_score: int = Field(ge=0, le=300)
    average_score: float = Field(ge=0, le=100)
    final_grade: FinalGrade
    final_indicator: FinalIndicator
    recommendation: FinalRecommendation
    decision_rationale: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    applied_config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_current_axes_and_consistent_totals(self) -> "ValuationResult":
        missing = [axis for axis in VALUATION_AXES if axis not in self.axes]
        extra = [axis for axis in self.axes if axis not in VALUATION_AXES]
        if missing:
            raise ValueError(f"missing valuation axes: {', '.join(missing)}")
        if extra:
            raise ValueError(f"unsupported valuation axes: {', '.join(extra)}")
        total = sum(self.axes[axis].score for axis in CORE_VALUATION_AXES if axis in self.axes)
        if self.total_score != total:
            raise ValueError(f"total_score {self.total_score} does not match core axis sum {total}")
        # average_score는 적용된 축 가중치 기준의 3축(core) 가중 평균이다(설정 미적용 시 균등 가중).
        weights = (self.applied_config or {}).get("axisWeights") or DEFAULT_AXIS_WEIGHTS
        weight_sum = sum(float(weights.get(axis, DEFAULT_AXIS_WEIGHTS[axis])) for axis in CORE_VALUATION_AXES)
        weighted = sum(
            float(weights.get(axis, DEFAULT_AXIS_WEIGHTS[axis])) * self.axes[axis].score
            for axis in CORE_VALUATION_AXES
        )
        expected_average = round(weighted / weight_sum, 1) if weight_sum > 0 else 0.0
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
