from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from threading import BoundedSemaphore
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.field_recommendation import recommend_fields
from app.config import settings
from app.main import save_outputs
from services.patent.shared_db_service import get_patent_identifiers
from workflow.graph import run_workflow
from workflow.state import PatentWorkflowState


app = FastAPI(
    title="PatentFlow Agent API",
    version="0.1.0",
    description="AI workflow serving API for PatentFlow.",
)

_EVALUATE_WORKERS = max(1, int(settings.evaluate_max_concurrency or 1))
_EVALUATE_EXECUTOR = ThreadPoolExecutor(max_workers=_EVALUATE_WORKERS, thread_name_prefix="patent-evaluate")
_EVALUATE_SEMAPHORE = BoundedSemaphore(_EVALUATE_WORKERS)


class PatentEvaluationRequest(BaseModel):
    managementNumber: str | None = None
    applicationNumber: str | None = None
    registrationNumber: str | None = None
    title: str | None = None
    noSave: bool = False
    useLlmSupervisor: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatentEvaluationScore(BaseModel):
    category: str
    score: int | None = None
    grade: str | None = None
    evidence: str


class PatentEvaluationResponse(BaseModel):
    patentId: str
    scores: list[PatentEvaluationScore]
    recommendation: str
    summaryMarkdown: str | None = None
    valuationReportMarkdown: str | None = None
    artifactDir: str | None = None
    totalScore: int | None = None
    averageScore: float | None = None
    finalGrade: str | None = None
    finalIndicator: str | None = None
    degraded: bool = False
    failureReason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    evidenceConfidence: str | None = None
    generatedAt: datetime


class FieldRecommendationRequest(BaseModel):
    title: str | None = None
    managementNumber: str | None = None
    applicationNumber: str | None = None
    technologyArea: str | None = None
    businessArea: str | None = None
    # 초록(요약) 본문. 제목만으론 분류 신호가 약해 함께 받는다. 미제공 시 에이전트가
    # applicationNumber로 KIPRIS 초록을 best-effort 조회한다.
    abstract: str | None = None
    # BE가 관리자 관리 분류 목록(taxonomy)을 넘겨준다. 에이전트는 공유 DB에 직접 접근하지 않고
    # 이 목록 안에서만 추천한다. 미제공 시 에이전트가 로컬 DB로 폴백한다.
    taxonomy: dict[str, list[str]] | None = None


class FieldRecommendationResponse(BaseModel):
    businessArea: str
    technologyArea: str
    confidence: float
    confidenceText: str
    reason: str


@app.post("/api/v1/ai/patents/{patent_id}/recommend-fields", response_model=FieldRecommendationResponse)
def recommend_patent_fields(patent_id: str, request: FieldRecommendationRequest) -> FieldRecommendationResponse:
    del patent_id
    try:
        result = recommend_fields(
            title=request.title,
            management_number=request.managementNumber,
            application_number=request.applicationNumber,
            technology_area=request.technologyArea,
            business_area=request.businessArea,
            abstract=request.abstract,
            taxonomy=request.taxonomy,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Field recommendation failed: {exc.__class__.__name__}") from exc
    return FieldRecommendationResponse(**result)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "PatentFlow Agent API",
        "status": "UP",
        "docs": "/docs",
        "health": "/health",
    }


@app.post("/api/v1/ai/patents/{patent_id}/evaluate")
def evaluate_patent(patent_id: str, request: PatentEvaluationRequest) -> PatentEvaluationResponse:
    initial_state = PatentWorkflowState(user_input=build_api_user_input(patent_id, request))
    final_state = run_workflow_guarded(initial_state)

    if not request.noSave:
        save_outputs(final_state)
    summary_result = final_state.summary_result or {}
    valuation_result = final_state.valuation_result or {}
    valuation_markdown = (valuation_result.get("final_report_markdown") or "").strip()
    summary_markdown = (summary_result.get("summary_markdown") or "").strip()

    return PatentEvaluationResponse(
        patentId=patent_id,
        scores=valuation_scores(valuation_result),
        recommendation=valuation_result.get("recommendation") or "추가 정보 필요",
        summaryMarkdown=summary_markdown or None,
        valuationReportMarkdown=valuation_markdown or None,
        artifactDir=str(final_state.user_input.get("artifact_dir") or "") or None,
        totalScore=valuation_result.get("total_score"),
        averageScore=valuation_average_score(valuation_result),
        finalGrade=valuation_result.get("final_grade") or final_grade_for_average(valuation_average_score(valuation_result)),
        finalIndicator=valuation_result.get("final_indicator"),
        degraded=is_degraded(final_state, valuation_result),
        failureReason=failure_reason(final_state, valuation_result),
        warnings=workflow_warnings(final_state),
        evidenceConfidence=evidence_confidence(final_state),
        generatedAt=datetime.now(timezone.utc),
    )


def run_workflow_guarded(initial_state: PatentWorkflowState) -> PatentWorkflowState:
    if not _EVALUATE_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Agent evaluate capacity exceeded.")
    future = _EVALUATE_EXECUTOR.submit(run_workflow, initial_state)

    def release_when_done(_future: Any) -> None:
        _EVALUATE_SEMAPHORE.release()

    future.add_done_callback(release_when_done)
    try:
        timeout_seconds = max(1, int(settings.evaluate_timeout_seconds or 1))
        return future.result(timeout=timeout_seconds)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Agent workflow timed out.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent workflow failed: {exc.__class__.__name__}") from exc


