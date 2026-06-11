from __future__ import annotations

import hmac
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.field_recommendation import recommend_fields
from agents.valuation_axes.common import grade_for_score
from agents.writing.final_report import build_evidence_references
from app.config import settings
from app.main import save_outputs
from schemas.valuation import resolve_valuation_config
from services.patent.shared_db_service import get_patent_identifiers
from workflow.graph import run_workflow
from workflow.state import PatentWorkflowState


app = FastAPI(
    title="PatentFlow Agent API",
    version="0.1.0",
    description="AI workflow serving API for PatentFlow.",
)

# SEC-01: BE→agent 내부 호출(ClusterIP)에 대한 opt-in 인바운드 인증. AGENT_INBOUND_API_KEY가
# 설정된 경우에만 X-API-Key를 상수시간 비교로 요구한다(미설정 시 통과 — 기존 배포 호환).
# BE(AiReportAgentClient)가 동일 키를 X-API-Key로 보내도록 동시 설정해야 활성화된다.
_AUTH_EXEMPT_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def require_inbound_api_key(request: Request, call_next: Any) -> Any:
    expected = os.getenv("AGENT_INBOUND_API_KEY")
    if not expected or request.method == "OPTIONS" or request.url.path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)
    provided = request.headers.get("X-API-Key")
    if not provided or not hmac.compare_digest(provided, expected):
        return JSONResponse(status_code=401, content={"detail": "유효한 X-API-Key 헤더가 필요합니다."})
    return await call_next(request)

_EVALUATE_WORKERS = max(1, int(settings.evaluate_max_concurrency or 1))
_EVALUATE_SEMAPHORE = BoundedSemaphore(_EVALUATE_WORKERS)
VALUATION_PROMPT_PATHS = {
    "legal": "valuation/valuation_legal.md",
    "technology": "valuation/valuation_technology.md",
    "market": "valuation/valuation_market.md",
    "business_fit": "valuation/valuation_business_fit.md",
}
VALUATION_PROMPT_LABELS = {
    "legal": "권리성",
    "technology": "기술성",
    "market": "시장성",
    "business_fit": "사업 연계성",
}


class PatentEvaluationRequest(BaseModel):
    managementNumber: str | None = None
    applicationNumber: str | None = None
    registrationNumber: str | None = None
    title: str | None = None
    noSave: bool = False
    useLlmSupervisor: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 계약 C1: BE가 전달하는 가치평가 기준(축 가중치/등급 컷오프/유지 임계/subscore 배점).
    # 누락 시 기본값으로 평가한다(구 BE 호환). 잘못된 값은 resolve_valuation_config가 보정한다.
    valuationConfig: dict[str, Any] | None = None


class ValuationPromptResponse(BaseModel):
    axis: str
    label: str
    path: str
    markdown: str
    checksum: str
    updatedAt: datetime | None = None


class ValuationPromptUpdateRequest(BaseModel):
    markdown: str
    reason: str | None = None
    expectedChecksum: str | None = None


# ORCH-06/AIREPORT-02: 축별 근거의 클릭형 출처. evidence_id로 연결되는 근거의 제목/URL을 노출한다.
class SourceRef(BaseModel):
    title: str | None = None
    url: str | None = None


class EvidenceDetail(BaseModel):
    text: str
    source: SourceRef | None = None


class ValuationPromptResponse(BaseModel):
    axis: str
    label: str
    path: str
    markdown: str
    checksum: str
    updatedAt: datetime | None = None


class ValuationPromptUpdateRequest(BaseModel):
    markdown: str
    reason: str | None = None
    expectedChecksum: str | None = None


class PatentEvaluationScore(BaseModel):
    category: str
    score: int | None = None
    grade: str | None = None
    evidence: str
    # ORCH-06/AIREPORT-02: 축별 세부 근거(출처 URL 포함). 데이터 없으면 빈 리스트.
    evidenceDetails: list[EvidenceDetail] = Field(default_factory=list)


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
    # ORCH-06/AIREPORT-02: 리포트 레벨 리치 근거. BE record가 그동안 수용하지 못해 FE까지 유실되던 필드들.
    missingInformation: list[str] = Field(default_factory=list)
    keyEvidence: str | None = None
    judgementGrounds: list[str] = Field(default_factory=list)
    businessCheckRequests: list[str] = Field(default_factory=list)
    externalSources: list[SourceRef] = Field(default_factory=list)
    # 계약 C1: 실제 적용된 가치평가 기준 스냅샷(source=request|default). BE가 레포트와 함께 보관해
    # "이 레포트는 어떤 기준으로 산정됐나"를 추적할 수 있게 한다.
    appliedValuationConfig: dict[str, Any] | None = None
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


@app.get("/api/v1/admin/valuation-criteria/prompts")
def list_valuation_prompts() -> list[ValuationPromptResponse]:
    return [read_valuation_prompt(axis) for axis in VALUATION_PROMPT_PATHS]


@app.get("/api/v1/admin/valuation-criteria/prompts/{axis}")
def get_valuation_prompt(axis: str) -> ValuationPromptResponse:
    return read_valuation_prompt(axis)


