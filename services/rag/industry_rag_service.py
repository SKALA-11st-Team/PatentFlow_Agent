from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import settings
from rag.industry_vector_store import (
    DEFAULT_CHUNKS_PATH,
    DEFAULT_DB_PATH,
    EmbeddingModel,
    IndustryVectorStore,
    build_industry_vector_db,
)
from services.evidence.store_service import now_iso, safe_filename


DEFAULT_INDUSTRY_RAG_OUTPUT_DIR = settings.output_dir / "industry_rag"

# EVID-03: 산업 RAG 코퍼스에 존재하는 산업 메타데이터 라벨. 특허 기술/사업 분야를 이 중 하나로
# 매핑해 검색을 해당 산업으로 한정함으로써 무관 산업(예: AI 특허에 조선/철강) 청크 혼입을 막는다.
# "공통"은 산업 공통 거시 전망 청크로, 산업이 매핑되면 함께 포함해 교차 맥락은 유지한다.
INDUSTRY_COMMON_LABEL = "공통"

# 라벨별 키워드. 한글 키워드는 부분문자열로, 영문/숫자 키워드는 단어 경계로 매칭한다.
INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI": (
        "인공지능", "머신러닝", "딥러닝", "기계학습", "신경망", "자연어", "컴퓨터비전",
        "강화학습", "생성형", "대규모언어", "초거대", "ai", "ml", "llm", "genai",
    ),
    "반도체": (
        "반도체", "웨이퍼", "파운드리", "포토", "식각", "증착", "집적회로", "패키징",
        "semiconductor", "wafer", "foundry", "euv",
    ),
    "이차전지": (
        "이차전지", "배터리", "리튬", "양극재", "음극재", "전해질", "분리막",
        "battery", "lithium", "ess",
    ),
    "디스플레이": (
        "디스플레이", "유기발광", "발광다이오드", "화소", "패널", "oled", "lcd", "qled", "display",
    ),
    "자동차": (
        "자동차", "차량", "전기차", "자율주행", "모빌리티", "파워트레인",
        "vehicle", "automotive", "adas",
    ),
    "정보통신기기": (
        "이동통신", "무선통신", "기지국", "네트워크장비", "라우터", "사물인터넷", "단말기",
        "telecom", "5g", "6g", "iot",
    ),
    "가전": (
        "가전", "냉장고", "세탁기", "에어컨", "청소기", "공기청정기", "appliance",
    ),
    "바이오헬스": (
        "바이오", "헬스케어", "의료기기", "제약", "신약", "진단", "임상", "유전자", "생명공학",
        "medical", "healthcare", "biotech", "pharma",
    ),
    "핀테크": (
        "핀테크", "금융", "결제", "자산배분", "포트폴리오", "로보어드바이저", "보험", "은행", "웰스",
        "fintech", "payment",
    ),
    "조선": (
        "조선", "선박", "해양플랜트", "운반선", "의장", "shipbuilding", "vessel",
    ),
    "철강": (
        "철강", "제철", "강판", "압연", "고로", "steel",
    ),
    "석유화학": (
        "석유화학", "폴리머", "고분자", "수지", "촉매", "petrochemical", "polymer",
    ),
    "정유": (
        "정유", "원유", "윤활유", "정제", "refinery",
    ),
    "섬유": (
        "섬유", "원단", "직물", "방적", "의류", "textile", "fabric",
    ),
    "일반기계": (
        "공작기계", "산업용로봇", "자동화설비", "감속기", "베어링", "유압", "공압",
        "machinery", "robot",
    ),
}

# 동점 시 우선순위(앞쪽일수록 우선). 더 구체적인 수직 산업을 범용 AI보다 앞세운다.
INDUSTRY_TIE_PRIORITY: tuple[str, ...] = (
    "반도체", "이차전지", "디스플레이", "자동차", "바이오헬스", "핀테크",
    "조선", "철강", "석유화학", "정유", "섬유", "정보통신기기", "가전",
    "일반기계", "AI",
)


def _industry_keyword_hits(blob_lower: str, keywords: tuple[str, ...]) -> int:
    hits = 0
    for keyword in keywords:
        token = keyword.lower()
        if re.search(r"[가-힣]", token):
            if token in blob_lower:
                hits += 1
        elif re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", blob_lower):
            hits += 1
    return hits


