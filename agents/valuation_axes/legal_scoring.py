from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text
from workflow.state import PatentWorkflowState


RIGHT_STABILITY_LABEL = "권리안정성"
CLAIM_PROTECTION_LABEL = "권리보호력"
PORTFOLIO_DEFENSIVE_LABEL = "포트폴리오·방어가치"

LABEL_SCORE_MAPS = {
    "prior_art_collision": {"low": 20, "medium": 13, "high": 6, "critical": 0},
    "similar_claim_density": {"low": 12, "medium": 8, "high": 4, "critical": 0},
    "dependent_claim_support": {"strong": 6, "moderate": 4, "weak": 2, "none": 0},
    "core_feature_covered": {"clear": 12, "partial": 7, "weak": 2},
    "claim_scope_limitation": {"broad": 10, "moderate": 7, "narrow": 3, "overly_narrow": 0},
    "design_around_difficulty": {"hard": 6, "moderate": 4, "easy": 1},
    "portfolio_connection": {"strong": 5, "moderate": 3, "weak": 1},
    "portfolio_coverage_extension": {"strong": 6, "moderate": 4, "weak": 2, "none": 0},
}


def apply_legal_scores(
    result: dict[str, Any],
    *,
    payload: dict[str, Any],
    state: PatentWorkflowState,
) -> dict[str, Any]:
    labels = result.get("scoring_labels") if isinstance(result.get("scoring_labels"), dict) else {}
    metrics = build_legal_scoring_metrics(payload=payload, state=state, labels=labels)
    right_stability_score = sum_metric_scores(
        metrics,
        (
            "prior_art_collision",
            "similar_claim_density",
            "claim_structure_stability",
        ),
    )
    claim_protection_score = sum_metric_scores(
        metrics,
        (
            "independent_claim_presence",
            "dependent_claim_support",
            "core_feature_covered",
            "claim_scope_limitation",
            "design_around_difficulty",
        ),
    )
    portfolio_defensive_score = sum_metric_scores(
        metrics,
        (
            "portfolio_connection",
            "portfolio_coverage_extension",
            "overseas_family_registration",
            "citing_reference_signal",
        ),
    )
    score = right_stability_score + claim_protection_score + portfolio_defensive_score
    missing_information = merge_missing_information(result.get("missing_information"), metrics)
    confidence = adjust_confidence(float(result.get("confidence") or 0), metrics)
    subscores = result.get("subscores") if isinstance(result.get("subscores"), dict) else {}
    return {
        **result,
        "score": max(0, min(100, score)),
        "grade": legal_grade_for_score(score),
        "subscores": {
            "right_stability": build_subscore(
                subscores,
                "right_stability",
                RIGHT_STABILITY_LABEL,
                right_stability_score,
                40,
                metrics,
                ("right_status_gate", "prior_art_collision", "similar_claim_density", "claim_structure_stability"),
            ),
            "claim_protection": build_subscore(
                subscores,
                "claim_protection",
                CLAIM_PROTECTION_LABEL,
                claim_protection_score,
                40,
                metrics,
                (
                    "independent_claim_presence",
                    "dependent_claim_support",
                    "core_feature_covered",
                    "claim_scope_limitation",
                    "design_around_difficulty",
                ),
            ),
            "portfolio_defensive_value": build_subscore(
                subscores,
                "portfolio_defensive_value",
                PORTFOLIO_DEFENSIVE_LABEL,
                portfolio_defensive_score,
                20,
                metrics,
                (
                    "portfolio_connection",
                    "portfolio_coverage_extension",
                    "overseas_family_registration",
                    "citing_reference_signal",
                ),
            ),
        },
        "sub_scores": {
            "right_stability_score": right_stability_score,
            "claim_protection_score": claim_protection_score,
            "portfolio_defensive_value_score": portfolio_defensive_score,
        },
        "legal_scoring_metrics": metrics,
        "missing_information": missing_information,
        "confidence": confidence,
    }


