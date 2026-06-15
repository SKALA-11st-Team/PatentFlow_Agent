from __future__ import annotations

import hmac
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.field_recommendation import recommend_fields
from agents.valuation import CORE_VALUATION_AXES
from agents.valuation_axes.common import grade_for_score
from agents.writing.final_report import build_evidence_references, parse_report_sections
from app.config import settings
from app.main import save_outputs
from schemas.valuation import resolve_valuation_config
from services.observability import progress_registry
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


# BE가 공유 PG에서 보유한 특허 메타데이터. 제공되면 에이전트는 로컬 patents.sqlite3 조회를
# 건너뛰고 이 값으로 patent_structured를 구성한다(특허 본문은 여전히 KIPRIS/PDF에서 수집).
# 누락 시 기존대로 번호로 로컬 DB를 조회한다(에이전트 단독 실행/구 BE 호환).
class PatentMetadata(BaseModel):
    title: str | None = None
    draftTitle: str | None = None
    businessArea: str | None = None
    technologyArea: str | None = None
    productName: str | None = None
    country: str | None = None
    coApplicants: str | None = None
    jointApplication: bool | None = None
    applicationDate: str | None = None
    registrationDate: str | None = None
    expectedExpirationDate: str | None = None


class PatentEvaluationRequest(BaseModel):
    managementNumber: str | None = None
    applicationNumber: str | None = None
    registrationNumber: str | None = None
    title: str | None = None
    patent: PatentMetadata | None = None
    noSave: bool = False
    useLlmSupervisor: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 계약 C1: BE가 전달하는 가치평가 기준(축 가중치/등급 컷오프/유지 임계/subscore 배점).
    # 누락 시 기본값으로 평가한다(구 BE 호환). 잘못된 값은 resolve_valuation_config가 보정한다.
    valuationConfig: dict[str, Any] | None = None


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
    # FE 카드용 구조화 요약(요약본을 한 번 더 요약). 생성 경로가 없는 배포에서는 null.
    # 필드: one_line_summary, problem, core_idea, key_components[], operation_steps[], expected_effect.
    summaryBrief: dict[str, Any] | None = None
    valuationReportMarkdown: str | None = None
    # 보고서 본문을 섹션별로 분리한 구조화 필드(헤더 제외, 줄글 본문). FE가 섹션별로 렌더링할 수 있다.
    # 키: evaluationScope(2. 평가대상 및 범위), judgmentBasis(3. 판단 근거),
    #     axisDetails(4. 평가축별 상세 근거), roleChecklist(5. 역할별 확인 사항), finalOpinion(6. 최종 검토 의견).
    reportSections: dict[str, str] = Field(default_factory=dict)
    artifactDir: str | None = None
    totalScore: int | None = None
    averageScore: float | None = None
    finalGrade: str | None = None
    # 사업 연계성 보정: business_fit 점수가 기준(appliedValuationConfig.businessFitOverrideThreshold)
    # 이상이면 3축 등급과 무관하게 AI 검토 의견을 '유지 권고'로 끌어올린다. 등급은 3축 기준 그대로라
    # 등급↔권고가 어긋날 수 있어, FE가 보정 사유 배지를 띄울 수 있도록 플래그·점수를 함께 노출한다.
    businessFitOverride: bool = False
    businessFitScore: int | None = None
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


# FR-006/UI-005: 평가 진행 단계 응답. BE(Spring)가 그대로 프록시하므로 필드명은 camelCase 계약 고정.
class PatentEvaluationProgressResponse(BaseModel):
    patentId: str
    stage: str
    stageLabel: str
    updatedAt: str


@app.get("/api/v1/ai/patents/{patent_id}/evaluate/progress")
def get_patent_evaluation_progress(patent_id: str) -> PatentEvaluationProgressResponse:
    entry = progress_registry.get(patent_id)
    if not entry:
        raise HTTPException(status_code=404, detail="no progress")
    return PatentEvaluationProgressResponse(patentId=patent_id, **entry)


