from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import settings


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
    identifier = (
        request.managementNumber
        or request.applicationNumber
        or request.registrationNumber
        or patent_id
    )
    title = request.title or "특허 정보"
    summary = f"{identifier} ({title})에 대한 AI 평가 요청을 정상 수신했습니다."

    scores = [
        PatentEvaluationScore(category="권리성", score=None, evidence="Agent workflow 연동 전 기본 응답입니다."),
        PatentEvaluationScore(category="기술성", score=None, evidence="Agent workflow 연동 전 기본 응답입니다."),
        PatentEvaluationScore(category="시장성", score=None, evidence="Agent workflow 연동 전 기본 응답입니다."),
        PatentEvaluationScore(category="라이프사이클 경제성", score=None, evidence="Agent workflow 연동 전 기본 응답입니다."),
    ]
    raw_markdown = "\n".join(
        [
            "# PatentFlow AI 평가",
            "",
            f"- 특허 ID: {patent_id}",
            f"- 식별자: {identifier}",
            f"- BE URL: {settings.unified_api_base_url}",
            f"- Vector table: {settings.pgvector_table_name}",
            "",
            "현재 응답은 Docker/BE 연동 확인용 기본 응답입니다. 실제 workflow 연결 시 이 엔드포인트에서 평가 그래프를 호출하도록 확장합니다.",
        ]
    )

    return PatentEvaluationResponse(
        patentId=patent_id,
        summary=summary,
        scores=scores,
        recommendation="REVIEW_AGAIN",
        rawMarkdown=raw_markdown,
        generatedAt=datetime.now(timezone.utc),
    )
