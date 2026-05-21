from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text
from agents.valuation_axes.payload_common import build_base_input_payload
from workflow.state import PatentWorkflowState


AXIS = "business_fit"
LABEL = "사업 연계성"
PROMPT_PATH = "valuation/valuation_business_fit.md"
DESCRIPTION_TEXT_LIMIT = 1000
EVIDENCE_EXCERPT_LIMIT = 1500


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = build_input_payload(state=state, evidence=evidence)
    payload["business_fit_scoring_rubric"] = business_fit_scoring_rubric()
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    return runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base_input_payload(state=state, evidence=evidence)
    payload["business_fit_context"] = {
        "patent_description": build_business_fit_patent_description(state),
        "skax_official_evidence": build_skax_official_evidence_summary(evidence, state=state),
    }
    return payload


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


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    keywords = business_fit_keywords(state)
    official_matches = []
    direct_matches = []
    secondary_matches = []
    for item in items:
        if is_sk_ax_official_evidence(item):
            official_matches.append(item)
            continue
        source_type = item.get("source_type")
        if source_type in {"company_disclosure", "portfolio_context"}:
            secondary_matches.append(item)
        if source_type != "news":
            continue
        text = evidence_text(item)
        if any(keyword and keyword in text for keyword in keywords):
            direct_matches.append(item)
        else:
            secondary_matches.append(item)
    return [*sort_official_evidence(official_matches, keywords), *direct_matches, *secondary_matches][:5]


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


def business_fit_scoring_rubric() -> dict[str, Any]:
    return {
        "total_score": 100,
        "components": {
            "official_business_evidence": {
                "max_score": 30,
                "description": "SK AX 공식 사이트에서 이 특허와 관련된 사업/서비스 근거가 확인되는지 평가한다.",
                "score_guide": {
                    "30": "관련 공식 페이지가 여러 개 있고 유효한 사업 근거로 확인됨",
                    "24": "관련 공식 페이지가 1~2개 있고 직접 근거로 사용 가능함",
                    "16": "공식 페이지는 있으나 broad category 수준임",
                    "8": "공식 페이지 후보는 있으나 관련성이 약함",
                    "0": "공식 사이트 근거 없음",
                },
            },
            "business_context_alignment": {
                "max_score": 45,
                "description": "특허의 핵심 제품/기술/문제 해결 방향과 SK AX 공식 사업 설명의 직접 연결성을 평가한다.",
                "score_guide": {
                    "45": "특허 핵심 제품/기술과 공식 사업 페이지가 직접 연결됨",
                    "36": "직접 제품명 일부 또는 강한 사업 맥락이 확인됨",
                    "27": "같은 산업/기술군이나 구체적 연결은 약함",
                    "12": "넓은 사업 분야만 같고 직접성 낮음",
                    "0": "연결 근거 없음",
                },
            },
            "application_scenario_specificity": {
                "max_score": 25,
                "description": "공식 근거가 실제 서비스/오퍼링/유스케이스 수준으로 구체적인지 평가한다.",
                "score_guide": {
                    "25": "실제 오퍼링/유스케이스/적용 방식이 구체적임",
                    "20": "서비스 방향과 적용 시나리오가 비교적 명확함",
                    "14": "적용 가능성은 있으나 설명이 일반적임",
                    "6": "추상적 사업 키워드만 있음",
                    "0": "적용 시나리오 확인 불가",
                },
            },
        },
        "grade_guide": {"A": "90 이상", "B": "75 이상", "C": "60 이상", "D": "60 미만"},
        "input_usage": (
            "Use business_fit_context.patent_description and "
            "business_fit_context.skax_official_evidence to compare patent content with official SK AX evidence."
        ),
        "official_evidence_principles": [
            "skax.co.kr 공식 evidence 중심으로 평가한다.",
            "외부 뉴스, 블로그, SK그룹 다른 도메인, 미러링 사이트는 공식 사업 근거로 보지 않는다.",
            "검색 결과 개수만으로 높은 점수를 주지 않는다.",
            "유효한 공식 근거의 직접성, 구체성, 사업 맥락 일치도를 본다.",
            "공식 근거가 없다고 특허 가치가 낮다고 단정하지 않는다.",
            "정보 부족은 missing_information 또는 confidence 하락 요인으로 처리한다.",
            "risk_factors에는 실제 약점만 작성한다.",
            "SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다.",
            "공식 사이트에서 관련 사업 근거가 확인된다/확인되지 않는다 수준으로 표현한다.",
        ],
        "rationale_instruction": (
            "Do not add subscores, sub_scores, or any new output field. "
            "Use the three internal criteria only to determine final score and explain the reasoning in rationale."
        ),
    }


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
    for token in normalize_text(title).replace("/", " ").replace("-", " ").split():
        text = token.strip("()[]{}.,;:·")
        if len(text) >= 2 and text not in terms:
            terms.append(text)
        if len(terms) >= limit:
            break
    return terms


def unique_texts(values: list[Any]) -> list[str]:
    result = []
    for value in values:
        text = normalize_text(value)
        if text and text not in result:
            result.append(text)
    return result
