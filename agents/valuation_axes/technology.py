from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.valuation_axes.common import grade_for_score, normalize_text, select_by_source_types
from agents.valuation_axes.market import clamp_int, extract_patent_country, extract_representative_cpc, extract_representative_ipc
from agents.valuation_axes.payload_common import build_base_input_payload, build_claim_context
from services.patent.prior_art_patent_service import build_prior_art_patent_context
from services.patent.similar_patent_service import build_similar_patent_context
from workflow.state import PatentWorkflowState


AXIS = "technology"
LABEL = "기술성"
PROMPT_PATH = "valuation/valuation_technology.md"
TECHNOLOGY_COMPARISON_TARGET_COUNT = 5
TECHNOLOGY_DETAIL_SCORE_CANDIDATES = {
    "technical_differentiation": {
        "configuration_operation_differentiation": (0, 8, 17, 25),
        "effect_differentiation": (0, 5, 10, 15),
        "imitation_avoidance_difficulty": (0, 3, 7, 10),
    },
    "implementation_specificity": {
        "component_specificity": (0, 7, 14, 20),
        "procedure_specificity": (0, 7, 14, 20),
        "implementation_utilization_specificity": (0, 3, 7, 10),
    },
}


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    metrics = build_technology_metrics(state)
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
        claim_context=build_claim_context(state, include_dependent_claims=True),
    )
    payload["patent"]["technical_context"] = build_target_technical_context(state)
    return payload


def build_target_technical_context(state: PatentWorkflowState) -> dict[str, Any]:
    preprocessed = state.preprocessed_patent or {}
    sections = preprocessed.get("sections") if isinstance(preprocessed.get("sections"), dict) else {}
    drawing_context = (
        preprocessed.get("drawing_context")
        if isinstance(preprocessed.get("drawing_context"), dict)
        else {}
    )
    return {
        "technical_field": normalize_text(sections.get("technical_field")),
        "background_art": normalize_text(sections.get("background_art")),
        "problem": normalize_text(sections.get("problem")),
        "solution": normalize_text(sections.get("solution")),
        "effect": normalize_text(sections.get("effect")),
        "detailed_description": normalize_text(sections.get("detailed_description")),
        "figure_description": normalize_text(drawing_context.get("figure_description")),
        "representative_figure_detail": normalize_text(
            drawing_context.get("representative_figure_detail")
        ),
    }


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
            "similar_patents": compact_comparison_items(prior_items[:target_top_k]),
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
        prior_items=prior_items,
        similar_items=list(similar.get("similar_patents") or []),
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
    for key in TECHNOLOGY_DETAIL_SCORE_CANDIDATES:
        item = dict(subscores.get(key) or {})
        item["score"] = normalize_technology_detail_scores(item, key=key)
        item["max_score"] = 50
        normalized[key] = item
    return normalized


def normalize_technology_detail_scores(item: dict[str, Any], *, key: str) -> int:
    details = item.get("details")
    candidates_by_detail = TECHNOLOGY_DETAIL_SCORE_CANDIDATES[key]
    if not isinstance(details, dict):
        return clamp_int(item.get("score"), default=0, max_value=50)
    normalized_details = {
        detail_key: nearest_candidate_score(details.get(detail_key), candidates)
        for detail_key, candidates in candidates_by_detail.items()
    }
    item["details"] = normalized_details
    return sum(normalized_details.values())


def nearest_candidate_score(value: Any, candidates: tuple[int, ...]) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    return min(candidates, key=lambda candidate: (abs(candidate - score), -candidate))


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
