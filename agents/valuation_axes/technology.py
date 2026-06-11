from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.valuation_axes.common import grade_for_score, select_by_source_types
from agents.valuation_axes.market import clamp_int, extract_patent_country, extract_representative_cpc, extract_representative_ipc
from agents.valuation_axes.payload_common import (
    build_base_input_payload,
    build_claim_context,
    build_element_structure_payload,
)
from services.patent.prior_art_patent_service import build_prior_art_patent_context
from services.patent.similar_patent_service import build_similar_patent_context
from workflow.state import PatentWorkflowState


AXIS = "technology"
LABEL = "기술성"
PROMPT_PATH = "valuation/valuation_technology.md"
TECHNOLOGY_COMPARISON_TARGET_COUNT = 5


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    # 비교군은 patent_structuring 노드가 이미 조립해 state.comparison_group에 담아둔다.
    # 없으면(단독 실행/테스트) 여기서 조립한다.
    # 비교군 특허의 원문 전문(pdf_text)은 프롬프트에 넣지 않는다 — 비교는
    # element_structure.comparisons(구조화 결과)로 수행한다. 식별자·CPC·초록 등
    # 경량 메타데이터만 남긴다.
    metrics = strip_comparison_fulltext(state.comparison_group or build_technology_metrics(state))
    payload = build_input_payload(state=state, evidence=evidence)
    payload["technology_metrics"] = metrics
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
        axis=AXIS,
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    return apply_technology_scores(result, metrics)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    return select_by_source_types(
        items,
        source_types={"portfolio_context", "industry_report", "patent_api"},
    )


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base_input_payload(
        state=state,
        evidence=evidence,
        claim_context=build_claim_context(state, include_dependent_claims=False),
    )
    payload["element_structure"] = build_element_structure_payload(state)
    return payload


def build_technology_metrics(state: PatentWorkflowState) -> dict[str, Any]:
    preprocessed = state.preprocessed_patent or {}
    sections = (preprocessed.get("sections") or {}) if isinstance(preprocessed, dict) else {}
    claims = (preprocessed.get("claims") or []) if isinstance(preprocessed, dict) else []
    metadata = {
        **(state.patent_structured or {}),
        **((preprocessed.get("metadata")) or {}),
        "abstract": sections.get("abstract"),
        "claims_text": sections.get("claims_text"),
        "solution": sections.get("solution"),
        "detailed_description": sections.get("detailed_description"),
        "representative_claim_text": "\n".join(str((claim or {}).get("text") or "") for claim in claims[:3]),
        "independent_claim_text": "\n".join(
            str((claim or {}).get("text") or "")
            for claim in claims
            if (claim or {}).get("is_independent")
        ),
    }
    country_code = extract_patent_country(state)
    foreign_patent = bool(country_code and country_code != "KR")
    representative_cpc = extract_representative_cpc(state)
    representative_ipc = extract_representative_ipc(state)
    artifact_dir = state.user_input.get("artifact_dir") if state.user_input else None
    similar_dir = Path(artifact_dir) / "similar_patents" if artifact_dir else None
    prior_art_dir = Path(artifact_dir) / "prior_art_patents" if artifact_dir else None
    return build_hybrid_context(
        metadata=metadata,
        kipris_api_data=state.kipris_api_data,
        representative_cpc=representative_cpc,
        representative_ipc=representative_ipc,
        country_code=country_code if foreign_patent else None,
        similar_dir=similar_dir,
        prior_art_dir=prior_art_dir,
        prior_art_context=state.prior_art_context,
    )


def build_similar_context(
    *,
    metadata: dict[str, Any],
    representative_cpc: str | None,
    representative_ipc: str | None,
    country_code: str | None,
    output_dir: Path | None,
) -> dict[str, Any]:
    try:
        return {
            **build_similar_patent_context(
                target_metadata=metadata,
                representative_cpc=representative_cpc,
                representative_ipc=representative_ipc,
                country_code=country_code,
                top_k=TECHNOLOGY_COMPARISON_TARGET_COUNT,
                collect_pdf=True,
                output_dir=output_dir,
                pdf_text_limit=None,
            ),
            "comparison_mode": "similar",
        }
    except Exception as exc:
        return {
            "comparison_mode": "similar",
            "representative_cpc": representative_cpc,
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": 0,
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": [f"similar_patent_search_failed:{exc.__class__.__name__}"],
        }


