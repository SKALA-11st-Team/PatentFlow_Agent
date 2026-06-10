from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import grade_for_score, normalize_text, select_by_types_or_axes
from agents.valuation_axes.payload_common import build_base_input_payload, build_claim_context, unique_texts
from schemas.valuation import DEFAULT_SUBSCORE_WEIGHTS
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
        axis=AXIS,
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    result = reconcile_legal_scores(result, state=state)
    return attach_legal_context(result, payload=payload, state=state)


LEGAL_SUBSCORE_MAX = dict(DEFAULT_SUBSCORE_WEIGHTS["legal"])


FOREIGN_LEGAL_EXCLUDED_DETAILS = {"prior_art_overlap"}
LEGAL_DETAIL_MAX = {
    "prior_art_overlap": 25,
    "claim_structure_stability": 10,
}


def legal_subscore_max_map(state: PatentWorkflowState) -> dict[str, int]:
    """운영 설정(valuation_config.subscoreWeights.legal)이 있으면 그 배점을, 없으면 기본 배점을 쓴다."""
    config = state.user_input.get("valuation_config") if isinstance(state.user_input, dict) else None
    configured = ((config or {}).get("subscoreWeights") or {}).get("legal") or {}
    return {
        key: int(configured.get(key, default_value))
        for key, default_value in LEGAL_SUBSCORE_MAX.items()
    }


def legal_detail_max_map(subscore_max: dict[str, int]) -> dict[str, int]:
    """right_stability 만점이 조정되면 그 하위 세부지표(prior_art_overlap/claim_structure_stability)
    배점도 같은 비율로 스케일한다(합계는 right_stability 만점과 정확히 일치)."""
    right_stability_max = int(subscore_max.get("right_stability") or 0)
    default_total = sum(LEGAL_DETAIL_MAX.values())  # 35
    if right_stability_max == default_total:
        return dict(LEGAL_DETAIL_MAX)
    if right_stability_max <= 0 or default_total <= 0:
        return {key: 0 for key in LEGAL_DETAIL_MAX}
    prior_art = round(LEGAL_DETAIL_MAX["prior_art_overlap"] * right_stability_max / default_total)
    prior_art = max(0, min(right_stability_max, prior_art))
    return {
        "prior_art_overlap": prior_art,
        "claim_structure_stability": right_stability_max - prior_art,
    }
# 각 권리성 subscore가 가져야 할 세부지표 키 전체 목록(docs/valuation_legal 공표 기준).
LEGAL_SUBSCORE_DETAIL_KEYS = {
    "right_stability": ["prior_art_overlap", "claim_structure_stability"],
    "claim_protection": [
        "core_solution_coverage",
        "independent_claim_scope",
        "dependent_claim_support",
        "claim_type_diversity",
    ],
    "portfolio_defensive_value": [
        "portfolio_connection_coverage",
        "overseas_right_coverage",
        "follow_on_right_signal",
    ],
}


def reconcile_legal_scores(result: dict[str, Any], *, state: PatentWorkflowState) -> dict[str, Any]:
    subscores = result.get("subscores") if isinstance(result.get("subscores"), dict) else {}
    reconciled: dict[str, Any] = {}
    total = 0
    total_max = 0
    foreign_patent = is_foreign_patent(state)
    subscore_max_map = legal_subscore_max_map(state)
    detail_max_map = legal_detail_max_map(subscore_max_map)
    for key, max_score in subscore_max_map.items():
        item = subscores.get(key) if isinstance(subscores.get(key), dict) else {}
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        effective_max_score = max_score
        if foreign_patent and key == "right_stability":
            effective_max_score = legal_subscore_max_without_details(
                max_score=max_score,
                details=details,
                excluded_detail_keys=FOREIGN_LEGAL_EXCLUDED_DETAILS,
                detail_max_map=detail_max_map,
            )
        excluded = FOREIGN_LEGAL_EXCLUDED_DETAILS if foreign_patent and key == "right_stability" else None
        detail_sum, missing_keys = sum_detail_scores(
            details,
            expected_keys=LEGAL_SUBSCORE_DETAIL_KEYS.get(key, []),
            excluded_detail_keys=excluded,
        )
        raw_score = coerce_int(item.get("score"))
        score_status = None
        if not missing_keys and detail_sum is not None:
            score = detail_sum
        elif detail_sum is None:
            # 세부지표가 전무하면 부분합 대신 LLM 자기보고로 폴백하되 표면화한다.
            score = raw_score if raw_score is not None else 0
            score_status = "no_details_self_report"
        elif raw_score is not None:
            # 일부 세부지표 누락 시 부분합으로 조용히 저평가하지 않고 자기보고를 사용한다.
            score = raw_score
            score_status = "partial_details_fallback_to_self_report"
        else:
            score = detail_sum
            score_status = "details_unavailable"
        score = max(0, min(effective_max_score, score or 0))
        reconciled_item = {**item, "score": score, "max_score": effective_max_score}
        if score_status:
            reconciled_item["score_status"] = score_status
            if missing_keys:
                reconciled_item["missing_detail_keys"] = missing_keys
        reconciled[key] = reconciled_item
        total += score
        total_max += effective_max_score
    total = normalize_score_to_100(total, total_max)
    return {
        **result,
        "subscores": {**subscores, **reconciled},
        "score": total,
        "grade": grade_for_score(total),
    }


