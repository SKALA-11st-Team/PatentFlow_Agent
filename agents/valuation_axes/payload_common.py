from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text
from workflow.state import PatentWorkflowState


def build_base_input_payload(
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
    claims: list[dict[str, Any]] | None = None,
    prior_art_candidates: list[str] | None = None,
    citation_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    representative_claims = valuation_representative_claims(state)
    claim_stats = ((state.kipris_api_data or {}).get("claim_stats") or {})
    full_claims = claims or []
    prior_art = prior_art_candidates or []
    citations = citation_evidence or {}
    return {
        "patent": {
            "metadata": state.patent_structured or {},
            "kipris_metadata": ((state.kipris_api_data or {}).get("metadata") or {}),
            "claim_stats": claim_stats,
            "representative_claims": representative_claims,
            "claims": full_claims,
            "prior_art_candidates": prior_art,
            "citation_evidence": citations,
            "claim_availability": {
                "claim_stats_provided": bool(claim_stats),
                "representative_claims_provided": bool(representative_claims),
                "full_claims_provided": bool(full_claims),
                "prior_art_candidates_provided": bool(prior_art),
                "citation_evidence_provided": bool(citations),
            },
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


def valuation_representative_claims(
    state: PatentWorkflowState,
    *,
    limit: int = 3,
    text_limit: int = 1500,
) -> list[dict[str, Any]]:
    claims = valuation_claims(state)

    selected = [claim for claim in claims if claim.get("is_independent") and claim.get("text")]
    if not selected:
        selected = [claim for claim in claims if claim.get("text")]

    result = []
    for claim in selected[:limit]:
        result.append(
            {
                "claim_no": claim.get("claim_no"),
                "is_independent": claim.get("is_independent"),
                "dependency": claim.get("dependency"),
                "text": normalize_text(claim.get("text"))[:text_limit],
            }
        )
    return result


def valuation_evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "source_type": item.get("source_type"),
        "source": item.get("source"),
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "collected_at": item.get("collected_at"),
        "related_axes": item.get("related_axes") or item.get("related_axis") or [],
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
