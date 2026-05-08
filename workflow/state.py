from typing import Any
from pydantic import BaseModel, Field


class PatentWorkflowState(BaseModel):
    user_input: dict[str, Any] = Field(default_factory=dict)
    current_stage: str | None = None
    patent_structured: dict[str, Any] | None = None
    kipris_api_data: dict[str, Any] | None = None
    pdf_paths: list[str] = Field(default_factory=list)
    parsed_pdf: dict[str, Any] | None = None
    preprocessed_patent: dict[str, Any] | None = None
    summary_result: dict[str, Any] | None = None
    query_plan: dict[str, Any] | None = None
    search_queries: list[str] = Field(default_factory=list)
    evidence_bundle: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_evidence: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_result: dict[str, Any] | None = None
    valuation_result: dict[str, Any] | None = None
    validation_result: dict[str, Any] | None = None
    supervisor_decision: dict[str, Any] | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    retry_count: int = 0
    final_report: dict[str, Any] | None = None