def build_prior_art_context(
    *,
    metadata: dict[str, Any],
    kipris_api_data: dict[str, Any] | None,
    output_dir: Path | None,
) -> dict[str, Any]:
    try:
        return build_prior_art_patent_context(
            target_metadata=metadata,
            kipris_api_data=kipris_api_data,
            collect_pdf=True,
            output_dir=output_dir,
            pdf_text_limit=None,
        )
    except Exception as exc:
        return {
            "comparison_mode": "prior-art",
            "candidate_count": 0,
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": [f"prior_art_search_failed:{exc.__class__.__name__}"],
        }


def build_hybrid_context(
    *,
    metadata: dict[str, Any],
    kipris_api_data: dict[str, Any] | None,
    representative_cpc: str | None,
    representative_ipc: str | None,
    country_code: str | None,
    similar_dir: Path | None,
    prior_art_dir: Path | None,
    prior_art_context: dict[str, Any] | None = None,
    target_top_k: int = TECHNOLOGY_COMPARISON_TARGET_COUNT,
) -> dict[str, Any]:
    prior_art = prior_art_context or build_prior_art_context(
        metadata=metadata,
        kipris_api_data=kipris_api_data,
        output_dir=prior_art_dir,
    )
    prior_items = list(prior_art.get("similar_patents") or [])

    if len(prior_items) >= target_top_k:
        return {
            "comparison_mode": "hybrid",
            "selection_policy": "prior-art-only",
            "representative_cpc": representative_cpc,
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": int(prior_art.get("candidate_count") or 0),
            "similar_patents": compact_comparison_items(
                tag_comparison_source(prior_items[:target_top_k], "prior_art")
            ),
            "target_count": target_top_k,
            "warnings": list(prior_art.get("warnings") or []),
        }

    similar = build_similar_context(
        metadata=metadata,
        representative_cpc=representative_cpc,
        representative_ipc=representative_ipc,
        country_code=country_code,
        output_dir=similar_dir,
    )
    hybrid_items = merge_hybrid_items(
        prior_items=tag_comparison_source(prior_items, "prior_art"),
        similar_items=tag_comparison_source(list(similar.get("similar_patents") or []), "similar"),
        target_count=target_top_k,
    )
    warnings = dedupe_texts(
        [
            *[str(item) for item in prior_art.get("warnings") or []],
            *[str(item) for item in similar.get("warnings") or []],
        ]
    )
    return {
        "comparison_mode": "hybrid",
        "selection_policy": "prior-art-first-then-similar",
        "representative_cpc": representative_cpc,
        "representative_ipc": representative_ipc,
        "country_code": country_code,
        "candidate_count": int(prior_art.get("candidate_count") or 0) + int(similar.get("candidate_count") or 0),
        "similar_patents": compact_comparison_items(hybrid_items),
        "target_count": target_top_k,
        "warnings": warnings,
    }


