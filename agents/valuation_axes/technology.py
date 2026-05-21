from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.valuation_axes.common import select_by_types_or_axes
from agents.valuation_axes.market import extract_representative_cpc, grade_for_score
from agents.valuation_axes.payload_common import build_base_input_payload
from services.patent.prior_art_patent_service import build_prior_art_patent_context
from services.patent.similar_patent_service import build_similar_patent_context
from workflow.state import PatentWorkflowState


AXIS = "technology"
LABEL = "기술성"
DIFFERENTIATION_PROMPT_PATH = "valuation/valuation_technology_differentiation.md"
IMPLEMENTATION_PROMPT_PATH = "valuation/valuation_technology_implementation.md"


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    metrics = build_technology_metrics(state)
    differentiation_payload = build_input_payload(state=state, evidence=evidence)
    differentiation_payload["technology_metrics"] = metrics
    differentiation_prompt = runtime.build_prompt(
        prompt_name=DIFFERENTIATION_PROMPT_PATH,
        state=state,
        payload=differentiation_payload,
        artifact_name=f"{AXIS}_differentiation_input",
    )
    differentiation_result = runtime.run_llm_required(axis=AXIS, prompt=differentiation_prompt, evidence=evidence)

    implementation_payload = build_input_payload(state=state, evidence=[])
    implementation_payload["technology_metrics"] = {"comparison_mode": "implementation-only"}
    implementation_prompt = runtime.build_prompt(
        prompt_name=IMPLEMENTATION_PROMPT_PATH,
        state=state,
        payload=implementation_payload,
        artifact_name=f"{AXIS}_implementation_input",
    )
    implementation_result = runtime.run_llm_required(axis=AXIS, prompt=implementation_prompt, evidence=[])
    implementation_result = override_implementation_result_from_state(implementation_result, state)
    merged = merge_technology_llm_results(
        differentiation_result=differentiation_result,
        implementation_result=implementation_result,
        metrics=metrics,
    )
    return apply_technology_scores(merged, metrics)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    return select_by_types_or_axes(
        items,
        source_types={"portfolio_context", "industry_report", "patent_api"},
        axes={AXIS},
    )


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return build_base_input_payload(state=state, evidence=evidence)


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
    }
    representative_cpc = extract_representative_cpc(state)
    artifact_dir = state.user_input.get("artifact_dir") if state.user_input else None
    mode = str(((state.user_input or {}).get("technology_comparison_mode") or "similar")).strip().lower()
    if mode not in {"similar", "prior-art", "hybrid"}:
        mode = "similar"

    similar_dir = Path(artifact_dir) / "similar_patents" if artifact_dir else None
    prior_art_dir = Path(artifact_dir) / "prior_art_patents" if artifact_dir else None
    if mode == "similar":
        return build_similar_context(metadata=metadata, representative_cpc=representative_cpc, output_dir=similar_dir)
    if mode == "prior-art":
        return build_prior_art_context(metadata=metadata, kipris_api_data=state.kipris_api_data, output_dir=prior_art_dir)
    return build_hybrid_context(
        metadata=metadata,
        kipris_api_data=state.kipris_api_data,
        representative_cpc=representative_cpc,
        similar_dir=similar_dir,
        prior_art_dir=prior_art_dir,
    )