def build_legal_scoring_metrics(
    *,
    payload: dict[str, Any],
    state: PatentWorkflowState,
    labels: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    patent = payload.get("patent") if isinstance(payload.get("patent"), dict) else {}
    claim_context = patent.get("claim_context") if isinstance(patent.get("claim_context"), dict) else {}
    citation_evidence = patent.get("citation_evidence") if isinstance(patent.get("citation_evidence"), dict) else {}
    independent_count = int(claim_context.get("independent_claim_count") or 0)
    dependent_count = int(claim_context.get("dependent_claim_count") or 0)
    total_claim_count = int(claim_context.get("total_claim_count") or 0)

    return {
        "right_status_gate": right_status_gate_metric(patent),
        "prior_art_collision": label_metric(labels, "prior_art_collision", 20),
        "similar_claim_density": label_metric(labels, "similar_claim_density", 12),
        "claim_structure_stability": claim_structure_stability_metric(independent_count, dependent_count, total_claim_count),
        "independent_claim_presence": independent_claim_presence_metric(independent_count, total_claim_count),
        "dependent_claim_support": label_metric(labels, "dependent_claim_support", 6),
        "core_feature_covered": label_metric(labels, "core_feature_covered", 12),
        "claim_scope_limitation": label_metric(labels, "claim_scope_limitation", 10),
        "design_around_difficulty": label_metric(labels, "design_around_difficulty", 6),
        "portfolio_connection": label_metric(labels, "portfolio_connection", 5),
        "portfolio_coverage_extension": label_metric(labels, "portfolio_coverage_extension", 6),
        "overseas_family_registration": overseas_family_registration_metric(state.kipris_family_patents or []),
        "citing_reference_signal": citing_reference_signal_metric(citation_evidence.get("citing_signal") or {}),
    }


def right_status_gate_metric(patent: dict[str, Any]) -> dict[str, Any]:
    metadata = patent.get("metadata") if isinstance(patent.get("metadata"), dict) else {}
    kipris_metadata = patent.get("kipris_metadata") if isinstance(patent.get("kipris_metadata"), dict) else {}
    status = first_text(
        metadata.get("status"),
        metadata.get("register_status"),
        metadata.get("registration_status"),
        kipris_metadata.get("status"),
        kipris_metadata.get("register_status"),
        kipris_metadata.get("registration_status"),
    )
    if not status:
        return metric("right_status_gate", None, 0, label="unknown", evidence="등록상태 정보 없음")
    if any(token in status for token in ("소멸", "거절", "취하", "포기")):
        return metric("right_status_gate", None, 0, label="inactive", evidence=status)
    if any(token in status for token in ("등록", "유효")):
        return metric("right_status_gate", None, 0, label="registered_or_active", evidence=status)
    return metric("right_status_gate", None, 0, label="unclear", evidence=status)


def claim_structure_stability_metric(
    independent_count: int,
    dependent_count: int,
    total_claim_count: int,
) -> dict[str, Any]:
    if independent_count and dependent_count:
        return metric("claim_structure_stability", 8, 8, label="independent_and_dependent")
    if independent_count:
        return metric("claim_structure_stability", 6, 8, label="independent_only")
    if total_claim_count:
        return metric("claim_structure_stability", 2, 8, label="claims_structure_unclear")
    return metric("claim_structure_stability", 0, 8, label="claims_missing")


def independent_claim_presence_metric(independent_count: int, total_claim_count: int) -> dict[str, Any]:
    if independent_count:
        return metric("independent_claim_presence", 6, 6, label="independent_claim_exists")
    if total_claim_count:
        return metric("independent_claim_presence", 3, 6, label="independent_structure_unclear")
    return metric("independent_claim_presence", 0, 6, label="claims_missing")


def overseas_family_registration_metric(family_patents: list[dict[str, Any]]) -> dict[str, Any]:
    if not family_patents:
        return metric("overseas_family_registration", None, 5, label="unknown", evidence="family_info_missing")
    countries = [extract_country_code(item) for item in family_patents if isinstance(item, dict)]
    countries = [country for country in countries if country]
    foreign = [country for country in countries if country != "KR"]
    priority_countries = {"US", "EP", "CN", "JP"}
    priority_registered = any(
        country in priority_countries and family_item_registered(item)
        for item in family_patents
        for country in [extract_country_code(item)]
        if country
    )
    if priority_registered:
        return metric("overseas_family_registration", 5, 5, label="priority_country_registered", evidence=countries)
    if foreign:
        return metric("overseas_family_registration", 3, 5, label="foreign_family_exists", evidence=countries)
    return metric("overseas_family_registration", 1, 5, label="domestic_family_only", evidence=countries)


def citing_reference_signal_metric(citing_signal: dict[str, Any]) -> dict[str, Any]:
    if not citing_signal.get("available"):
        return metric("citing_reference_signal", None, 4, label="unknown", evidence="citing_stats_missing")
    total_count = int(citing_signal.get("total_count") or 0)
    if total_count >= 3:
        return metric("citing_reference_signal", 4, 4, label="three_or_more", evidence=total_count)
    if total_count >= 1:
        return metric("citing_reference_signal", 2, 4, label="one_or_two", evidence=total_count)
    return metric("citing_reference_signal", 0, 4, label="none", evidence=total_count)


def label_metric(labels: dict[str, Any], key: str, max_score: int) -> dict[str, Any]:
    label = normalize_text(labels.get(key)).lower()
    score_map = LABEL_SCORE_MAPS[key]
    score = score_map.get(label)
    return metric(key, score, max_score, label=label or "unknown")


def metric(key: str, score: int | None, max_score: int, *, label: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "score": score,
        "max_score": max_score,
        "evidence": evidence,
    }


def build_subscore(
    subscores: dict[str, Any],
    key: str,
    label: str,
    score: int,
    max_score: int,
    metrics: dict[str, dict[str, Any]],
    metric_keys: tuple[str, ...],
) -> dict[str, Any]:
    original = subscores.get(key) if isinstance(subscores.get(key), dict) else {}
    return {
        "label": label,
        "score": score,
        "max_score": max_score,
        "rationale": normalize_text(original.get("rationale")),
        "metrics": {metric_key: metrics[metric_key] for metric_key in metric_keys},
    }


def sum_metric_scores(metrics: dict[str, dict[str, Any]], keys: tuple[str, ...]) -> int:
    return sum(int(metrics[key]["score"]) for key in keys if metrics[key].get("score") is not None)


def merge_missing_information(values: Any, metrics: dict[str, dict[str, Any]]) -> list[str]:
    missing = [normalize_text(value) for value in (values or []) if normalize_text(value)]
    if metrics["independent_claim_presence"]["score"] == 0:
        append_unique(missing, "최종 청구항 정보 확인 필요")
    if metrics["prior_art_collision"]["score"] is None and metrics["similar_claim_density"]["score"] is None:
        append_unique(missing, "선행문헌 비교 정보 확인 필요")
    return missing[:3]


def adjust_confidence(confidence: float, metrics: dict[str, dict[str, Any]]) -> float:
    adjusted = max(0.0, min(1.0, confidence))
    if metrics["independent_claim_presence"]["score"] == 0:
        adjusted = min(adjusted, 0.39)
    if metrics["prior_art_collision"]["score"] is None or metrics["similar_claim_density"]["score"] is None:
        adjusted = min(adjusted, 0.69)
    if metrics["overseas_family_registration"]["score"] is None or metrics["citing_reference_signal"]["score"] is None:
        adjusted = min(adjusted, 0.79)
    return adjusted


def legal_grade_for_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def extract_country_code(item: dict[str, Any]) -> str | None:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    for key in ("country_code", "countryCode", "publicationCountryCode", "applicationCountryCode", "country"):
        text = normalize_text(item.get(key) or raw.get(key)).upper()
        if text:
            return text
    return None


def family_item_registered(item: dict[str, Any]) -> bool:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    registration_number = first_text(item.get("registration_number"), item.get("registrationNumber"), raw.get("registrationNumber"))
    kind_code = first_text(item.get("kind_code"), item.get("kindCode"), raw.get("kindCode")).upper()
    status = first_text(item.get("register_status"), item.get("status"), raw.get("registerStatus"))
    return bool(registration_number or kind_code.startswith("B") or "등록" in status)


def first_text(*values: Any) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


def append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
