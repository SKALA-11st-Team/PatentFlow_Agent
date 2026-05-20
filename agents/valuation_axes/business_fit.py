from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text
from services.evidence.skax_site_search_service import collect_skax_site_evidence
from workflow.state import PatentWorkflowState


AXIS = "business_fit"
LABEL = "사업 연계성"
PROMPT_PATH = "valuation/valuation_business_fit.md"


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    state = append_skax_official_evidence_to_state(state)
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = runtime.build_input_payload(axis=AXIS, state=state, evidence=evidence)
    payload["business_fit_scoring_rubric"] = business_fit_scoring_rubric()
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    return runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)


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


def append_skax_official_evidence_to_state(state: PatentWorkflowState) -> PatentWorkflowState:
    patent_context = build_patent_context_from_state(state)
    if not has_business_fit_search_context(patent_context):
        return state

    try:
        result = collect_skax_site_evidence(patent_context)
    except Exception:
        return state

    items = [
        item
        for item in result.get("items", [])
        if is_sk_ax_official_evidence(item)
    ]
    if not items:
        return state

    state.evidence_bundle = append_unique_evidence(state.evidence_bundle or [], items)
    return state


def build_patent_context_from_state(state: PatentWorkflowState) -> dict[str, Any]:
    patent = state.patent_structured or {}
    kipris_metadata = ((state.kipris_api_data or {}).get("metadata") or {})
    preprocessed_metadata = ((state.preprocessed_patent or {}).get("metadata") or {})
    summary = state.summary_result or {}
    user_input = state.user_input or {}
    user_metadata = user_input.get("metadata") if isinstance(user_input.get("metadata"), dict) else {}
    return {
        "management_number": first_text(
            patent.get("management_number"),
            patent.get("관리번호"),
            user_input.get("management_number"),
            user_input.get("관리번호"),
            user_metadata.get("management_number"),
            user_metadata.get("관리번호"),
        ),
        "title_final": first_text(
            patent.get("title_final"),
            patent.get("발명의 명칭(최종)"),
            kipris_metadata.get("title"),
            kipris_metadata.get("발명의 명칭(최종)"),
            preprocessed_metadata.get("title"),
            preprocessed_metadata.get("발명의 명칭(최종)"),
            summary.get("title"),
            summary.get("발명의 명칭(최종)"),
            user_input.get("title"),
            user_input.get("발명의 명칭(최종)"),
            user_metadata.get("title_final"),
            user_metadata.get("title"),
            user_metadata.get("발명의 명칭(최종)"),
        ),
        "title_draft": first_text(
            patent.get("title_draft"),
            patent.get("발명의 명칭(가제)"),
            user_metadata.get("title_draft"),
            user_metadata.get("발명의 명칭(가제)"),
        ),
        "business_area": first_text(
            patent.get("business_area"),
            patent.get("관련사업 분야"),
            patent.get("관련 사업 분야"),
            preprocessed_metadata.get("business_area"),
            preprocessed_metadata.get("관련사업 분야"),
            preprocessed_metadata.get("관련 사업 분야"),
            user_metadata.get("business_area"),
            user_metadata.get("관련사업 분야"),
            user_metadata.get("관련 사업 분야"),
        ),
        "technology_area": first_text(
            patent.get("technology_area"),
            patent.get("관련기술 분야"),
            patent.get("관련 기술 분야"),
            preprocessed_metadata.get("technology_area"),
            preprocessed_metadata.get("관련기술 분야"),
            preprocessed_metadata.get("관련 기술 분야"),
            user_metadata.get("technology_area"),
            user_metadata.get("관련기술 분야"),
            user_metadata.get("관련 기술 분야"),
        ),
        "related_product": first_text(
            patent.get("related_product"),
            patent.get("관련제품"),
            preprocessed_metadata.get("related_product"),
            preprocessed_metadata.get("관련제품"),
            user_metadata.get("related_product"),
            user_metadata.get("관련제품"),
        ),
    }


def has_business_fit_search_context(patent_context: dict[str, Any]) -> bool:
    return any(
        normalize_text(patent_context.get(key))
        for key in ("related_product", "title_final", "title_draft", "business_area", "technology_area")
    )


def append_unique_evidence(existing: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = list(existing)
    seen = {evidence_identity(item) for item in result}
    for item in additions:
        identity = evidence_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def evidence_identity(item: dict[str, Any]) -> str:
    return normalize_text(item.get("url")) or normalize_text(item.get("evidence_id")) or normalize_text(item.get("title"))


def first_text(*values: Any) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


def business_fit_scoring_rubric() -> dict[str, Any]:
    return {
        "total_score": 100,
        "components": {
            "business_connection": 40,
            "portfolio_necessity": 35,
            "practical_applicability": 15,
            "evidence_reliability": 10,
        },
        "rationale_instruction": (
            "Do not add sub_scores or any new output field. "
            "Explain the four component scores and reasoning inside the existing rationale field."
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
    value = item.get("relevance_score")
    if value is None and isinstance(item.get("metadata"), dict):
        value = item["metadata"].get("relevance_score")
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
