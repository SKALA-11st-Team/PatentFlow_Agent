from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text, select_by_types_or_axes
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
    result = reconcile_legal_scores(result)
    return attach_legal_context(result, payload=payload, state=state)


LEGAL_SUBSCORE_MAX = {
    "right_stability": 35,
    "claim_protection": 40,
    "portfolio_defensive_value": 25,
}


def reconcile_legal_scores(result: dict[str, Any]) -> dict[str, Any]:
    subscores = result.get("subscores") if isinstance(result.get("subscores"), dict) else {}
    reconciled: dict[str, Any] = {}
    total = 0
    for key, max_score in LEGAL_SUBSCORE_MAX.items():
        item = subscores.get(key) if isinstance(subscores.get(key), dict) else {}
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        detail_sum = sum_detail_scores(details)
        raw_score = coerce_int(item.get("score"))
        score = detail_sum if detail_sum is not None else raw_score
        score = max(0, min(max_score, score or 0))
        reconciled[key] = {**item, "score": score, "max_score": max_score}
        total += score
    total = max(0, min(100, total))
    return {
        **result,
        "subscores": {**subscores, **reconciled},
        "score": total,
        "grade": legal_grade_for_score(total),
    }


def sum_detail_scores(details: dict[str, Any]) -> int | None:
    values: list[int] = []
    for detail in details.values():
        if isinstance(detail, dict) and "score" in detail:
            value = coerce_int(detail.get("score"))
            if value is not None:
                values.append(value)
    return sum(values) if values else None


def coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def legal_grade_for_score(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


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
    payload["legal_context"] = build_legal_context(payload=payload, state=state, labels={})
    return payload


def attach_legal_context(
    result: dict[str, Any],
    *,
    payload: dict[str, Any],
    state: PatentWorkflowState,
) -> dict[str, Any]:
    return {
        **result,
        "legal_context": build_legal_context(state=state, payload=payload),
    }


def build_legal_context(
    *,
    state: PatentWorkflowState,
    payload: dict[str, Any] | None = None,
    labels: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    del labels
    patent = payload.get("patent") if isinstance(payload, dict) and isinstance(payload.get("patent"), dict) else {}
    claim_context = patent.get("claim_context") if isinstance(patent.get("claim_context"), dict) else {}
    citation_evidence = patent.get("citation_evidence") if isinstance(patent.get("citation_evidence"), dict) else {}
    if not claim_context:
        preprocessed = state.preprocessed_patent or {}
        claim_context = {
            "independent_claim_count": count_claims(preprocessed, independent=True),
            "dependent_claim_count": count_claims(preprocessed, independent=False),
            "total_claim_count": len(preprocessed.get("claims") or []),
        }
    if not citation_evidence:
        citation_evidence = state.citation_evidence or {}
    return {
        "right_status_gate": legal_context_metric(
            "right_status_gate",
            label=right_status_gate_label(state, patent),
            evidence=right_status_gate_evidence(state, patent),
        ),
        "claim_count_context": legal_context_metric(
            "claim_count_context",
            label="context_only",
            evidence={
                "independent_claim_count": int(claim_context.get("independent_claim_count") or 0),
                "dependent_claim_count": int(claim_context.get("dependent_claim_count") or 0),
                "total_claim_count": int(claim_context.get("total_claim_count") or 0),
            },
        ),
        "citing_reference_context": legal_context_metric(
            "citing_reference_context",
            label="context_only",
            evidence=(citation_evidence.get("citing_signal") if isinstance(citation_evidence, dict) else {}) or {},
        ),
    }


def right_status_gate_label(state: PatentWorkflowState, patent: dict[str, Any]) -> str:
    status = right_status_text(state, patent)
    if not status:
        return "unknown"
    if any(token in status for token in ("소멸", "거절", "취하", "포기")):
        return "inactive"
    if any(token in status for token in ("등록", "유효")):
        return "registered_or_active"
    return "unclear"


def right_status_gate_evidence(state: PatentWorkflowState, patent: dict[str, Any]) -> str:
    return right_status_text(state, patent) or "등록상태 정보 없음"


def right_status_text(state: PatentWorkflowState, patent: dict[str, Any]) -> str:
    metadata = patent.get("metadata") if isinstance(patent.get("metadata"), dict) else {}
    kipris_metadata = patent.get("kipris_metadata") if isinstance(patent.get("kipris_metadata"), dict) else {}
    structured = state.patent_structured or {}
    return first_text(
        metadata.get("status"),
        metadata.get("register_status"),
        metadata.get("registration_status"),
        kipris_metadata.get("status"),
        kipris_metadata.get("register_status"),
        kipris_metadata.get("registration_status"),
        structured.get("status"),
    )


def count_claims(preprocessed: dict[str, Any], *, independent: bool) -> int:
    return sum(
        1
        for claim in preprocessed.get("claims") or []
        if isinstance(claim, dict) and bool(claim.get("is_independent")) is independent
    )


def legal_context_metric(key: str, *, label: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "score": None,
        "max_score": 0,
        "rationale": "",
        "evidence": evidence,
    }


def first_text(*values: Any) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


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
