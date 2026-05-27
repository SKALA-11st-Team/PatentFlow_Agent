from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text
from workflow.state import PatentWorkflowState


RIGHT_STABILITY_LABEL = "권리안정성"
CLAIM_PROTECTION_LABEL = "권리보호력"
PORTFOLIO_DEFENSIVE_LABEL = "포트폴리오·방어가치"

SUBSCORE_DEFINITIONS = {
    "right_stability": {
        "label": RIGHT_STABILITY_LABEL,
        "max_score": 40,
        "metrics": {
            "prior_art_overlap": {30, 22, 10, 0},
            "claim_structure_stability": {10, 7, 4, 0},
        },
    },
    "claim_protection": {
        "label": CLAIM_PROTECTION_LABEL,
        "max_score": 40,
        "metrics": {
            "core_solution_coverage": {12, 8, 3, 0},
            "independent_claim_scope": {12, 8, 3, 0},
            "dependent_claim_support": {10, 6, 3, 0},
            "claim_type_diversity": {6, 4, 2, 0},
        },
    },
    "portfolio_defensive_value": {
        "label": PORTFOLIO_DEFENSIVE_LABEL,
        "max_score": 20,
        "metrics": {
            "portfolio_connection": {6, 4, 2, 0},
            "portfolio_coverage_extension": {10, 7, 4, 0},
            "follow_on_right_signal": {4, 2, 0},
        },
    },
}


def apply_legal_scores(
    result: dict[str, Any],
    *,
    payload: dict[str, Any],
    state: PatentWorkflowState,
) -> dict[str, Any]:
    metrics = normalize_legal_metrics(result.get("subscores"))
    right_stability_score = subscore_total(metrics, "right_stability")
    claim_protection_score = subscore_total(metrics, "claim_protection")
    portfolio_defensive_score = subscore_total(metrics, "portfolio_defensive_value")
    score = right_stability_score + claim_protection_score + portfolio_defensive_score
    missing_information = merge_missing_information(result.get("missing_information"), metrics)
    confidence = adjust_confidence(float(result.get("confidence") or 0), metrics)
    context = build_legal_scoring_metrics(state=state, payload=payload)
    return {
        **result,
        "score": max(0, min(100, score)),
        "grade": legal_grade_for_score(score),
        "subscores": {
            key: build_subscore(result.get("subscores"), key, metrics)
            for key in SUBSCORE_DEFINITIONS
        },
        "sub_scores": {
            "right_stability_score": right_stability_score,
            "claim_protection_score": claim_protection_score,
            "portfolio_defensive_value_score": portfolio_defensive_score,
        },
        "legal_scoring_metrics": {
            **flatten_metrics(metrics),
            **context,
        },
        "missing_information": missing_information,
        "confidence": confidence,
    }


def build_legal_scoring_metrics(
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
        "right_status_gate": right_status_gate_metric(state, patent),
        "claim_count_context": metric(
            "claim_count_context",
            None,
            0,
            label="context_only",
            evidence={
                "independent_claim_count": int(claim_context.get("independent_claim_count") or 0),
                "dependent_claim_count": int(claim_context.get("dependent_claim_count") or 0),
                "total_claim_count": int(claim_context.get("total_claim_count") or 0),
            },
        ),
        "citing_reference_context": metric(
            "citing_reference_context",
            None,
            0,
            label="context_only",
            evidence=(citation_evidence.get("citing_signal") if isinstance(citation_evidence, dict) else {}) or {},
        ),
    }


def normalize_legal_metrics(subscores: Any) -> dict[str, dict[str, dict[str, Any]]]:
    normalized: dict[str, dict[str, dict[str, Any]]] = {}
    subscores = subscores if isinstance(subscores, dict) else {}
    for subscore_key, definition in SUBSCORE_DEFINITIONS.items():
        subscore = subscores.get(subscore_key) if isinstance(subscores.get(subscore_key), dict) else {}
        raw_metrics = subscore.get("metrics") if isinstance(subscore.get("metrics"), dict) else {}
        normalized[subscore_key] = {}
        for metric_key, allowed_scores in definition["metrics"].items():
            raw_metric = raw_metrics.get(metric_key) if isinstance(raw_metrics.get(metric_key), dict) else {}
            score = normalize_metric_score(raw_metric.get("score"), allowed_scores)
            normalized[subscore_key][metric_key] = metric(
                metric_key,
                score,
                max(allowed_scores),
                label=normalize_text(raw_metric.get("label")) or metric_key,
                rationale=normalize_text(raw_metric.get("rationale")),
            )
    return normalized