def sum_detail_scores(
    details: dict[str, Any],
    *,
    expected_keys: list[str],
    excluded_detail_keys: set[str] | None = None,
) -> tuple[int | None, list[str]]:
    excluded = excluded_detail_keys or set()
    values: list[int] = []
    missing_keys: list[str] = []
    for key in expected_keys:
        if key in excluded:
            continue
        detail = details.get(key)
        value = coerce_int(detail.get("score")) if isinstance(detail, dict) else None
        if value is None:
            missing_keys.append(key)
        else:
            values.append(value)
    detail_sum = sum(values) if values else None
    return detail_sum, missing_keys


def legal_subscore_max_without_details(
    *,
    max_score: int,
    details: dict[str, Any],
    excluded_detail_keys: set[str],
    detail_max_map: dict[str, int] | None = None,
) -> int:
    detail_max = detail_max_map if detail_max_map is not None else LEGAL_DETAIL_MAX
    excluded_total = 0
    for key in excluded_detail_keys:
        if key in details:
            excluded_total += detail_max.get(key, 0)
    return max(0, max_score - excluded_total)


def normalize_score_to_100(score: int, max_score: int) -> int:
    if max_score <= 0:
        return 0
    return max(0, min(100, round(score * 100 / max_score)))


def is_foreign_patent(state: PatentWorkflowState) -> bool:
    country = normalize_text((state.patent_structured or {}).get("country"))
    return bool(country and country.upper() != "KR")


def coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    result = enforce_prior_art_comparison_status(result, payload)
    return {
        **result,
        "legal_context": build_legal_context(state=state, payload=payload),
    }


def enforce_prior_art_comparison_status(
    result: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    citation_evidence = ((payload.get("patent") or {}).get("citation_evidence") or {})
    collection = citation_evidence.get("prior_art_collection") or {}
    ready_count = int(collection.get("comparison_ready_count") or 0)
    subscores = result.get("subscores")
    if not isinstance(subscores, dict):
        return result
    right_stability = subscores.get("right_stability")
    if not isinstance(right_stability, dict):
        return result
    details = right_stability.get("details")
    if not isinstance(details, dict):
        return result
    overlap = details.get("prior_art_overlap")
    if not isinstance(overlap, dict):
        return result

    overlap["compared_prior_art_count"] = ready_count
    overlap["assessment_status"] = "comparison_ready" if ready_count else "unknown"
    if ready_count and int(collection.get("identifier_only_count") or 0) == 0:
        missing = result.get("missing_information")
        if isinstance(missing, list):
            result["missing_information"] = [
                item
                for item in missing
                if not _is_resolved_prior_art_missing_message(item)
            ]
    if ready_count == 0:
        overlap["overlap_basis"] = "상세 내용이 확보된 선행문헌이 없어 청구항 중복도를 판단할 수 없음"
        overlap["rationale"] = "선행문헌 식별번호만 확인되어 청구항·초록 기반 비교는 수행하지 않음"
        result["prior_art_references"] = []
        missing = result.setdefault("missing_information", [])
        message = "선행문헌의 대표 청구항 또는 초록"
        if isinstance(missing, list) and message not in missing:
            missing.append(message)
    return result


def _is_resolved_prior_art_missing_message(value: Any) -> bool:
    text = normalize_text(value)
    return "선행문헌" in text and any(token in text for token in ("청구항", "초록", "원문", "전문"))


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
        "foreign_identifier_only_documents": [
            {
                "country_code": item.get("country_code"),
                "document_number": item.get("document_number"),
                "kind_code": item.get("kind_code"),
                "display_number": item.get("display_number"),
                "comparison_status": "identifier_only",
            }
            for item in (evidence.get("foreign_identifier_only_documents") or [])
            if isinstance(item, dict)
        ],
        "prior_art_collection": evidence.get("prior_art_collection") or {},
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
        "document_number": item.get("document_number"),
        "kind_code": item.get("kind_code"),
        "display_number": item.get("display_number"),
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
        "comparison_status": item.get("comparison_status"),
    }
