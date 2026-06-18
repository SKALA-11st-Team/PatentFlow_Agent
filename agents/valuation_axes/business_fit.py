from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import grade_for_score, normalize_text
from agents.valuation_axes.payload_common import build_base_input_payload
from schemas.valuation import DEFAULT_SUBSCORE_WEIGHTS
from workflow.state import PatentWorkflowState


# @author 배세은
# @date 2026-05-19
# @relatedFR FR-006, FR-007
# @relatedUI UI-005
# @description 사업 연계성(business_fit) 평가 축. 공식 사업 근거·제품/기능 직접 일치·사업 맥락 적합성으로
# 채점한다. 이 점수는 종합 합산에 들어가지 않고, 기준 이상이면 AI 검토 의견을 '유지 권고'로 끌어올리는
# 오버라이드로만 작용한다(BUSINESS_ALIGNMENT 축, 라이프사이클 경제성 아님).
AXIS = "business_fit"
LABEL = "사업 연계성"
PROMPT_PATH = "valuation/valuation_business_fit.md"
DESCRIPTION_TEXT_LIMIT = 1000
EVIDENCE_EXCERPT_LIMIT = 1500
STOPWORDS = {
    "시스템",
    "방법",
    "장치",
    "기반",
    "적용",
    "적용한",
    "반영",
    "반영한",
    "예측을",
    "제공",
    "관리",
    "관련",
    "모델",
    "프로그램",
    "서비스",
    "플랫폼",
    "및",
    "위한",
    "통한",
    "과정",
    # 명세서·청구항에 흔한 연결어/지시어. 단독 핵심어로 잡히면 매칭 분모만 오염시킨다.
    "따른",
    "따라",
    "대한",
    "관한",
    "의한",
    "위해",
    "통해",
    "대해",
    "관해",
    "이용한",
    "이용하여",
    "수행",
    "포함",
    "포함하는",
}
# VAL-08: 영문/타산업 특허 핵심어 추출이 한국어 STOPWORDS에만 의존해 무력화되던 문제 보완.
# 영문 특허 명칭의 일반어(system/method/apparatus 등)·연결어를 대소문자 무관하게 불용어로 거른다.
ENGLISH_STOPWORDS = {
    "system", "systems", "method", "methods", "apparatus", "device", "devices",
    "process", "processes", "means", "unit", "units", "module", "modules",
    "assembly", "component", "components", "structure", "arrangement",
    "and", "for", "the", "with", "based", "using", "via", "said", "comprising",
    "wherein", "having", "configured", "thereof", "from", "into", "between",
}


def is_stopword(text: str) -> bool:
    """한국어·영문 불용어를 대소문자 무관하게 판정한다(VAL-08)."""
    lowered = text.lower()
    return lowered in STOPWORDS or lowered in ENGLISH_STOPWORDS
BUSINESS_FIT_SUBSCORE_MAX = dict(DEFAULT_SUBSCORE_WEIGHTS["business_fit"])


def business_fit_subscore_max_map(state: PatentWorkflowState | None) -> dict[str, int]:
    """운영 설정(valuation_config.subscoreWeights.business_fit)이 있으면 그 배점을 쓴다."""
    user_input = state.user_input if state is not None and isinstance(state.user_input, dict) else {}
    config = user_input.get("valuation_config")
    configured = ((config or {}).get("subscoreWeights") or {}).get("business_fit") or {}
    return {
        key: int(configured.get(key, default_value))
        for key, default_value in BUSINESS_FIT_SUBSCORE_MAX.items()
    }


# @relatedFR FR-006, FR-007
# @relatedUI UI-005
# @description 사업 연계성 축 실행: 근거 선택→프롬프트 구성→LLM 채점 후 결과 반환.
def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = build_input_payload(state=state, evidence=evidence)
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
        axis=AXIS,
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    return reconcile_business_fit_scores(result, state=state)


