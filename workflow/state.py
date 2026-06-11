from typing import Any
from pydantic import BaseModel, Field


class PatentWorkflowState(BaseModel):
    # Run control
    user_input: dict[str, Any] = Field(default_factory=dict)
    current_stage: str | None = None
    current_team: str | None = None
    team_status: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0

    # Patent source data
    patent_structured: dict[str, Any] | None = None
    kipris_api_data: dict[str, Any] | None = None
    kipris_family_patents: list[dict[str, Any]] = Field(default_factory=list)
    citation_evidence: dict[str, Any] = Field(default_factory=dict)
    pdf_paths: list[str] = Field(default_factory=list)
    parsed_pdf: dict[str, Any] | None = None
    prior_art_context: dict[str, Any] | None = None
    # 비교 특허군 조립 결과(prior-art-first-then-similar). 구조화 노드가 한 번 조립하고
    # 기술성 축이 재사용한다(중복 조립 방지).
    comparison_group: dict[str, Any] | None = None
    # 구성요소 구조화 결과(타깃 + 비교 특허군). 권리성·기술성 축이 element 단위 비교에 사용.
    target_structure: dict[str, Any] | None = None
    comparison_structures: list[dict[str, Any]] = Field(default_factory=list)

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
    valuation_retry_axes: list[str] = Field(default_factory=list)
    final_report: dict[str, Any] | None = None

    # Validation/supervisor loop
    validation_result: dict[str, Any] | None = None
    summary_validation_result: dict[str, Any] | None = None
    report_validation_result: dict[str, Any] | None = None
    # writing supervisor의 요약/보고서 LLM 품질검사 직전 결과(선택적 재검증용).
    writing_quality_checks: dict[str, Any] = Field(default_factory=dict)
    supervisor_decision: dict[str, Any] | None = None
    missing_evidence: list[str] = Field(default_factory=list)