@app.put("/api/v1/admin/valuation-criteria/prompts/{axis}")
def update_valuation_prompt(axis: str, request: ValuationPromptUpdateRequest) -> ValuationPromptResponse:
    path = valuation_prompt_path(axis)
    current = path.read_text(encoding="utf-8")
    current_checksum = checksum_text(current)
    if request.expectedChecksum and request.expectedChecksum != current_checksum:
        raise HTTPException(status_code=409, detail="평가 기준 md가 이미 변경되었습니다. 새로고침 후 다시 저장해 주세요.")
    markdown = request.markdown.strip()
    validate_valuation_prompt_markdown(axis, markdown)
    path.write_text(markdown + "\n", encoding="utf-8")
    return read_valuation_prompt(axis)


def read_valuation_prompt(axis: str) -> ValuationPromptResponse:
    path = valuation_prompt_path(axis)
    markdown = path.read_text(encoding="utf-8")
    stat = path.stat()
    return ValuationPromptResponse(
        axis=axis,
        label=VALUATION_PROMPT_LABELS[axis],
        path=str(path.relative_to(settings.project_root)),
        markdown=markdown,
        checksum=checksum_text(markdown),
        updatedAt=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
    )


def valuation_prompt_path(axis: str) -> Path:
    if axis not in VALUATION_PROMPT_PATHS:
        raise HTTPException(status_code=404, detail="지원하지 않는 가치평가 축입니다.")
    path = (settings.project_root / "prompts" / VALUATION_PROMPT_PATHS[axis]).resolve()
    prompt_root = (settings.project_root / "prompts" / "valuation").resolve()
    if prompt_root not in path.parents:
        raise HTTPException(status_code=400, detail="평가 기준 md 경로가 허용 범위를 벗어났습니다.")
    return path


