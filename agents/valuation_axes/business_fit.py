from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import grade_for_score, normalize_text
from agents.valuation_axes.payload_common import build_base_input_payload
from schemas.valuation import DEFAULT_SUBSCORE_WEIGHTS
from workflow.state import PatentWorkflowState


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
BROAD_TERMS = {"ai", "data", "cloud", "제조", "솔루션", "서비스", "플랫폼", "데이터"}
WEAK_TERMS = {"예측", "분석", "관리", "서비스", "플랫폼", "솔루션"}
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


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base_input_payload(state=state, evidence=evidence)
    patent_description = build_business_fit_patent_description(state)
    skax_evidence = build_skax_official_evidence_summary(evidence, state=state)
    sk_owned_media_evidence = build_sk_owned_media_evidence_summary(evidence, state=state)
    sk_ax_relevant_news_evidence = build_sk_ax_relevant_news_evidence_summary(evidence, state=state)
    payload["business_fit_context"] = {
        "patent_description": patent_description,
        "target_source_status": build_target_source_status(state),
        "skax_official_evidence": skax_evidence,
        "sk_owned_media_evidence": sk_owned_media_evidence,
        "sk_ax_relevant_news_evidence": sk_ax_relevant_news_evidence,
        "quantitative_metrics": build_business_fit_quantitative_metrics(
            state=state,
            evidence=evidence,
            patent_description=patent_description,
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
    patent_description: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = patent_description or build_business_fit_patent_description(state)
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
    product_score = score_product_function_direct_match(description, business_evidence_items)
    return {
        "official_evidence_count": len(official_site_items),
        "official_site_evidence_count": len(official_site_items),
        "sk_owned_media_evidence_count": len(owned_media_items),
        "business_evidence_count": len(business_evidence_items),
        "best_relevance_score": max((evidence_relevance_score(item) for item in business_evidence_items), default=0.0),
        "official_business_evidence": official_score,
        "product_function_direct_match": product_score,
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


def score_product_function_direct_match(description: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    summary = build_product_function_match_summary(description, items)
    product_level = summary["product_match_level"]
    strong_ratio = summary["strong_core_match_ratio"]
    if product_level == "direct" and strong_ratio >= 0.6:
        score = 45
        reason = "product_direct_and_most_core_functions_matched"
        rationale = product_function_rationale(summary, "관련제품과 특허 핵심 기능 대부분이 SK AX 공식 evidence에서 직접 확인된다.")
    elif product_level == "direct" and strong_ratio > 0:
        score = 36
        reason = "product_direct_and_some_core_functions_matched"
        rationale = product_function_rationale(summary, "관련제품은 SK AX 공식 evidence에서 직접 확인되며 특허 핵심 기능 일부도 확인된다.")
    elif product_level in {"direct", "partial"}:
        score = 24
        reason = "product_context_matched_but_core_functions_weak"
        rationale = product_function_rationale(summary, "관련제품 또는 유사 제품/서비스 맥락은 확인되지만 특허 핵심 기능 직접 매칭은 약하다.")
    elif product_level == "broad" or summary["area_matched"]:
        score = 12
        reason = "broad_business_context_only"
        rationale = product_function_rationale(summary, "같은 산업 또는 사업군 수준의 연결은 있으나 제품명과 핵심 기능의 직접 연결은 확인되지 않는다.")
    else:
        score = 0
        reason = "no_product_or_function_match"
        rationale = product_function_rationale(summary, "제품/서비스 및 핵심 기능 연결이 확인되지 않는다.")
    return {
        "score": score,
        "max_score": 45,
        "rationale": rationale,
        "score_reasons": [reason],
        **summary,
    }


def build_product_function_match_summary(description: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(evidence_text(item) for item in items).lower()
    related_product = normalize_text(description.get("related_product"))
    product_match_level = product_match_level_for(related_product, text)
    core = extract_business_fit_core_terms(description)
    strong_terms = core["strong_core_terms"]
    matched_strong = [term for term in strong_terms if term.lower() in text]
    core_terms = core["core_terms"]
    matched_core = [term for term in core_terms if term.lower() in text]
    product_match_evidence = evidence_refs_for_terms(items, [related_product]) if product_match_level == "direct" else []
    matched_core_evidence = evidence_refs_for_terms(items, matched_strong[:3])
    area_matched = any(
        normalize_text(description.get(key)).lower() in text
        for key in ("business_area", "technology_area")
        if normalize_text(description.get(key)) and normalize_text(description.get(key)).lower() not in BROAD_TERMS
    )
    return {
        "related_product": related_product,
        "related_product_matched": product_match_level == "direct",
        "product_match_level": product_match_level,
        "core_terms": core_terms,
        "matched_core_terms": matched_core,
        "missing_core_terms": [term for term in core_terms if term not in matched_core],
        "core_match_ratio": ratio(len(matched_core), len(core_terms)),
        "strong_core_terms": strong_terms,
        "matched_strong_core_terms": matched_strong,
        "strong_core_match_ratio": ratio(len(matched_strong), len(strong_terms)),
        "product_match_evidence": product_match_evidence,
        "matched_core_evidence": matched_core_evidence,
        "area_matched": area_matched,
    }


def product_function_rationale(summary: dict[str, Any], base: str) -> str:
    parts = [base]
    product = summary.get("related_product")
    product_refs = summary.get("product_match_evidence") or []
    if product and product_refs:
        refs = ", ".join(ref["title"] for ref in product_refs[:2] if ref.get("title"))
        parts.append(f"관련제품 '{product}'은 공식 evidence({refs})에서 확인된다.")
    elif product:
        parts.append(f"관련제품 '{product}'의 직접 언급은 공식 evidence에서 확인되지 않는다.")
    matched = [term for term in summary.get("matched_strong_core_terms", []) if len(term) <= 40]
    missing = [term for term in summary.get("missing_core_terms", []) if term not in {product} and len(term) <= 30]
    if matched:
        parts.append(f"확인된 핵심 기능/용어는 {', '.join(matched[:4])}이다.")
    if missing:
        parts.append(f"직접 확인되지 않은 핵심 기능/용어는 {', '.join(missing[:4])}이다.")
    return " ".join(parts)


def evidence_refs_for_terms(items: list[dict[str, Any]], terms: list[str]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for term in terms:
        if not term:
            continue
        lowered = term.lower()
        for item in items:
            if lowered not in evidence_text(item).lower():
                continue
            ref = {
                "evidence_id": normalize_text(item.get("evidence_id")),
                "title": normalize_text(item.get("title")) or normalize_text(item.get("url")),
                "url": normalize_text(item.get("url")),
                "matched_term": term,
            }
            if ref not in refs:
                refs.append(ref)
            break
    return refs[:5]


# 핵심어 매칭(분모)을 오염시키는 noise. evidence 본문에 제품·기능어가 들어있나를 보는
# substring 매칭이라, 아래 유형은 분모만 늘리고 매칭은 불가능해 ratio를 0쪽으로 끌어내린다.
#  - 출원인 법인명(주식회사/Inc 등): 특허의 "핵심 기능"이 아니다.
#  - 명세서 boilerplate(발명/실시예/도면 등): 의미 없는 상투어다.
#  - 통문장(제목 등 여러 어절): evidence 본문에 그대로 박힐 일이 없어 영영 매칭되지 않는다.
COMPANY_NAME_MARKERS = (
    "주식회사", "(주)", "㈜", "유한회사",
    " inc", " inc.", " corp", " corp.", " ltd", " ltd.", " co.", " co.,", " llc", " gmbh", " ag",
)
PATENT_BOILERPLATE_TERMS = {
    "발명", "본발명", "실시예", "실시 예", "도면", "도면부호", "청구항", "명세서",
    "출원", "특허", "기재", "수단", "단계", "구성", "구성요소",
}
MAX_CORE_TERM_WORDS = 3


def is_noise_core_term(text: str, *, related_product: str = "") -> bool:
    """핵심어로 부적절한 noise(법인명·boilerplate·통문장)면 True. 제품명 자체는 면제한다."""
    if related_product and text == related_product:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in COMPANY_NAME_MARKERS):
        return True
    base = strip_korean_particle(text)
    if text in PATENT_BOILERPLATE_TERMS or base in PATENT_BOILERPLATE_TERMS:
        return True
    if base.startswith("실시예") or base.startswith("발명"):
        return True
    if len(text.split()) > MAX_CORE_TERM_WORDS:
        return True
    return False


def extract_business_fit_core_terms(description: dict[str, Any]) -> dict[str, Any]:
    related_product = normalize_text(description.get("related_product"))
    raw_terms = [
        related_product,
        *(description.get("key_points") or []),
        *(description.get("key_terms") or []),
        *title_keyword_terms(first_text(description.get("title_final"), description.get("title"), description.get("title_draft")), limit=8),
        *title_keyword_terms(description.get("solution_or_core_technology"), limit=4),
        *title_keyword_terms(description.get("use_case_or_application"), limit=4),
    ]
    core_terms = []
    weak_terms = []
    for value in raw_terms:
        text = normalize_core_term(value)
        if not text or text in core_terms or is_stopword(text):
            continue
        if is_noise_core_term(text, related_product=related_product):
            continue
        if text.lower() in BROAD_TERMS or text in WEAK_TERMS:
            weak_terms.append(text)
            continue
        core_terms.append(text)
        if len(core_terms) >= 8:
            break
    return {
        "core_terms": core_terms[:8],
        "strong_core_terms": [term for term in core_terms if term != related_product][:6],
        "weak_terms": unique_texts(weak_terms)[:6],
    }


def product_match_level_for(related_product: str, evidence_text_value: str) -> str:
    if not related_product:
        return "none"
    product = related_product.lower()
    if product in evidence_text_value:
        return "direct"
    tokens = [token.lower() for token in title_keyword_terms(related_product, limit=4) if not is_stopword(token)]
    matched = [token for token in tokens if token in evidence_text_value and token.lower() not in BROAD_TERMS]
    if matched:
        return "partial"
    return "none"


def is_broad_or_weak_official_evidence(item: dict[str, Any]) -> bool:
    return is_insight_or_trend_page(item) or not is_concrete_business_page(item)


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def normalize_core_term(value: Any) -> str:
    text = strip_korean_particle(normalize_text(value).strip("()[]{}.,;:·"))
    return "" if is_stopword(text) else text


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