def build_similar_context(
    *,
    metadata: dict[str, Any],
    representative_cpc: str | None,
    output_dir: Path | None,
) -> dict[str, Any]:
    try:
        return {
            **build_similar_patent_context(
                target_metadata=metadata,
                representative_cpc=representative_cpc,
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
    similar_dir: Path | None,
    prior_art_dir: Path | None,
    target_top_k: int = 3,
) -> dict[str, Any]:
    prior_art = build_prior_art_context(metadata=metadata, kipris_api_data=kipris_api_data, output_dir=prior_art_dir)
    prior_items = list(prior_art.get("similar_patents") or [])
    prior_pdf_count = count_pdf_collected(prior_items)

    if prior_pdf_count >= target_top_k:
        return {
            **prior_art,
            "comparison_mode": "hybrid",
            "selection_policy": "prior-art-only",
        }

    similar = build_similar_context(metadata=metadata, representative_cpc=representative_cpc, output_dir=similar_dir)
    hybrid_items = merge_hybrid_items(
        prior_items=prior_items,
        similar_items=list(similar.get("similar_patents") or []),
        min_pdf_count=target_top_k,
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
        "candidate_count": int(prior_art.get("candidate_count") or 0) + int(similar.get("candidate_count") or 0),
        "similar_patents": hybrid_items,
        "prior_art_patents": prior_items,
        "warnings": warnings,
    }


def merge_technology_contexts(
    *,
    mode: str,
    representative_cpc: str | None,
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    merged_items: list[dict[str, Any]] = []
    prior_art_items: list[dict[str, Any]] = []
    warnings: list[str] = []
    candidate_count = 0
    seen = set()

    for context in contexts:
        candidate_count += int(context.get("candidate_count") or 0)
        warnings.extend(str(item) for item in context.get("warnings") or [])
        for item in context.get("prior_art_patents") or []:
            prior_art_items.append(item)
        for item in context.get("similar_patents") or []:
            key = item_identity(item)
            if key in seen:
                continue
            seen.add(key)
            merged_items.append(item)

    return {
        "comparison_mode": mode,
        "representative_cpc": representative_cpc,
        "candidate_count": candidate_count,
        "similar_patents": merged_items,
        "prior_art_patents": prior_art_items,
        "warnings": dedupe_texts(warnings),
    }


def merge_hybrid_items(*, prior_items: list[dict[str, Any]], similar_items: list[dict[str, Any]], min_pdf_count: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen = set()
    pdf_count = 0
    for item in prior_items:
        key = item_identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if item.get("pdf_collected"):
            pdf_count += 1
    for item in similar_items:
        if pdf_count >= min_pdf_count:
            break
        key = item_identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if item.get("pdf_collected"):
            pdf_count += 1
    return merged


def count_pdf_collected(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("pdf_collected"))


def apply_technology_scores(result: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    sub_scores = result.get("sub_scores") or {}
    comparison_items = metrics.get("similar_patents") or []
    has_pdf_evidence = any(bool(item.get("pdf_collected")) for item in comparison_items)
    technical_breakdown = normalize_ternary_breakdown(
        result.get("technical_differentiation_breakdown") or {},
        {
            "new_component_score": 15,
            "combination_difference_score": 15,
            "processing_structure_difference_score": 15,
            "solution_approach_difference_score": 10,
            "evidence_clarity_score": 5,
        },
    )
    implementation_breakdown = normalize_binary_breakdown(
        result.get("implementation_specificity_breakdown") or {},
        {
            "input_data_score": 4,
            "processing_target_score": 3,
            "core_variable_score": 3,
            "output_structure_score": 3,
            "component_linkage_score": 2,
            "procedure_score": 6,
            "logic_score": 6,
            "condition_parameter_score": 5,
            "calculation_decision_score": 5,
            "exception_iteration_update_score": 3,
        },
    )
    risk_factors = list(result.get("risk_factors") or [])
    missing_information = list(result.get("missing_information") or [])
    technical_breakdown = apply_technical_penalties(
        technical_breakdown,
        risk_factors=risk_factors,
        missing_information=missing_information,
    )

    if not has_pdf_evidence:
        technical_breakdown = {key: 0 for key in technical_breakdown}

    technical_differentiation_score = sum(technical_breakdown.values())
    if not has_pdf_evidence:
        technical_differentiation_score = 0
    input_output_specificity_score = sum(
        implementation_breakdown[key]
        for key in (
            "input_data_score",
            "processing_target_score",
            "core_variable_score",
            "output_structure_score",
            "component_linkage_score",
        )
    )
    implementation_logic_score = sum(
        implementation_breakdown[key]
        for key in (
            "procedure_score",
            "logic_score",
            "condition_parameter_score",
            "calculation_decision_score",
            "exception_iteration_update_score",
        )
    )
    implementation_specificity_score = input_output_specificity_score + implementation_logic_score

    score = technical_differentiation_score + implementation_specificity_score
    if not metrics.get("similar_patents"):
        mode = str(metrics.get("comparison_mode") or "similar")
        message = "선행기술/유사 특허 비교 근거 확인 필요" if mode == "hybrid" else (
            "선행기술조사문헌 비교 근거 확인 필요" if mode == "prior-art" else "유사 특허 Top 3 비교 근거 확인 필요"
        )
        if message not in missing_information:
            missing_information.append(message)
    elif not has_pdf_evidence:
        message = "비교 문헌 PDF 원문 확보 실패로 기술 차별성 평가는 0점 처리"
        if message not in missing_information:
            missing_information.append(message)

    return {
        **result,
        "score": max(0, min(100, score)),
        "grade": grade_for_score(score),
        "technical_differentiation_score": technical_differentiation_score,
        "implementation_specificity_score": implementation_specificity_score,
        "sub_scores": {
            **sub_scores,
            "technical_differentiation_score": technical_differentiation_score,
            "implementation_specificity_score": implementation_specificity_score,
            "input_output_specificity_score": input_output_specificity_score,
            "implementation_logic_score": implementation_logic_score,
        },
        "technical_differentiation_breakdown": technical_breakdown,
        "implementation_specificity_breakdown": implementation_breakdown,
        "technology_metrics": metrics,
        "missing_information": missing_information,
    }


def merge_technology_llm_results(
    *,
    differentiation_result: dict[str, Any],
    implementation_result: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    risk_factors = dedupe_texts(
        [*list(differentiation_result.get("risk_factors") or []), *list(implementation_result.get("risk_factors") or [])]
    )
    missing_information = dedupe_texts(
        [
            *list(differentiation_result.get("missing_information") or []),
            *list(implementation_result.get("missing_information") or []),
        ]
    )
    rationale_parts = [
        f"기술 차별성 판단: {str(differentiation_result.get('rationale') or '').strip()}",
        f"구현 구체성 판단: {str(implementation_result.get('rationale') or '').strip()}",
    ]
    rationale = "\n\n".join(part for part in rationale_parts if not part.endswith(":"))
    return {
        **differentiation_result,
        "score": 0,
        "grade": differentiation_result.get("grade") or implementation_result.get("grade") or "D",
        "rationale": rationale,
        "risk_factors": risk_factors,
        "missing_information": missing_information,
        "confidence": min(
            float(differentiation_result.get("confidence") or 0.0),
            float(implementation_result.get("confidence") or 0.0),
        ),
        "technical_differentiation_score": differentiation_result.get("technical_differentiation_score"),
        "technical_differentiation_breakdown": differentiation_result.get("technical_differentiation_breakdown") or {},
        "implementation_specificity_score": implementation_result.get("implementation_specificity_score"),
        "implementation_specificity_breakdown": implementation_result.get("implementation_specificity_breakdown") or {},
        "sub_scores": {
            **(differentiation_result.get("sub_scores") or {}),
            **(implementation_result.get("sub_scores") or {}),
        },
        "technology_metrics": metrics,
    }


def override_implementation_result_from_state(result: dict[str, Any], state: PatentWorkflowState) -> dict[str, Any]:
    breakdown = build_implementation_breakdown_from_state(state)
    input_output_specificity_score = sum(
        breakdown[key]
        for key in (
            "input_data_score",
            "processing_target_score",
            "core_variable_score",
            "output_structure_score",
            "component_linkage_score",
        )
    )
    implementation_logic_score = sum(
        breakdown[key]
        for key in (
            "procedure_score",
            "logic_score",
            "condition_parameter_score",
            "calculation_decision_score",
            "exception_iteration_update_score",
        )
    )
    implementation_specificity_score = input_output_specificity_score + implementation_logic_score
    sub_scores = dict(result.get("sub_scores") or {})
    sub_scores.update(
        {
            "implementation_specificity_score": implementation_specificity_score,
            "input_output_specificity_score": input_output_specificity_score,
            "implementation_logic_score": implementation_logic_score,
        }
    )
    return {
        **result,
        "implementation_specificity_score": implementation_specificity_score,
        "implementation_specificity_breakdown": breakdown,
        "sub_scores": sub_scores,
    }


def build_implementation_breakdown_from_state(state: PatentWorkflowState) -> dict[str, int]:
    text = implementation_source_text(state)
    compact = normalize_space(text).lower()
    metadata = ((state.preprocessed_patent or {}).get("metadata") or {})
    claims = (state.preprocessed_patent.get("claims") or []) if isinstance(state.preprocessed_patent, dict) else []
    claim_text = " ".join(str((claim or {}).get("text") or "") for claim in claims)
    all_text = normalize_space(f"{compact}\n{claim_text.lower()}")

    def has_any(patterns: list[str]) -> bool:
        return any(re.search(pattern, all_text, re.I) for pattern in patterns)

    breakdown = {
        "input_data_score": 4 if has_any([r"입력", r"수집", r"데이터", r"정보를 입력", r"뉴스", r"지표"]) else 0,
        "processing_target_score": 3 if has_any([r"기업", r"피평가", r"산업", r"중분류", r"대분류", r"세부지표"]) else 0,
        "core_variable_score": 3 if has_any([r"가중치", r"점수", r"key issue", r"이상값", r"iqr", r"q1", r"q3"]) else 0,
        "output_structure_score": 3 if has_any([r"평가 결과", r"점수", r"산출", r"출력", r"등급"]) else 0,
        "component_linkage_score": 2 if has_any([r"기초하여", r"기반", r"연관성", r"반영", r"결합", r"산출"]) else 0,
        "procedure_score": 6 if has_any([r"s\d{2,4}", r"단계", r"먼저", r"이어서", r"다음", r"포함"]) else 0,
        "logic_score": 6 if has_any([r"산출", r"계산", r"비교", r"평가", r"결정", r"제거"]) else 0,
        "condition_parameter_score": 5 if has_any([r"임계", r"threshold", r"가중치", r"q1", r"q3", r"iqr", r"%", r"상위 25", r"하위 25"]) else 0,
        "calculation_decision_score": 5 if has_any([r"수학식", r"계산", r"공식", r"산출식", r"1\.5\s*\*\s*iqr", r"곱", r"합"]) else 0,
        "exception_iteration_update_score": 3 if has_any([r"반복", r"재", r"갱신", r"업데이트", r"제외", r"이상값 제거"]) else 0,
    }
    # If metadata/claims are very sparse, avoid over-crediting.
    if len(normalize_space(claim_text)) < 40 and len(compact) < 500:
        for key in ("logic_score", "condition_parameter_score", "calculation_decision_score", "exception_iteration_update_score"):
            breakdown[key] = 0
    return breakdown


def implementation_source_text(state: PatentWorkflowState) -> str:
    sections = ((state.preprocessed_patent or {}).get("sections") or {}) if isinstance(state.preprocessed_patent, dict) else {}
    section_text = "\n".join(str(value or "") for value in sections.values() if isinstance(value, str))
    claims = (state.preprocessed_patent.get("claims") or []) if isinstance(state.preprocessed_patent, dict) else []
    claim_text = "\n".join(str((claim or {}).get("text") or "") for claim in claims)
    metadata = ((state.preprocessed_patent or {}).get("metadata") or {}) if isinstance(state.preprocessed_patent, dict) else {}
    metadata_text = "\n".join(
        normalize_space(" ".join(value) if isinstance(value, list) else str(value or ""))
        for value in metadata.values()
        if isinstance(value, (str, list))
    )
    api_sections = ((state.kipris_api_data or {}).get("sections") or {}) if isinstance(state.kipris_api_data, dict) else {}
    api_text = "\n".join(str(value or "") for value in api_sections.values() if isinstance(value, str))
    return "\n".join(part for part in [section_text, claim_text, metadata_text, api_text] if part)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_score(value: Any, *, maximum: int) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(maximum, score))


def normalize_binary_breakdown(values: dict[str, Any], maximums: dict[str, int]) -> dict[str, int]:
    normalized = {}
    for key, maximum in maximums.items():
        try:
            score = int(values.get(key) or 0)
        except (TypeError, ValueError):
            score = 0
        normalized[key] = maximum if score >= maximum else 0
    return normalized


def normalize_ternary_breakdown(values: dict[str, Any], maximums: dict[str, int]) -> dict[str, int]:
    normalized = {}
    for key, maximum in maximums.items():
        raw = values.get(key)
        try:
            score = float(raw or 0)
        except (TypeError, ValueError):
            score = 0.0
        midpoint = maximum / 2
        if score >= maximum:
            normalized[key] = maximum
        elif score >= midpoint:
            normalized[key] = int(round(midpoint))
        else:
            normalized[key] = 0
    return normalized


def apply_conservative_penalties(
    technical_breakdown: dict[str, int],
    implementation_breakdown: dict[str, int],
    *,
    risk_factors: list[Any],
    missing_information: list[Any],
) -> tuple[dict[str, int], dict[str, int]]:
    technical = apply_technical_penalties(
        technical_breakdown,
        risk_factors=risk_factors,
        missing_information=missing_information,
    )
    implementation = apply_implementation_penalties(
        implementation_breakdown,
        risk_factors=risk_factors,
        missing_information=missing_information,
    )
    return technical, implementation


def apply_technical_penalties(
    technical_breakdown: dict[str, int],
    *,
    risk_factors: list[Any],
    missing_information: list[Any],
) -> dict[str, int]:
    combined = " ".join(str(item or "") for item in [*risk_factors, *missing_information]).lower()
    technical = dict(technical_breakdown)

    if contains_any(combined, ["중복", "overlap", "대체", "유사", "차별", "novelty"]):
        technical["new_component_score"] = 0
        technical["solution_approach_difference_score"] = 0
    if contains_any(combined, ["근거", "전문", "명세서", "비교", "disclosure"]):
        technical["evidence_clarity_score"] = 0
    return technical


def apply_implementation_penalties(
    implementation_breakdown: dict[str, int],
    *,
    risk_factors: list[Any],
    missing_information: list[Any],
) -> dict[str, int]:
    combined = " ".join(str(item or "") for item in [*risk_factors, *missing_information]).lower()
    implementation = dict(implementation_breakdown)

    if contains_any(combined, ["수식", "공식", "formula", "임계", "threshold", "가중", "weight", "파라미터", "parameter", "하이퍼"]):
        implementation["condition_parameter_score"] = 0
        implementation["calculation_decision_score"] = 0
    if contains_any(combined, ["예외", "반복", "갱신", "업데이트", "update", "반영 규칙", "전환 규칙"]):
        implementation["exception_iteration_update_score"] = 0
    if contains_any(combined, ["불명확", "부족", "추가", "검증", "성능", "backtest", "benchmark"]):
        implementation["logic_score"] = 0

    return implementation


def contains_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


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
