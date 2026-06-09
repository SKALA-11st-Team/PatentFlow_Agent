from typing import Any
from pydantic import BaseModel, Field


class PatentWorkflowState(BaseModel):
    # Run control
    user_input: dict[str, Any] = Field(default_factory=dict)
    current_stage: str | None = None
    current_team: str | None = None
    team_status: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    # VAL-02: 시장 성장성 기준 시점(ISO date). 설정 시 datetime.now() 대신 사용해 평가 재현성을 확보한다.
    evaluation_reference_date: str | None = None

    # Patent source data
    patent_structured: dict[str, Any] | None = None
    kipris_api_data: dict[str, Any] | None = None
    kipris_family_patents: list[dict[str, Any]] = Field(default_factory=list)
    citation_evidence: dict[str, Any] = Field(default_factory=dict)
    pdf_paths: list[str] = Field(default_factory=list)
    parsed_pdf: dict[str, Any] | None = None

    # Patent markdown/preprocessed content
    preprocessed_patent: dict[str, Any] | None = None
    summary_result: dict[str, Any] | None = None

    # External and portfolio evidence
    portfolio_evidence: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_result: dict[str, Any] | None = None
    query_plan: dict[str, Any] | None = None
    search_queries: list[str] = Field(default_factory=list)
    evidence_bundle: list[dict[str, Any]] = Field(default_factory=list)

    # Valuation and report markdown
    valuation_result: dict[str, Any] | None = None
    valuation_axis_legal: dict[str, Any] | None = None
    valuation_axis_technology: dict[str, Any] | None = None
    valuation_axis_market: dict[str, Any] | None = None
    valuation_axis_business_fit: dict[str, Any] | None = None
    valuation_retry_axes: list[str] = Field(default_factory=list)
    final_report: dict[str, Any] | None = None

    # Validation/supervisor loop
    validation_result: dict[str, Any] | None = None
    summary_validation_result: dict[str, Any] | None = None
    report_validation_result: dict[str, Any] | None = None
    supervisor_decision: dict[str, Any] | None = None
    missing_evidence: list[str] = Field(default_factory=list)