# @author 배세은
# @date 2026-05-06
# @relatedFR FR-007
# @relatedUI UI-005
# @description 특허의 자유 텍스트(기술/사업 분야 등)를 산업 RAG 코퍼스의 산업 라벨 하나로 매핑한다.
# 시장성 축 근거를 해당 산업으로 한정 검색하기 위한 전처리(무관 산업 청크 혼입 방지).
def match_industry(text: str | None) -> str | None:
    """자유 텍스트(기술/사업 분야 등)를 코퍼스 산업 라벨 하나로 매핑한다. 매칭 없으면 None."""
    blob = " ".join(str(text or "").split()).lower()
    if not blob:
        return None
    scored = [
        (label, _industry_keyword_hits(blob, keywords))
        for label, keywords in INDUSTRY_KEYWORDS.items()
    ]
    best_score = max((score for _, score in scored), default=0)
    if best_score <= 0:
        return None
    candidates = [label for label, score in scored if score == best_score]
    if len(candidates) == 1:
        return candidates[0]
    for label in INDUSTRY_TIE_PRIORITY:
        if label in candidates:
            return label
    return candidates[0]


# @relatedFR FR-007
# @relatedUI UI-005
# @description 특허 메타데이터·전처리 섹션을 모아 산업 라벨로 매핑하고, 시장성 근거 검색에 쓸
# 필터 목록([매핑산업, "공통"])을 만든다. 매핑 실패 시 None을 반환해 전체 검색으로 안전 폴백한다.
def resolve_patent_industries(
    patent_context: dict[str, Any] | None = None,
    preprocessed_patent: dict[str, Any] | None = None,
) -> list[str] | None:
    """특허 분야를 산업 라벨로 매핑해 검색 필터 목록을 만든다.

    매핑되면 ``[매핑산업, "공통"]``을, 매핑 실패 시 ``None``(필터 미적용=전체 검색)을 반환한다.
    실패 시 None을 반환해 기존 동작으로 안전하게 폴백한다.
    """
    parts: list[str] = []
    for source in (patent_context or {}, (preprocessed_patent or {}).get("metadata") or {}):
        for key in ("technology_area", "business_area", "related_product", "title_final", "title"):
            value = source.get(key)
            if value:
                parts.append(str(value))
    sections = (preprocessed_patent or {}).get("sections") or {}
    for key in ("technical_field", "abstract"):
        value = sections.get(key)
        if value:
            parts.append(str(value))
    industry = match_industry(" ".join(parts))
    if not industry:
        return None
    if industry == INDUSTRY_COMMON_LABEL:
        return [INDUSTRY_COMMON_LABEL]
    return [industry, INDUSTRY_COMMON_LABEL]


def index_industry_evidence(
    *,
    chunks_path: Path | str = DEFAULT_CHUNKS_PATH,
    database_url: str | None = DEFAULT_DB_PATH,
    reset: bool = True,
    embedding_model: EmbeddingModel | None = None,
) -> int:
    return build_industry_vector_db(
        chunks_path=chunks_path,
        database_url=database_url,
        reset=reset,
        embedding_model=embedding_model,
    )


# @relatedFR FR-007
# @relatedUI UI-005
# @description 산업/시장 RAG 벡터스토어를 검색해 시장성 평가 근거 항목(출처·발행연도·본문·스코어)을
# 반환한다. 멀티쿼리 배치에서는 상위가 주입한 store를 재사용해 임베딩 모델·캐시·DB 연결을 공유한다.
def search_industry_evidence(
    query: str,
    *,
    industry: str | None = None,
    industries: list[str] | None = None,
    top_k: int = 5,
    database_url: str | None = DEFAULT_DB_PATH,
    embedding_model: EmbeddingModel | None = None,
    store: IndustryVectorStore | None = None,
) -> list[dict[str, Any]]:
    # EVID-09: 멀티쿼리 배치에서 상위가 store를 주입하면 임베딩 모델·쿼리 캐시·DB 연결을 재사용한다.
    if store is None:
        store = IndustryVectorStore(database_url, embedding_model=embedding_model)
    collected_at = now_iso()
    return [
        {
            "evidence_id": result.chunk_id,
            "source": result.metadata.get("source_name"),
            "source_type": result.metadata.get("source_type", "industry_report"),
            "title": result.metadata.get("heading"),
            "url": None,
            "published_at": str(result.metadata.get("published_year")) if result.metadata.get("published_year") else None,
            "published_at_precision": "year" if result.metadata.get("published_year") else None,
            "collected_at": collected_at,
            "industry": result.metadata.get("industry"),
            "page": result.metadata.get("page"),
            "heading": result.metadata.get("heading"),
            "context": result.text,
            "confidence": None,
            "score": result.score,
            "metadata": result.metadata,
        }
        for result in store.search(query, top_k=top_k, industry=industry, industries=industries)
    ]