@app.post("/api/v1/ai/patents/{patent_id}/evaluate")
def evaluate_patent(patent_id: str, request: PatentEvaluationRequest) -> PatentEvaluationResponse:
    initial_state = PatentWorkflowState(user_input=build_api_user_input(patent_id, request))
    final_state = run_workflow_guarded(initial_state)

    # 영속화(부가 기능) 실패가 이미 계산된 평가 응답을 통째로 날리지 않도록 best-effort로 처리한다.
    # 실패는 warnings로 표면화해 BE/FE가 인지하되, 핵심 PatentEvaluationResponse는 정상 반환한다.
    save_warnings: list[str] = []
    if not request.noSave:
        try:
            save_outputs(final_state)
        except Exception as exc:  # noqa: BLE001 - 저장 실패는 경고로만 표면화하고 평가 응답은 유지
            save_warnings.append(f"artifact_save_failed:{exc.__class__.__name__}")
    summary_result = final_state.summary_result or {}
    valuation_result = final_state.valuation_result or {}
    valuation_markdown = (valuation_result.get("final_report_markdown") or "").strip()
    summary_markdown = (summary_result.get("summary_markdown") or "").strip()

    evidence_bundle = final_state.evidence_bundle or []
    applied_config = valuation_result.get("applied_config") or final_state.user_input.get("valuation_config")
    degraded = is_degraded(final_state, valuation_result)
    return PatentEvaluationResponse(
        patentId=patent_id,
        scores=valuation_scores(valuation_result, evidence_bundle),
        # 평가 미산출/degraded로 recommendation이 비면 '포기(ABANDON)'로 단정하지 않고
        # 중립값 '추가 정보 필요'(BE Recommendation.REVIEW_AGAIN)를 기본값으로 둔다(BE fallback과 정렬).
        recommendation=valuation_result.get("recommendation") or ("추가 정보 필요" if degraded else "포기 검토"),
        summaryMarkdown=summary_markdown or None,
        summaryBrief=summary_result.get("summary_brief") or None,
        valuationReportMarkdown=valuation_markdown or None,
        reportSections=build_report_sections(valuation_markdown),
        artifactDir=str(final_state.user_input.get("artifact_dir") or "") or None,
        totalScore=valuation_result.get("total_score"),
        averageScore=valuation_average_score(valuation_result),
        finalGrade=valuation_result.get("final_grade")
        or final_grade_for_average(valuation_average_score(valuation_result), applied_config),
        businessFitOverride=bool(valuation_result.get("business_fit_override")),
        businessFitScore=valuation_result.get("business_fit_score"),
        degraded=degraded,
        failureReason=failure_reason(final_state, valuation_result),
        warnings=dedupe(workflow_warnings(final_state) + save_warnings),
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
        # FR-006: 평가 진행 단계 레지스트리 키. BE가 progress 조회에 쓰는 경로의 patent_id를 그대로 사용한다.
        "progress_patent_id": patent_id,
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
    # BE가 특허 메타데이터를 넘기면 로컬 patents.sqlite3 조회 없이 patent_record를 구성한다.
    # patent_fetch_node가 이 레코드를 patent_structured로 쓰고, 본문은 application_number로 KIPRIS/PDF에서 수집한다.
    if request.patent is not None:
        meta = request.patent
        record = {
            "id": patent_id,
            "management_number": management_number,
            "application_number": application_number,
            "registration_number": registration_number,
            "title_final": meta.title,
            "title": meta.title,
            "title_draft": meta.draftTitle,
            "business_area": meta.businessArea,
            "technology_area": meta.technologyArea,
            "related_product": meta.productName,
            "country": meta.country,
            "joint_application": meta.jointApplication,
            "joint_applicant_name": meta.coApplicants,
            # 에이전트 status는 법적 상태("등록"/"출원") 컨벤션 — 등록번호 유무로 충실히 파생한다.
            "status": "등록" if registration_number else "출원",
            "application_date": meta.applicationDate,
            "registration_date": meta.registrationDate,
            "expected_expiration_date": meta.expectedExpirationDate,
        }
        user_input["patent_record"] = {key: value for key, value in record.items() if value is not None}
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


# 보고서 최상위 섹션 번호 → FE 계약용 키. 1번(한눈에 보는 검토 결과)은 구조화 필드로 이미
# 제공되므로 제외하고, 2~6번 본문을 섹션별로 내보낸다.
REPORT_SECTION_KEYS = {
    "2": "evaluationScope",
    "3": "judgmentBasis",
    "4": "axisDetails",
    "5": "roleChecklist",
    "6": "finalOpinion",
}


def build_report_sections(markdown: str | None) -> dict[str, str]:
    """가치평가 보고서 마크다운을 `## N.` 최상위 섹션 단위로 분리해 FE 계약 키로 매핑한다(2~6번 본문).

    섹션 분리는 parse_report_sections(단일 출처)에 위임해 report_validation_node의 누락 검증과
    동일 기준을 보장한다. 4번의 4.1~4.4 같은 `### ` 하위 섹션은 본문에 그대로 포함된다.
    """
    sections: dict[str, str] = {}
    for number, body in parse_report_sections(markdown).items():
        key = REPORT_SECTION_KEYS.get(number)
        if key:
            sections[key] = body
    return sections


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
                # 축 근거 미산출(degraded) 시 출처 결손 표준 표현으로 표기한다.
                evidence=axis_result.get("rationale") or "추가 확인 필요(평가 근거 미산출)",
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
        # total_score는 권리성·기술성·시장성 3축 합(max 300)이므로 핵심 축 수로 나눠 평균을 환산한다.
        return round(float(total_score) / len(CORE_VALUATION_AXES), 1)
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
        return external_evidence_empty_reason(state)
    return "일부 근거 수집 단계가 실패해 제한된 근거로 AI 평가가 생성되었습니다."


def external_evidence_empty_reason(state: PatentWorkflowState) -> str:
    """evidence_bundle이 비었을 때 가능한 한 구체적인 원인을 돌려준다.

    '근거 0건'은 정상적으로 외부 결과가 없는 경우와, 검색 API 키 미설정·외부 호출 전면
    실패처럼 운영 조치가 필요한 경우가 섞여 있다. 후자를 일반 문구로 덮으면 운영자가
    키 누락을 정상 결과로 오인한다 — query_plan에 저장된 진단 신호로 원인을 구분한다.
    """
    query_plan = state.query_plan or {}
    external = query_plan.get("external_evidence") or {}
    skax = query_plan.get("skax_site_search") or {}
    search_warnings = query_plan.get("search_warnings")
    signal = " ".join(
        str(item)
        for item in [
            *(search_warnings if isinstance(search_warnings, list) else []),
            external.get("missing_reason"),
            skax.get("warning"),
        ]
        if item
    )
    # 검색 자격증명(키) 미설정 — 배포 환경에서 가장 흔한 원인. 명시해 즉시 조치하게 한다.
    if "is not set" in signal or "미설정" in signal:
        return (
            "외부 검색 API 키가 설정되지 않아 외부 근거를 수집하지 못했습니다. "
            "검색 API 키 설정을 확인해 주세요(제한된 근거로 AI 평가가 생성되었습니다)."
        )
    # 외부 호출이 전부 실패(게이트웨이 미도달 등)해 근거를 확보하지 못한 경우.
    if external.get("gateway_unreachable") or external.get("missing_reason"):
        return (
            "외부 근거 수집 호출이 모두 실패해 외부 근거를 확보하지 못했습니다. "
            "제한된 근거로 AI 평가가 생성되었습니다."
        )
    return "외부 근거 수집 결과가 없어 AI 평가 신뢰도가 낮습니다."


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
