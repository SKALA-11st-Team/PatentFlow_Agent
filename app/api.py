from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.main import save_outputs
from workflow.graph import run_workflow
from workflow.state import PatentWorkflowState


app = FastAPI(
    title="PatentFlow Agent API",
    version="0.1.0",
    description="AI workflow serving API for PatentFlow.",
)


class PatentEvaluationRequest(BaseModel):
    managementNumber: str | None = None
    applicationNumber: str | None = None
    registrationNumber: str | None = None
    title: str | None = None
    noSave: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatentEvaluationScore(BaseModel):
    category: str
    score: int | None = None
    evidence: str


class PatentEvaluationResponse(BaseModel):
    patentId: str
    summary: str
    scores: list[PatentEvaluationScore]
    recommendation: str
    rawMarkdown: str
    summaryMarkdown: str | None = None
    valuationReportMarkdown: str | None = None
    artifactDir: str | None = None
    totalScore: int | None = None
    generatedAt: datetime


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
    try:
        final_state = run_workflow(initial_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent workflow failed: {exc.__class__.__name__}") from exc

    if not request.noSave:
        save_outputs(final_state)
    summary_result = final_state.summary_result or {}
    valuation_result = final_state.valuation_result or {}
    valuation_markdown = (valuation_result.get("final_report_markdown") or "").strip()
    summary_markdown = (summary_result.get("summary_markdown") or "").strip()

    return PatentEvaluationResponse(
        patentId=patent_id,
        summary=summary_text(final_state),
        scores=valuation_scores(valuation_result),
        recommendation=valuation_result.get("recommendation") or "추가 정보 필요",
        rawMarkdown=valuation_markdown,
        summaryMarkdown=summary_markdown or None,
        valuationReportMarkdown=valuation_markdown or None,
        artifactDir=str(final_state.user_input.get("artifact_dir") or "") or None,
        totalScore=valuation_result.get("total_score"),
        generatedAt=datetime.now(timezone.utc),
    )


def build_api_user_input(patent_id: str, request: PatentEvaluationRequest) -> dict[str, Any]:
    identifier = (
        request.managementNumber
        or request.applicationNumber
        or request.registrationNumber
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
    }
    if request.managementNumber:
        user_input["management_number"] = request.managementNumber
    elif request.applicationNumber:
        user_input["application_number"] = request.applicationNumber
    elif request.registrationNumber:
        user_input["registration_number"] = request.registrationNumber
    elif patent_id.isdigit():
        user_input["patent_id"] = int(patent_id)
    else:
        user_input["management_number"] = patent_id
    return user_input


def api_run_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")


def safe_identifier(value: str) -> str:
    return str(value).replace("/", "_").replace(" ", "_")


def summary_text(state: PatentWorkflowState) -> str:
    summary_result = state.summary_result or {}
    return (
        summary_result.get("plain_summary")
        or summary_result.get("title")
        or "요약 결과가 생성되지 않았습니다."
    )


def valuation_scores(valuation_result: dict[str, Any]) -> list[PatentEvaluationScore]:
    axes = valuation_result.get("axes") or {}
    scores = []
    for axis in ["legal", "technology", "market", "business_fit"]:
        axis_result = axes.get(axis) or {}
        scores.append(
            PatentEvaluationScore(
                category=axis_result.get("label") or axis,
                score=axis_result.get("score"),
                evidence=axis_result.get("rationale") or "평가 근거가 생성되지 않았습니다.",
            )
        )
    return scores
