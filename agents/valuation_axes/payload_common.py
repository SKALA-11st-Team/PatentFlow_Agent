from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text
from workflow.state import PatentWorkflowState


def build_base_input_payload(
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
    claim_context: dict[str, Any] | None = None,
    prior_art_candidates: list[str] | None = None,
    citation_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claim_stats = ((state.kipris_api_data or {}).get("claim_stats") or {})
    prior_art = prior_art_candidates or []
    citations = citation_evidence or {}
    claims_provided = bool((claim_context or {}).get("all_claims_included"))
    return {
        "patent": {
            "document_role": "target_patent",
            "metadata": state.patent_structured or {},
            "kipris_metadata": ((state.kipris_api_data or {}).get("metadata") or {}),
            "claim_stats": claim_stats,
            "claim_context": claim_context or {},
            "prior_art_candidates": prior_art,
            "citation_evidence": citations,
            "claim_availability": {
                "claim_stats_provided": bool(claim_stats),
                "claim_context_provided": bool(claim_context),
                "full_claims_provided": claims_provided,
                "prior_art_candidates_provided": bool(prior_art),
                "citation_evidence_provided": bool(citations),
            },
        },
        "document_role_policy": {
            "target_patent": "patent 및 summary_result에 포함된 대상 특허",
            "prior_art": "patent.citation_evidence에 포함된 비교 문헌",
            "rule": "prior_art의 기술요소를 target_patent의 구성으로 서술하지 않음",
        },
        "summary_result": state.summary_result,
        "evidence": [valuation_evidence_payload(item) for item in evidence],
    }


def valuation_claims(state: PatentWorkflowState) -> list[dict[str, Any]]:
    claims = []
    preprocessed = state.preprocessed_patent or {}
    if isinstance(preprocessed.get("claims"), list):
        claims = preprocessed["claims"]
    elif isinstance((state.kipris_api_data or {}).get("claims"), list):
        claims = (state.kipris_api_data or {})["claims"]

    return [
        {
            "claim_no": claim.get("claim_no"),
            "is_independent": claim.get("is_independent"),
            "dependency": claim.get("dependency"),
            "text": normalize_text(claim.get("text")),
        }
        for claim in claims
        if claim.get("text")
    ]


def build_claim_context(
    state: PatentWorkflowState,
    *,
    include_dependent_claims: bool = True,
) -> dict[str, Any]:
    claims = valuation_claims(state)
    independent_claims = [claim for claim in claims if claim.get("is_independent")]
    dependent_claims = [claim for claim in claims if not claim.get("is_independent")]

    context = {
        "independent_claims": independent_claims,
        "independent_claim_count": len(independent_claims),
        "dependent_claim_count": len(dependent_claims),
        "total_claim_count": len(claims),
        "all_claims_included": include_dependent_claims and bool(claims),
    }
    if include_dependent_claims:
        context["dependent_claims"] = dependent_claims
    return context


def valuation_evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "source_type": item.get("source_type"),
        "source": item.get("source"),
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "collected_at": item.get("collected_at"),
        "compressed_summary": item.get("compressed_summary"),
        "key_facts": item.get("key_facts") or [],
        "sibling_patents": item.get("sibling_patents") or [],
        "group_size": item.get("group_size"),
        "metadata": item.get("metadata") or {},
    }


def unique_texts(values: Any) -> list[str]:
    result = []
    for value in values:
        text = normalize_text(value)
        if text and text not in result:
            result.append(text)
    return result