def checksum_text(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def validate_valuation_prompt_markdown(axis: str, markdown: str) -> None:
    if not markdown:
        raise HTTPException(status_code=400, detail="평가 기준 md는 비워둘 수 없습니다.")
    if len(markdown) > 120_000:
        raise HTTPException(status_code=400, detail="평가 기준 md가 너무 깁니다.")
    label = VALUATION_PROMPT_LABELS[axis]
    required_fragments = [
        label,
        "총점",
        "100점",
        "score",
        "grade",
        "confidence",
        "missing_information",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in markdown]
    if missing:
        raise HTTPException(status_code=400, detail=f"평가 기준 md 필수 항목 누락: {', '.join(missing)}")
    if "라이프사이클 경제성" in markdown:
        raise HTTPException(status_code=400, detail="미지원 평가축(라이프사이클 경제성)은 사용할 수 없습니다.")


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

    evidence_bundle = final_state.evidence_bundle or []
    applied_config = valuation_result.get("applied_config") or final_state.user_input.get("valuation_config")
    return PatentEvaluationResponse(
        patentId=patent_id,
        scores=valuation_scores(valuation_result, evidence_bundle),
        recommendation=valuation_result.get("recommendation") or "추가 정보 필요",
        summaryMarkdown=summary_markdown or None,
        valuationReportMarkdown=valuation_markdown or None,
        artifactDir=str(final_state.user_input.get("artifact_dir") or "") or None,
        totalScore=valuation_result.get("total_score"),
        averageScore=valuation_average_score(valuation_result),
        finalGrade=valuation_result.get("final_grade")
        or final_grade_for_average(valuation_average_score(valuation_result), applied_config),
        finalIndicator=valuation_result.get("final_indicator"),
        degraded=is_degraded(final_state, valuation_result),
        failureReason=failure_reason(final_state, valuation_result),
        warnings=workflow_warnings(final_state),
        evidenceConfidence=evidence_confidence(final_state),
        # ORCH-06/AIREPORT-02: 워크플로가 이미 산출한 리치 근거를 API로 풀스루한다.
        missingInformation=[str(item) for item in (valuation_result.get("missing_information") or []) if item],
        keyEvidence=build_key_evidence(valuation_result),
        judgementGrounds=[str(item) for item in (valuation_result.get("decision_rationale") or []) if item],
        businessCheckRequests=[str(item) for item in (valuation_result.get("required_actions") or []) if item],
        externalSources=build_external_sources(final_state, valuation_result),
        appliedValuationConfig=applied_config,
        generatedAt=datetime.now(timezone.utc),
    )


def run_workflow_guarded(initial_state: PatentWorkflowState) -> PatentWorkflowState:
    # 세마포어로 동시 평가 수를 제한한다(초과 시 429 백프레셔). 파이썬 스레드는 취소가 불가능하므로
    # 서버측 단축 타임아웃으로 요청만 끊으면 워크플로우 스레드가 슬롯을 계속 점유해 용량이 고갈된다.
    # 따라서 요청 스레드에서 직접 실행하고 finally에서 슬롯을 확실히 반납한다.
    # 인터랙티브 경로의 시간 제한은 BE 비동기 잡(긴 타임아웃 + 상태 폴링)이 담당한다.
    if not _EVALUATE_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Agent evaluate capacity exceeded.")
    try:
        return run_workflow(initial_state)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent workflow failed: {exc.__class__.__name__}") from exc
    finally:
        _EVALUATE_SEMAPHORE.release()


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
        # 계약 C1: 요청의 가치평가 기준을 보정해 워크플로 전체(축 reconcile/최종 합산/프롬프트)에 전달.
        "valuation_config": resolve_valuation_config(request.valuationConfig),
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
    # EVID-02: DART 재무근거(opt-in) — 요청 metadata에 corp_code가 있으면 근거 수집에 전달.
    dart_corp_code = normalize_optional_identifier((request.metadata or {}).get("dart_corp_code"))
    if dart_corp_code:
        user_input["dart_corp_code"] = dart_corp_code
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


_MAX_AXIS_EVIDENCE_DETAILS = 5


def build_evidence_index(evidence_bundle: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in evidence_bundle or []:
        evidence_id = item.get("evidence_id")
        if evidence_id and evidence_id not in index:
            index[str(evidence_id)] = item
    return index


def build_axis_evidence_details(
    axis_result: dict[str, Any], evidence_index: dict[str, dict[str, Any]]
) -> list[EvidenceDetail]:
    # ORCH-06/AIREPORT-02: 축의 evidence_ids를 근거 번들과 매핑해 클릭형 출처(제목/URL)를 채운다.
    details: list[EvidenceDetail] = []
    for evidence_id in axis_result.get("evidence_ids") or []:
        item = evidence_index.get(str(evidence_id))
        if not item:
            continue
        text = (
            item.get("title")
            or item.get("summary")
            or item.get("context")
            or item.get("content")
            or item.get("source")
            or str(evidence_id)
        )
        source = None
        if item.get("url") or item.get("source") or item.get("title"):
            source = SourceRef(title=item.get("title") or item.get("source"), url=item.get("url"))
        details.append(EvidenceDetail(text=str(text)[:500], source=source))
        if len(details) >= _MAX_AXIS_EVIDENCE_DETAILS:
            break
    return details


def valuation_scores(
    valuation_result: dict[str, Any], evidence_bundle: list[dict[str, Any]] | None = None
) -> list[PatentEvaluationScore]:
    axes = valuation_result.get("axes") or {}
    evidence_index = build_evidence_index(evidence_bundle or [])
    scores = []
    for axis in ["legal", "technology", "market", "business_fit"]:
        axis_result = axes.get(axis) or {}
        scores.append(
            PatentEvaluationScore(
                category=axis_result.get("label") or axis,
                score=axis_result.get("score"),
                grade=axis_result.get("grade"),
                evidence=axis_result.get("rationale") or "평가 근거가 생성되지 않았습니다.",
                evidenceDetails=build_axis_evidence_details(axis_result, evidence_index),
            )
        )
    return scores


def build_key_evidence(valuation_result: dict[str, Any]) -> str | None:
    # ORCH-06/AIREPORT-02: 핵심 근거 = 가장 높은 점수 축의 근거 문장(가장 강한 지지 근거).
    axes = valuation_result.get("axes") or {}
    candidates = [
        (int(axis.get("score") or 0), str(axis.get("rationale") or "").strip(), axis.get("label") or key)
        for key, axis in axes.items()
        if isinstance(axis, dict)
    ]
    candidates = [item for item in candidates if item[1]]
    if not candidates:
        return None
    _, rationale, label = max(candidates, key=lambda item: item[0])
    return f"{label}: {rationale}"


def build_external_sources(
    state: PatentWorkflowState, valuation_result: dict[str, Any]
) -> list[SourceRef]:
    # ORCH-06/AIREPORT-02: 실제 사용된 외부 근거(뉴스/산업/공시/포트폴리오)의 제목·URL을 노출한다.
    references = build_evidence_references(state, valuation_result)
    seen: set[tuple[str | None, str | None]] = set()
    sources: list[SourceRef] = []
    for ref in references:
        key = (ref.get("title"), ref.get("url"))
        if key in seen:
            continue
        seen.add(key)
        sources.append(SourceRef(title=ref.get("title") or ref.get("source"), url=ref.get("url")))
    return sources


def valuation_average_score(valuation_result: dict[str, Any]) -> float | None:
    average_score = valuation_result.get("average_score")
    if isinstance(average_score, (int, float)):
        return round(float(average_score), 1)
    total_score = valuation_result.get("total_score")
    if isinstance(total_score, (int, float)):
        return round(float(total_score) / 4, 1)
    return None


def final_grade_for_average(
    average_score: float | None, applied_config: dict[str, Any] | None = None
) -> str | None:
    # 등급 컷오프 산정은 agents.valuation_axes.common.grade_for_score 한 곳으로 통일한다(중복 제거).
    if average_score is None:
        return None
    cutoffs = (applied_config or {}).get("gradeCutoffs")
    return grade_for_score(average_score, cutoffs)


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
        # market 등 일부 축은 실패를 warning이 아니라 missing_reason('..._failed:..')에 기록한다.
        missing_reason = value.get("missing_reason")
        if missing_reason and "_failed:" in str(missing_reason):
            warnings.append(str(missing_reason))
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