def build_api_user_input(patent_id: str, request: PatentEvaluationRequest) -> dict[str, Any]:
    management_number = normalize_optional_identifier(request.managementNumber)
    application_number = normalize_optional_identifier(request.applicationNumber)
    registration_number = normalize_optional_identifier(request.registrationNumber)
    if not management_number and not application_number and not registration_number and settings.enable_shared_db_fallback:
        db_identifiers = get_patent_identifiers(patent_id) or {}
        management_number = normalize_optional_identifier(db_identifiers.get("management_number"))
        application_number = normalize_optional_identifier(db_identifiers.get("application_number"))
        registration_number = normalize_optional_identifier(db_identifiers.get("registration_number"))
    identifier = (
        management_number
        or application_number
        or registration_number
        or patent_id
    )
    artifact_dir = settings.run_outputs_dir / f"{api_run_timestamp()}_{safe_identifier(identifier)}"
    user_input: dict[str, Any] = {
        "collect_pdf": True,
        "collect_kipris_api": True,
        "no_save": request.noSave,
        "artifact_dir": str(artifact_dir),
        "use_llm_summary": True,
        "use_llm_valuation": True,
        "use_llm_final_report": True,
        "use_llm_supervisor": request.useLlmSupervisor,
    }
    if management_number:
        user_input["management_number"] = management_number
    elif application_number:
        user_input["application_number"] = application_number
    elif registration_number:
        user_input["registration_number"] = registration_number
    elif patent_id.isdigit():
        user_input["patent_id"] = int(patent_id)
    else:
        user_input["management_number"] = patent_id
    return user_input


def normalize_optional_identifier(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() == "string":
        return None
    return text


def api_run_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")


def safe_identifier(value: str) -> str:
    return str(value).replace("/", "_").replace(" ", "_")


def valuation_scores(valuation_result: dict[str, Any]) -> list[PatentEvaluationScore]:
    axes = valuation_result.get("axes") or {}
    scores = []
    for axis in ["legal", "technology", "market", "business_fit"]:
        axis_result = axes.get(axis) or {}
        scores.append(
            PatentEvaluationScore(
                category=axis_result.get("label") or axis,
                score=axis_result.get("score"),
                grade=axis_result.get("grade"),
                evidence=axis_result.get("rationale") or "평가 근거가 생성되지 않았습니다.",
            )
        )
    return scores


def valuation_average_score(valuation_result: dict[str, Any]) -> float | None:
    average_score = valuation_result.get("average_score")
    if isinstance(average_score, (int, float)):
        return round(float(average_score), 1)
    total_score = valuation_result.get("total_score")
    if isinstance(total_score, (int, float)):
        return round(float(total_score) / 4, 1)
    return None


def final_grade_for_average(average_score: float | None) -> str | None:
    if average_score is None:
        return None
    if average_score >= 80:
        return "A"
    if average_score >= 60:
        return "B"
    if average_score >= 40:
        return "C"
    return "D"


def is_degraded(state: PatentWorkflowState, valuation_result: dict[str, Any]) -> bool:
    scores = valuation_scores(valuation_result)
    has_scored_axis = any(isinstance(score.score, int) for score in scores)
    warnings = set(workflow_warnings(state))
    return (
        not has_scored_axis
        or evidence_confidence(state) == "LOW"
        or "technology_comparison_empty" in warnings
        or any("_failed:" in warning for warning in warnings)
    )


def failure_reason(state: PatentWorkflowState, valuation_result: dict[str, Any]) -> str | None:
    if not is_degraded(state, valuation_result):
        return None
    if "technology_comparison_empty" in workflow_warnings(state):
        return "기술성 비교군을 확보하지 못해 제한된 근거로 AI 평가가 생성되었습니다."
    if not state.evidence_bundle:
        return "외부 근거 수집 결과가 없어 AI 평가 신뢰도가 낮습니다."
    return "일부 근거 수집 단계가 실패해 제한된 근거로 AI 평가가 생성되었습니다."


def evidence_confidence(state: PatentWorkflowState) -> str:
    evidence_count = len(state.evidence_bundle or [])
    if evidence_count >= 3:
        return "HIGH"
    if evidence_count > 0:
        return "MEDIUM"
    return "LOW"


def workflow_warnings(state: PatentWorkflowState) -> list[str]:
    warnings: list[str] = []
    collect_warning_values(state.portfolio_result, warnings)
    collect_warning_values(state.query_plan, warnings)
    collect_warning_values(state.validation_result, warnings)
    collect_warning_values(state.summary_validation_result, warnings)
    collect_warning_values(state.report_validation_result, warnings)
    collect_warning_values(state.valuation_result, warnings)
    if state.missing_evidence:
        warnings.extend(str(item) for item in state.missing_evidence if item)
    if not state.evidence_bundle:
        warnings.append("evidence_bundle_empty")
    return dedupe(warnings)


def collect_warning_values(value: Any, warnings: list[str]) -> None:
    if isinstance(value, dict):
        warning = value.get("warning")
        if warning:
            warnings.append(str(warning))
        nested_warnings = value.get("warnings")
        if isinstance(nested_warnings, list):
            warnings.extend(str(item) for item in nested_warnings if item)
        for nested in value.values():
            collect_warning_values(nested, warnings)
    elif isinstance(value, list):
        for item in value:
            collect_warning_values(item, warnings)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