def normalize_metric_score(value: Any, allowed_scores: set[int]) -> int | None:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    return score if score in allowed_scores else None


def build_subscore(
    subscores: Any,
    key: str,
    metrics: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    definition = SUBSCORE_DEFINITIONS[key]
    original = subscores.get(key) if isinstance(subscores, dict) and isinstance(subscores.get(key), dict) else {}
    score = subscore_total(metrics, key)
    return {
        "label": normalize_text(original.get("label")) or definition["label"],
        "score": score,
        "max_score": definition["max_score"],
        "rationale": normalize_text(original.get("rationale")),
        "metrics": metrics[key],
    }


def subscore_total(metrics: dict[str, dict[str, dict[str, Any]]], key: str) -> int:
    return sum(
        int(item["score"])
        for item in metrics.get(key, {}).values()
        if item.get("score") is not None
    )


def flatten_metrics(metrics: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    flattened = {}
    for subscore_metrics in metrics.values():
        flattened.update(subscore_metrics)
    return flattened


def right_status_gate_metric(state: PatentWorkflowState, patent: dict[str, Any]) -> dict[str, Any]:
    metadata = patent.get("metadata") if isinstance(patent.get("metadata"), dict) else {}
    kipris_metadata = patent.get("kipris_metadata") if isinstance(patent.get("kipris_metadata"), dict) else {}
    structured = state.patent_structured or {}
    status = first_text(
        metadata.get("status"),
        metadata.get("register_status"),
        metadata.get("registration_status"),
        kipris_metadata.get("status"),
        kipris_metadata.get("register_status"),
        kipris_metadata.get("registration_status"),
        structured.get("status"),
    )
    if not status:
        return metric("right_status_gate", None, 0, label="unknown", evidence="등록상태 정보 없음")
    if any(token in status for token in ("소멸", "거절", "취하", "포기")):
        return metric("right_status_gate", None, 0, label="inactive", evidence=status)
    if any(token in status for token in ("등록", "유효")):
        return metric("right_status_gate", None, 0, label="registered_or_active", evidence=status)
    return metric("right_status_gate", None, 0, label="unclear", evidence=status)


def count_claims(preprocessed: dict[str, Any], *, independent: bool) -> int:
    return sum(
        1
        for claim in preprocessed.get("claims") or []
        if isinstance(claim, dict) and bool(claim.get("is_independent")) is independent
    )


def metric(
    key: str,
    score: int | None,
    max_score: int,
    *,
    label: str,
    rationale: str = "",
    evidence: Any = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "score": score,
        "max_score": max_score,
        "rationale": rationale,
        "evidence": evidence,
    }


def merge_missing_information(values: Any, metrics: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    missing = [normalize_text(value) for value in (values or []) if normalize_text(value)]
    if metrics["right_stability"]["prior_art_overlap"]["score"] is None:
        append_unique(missing, "선행문헌 비교 정보 확인 필요")
    if any_metric_missing(metrics["claim_protection"], ("core_solution_coverage", "independent_claim_scope")):
        append_unique(missing, "최종 청구항 및 과제의 해결수단 확인 필요")
    return missing[:3]


def adjust_confidence(confidence: float, metrics: dict[str, dict[str, dict[str, Any]]]) -> float:
    adjusted = max(0.0, min(1.0, confidence))
    if metrics["right_stability"]["prior_art_overlap"]["score"] is None:
        adjusted = min(adjusted, 0.69)
    if any_metric_missing(metrics["claim_protection"], ("core_solution_coverage", "independent_claim_scope")):
        adjusted = min(adjusted, 0.69)
    if any_metric_missing(metrics["portfolio_defensive_value"], ("portfolio_connection", "portfolio_coverage_extension")):
        adjusted = min(adjusted, 0.79)
    return adjusted


def any_metric_missing(metrics: dict[str, dict[str, Any]], keys: tuple[str, ...]) -> bool:
    return any(metrics[key].get("score") is None for key in keys)


def legal_grade_for_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def first_text(*values: Any) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


def append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