def tag_comparison_source(items: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """비교군 항목에 출처(prior_art=선행문헌 / similar=CPC유사)를 태깅한다.

    권리성은 선행문헌만 비교문헌으로 사용하므로, 이 태그로 필터링한다.
    """
    return [{**item, "comparison_source": source} for item in items if isinstance(item, dict)]


def merge_hybrid_items(*, prior_items: list[dict[str, Any]], similar_items: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen = set()
    for item in prior_items:
        if len(merged) >= target_count:
            break
        key = item_identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    for item in similar_items:
        if len(merged) >= target_count:
            break
        key = item_identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def compact_comparison_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drop_keys = {"pdf_text_excerpt", "similarity_text", "resolved_search_matches"}
    return [
        {
            "document_role": "prior_art_or_similar_comparison",
            **{key: value for key, value in item.items() if key not in drop_keys},
        }
        for item in items
    ]


# 비교군 원문 전문 등 무거운 텍스트는 프롬프트에 싣지 않는다(구조화 결과로 대체).
# pdf_text는 구조화 노드가 먼저 소비한 뒤이므로 여기서 빼도 비교 정보는 유지된다.
COMPARISON_PROMPT_DROP_KEYS = {
    "pdf_text",
    "pdf_text_excerpt",
    "pdf_text_chars",
    "pdf_text_truncated",
    "markdown_paths",
    "pdf_path",
    "pdf_drawings_removed",
    "pdf_collected",
    "similarity_text",
    "resolved_search_matches",
}


def strip_comparison_fulltext(metrics: dict[str, Any]) -> dict[str, Any]:
    """비교군(technology_metrics) 항목에서 원문 전문 필드를 제거한 사본을 만든다.

    state.comparison_group은 그대로 두고(구조화 입력 보존), 프롬프트·결과에 들어가는
    사본에서만 무거운 텍스트를 떼어낸다.
    """
    if not isinstance(metrics, dict):
        return metrics
    items = metrics.get("similar_patents")
    if not isinstance(items, list):
        return metrics
    return {
        **metrics,
        "similar_patents": [
            {key: value for key, value in item.items() if key not in COMPARISON_PROMPT_DROP_KEYS}
            for item in items
            if isinstance(item, dict)
        ],
    }
def apply_technology_scores(result: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    subscores = normalize_candidate_subscores(result.get("subscores") or {})
    technical_differentiation_score = int(subscores["technical_differentiation"]["score"])
    implementation_specificity_score = int(subscores["implementation_specificity"]["score"])
    score = technical_differentiation_score + implementation_specificity_score
    return {
        **result,
        "score": max(0, min(100, score)),
        "grade": grade_for_score(score),
        "subscores": subscores,
        "technology_metrics": metrics,
    }


def normalize_candidate_subscores(subscores: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in ("technical_differentiation", "implementation_specificity"):
        item = dict(subscores.get(key) or {})
        if key == "technical_differentiation":
            item["score"] = normalize_technology_differentiation_candidate_score(item)
        else:
            item["score"] = normalize_implementation_specificity_candidate_score(item)
        normalized[key] = item
    return normalized


def normalize_technology_differentiation_candidate_score(item: dict[str, Any]) -> int:
    details = item.get("details")
    if isinstance(details, dict):
        configuration = clamp_int(details.get("configuration_differentiation"), default=0, max_value=20)
        operation = clamp_int(details.get("operation_differentiation"), default=0, max_value=25)
        effect = clamp_int(details.get("effect_differentiation"), default=0, max_value=15)
        item["details"] = {
            "configuration_differentiation": configuration,
            "operation_differentiation": operation,
            "effect_differentiation": effect,
        }
        return configuration + operation + effect
    return clamp_int(item.get("score"), default=0, max_value=60)


def normalize_implementation_specificity_candidate_score(item: dict[str, Any]) -> int:
    details = item.get("details")
    if isinstance(details, dict):
        component = clamp_int(details.get("component_specificity"), default=0, max_value=15)
        procedure = clamp_int(details.get("procedure_specificity"), default=0, max_value=15)
        implementation = clamp_int(details.get("implementation_specificity_detail"), default=0, max_value=10)
        item["details"] = {
            "component_specificity": component,
            "procedure_specificity": procedure,
            "implementation_specificity_detail": implementation,
        }
        return component + procedure + implementation
    return clamp_int(item.get("score"), default=0, max_value=40)


def item_identity(item: dict[str, Any]) -> str:
    return "|".join(
        [
            str(item.get("application_number") or ""),
            str(item.get("registration_number") or ""),
            str(item.get("display_number") or ""),
            str(item.get("title") or ""),
        ]
    )


def dedupe_texts(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
