from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.valuation_axes.common import select_by_types_or_axes
from agents.valuation_axes.market import extract_representative_cpc, grade_for_score
from services.patent.similar_patent_service import build_similar_patent_context
from workflow.state import PatentWorkflowState


AXIS = "technology"
LABEL = "기술성"
PROMPT_PATH = "valuation/valuation_technology.md"


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = runtime.build_input_payload(axis=AXIS, state=state, evidence=evidence)
    metrics = build_technology_metrics(state)
    payload["technology_metrics"] = metrics
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    return apply_technology_scores(result, metrics)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    return select_by_types_or_axes(
        items,
        source_types={"portfolio_context", "industry_report", "patent_api"},
        axes={AXIS},
    )


def build_technology_metrics(state: PatentWorkflowState) -> dict[str, Any]:
    metadata = {
        **(state.patent_structured or {}),
        **(((state.preprocessed_patent or {}).get("metadata")) or {}),
    }
    representative_cpc = extract_representative_cpc(state)
    artifact_dir = state.user_input.get("artifact_dir") if state.user_input else None
    output_dir = Path(artifact_dir) / "similar_patents" if artifact_dir else None
    try:
        similar_context = build_similar_patent_context(
            target_metadata=metadata,
            representative_cpc=representative_cpc,
            collect_pdf=True,
            output_dir=output_dir,
            pdf_text_limit=None,
        )
    except Exception as exc:
        similar_context = {
            "representative_cpc": representative_cpc,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": [f"similar_patent_search_failed:{exc.__class__.__name__}"],
        }
    return similar_context


def apply_technology_scores(result: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    sub_scores = result.get("sub_scores") or {}
    technical_breakdown = normalize_binary_breakdown(
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
    technical_breakdown, implementation_breakdown = apply_conservative_penalties(
        technical_breakdown,
        implementation_breakdown,
        risk_factors=risk_factors,
        missing_information=missing_information,
    )

    technical_differentiation_score = sum(technical_breakdown.values()) or normalize_score(
        result.get("technical_differentiation_score")
        or sub_scores.get("technical_differentiation_score"),
        maximum=60,
    )
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
    if implementation_specificity_score == 0:
        implementation_specificity_score = normalize_score(
            result.get("implementation_specificity_score")
            or sub_scores.get("implementation_specificity_score"),
            maximum=40,
        )

    score = technical_differentiation_score + implementation_specificity_score
    if not metrics.get("similar_patents"):
        message = "유사 특허 Top 3 비교 근거 확인 필요"
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


def apply_conservative_penalties(
    technical_breakdown: dict[str, int],
    implementation_breakdown: dict[str, int],
    *,
    risk_factors: list[Any],
    missing_information: list[Any],
) -> tuple[dict[str, int], dict[str, int]]:
    combined = " ".join(str(item or "") for item in [*risk_factors, *missing_information]).lower()
    technical = dict(technical_breakdown)
    implementation = dict(implementation_breakdown)

    if contains_any(combined, ["중복", "overlap", "대체", "유사", "차별", "novelty"]):
        technical["new_component_score"] = 0
        technical["solution_approach_difference_score"] = 0
    if contains_any(combined, ["근거", "전문", "명세서", "비교", "disclosure"]):
        technical["evidence_clarity_score"] = 0
    if contains_any(combined, ["수식", "공식", "formula", "임계", "threshold", "가중", "weight", "파라미터", "parameter", "하이퍼"]):
        implementation["condition_parameter_score"] = 0
        implementation["calculation_decision_score"] = 0
    if contains_any(combined, ["예외", "반복", "갱신", "업데이트", "update", "반영 규칙", "전환 규칙"]):
        implementation["exception_iteration_update_score"] = 0
    if contains_any(combined, ["불명확", "부족", "추가", "검증", "성능", "backtest", "benchmark"]):
        implementation["logic_score"] = 0

    return technical, implementation


def contains_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)