def reconcile_business_fit_scores(
    result: dict[str, Any], *, state: PatentWorkflowState | None = None
) -> dict[str, Any]:
    subscores = result.get("subscores") if isinstance(result.get("subscores"), dict) else {}
    reconciled: dict[str, Any] = {}
    total = 0
    total_max = 0
    for key, max_score in business_fit_subscore_max_map(state).items():
        item = subscores.get(key) if isinstance(subscores.get(key), dict) else {}
        score = coerce_int(item.get("score"))
        score = max(0, min(max_score, score or 0))
        reconciled[key] = {**item, "score": score, "max_score": max_score}
        total += score
        total_max += max_score
    # 배점 합이 100이 아니어도 축 점수는 0~100 스케일을 유지한다(설정 배점은 비율로 해석).
    total = round(total * 100 / total_max) if total_max > 0 else 0
    total = max(0, min(100, total))
    return {
        **result,
        "score": total,
        "grade": grade_for_score(total),
        "subscores": {**subscores, **reconciled},
    }


def coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_patent_structure_payload(state: PatentWorkflowState) -> dict[str, Any]:
    """구조화 결과(target_structure)에서 제품·기능 매칭 판단에 필요한 부분만 경량화해 전달한다.

    key_elements는 식별·역할 정보만, key_flow는 구성요소 간 관계만 남긴다(명세서 위치·도면 등 제외).
    """
    structure = state.target_structure if isinstance(state.target_structure, dict) else {}
    key_elements = [
        {
            "key_element_id": element.get("key_element_id"),
            "key_element_name": element.get("key_element_name"),
            "why_essential": element.get("why_essential"),
            "core_role": element.get("core_role"),
        }
        for element in (structure.get("key_elements") or [])
        if isinstance(element, dict)
    ]
    key_flow = [
        {
            "key_element_id": flow.get("key_element_id"),
            "next_key_element_id": flow.get("next_key_element_id"),
            "relation_summary": flow.get("relation_summary"),
            "coupling_strength": flow.get("coupling_strength"),
        }
        for flow in (structure.get("key_flow") or [])
        if isinstance(flow, dict)
    ]
    return {"key_elements": key_elements, "key_flow": key_flow}


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base_input_payload(state=state, evidence=evidence)
    patent_description = build_business_fit_patent_description(state)
    skax_evidence = build_skax_official_evidence_summary(evidence, state=state)
    sk_owned_media_evidence = build_sk_owned_media_evidence_summary(evidence, state=state)
    sk_ax_relevant_news_evidence = build_sk_ax_relevant_news_evidence_summary(evidence, state=state)
    payload["business_fit_context"] = {
        "patent_description": patent_description,
        "patent_structure": build_patent_structure_payload(state),
        "target_source_status": build_target_source_status(state),
        "skax_official_evidence": skax_evidence,
        "sk_owned_media_evidence": sk_owned_media_evidence,
        "sk_ax_relevant_news_evidence": sk_ax_relevant_news_evidence,
        "quantitative_metrics": build_business_fit_quantitative_metrics(
            state=state,
            evidence=evidence,
        ),
    }
    return payload


def build_target_source_status(state: PatentWorkflowState) -> dict[str, Any]:
    preprocessed = state.preprocessed_patent or {}
    claims = preprocessed.get("claims") if isinstance(preprocessed.get("claims"), list) else []
    sections = preprocessed.get("sections") if isinstance(preprocessed.get("sections"), dict) else {}
    claim_stats = preprocessed.get("claim_stats") if isinstance(preprocessed.get("claim_stats"), dict) else {}
    active_claim_count = coerce_int(claim_stats.get("active_claim_count"))
    if active_claim_count is None:
        active_claim_count = len([claim for claim in claims if normalize_text(claim.get("text"))])
    description_fields = (
        sections.get("technical_field"),
        sections.get("background"),
        sections.get("problem"),
        sections.get("solution"),
        sections.get("effect"),
        sections.get("detailed_description"),
    )
    return {
        "claims_available": active_claim_count > 0,
        "active_claim_count": active_claim_count,
        "abstract_available": bool(normalize_text(sections.get("abstract"))),
        "description_available": any(normalize_text(value) for value in description_fields),
        "authority": "preprocessed_patent",
    }


