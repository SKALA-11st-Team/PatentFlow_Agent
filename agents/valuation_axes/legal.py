from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text, select_by_types_or_axes
from agents.valuation_axes.legal_scoring import apply_legal_scores, build_legal_scoring_metrics
from agents.valuation_axes.payload_common import build_base_input_payload, build_claim_context, unique_texts
from workflow.state import PatentWorkflowState


AXIS = "legal"
LABEL = "권리성"
PROMPT_PATH = "valuation/valuation_legal.md"


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = build_input_payload(state=state, evidence=evidence)
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    return apply_legal_scores(result, payload=payload, state=state)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    return select_by_types_or_axes(
        items,
        source_types={"portfolio_context", "patent_api", "prior_art", "citation"},
        axes={AXIS},
    )


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base_input_payload(
        state=state,
        evidence=evidence,
        claim_context=build_claim_context(state, include_dependent_claims=True),
        prior_art_candidates=valuation_prior_art_candidates(state),
        citation_evidence=valuation_citation_evidence(state),
    )
    payload["legal_scoring_context"] = build_legal_scoring_metrics(payload=payload, state=state, labels={})
    return payload


def valuation_prior_art_candidates(state: PatentWorkflowState) -> list[str]:
    candidates = []
    for source in (
        (state.preprocessed_patent or {}).get("metadata") or {},
        (state.kipris_api_data or {}).get("metadata") or {},
        state.patent_structured or {},
    ):
        values = source.get("prior_art") or source.get("citation_documents") or []
        if isinstance(values, str):
            values = [values]
        candidates.extend(normalize_text(value) for value in values if normalize_text(value))
    return unique_texts(candidates)


def valuation_citation_evidence(state: PatentWorkflowState, *, claim_text_limit: int = 1200) -> dict[str, Any]:
    evidence = state.citation_evidence or (state.kipris_api_data or {}).get("citation_evidence") or {}
    if not isinstance(evidence, dict):
        return {}
    return {
        "kr_citation_documents": [
            _valuation_reference_document_payload(item, claim_text_limit=claim_text_limit, max_claims=6)
            for item in (evidence.get("kr_citation_documents") or [])
            if isinstance(item, dict)
        ],
        "citing_signal": _valuation_citing_signal(state),
        "foreign_citation_documents": [
            _valuation_reference_document_payload(item, claim_text_limit=claim_text_limit, max_claims=5)
            for item in (evidence.get("foreign_citation_documents") or [])
            if isinstance(item, dict)
        ],
        "foreign_claim_lookup_candidates": [
            {
                "direction": item.get("direction"),
                "country_code": item.get("country_code"),
                "document_number": item.get("document_number"),
                "kind_code": item.get("kind_code"),
                "original_number": item.get("original_number"),
                "display_number": item.get("display_number"),
                "lookup_source": item.get("lookup_source"),
            }
            for item in (evidence.get("foreign_claim_lookup_candidates") or [])
            if isinstance(item, dict)
        ],
        "warnings": evidence.get("warnings") or [],
    }


def _valuation_citing_signal(state: PatentWorkflowState) -> dict[str, Any]:
    kipris_api_data = state.kipris_api_data or {}
    stats = kipris_api_data.get("citing_stats") or {}
    if not stats and state.citation_evidence:
        stats = state.citation_evidence.get("citing_stats") or {}
    available = bool(stats)
    if not isinstance(stats, dict):
        stats = {}
        available = False
    return {
        "available": available,
        "total_count": int(stats.get("total_count") or 0),
        "standardized_count": int(stats.get("standardized_count") or 0),
        "non_standardized_count": int(stats.get("non_standardized_count") or 0),
        "used_for": "portfolio_defensive_value_only",
    }


def _valuation_reference_document_payload(
    item: dict[str, Any],
    *,
    claim_text_limit: int,
    max_claims: int = 3,
) -> dict[str, Any]:
    return {
        "direction": item.get("direction"),
        "country_code": item.get("country_code"),
        "application_number": item.get("application_number"),
        "registration_number": item.get("registration_number"),
        "publication_number": item.get("publication_number"),
        "title": item.get("title"),
        "abstract": normalize_text(item.get("abstract"))[:1500],
        "register_status": item.get("register_status"),
        "claim_stats": item.get("claim_stats") or {},
        "representative_claims": [
            {
                "claim_no": claim.get("claim_no"),
                "is_independent": claim.get("is_independent"),
                "dependency": claim.get("dependency"),
                "text": normalize_text(claim.get("text"))[:claim_text_limit],
            }
            for claim in (item.get("representative_claims") or [])[:max_claims]
            if isinstance(claim, dict) and claim.get("text")
        ],
        "lookup_status": item.get("lookup_status"),
        "lookup_source": item.get("lookup_source"),
    }
