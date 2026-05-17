from typing import Any
from pydantic import BaseModel, Field


class PatentWorkflowState(BaseModel):
    # Run control
    user_input: dict[str, Any] = Field(default_factory=dict)
    current_stage: str | None = None
    retry_count: int = 0

    # Patent source data
    patent_structured: dict[str, Any] | None = None
    kipris_api_data: dict[str, Any] | None = None
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
    final_report: dict[str, Any] | None = None

    # Validation/supervisor loop
    validation_result: dict[str, Any] | None = None
    supervisor_decision: dict[str, Any] | None = None
    missing_evidence: list[str] = Field(default_factory=list)