def build_business_fit_patent_description(state: PatentWorkflowState) -> dict[str, Any]:
    patent = state.patent_structured or {}
    summary = state.summary_result or {}
    preprocessed = state.preprocessed_patent or {}
    preprocessed_metadata = preprocessed.get("metadata") or {}
    sections = preprocessed.get("sections") or {}
    agent_summary = ((preprocessed.get("agent_inputs") or {}).get("summary") or {})
    kipris_metadata = (state.kipris_api_data or {}).get("metadata") or {}
    kipris_sections = (state.kipris_api_data or {}).get("sections") or {}

    return {
        "management_number": first_text(patent.get("management_number"), patent.get("관리번호")),
        "title": first_text(
            patent.get("title"),
            patent.get("발명의 명칭"),
            summary.get("title"),
            preprocessed_metadata.get("title"),
            kipris_metadata.get("title"),
        ),
        "title_final": first_text(
            patent.get("title_final"),
            patent.get("발명의 명칭(최종)"),
            preprocessed_metadata.get("title"),
            kipris_metadata.get("title"),
        ),
        "title_draft": first_text(patent.get("title_draft"), patent.get("발명의 명칭(가제)")),
        "related_product": first_text(patent.get("related_product"), patent.get("관련제품")),
        "business_area": first_text(patent.get("business_area"), patent.get("관련사업 분야"), patent.get("관련 사업 분야")),
        "technology_area": first_text(patent.get("technology_area"), patent.get("관련기술 분야"), patent.get("관련 기술 분야")),
        "summary": limit_text(
            first_text(
                summary.get("plain_summary"),
                sections.get("abstract"),
                agent_summary.get("abstract"),
                kipris_sections.get("abstract"),
            )
        ),
        "key_points": [limit_text(item, 500) for item in summary.get("key_points", []) if normalize_text(item)][:8],
        "problem_or_purpose": limit_text(
            first_text(
                sections.get("problem"),
                sections.get("purpose"),
                agent_summary.get("problem"),
                kipris_sections.get("problem"),
            )
        ),
        "solution_or_core_technology": limit_text(
            first_text(
                sections.get("solution"),
                sections.get("technical_field"),
                agent_summary.get("solution"),
                agent_summary.get("technical_field"),
                kipris_sections.get("solution"),
                kipris_sections.get("technical_field"),
            )
        ),
        "effect_or_expected_benefit": limit_text(first_text(sections.get("effect"), agent_summary.get("effect"))),
        "use_case_or_application": limit_text(first_text(sections.get("application"), sections.get("use_case"))),
        "key_terms": build_business_fit_key_terms(state)[:12],
    }