def build_patent_industry_query(preprocessed_patent: dict[str, Any]) -> str:
    metadata = preprocessed_patent.get("metadata") or {}
    sections = preprocessed_patent.get("sections") or {}
    title = str(metadata.get("title") or "").strip()
    section_parts = [
        sections.get("abstract"),
        sections.get("technical_field"),
        sections.get("problem"),
        sections.get("solution"),
    ]
    claims = preprocessed_patent.get("claims") or []
    representative_claim = next(
        (claim.get("text") for claim in claims if claim.get("is_independent") and claim.get("text")),
        claims[0].get("text") if claims and isinstance(claims[0], dict) else None,
    )
    parts = [title, *section_parts, representative_claim]
    return "\n\n".join(
        compact[:1500]
        for value in parts
        if (compact := " ".join(str(value or "").split()))
    )[:6000]


def compact_rag_queries(queries: list[str]) -> list[str]:
    compacted: list[str] = []
    seen: set[str] = set()
    for query in queries:
        value = " ".join(str(query or "").split())
        if not value or value in seen:
            continue
        seen.add(value)
        compacted.append(value)
    return compacted


def dedupe_industry_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id and evidence_id in seen:
            continue
        if evidence_id:
            seen.add(evidence_id)
        deduped.append(item)
    return deduped


# @relatedFR FR-007
# @relatedUI UI-005
# @description 특허 1건의 산업/시장성 RAG 근거를 다중 쿼리로 수집·중복제거하고 결과를 아티팩트로 저장하는
# 메인 진입점. 쿼리가 비면 특허 메타/섹션으로 폴백 쿼리를 구성하고, 배치 전체에 단일 store를 재사용한다.
def search_and_save_patent_industry_evidence(
    *,
    preprocessed_patent: dict[str, Any],
    patent_id: str | int | None = None,
    rag_queries: list[str] | None = None,
    top_k: int = 3,
    industry: str | None = None,
    industries: list[str] | None = None,
    database_url: str | None = DEFAULT_DB_PATH,
    embedding_model: EmbeddingModel | None = None,
    output_dir: Path | str = DEFAULT_INDUSTRY_RAG_OUTPUT_DIR,
    save: bool = True,
) -> dict[str, Any]:
    queries = compact_rag_queries(rag_queries or [])
    if not queries:
        fallback_query = build_patent_industry_query(preprocessed_patent)
        if fallback_query:
            queries = [fallback_query]
    if not queries:
        return {
            "query": "",
            "queries": [],
            "items": [],
            "output_path": None,
            "warning": "title and abstract are both empty",
        }

    # EVID-09: 쿼리마다 store/연결을 새로 만들던 것을 배치 단위 단일 store로 통일 — 임베딩 모델·쿼리 캐시
    # 재사용 + DB 연결 1회 재사용(쿼리당 connect 제거). open/close는 fake store(테스트) 호환 위해 guard.
    shared_store = IndustryVectorStore(database_url, embedding_model=embedding_model)
    opener = getattr(shared_store, "open", None)
    if callable(opener):
        opener()
    collected_items: list[dict[str, Any]] = []
    try:
        for query in queries:
            query_items = search_industry_evidence(
                query,
                industry=industry,
                industries=industries,
                top_k=top_k,
                store=shared_store,
            )
            collected_items.extend({**item, "rag_query": query} for item in query_items)
    finally:
        closer = getattr(shared_store, "close", None)
        if callable(closer):
            closer()
    items = dedupe_industry_items(collected_items)
    query = queries[0] if len(queries) == 1 else "\n".join(queries)
    output_path = None
    if save:
        output_path = save_industry_rag_result(
            patent_id=patent_id,
            query=query,
            queries=queries,
            items=items,
            output_dir=output_dir,
        )
    return {
        "query": query,
        "queries": queries,
        "items": items,
        "industries": list(industries) if industries else None,
        "output_path": str(output_path) if output_path else None,
        "warning": None,
    }


def save_industry_rag_result(
    *,
    patent_id: str | int | None,
    query: str,
    queries: list[str] | None = None,
    items: list[dict[str, Any]],
    output_dir: Path | str = DEFAULT_INDUSTRY_RAG_OUTPUT_DIR,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(str(patent_id) if patent_id is not None else 'unknown')}_industry_rag_top3.json"
    path = directory / filename
    payload = {
        "source_type": "industry_report",
        "source": "pgvector",
        "query": query,
        "queries": queries or ([query] if query else []),
        "patent_id": str(patent_id) if patent_id is not None else None,
        "top_k": len(items),
        "collected_at": now_iso(),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