def build_skax_official_evidence_summary(
    evidence_items: list[dict[str, Any]],
    state: PatentWorkflowState | None = None,
    *,
    max_items: int = 3,
    max_content_chars: int = EVIDENCE_EXCERPT_LIMIT,
) -> list[dict[str, Any]]:
    del state
    official_items = [item for item in evidence_items if is_sk_ax_official_evidence(item)]
    ordered = sort_official_evidence(official_items, []) if official_items else []
    summaries = []
    for item in ordered[: max(1, int(max_items))]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        score = item.get("relevance_score") or item.get("candidate_relevance_score") or metadata.get("relevance_score")
        summaries.append(
            {
                "evidence_id": item.get("evidence_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "source_type": item.get("source_type"),
                "relevance_score": score,
                "matched_keywords": item.get("matched_keywords") or metadata.get("matched_keywords") or [],
                "matched_terms": item.get("matched_terms") or metadata.get("matched_terms") or [],
                "score_reasons": item.get("score_reasons") or metadata.get("score_reasons") or [],
                "content_excerpt": limit_text(item.get("content") or item.get("compressed_summary"), max_content_chars),
                "business_context_hint": first_text(
                    item.get("business_context_hint"),
                    item.get("business_area"),
                    metadata.get("business_context_hint"),
                    metadata.get("business_area"),
                ),
            }
        )
    return summaries


def build_sk_owned_media_evidence_summary(
    evidence_items: list[dict[str, Any]],
    state: PatentWorkflowState | None = None,
    *,
    max_items: int = 3,
    max_content_chars: int = EVIDENCE_EXCERPT_LIMIT,
) -> list[dict[str, Any]]:
    del state
    media_items = [
        item
        for item in evidence_items
        if is_sk_owned_media_evidence(item)
    ]
    summaries = []
    for item in sort_official_evidence(media_items, [])[: max(1, int(max_items))]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        score = item.get("relevance_score") or item.get("candidate_relevance_score") or metadata.get("relevance_score")
        summaries.append(
            {
                "evidence_id": item.get("evidence_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "source_domain": item.get("source_domain") or metadata.get("source_domain"),
                "source_type": item.get("source_type"),
                "source_tier": item.get("source_tier") or metadata.get("source_tier"),
                "relevance_score": score,
                "matched_keywords": item.get("matched_keywords") or metadata.get("matched_keywords") or [],
                "matched_terms": item.get("matched_terms") or metadata.get("matched_terms") or [],
                "score_reasons": item.get("score_reasons") or metadata.get("score_reasons") or [],
                "content_excerpt": limit_text(item.get("content") or item.get("compressed_summary"), max_content_chars),
                "business_context_hint": first_text(
                    item.get("business_context_hint"),
                    item.get("business_area"),
                    metadata.get("business_context_hint"),
                    metadata.get("business_area"),
                ),
            }
        )
    return summaries


def build_sk_ax_relevant_news_evidence_summary(
    evidence_items: list[dict[str, Any]],
    state: PatentWorkflowState | None = None,
    *,
    max_items: int = 3,
    max_content_chars: int = EVIDENCE_EXCERPT_LIMIT,
) -> list[dict[str, Any]]:
    # 압축 단계에서 sk_ax_relevant=True로 판단된 뉴스 등 보조 근거.
    # 공식 사이트 근거보다 낮은 tier이며, 공식 근거 존재성(30점) 산정에는 쓰지 않는다.
    del state
    items = [item for item in evidence_items if is_sk_ax_relevant_supporting_evidence(item)]
    summaries = []
    for item in items[: max(1, int(max_items))]:
        summaries.append(
            {
                "evidence_id": item.get("evidence_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "source_type": item.get("source_type"),
                "published_at": item.get("published_at"),
                "content_excerpt": limit_text(
                    item.get("compressed_summary") or item.get("content"), max_content_chars
                ),
                "key_facts": item.get("key_facts") or [],
            }
        )
    return summaries


def build_business_fit_quantitative_metrics(
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    official_site_items = [item for item in evidence if is_sk_ax_official_evidence(item)]
    owned_media_items = [
        item
        for item in evidence
        if is_sk_owned_media_evidence(item)
    ]
    business_evidence_items = sort_official_evidence(
        [*official_site_items, *owned_media_items],
        business_fit_keywords(state),
    )
    official_score = score_official_evidence_presence(official_site_items, owned_media_items)
    return {
        "official_evidence_count": len(official_site_items),
        "official_site_evidence_count": len(official_site_items),
        "sk_owned_media_evidence_count": len(owned_media_items),
        "business_evidence_count": len(business_evidence_items),
        "best_relevance_score": max((evidence_relevance_score(item) for item in business_evidence_items), default=0.0),
        "official_business_evidence": official_score,
    }


def score_official_evidence_presence(
    items: list[dict[str, Any]],
    owned_media_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    count = len(items)
    owned_media_count = len(owned_media_items or [])
    if count == 0 and owned_media_count == 0:
        score = 0
        reasons = ["no_skax_official_evidence"]
        rationale = "SK AX 공식 사이트 또는 SK 계열 매체 evidence가 확인되지 않아 공식 근거 존재성은 0점이다."
    elif count == 0:
        score = 8 if owned_media_count == 1 else 16
        reasons = ["sk_owned_media_only"]
        rationale = "SK AX 공식 사이트 근거는 없으나 SK 계열 매체에서 SK AX/SK C&C 언급이 확인되어 보조 근거로 평가된다."
    elif all(is_broad_or_weak_official_evidence(item) for item in items):
        score = 8
        reasons = ["broad_or_weak_official_evidence_only"]
        rationale = "SK AX 공식 evidence는 있으나 일반 소개 또는 인사이트 성격이 강해 공식 근거 존재성은 제한적으로 평가된다."
    elif count >= 3:
        score = 30
        reasons = ["official_evidence_3_or_more"]
        rationale = "SK AX 공식 evidence 3건 이상이 확인되어 공식 근거 존재성은 높게 평가된다."
    elif count == 2:
        score = 24
        reasons = ["official_evidence_2"]
        rationale = "SK AX 공식 evidence 2건이 확인되어 공식 근거 존재성은 충분한 수준으로 평가된다."
    else:
        score = 16
        reasons = ["official_evidence_1"]
        rationale = "SK AX 공식 evidence 1건이 확인되어 공식 근거 존재성은 일부 확인된 수준으로 평가된다."
    return {
        "score": score,
        "max_score": 30,
        "rationale": rationale,
        "evidence_count": count,
        "official_site_evidence_count": count,
        "sk_owned_media_evidence_count": owned_media_count,
        "score_reasons": reasons,
    }


def is_broad_or_weak_official_evidence(item: dict[str, Any]) -> bool:
    return is_insight_or_trend_page(item) or not is_concrete_business_page(item)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    # 사업연계성 근거 tier:
    #  1) SK AX 공식 사이트 콘텐츠, 2) SK 계열 매체, 3) 압축 단계에서 SK AX 사업/
    #     제품과 직접 관련 있다고 판단된(sk_ax_relevant=True) 뉴스 등 보조 근거.
    # sk_ax_relevant 뉴스는 시장성 축에도 그대로 쓰이며, 여기서는 보조 tier로만 더한다.
    keywords = business_fit_keywords(state)
    official_matches = []
    owned_media_matches = []
    sk_ax_relevant_matches = []
    for item in items:
        if is_sk_ax_official_evidence(item):
            official_matches.append(item)
            continue
        if is_sk_owned_media_evidence(item):
            owned_media_matches.append(item)
            continue
        if item.get("sk_ax_relevant") is True:
            sk_ax_relevant_matches.append(item)
    return [
        *sort_official_evidence(official_matches, keywords),
        *sort_official_evidence(owned_media_matches, keywords),
        *sort_official_evidence(sk_ax_relevant_matches, keywords),
    ][:5]


def is_sk_ax_relevant_supporting_evidence(item: dict[str, Any]) -> bool:
    return (
        item.get("sk_ax_relevant") is True
        and not is_sk_ax_official_evidence(item)
        and not is_sk_owned_media_evidence(item)
    )


def business_fit_keywords(state: PatentWorkflowState) -> list[str]:
    patent = state.patent_structured or {}
    metadata = ((state.kipris_api_data or {}).get("metadata") or {})
    raw_keywords: list[Any] = [
        patent.get("title_final"),
        patent.get("title_draft"),
        patent.get("related_product"),
        patent.get("technology_area"),
        patent.get("business_area"),
        patent.get("joint_applicant_name"),
        *(metadata.get("assignee") or []),
        *(metadata.get("assignee_eng") or []),
    ]
    company_context = patent.get("company_context") or state.user_input.get("company_context") or {}
    if isinstance(company_context, dict):
        raw_keywords.extend([company_context.get("company_name"), company_context.get("product_name")])
    return [normalize_text(keyword) for keyword in raw_keywords if normalize_text(keyword)]


def build_business_fit_key_terms(state: PatentWorkflowState) -> list[str]:
    patent = state.patent_structured or {}
    summary = state.summary_result or {}
    terms = [
        *business_fit_keywords(state),
        *title_keyword_terms(first_text(patent.get("title_final"), patent.get("발명의 명칭(최종)"))),
        *(summary.get("key_points") or []),
    ]
    return [term for term in unique_texts(terms) if len(term) <= 80]


def is_sk_ax_official_evidence(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    values = [
        item.get("source"),
        item.get("source_type"),
        item.get("evidence_type"),
        metadata.get("source"),
        metadata.get("source_type"),
        metadata.get("evidence_type"),
    ]
    return any(normalize_text(value) == "sk_ax_official" for value in values)


def is_sk_owned_media_evidence(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    values = [
        item.get("source"),
        item.get("source_tier"),
        item.get("source_domain"),
        metadata.get("source"),
        metadata.get("source_tier"),
        metadata.get("source_domain"),
    ]
    return any(normalize_text(value) == "sk_group_owned_media" for value in values) or any(
        normalize_text(value) in {"skcareersjournal.com", "openapi.sk.com", "sk_related_owned_media"}
        for value in values
    )


def sort_official_evidence(items: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            evidence_relevance_score(item),
            official_keyword_match_count(item, keywords),
            normalize_text(item.get("published_at") or item.get("collected_at")),
            normalize_text(item.get("title")),
        ),
        reverse=True,
    )


def evidence_relevance_score(item: dict[str, Any]) -> float:
    value = item.get("relevance_score") or item.get("candidate_relevance_score")
    if value is None and isinstance(item.get("metadata"), dict):
        value = item["metadata"].get("relevance_score") or item["metadata"].get("candidate_relevance_score")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def official_keyword_match_count(item: dict[str, Any], keywords: list[str]) -> int:
    text = evidence_text(item)
    return sum(1 for keyword in keywords if keyword and keyword in text)


def evidence_text(item: dict[str, Any]) -> str:
    values = [
        item.get("title"),
        item.get("compressed_summary"),
        item.get("content"),
        item.get("context"),
        " ".join(str(fact) for fact in item.get("key_facts", [])),
    ]
    return " ".join(normalize_text(value) for value in values if normalize_text(value))


def evidence_url_path(item: dict[str, Any]) -> str:
    url = normalize_text(item.get("url")).lower()
    for marker in ("skax.co.kr",):
        if marker in url:
            return url.split(marker, 1)[1].split("?", 1)[0]
    return url.split("?", 1)[0]


def is_concrete_business_page(item: dict[str, Any]) -> bool:
    path = evidence_url_path(item)
    concrete_markers = (
        "/finance",
        "/manufacturing",
        "/case-study",
        "/story",
        "/solution",
        "/service",
        "/industry",
        "/enterprise",
        "/security",
        "/blockchain",
    )
    return any(marker in path for marker in concrete_markers)


def is_insight_or_trend_page(item: dict[str, Any]) -> bool:
    path = evidence_url_path(item)
    return "/insight" in path or "/trend" in path


def first_text(*values: Any) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


def limit_text(value: Any, limit: int = DESCRIPTION_TEXT_LIMIT) -> str:
    return normalize_text(value)[:limit]


def title_keyword_terms(title: Any, *, limit: int = 4) -> list[str]:
    terms = []
    normalized_title = normalize_text(title)
    compound_candidates = [
        "강화학습",
        "자산배분",
        "트렌드 예측",
        "상품 트렌드",
        "로보어드바이저",
        "블록체인",
        "서명 검증",
        "데이터분석",
        "문서변환",
    ]
    for candidate in compound_candidates:
        if candidate in normalized_title and candidate not in terms:
            terms.append(candidate)
        if len(terms) >= limit:
            return terms

    extra_stopwords = {"상품"}
    for token in normalized_title.replace("/", " ").replace("-", " ").split():
        text = strip_korean_particle(token.strip("()[]{}.,;:·"))
        if is_stopword(text) or text in extra_stopwords:
            continue
        if len(text) >= 2 and text not in terms:
            terms.append(text)
        if len(terms) >= limit:
            break
    return terms


def strip_korean_particle(value: str) -> str:
    for suffix in ("으로", "에서", "에게", "부터", "까지", "을", "를", "은", "는", "이", "가", "의", "와", "과", "한"):
        if len(value) > len(suffix) + 1 and value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def unique_texts(values: list[Any]) -> list[str]:
    result = []
    for value in values:
        text = normalize_text(value)
        if text and text not in result:
            result.append(text)
    return result
