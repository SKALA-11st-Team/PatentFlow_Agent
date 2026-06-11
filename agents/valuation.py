from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace
from agents.valuation_axes import AXIS_MODULES
from agents.valuation_axes.common import grade_for_score
from schemas.valuation import (
    DEFAULT_SUBSCORE_WEIGHTS,
    is_default_valuation_config,
    resolve_valuation_config,
    validate_axis_result,
    validate_valuation_result,
)
from workflow.state import PatentWorkflowState


VALUATION_AXES = list(AXIS_MODULES)
AXIS_LABELS = {axis: module.LABEL for axis, module in AXIS_MODULES.items()}


@dataclass(frozen=True)
class AxisRuntime:
    build_prompt: Callable[..., str]
    run_llm_required: Callable[..., dict[str, Any]]


@trace(name="valuation_agent", run_type="chain")
def run_valuation_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    if state.user_input.get("use_llm_valuation", True) is False:
        raise RuntimeError("LLM valuation is required, but use_llm_valuation is disabled.")

    for axis in VALUATION_AXES:
        state = run_axis_valuation_agent(axis, state)
    return finalize_valuation_agent(state)


@trace(name="valuation_axis_agent", run_type="chain")
def run_axis_valuation_agent(axis: str, state: PatentWorkflowState) -> PatentWorkflowState:
    if state.user_input.get("use_llm_valuation", True) is False:
        raise RuntimeError("LLM valuation is required, but use_llm_valuation is disabled.")
    if axis not in VALUATION_AXES:
        raise ValueError(f"Unknown valuation axis: {axis}")

    current_result = {} if axis == VALUATION_AXES[0] else dict(state.valuation_result or {})
    axes = dict(current_result.get("axes") or {})
    axes[axis] = AXIS_MODULES[axis].run(state, AXIS_RUNTIME)

    state.valuation_result = {
        **current_result,
        "axes": axes,
    }
    state.current_stage = "valuation_check"
    return state


@trace(name="valuation_axis_result_agent", run_type="chain")
def run_axis_valuation_result(axis: str, state: PatentWorkflowState) -> dict[str, Any]:
    if state.user_input.get("use_llm_valuation", True) is False:
        raise RuntimeError("LLM valuation is required, but use_llm_valuation is disabled.")
    if axis not in VALUATION_AXES:
        raise ValueError(f"Unknown valuation axis: {axis}")
    return AXIS_MODULES[axis].run(state, AXIS_RUNTIME)


def finalize_valuation_axis_results(
    state: PatentWorkflowState,
    axis_results: dict[str, dict[str, Any]],
) -> PatentWorkflowState:
    state.valuation_result = {"axes": axis_results}
    return finalize_valuation_agent(state)


@trace(name="valuation_finalize_agent", run_type="chain")
def finalize_valuation_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    axes = dict((state.valuation_result or {}).get("axes") or {})
    missing_axes = [axis for axis in VALUATION_AXES if axis not in axes]
    if missing_axes:
        raise RuntimeError(f"Valuation axes are incomplete: {', '.join(missing_axes)}.")

    ordered_axes = {axis: axes[axis] for axis in VALUATION_AXES}
    state.valuation_result = build_final_valuation_result(ordered_axes, config=valuation_config_from_state(state))
    state.current_stage = "valuation_check"
    return state


def valuation_config_from_state(state: PatentWorkflowState) -> dict[str, Any]:
    """state.user_input의 valuation_config(이미 resolve된 dict)를 돌려준다.
    CLI 등 API 외 경로에서 주입되지 않았으면 기본값으로 resolve한다."""
    config = state.user_input.get("valuation_config")
    if isinstance(config, dict) and config.get("axisWeights"):
        return config
    return resolve_valuation_config(None)


def valuation_seed() -> int | None:
    # seed 지원 모델로 고정된 배포에서만 seed를 전달한다(VALUATION_SEED_SUPPORTED=true + VALUATION_SEED).
    if settings.valuation_seed_supported and settings.valuation_seed is not None:
        return settings.valuation_seed
    return None


def run_axis_llm_required(*, axis: str, prompt: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    # 재현성(VAL-01): seed 지원 모델이면 run_axis_llm_once가 seed를 전달해 동일 입력→동일 점수를 보장한다.
    # seed 미지원(기본 gpt-5)일 때는 VALUATION_ENSEMBLE_RUNS 앙상블 중앙값으로 점수 분산을 줄인다.
    ensemble_runs = max(1, int(settings.valuation_ensemble_runs or 1))
    if ensemble_runs > 1:
        results = [run_axis_llm_once(axis=axis, prompt=prompt, evidence=evidence) for _ in range(ensemble_runs)]
        return combine_axis_ensemble(axis, results)
    return run_axis_llm_once(axis=axis, prompt=prompt, evidence=evidence)


def run_axis_llm_once(*, axis: str, prompt: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    # 재현성: seed 지원 모델 + temperature=0 + 고정 seed로 동일 입력→동일 점수를 보장한다.
    raw = call_llm(prompt, model=settings.valuation_model, temperature=0.0, seed=valuation_seed())
    parsed = parse_json_object(raw)
    if not parsed:
        raise RuntimeError(f"LLM valuation response for {axis} was not valid JSON.")
    return normalize_axis_llm_result(axis, parsed, evidence=evidence)


def combine_axis_ensemble(axis: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        raise RuntimeError(f"LLM valuation ensemble for {axis} produced no results.")
    ordered = sorted(results, key=lambda item: int(item.get("score") or 0))
    selected = dict(ordered[len(ordered) // 2])
    average_score = round(sum(int(item.get("score") or 0) for item in results) / len(results))
    selected["score"] = max(0, min(100, int(average_score)))
    selected["grade"] = grade_for_score(selected["score"])
    selected["ensemble_runs"] = len(results)
    selected["ensemble_scores"] = [int(item.get("score") or 0) for item in results]
    selected["risk_factors"] = unique_texts(item for result in results for item in result.get("risk_factors", []))
    selected["missing_information"] = unique_texts(
        item for result in results for item in result.get("missing_information", [])
    )
    selected["evidence_ids"] = unique_texts(item for result in results for item in result.get("evidence_ids", []))
    selected["confidence"] = round(
        sum(float(result.get("confidence") or 0.0) for result in results) / len(results),
        2,
    )
    return validate_axis_result(axis, selected)


def build_axis_prompt(
    *,
    prompt_name: str,
    state: PatentWorkflowState,
    payload: dict[str, Any],
    artifact_name: str,
    axis: str,
) -> str:
    common_template = load_prompt("valuation/common_valuation.md").strip()
    template = load_prompt(prompt_name).strip()
    save_valuation_input_payload(state, artifact_name, payload)
    prompt = f"{common_template}\n\n{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    override = subscore_override_block(state, axis)
    if override:
        prompt = f"{prompt}\n\n{override}"
    feedback = axis_retry_feedback_block(state, axis)
    if feedback:
        prompt = f"{prompt}\n\n{feedback}"
    return prompt


SUBSCORE_LABELS = {
    "right_stability": "권리안정성",
    "claim_protection": "권리보호력",
    "portfolio_defensive_value": "포트폴리오·해외 권리",
    "official_business_evidence": "공식 사업 근거",
    "product_function_direct_match": "제품·기능 직접 일치",
    "business_context_fit": "사업 맥락 적합성",
}


def subscore_override_block(state: PatentWorkflowState, axis: str) -> str:
    """운영 설정이 기본 배점과 다를 때만, 프롬프트 본문(markdown)에 명시된 배점을 덮어쓰는
    재정의 블록을 만든다. 프롬프트 파일 자체는 수정하지 않아 버전 추적이 단순해진다."""
    config = valuation_config_from_state(state)
    configured = (config.get("subscoreWeights") or {}).get(axis)
    defaults = DEFAULT_SUBSCORE_WEIGHTS.get(axis)
    if not configured or not defaults or configured == defaults:
        return ""
    lines = [
        "## 배점 재정의 (운영 설정 — 아래 배점이 프롬프트 본문의 배점보다 우선합니다)",
        "법무팀이 평가 기준 배점을 조정했습니다. subscores의 max_score와 점수 산정에 아래 배점을 사용하세요.",
    ]
    for key, max_score in configured.items():
        label = SUBSCORE_LABELS.get(key, key)
        default_value = defaults.get(key)
        if default_value == max_score:
            lines.append(f"- {label}({key}): {max_score}점")
        else:
            lines.append(f"- {label}({key}): {default_value}점 → {max_score}점")
    lines.append(f"- 위 subscore 만점 합계: {sum(configured.values())}점")
    lines.append(
        "- 단, 세부지표(details)의 개별 배점은 프롬프트 본문 기준을 그대로 유지해 채점하세요. "
        "세부지표 부분합은 시스템이 위 배점으로 자동 변환합니다(이중 변환 금지)."
    )
    return "\n".join(lines)


def axis_retry_feedback_block(state: PatentWorkflowState, axis: str) -> str:
    """Render the previous supervisor reject reason so a re-evaluation corrects
    the flagged issue instead of blindly re-rolling. Only emitted when the axis
    was sent back with status valuation_retry on the prior pass."""
    checks = (state.valuation_result or {}).get("axis_supervisor_checks") or {}
    check = checks.get(axis) if isinstance(checks, dict) else None
    if not isinstance(check, dict) or check.get("status") != "valuation_retry":
        return ""
    issues = [text for text in (normalize_text(item) for item in (check.get("issues") or [])) if text]
    reason = normalize_text(check.get("reason"))
    if not issues and not reason:
        return ""
    lines = [
        "## 이전 평가 반려 사유 (반드시 교정)",
        "직전 평가가 아래 사유로 반려되었습니다. 같은 문제를 반복하지 말고 평가 논리와 점수를 교정하세요.",
    ]
    lines.extend(f"- {issue}" for issue in issues)
    if reason:
        lines.append(f"종합 사유: {reason}")
    return "\n".join(lines)


AXIS_RUNTIME = AxisRuntime(
    build_prompt=build_axis_prompt,
    run_llm_required=run_axis_llm_required,
)


def normalize_axis_llm_result(axis: str, parsed: dict[str, Any], *, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    known_evidence_ids = {item.get("evidence_id") for item in evidence if item.get("evidence_id")}
    evidence_ids = [
        evidence_id
        for evidence_id in normalize_list(parsed.get("evidence_ids"))
        if evidence_id in known_evidence_ids
    ]
    required_fields = ["score", "rationale", "confidence"]
    missing_fields = [field for field in required_fields if parsed.get(field) in (None, "", [])]
    if missing_fields:
        raise RuntimeError(f"LLM valuation response for {axis} is missing: {', '.join(missing_fields)}.")
    score = max(0, min(100, int(parsed["score"])))
    result = {
        "axis": axis,
        "label": AXIS_LABELS[axis],
        "score": score,
        "grade": grade_for_score(score),
        "rationale": normalize_text(parsed.get("rationale")),
        "evidence_ids": evidence_ids,
        "risk_factors": normalize_list(parsed.get("risk_factors")),
        "missing_information": normalize_list(parsed.get("missing_information")),
        "confidence": max(0.0, min(1.0, float(parsed["confidence"]))),
    }
    for optional_field in (
        "industry_marketability_score",
        "industry_marketability_breakdown",
        "technical_differentiation_score",
        "implementation_specificity_score",
        "marketability_metrics",
        "technology_metrics",
        "technical_differentiation_breakdown",
        "implementation_specificity_breakdown",
        "scoring_labels",
    ):
        if optional_field in parsed:
            result[optional_field] = parsed[optional_field]
    if "prior_art_references" in parsed:
        result["prior_art_references"] = normalize_list(parsed.get("prior_art_references"))
    subscores = normalize_subscores(parsed.get("subscores"))
    if subscores:
        result["subscores"] = subscores
    return validate_axis_result(axis, result)


def normalize_subscores(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        try:
            score = int(item.get("score"))
            max_score = int(item.get("max_score"))
        except (TypeError, ValueError):
            continue
        normalized_item = {
            "label": normalize_text(item.get("label")),
            "score": max(0, min(max_score, score)),
            "max_score": max_score,
            "rationale": normalize_text(item.get("rationale")),
        }
        if isinstance(item.get("details"), dict):
            normalized_item["details"] = item["details"]
        details = normalized_item.get("details")
        if isinstance(details, dict) and all(isinstance(value, (int, float)) for value in details.values()):
            normalized_item["details"] = {
                str(detail_key): detail_value
                for detail_key, detail_value in details.items()
                if isinstance(detail_key, str) and isinstance(detail_value, (int, float))
            }
        normalized[str(key)] = normalized_item
    return normalized


def build_final_valuation_result(
    axes: dict[str, dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    applied_config = config or resolve_valuation_config(None)
    cutoffs = applied_config.get("gradeCutoffs") or {}
    axis_weights = applied_config.get("axisWeights") or {}
    validated_axes = {axis: validate_axis_result(axis, payload) for axis, payload in axes.items()}
    # 운영 설정 컷오프로 축 등급을 재산정한다(앙상블/reconcile 단계는 기본 컷오프로 계산했을 수 있음).
    for axis_result in validated_axes.values():
        axis_result["grade"] = grade_for_score(int(axis_result.get("score") or 0), cutoffs)
    total_score = sum(int(axis.get("score") or 0) for axis in validated_axes.values())
    # total_score는 기존 의미(축 점수 단순 합, 0~400)를 유지하고, 가중치는 average_score에만 반영한다.
    average_score = weighted_average_score(validated_axes, axis_weights)
    final_grade = grade_for_score(average_score, cutoffs)
    # `or 60`은 유효 설정값 0(항상 유지 권고)을 기본값 60으로 되돌리는 버그라 None 체크로 처리한다.
    configured_threshold = applied_config.get("maintainThreshold")
    final_indicator = score_to_final_recommendation(
        average_score,
        threshold=60.0 if configured_threshold is None else float(configured_threshold),
    )
    missing_information = unique_texts(
        item for axis in validated_axes.values() for item in axis.get("missing_information", [])
    )
    if missing_information:
        final_indicator = "추가 정보 필요"
    warnings = valuation_warnings(validated_axes)
    required_actions = []
    business_fit = validated_axes.get("business_fit") or {}
    if business_fit.get("missing_information"):
        required_actions.append("사업부 적용 여부 및 향후 적용 계획 확인")
    if missing_information:
        required_actions.append("부족 정보 보완 후 최종 판단 재검토")
    result = {
        "axes": validated_axes,
        "total_score": total_score,
        "average_score": average_score,
        "final_grade": final_grade,
        "final_indicator": final_indicator,
        "recommendation": final_indicator,
        "decision_rationale": build_decision_rationale(
            validated_axes,
            total_score,
            average_score,
            final_grade,
            final_indicator,
            config=applied_config,
        ),
        "required_actions": unique_texts(required_actions),
        "missing_information": missing_information,
        "warnings": warnings,
        "applied_config": applied_config,
    }
    if settings.valuation_schema_strict:
        return validate_valuation_result(result)
    return result


def weighted_average_score(axes: dict[str, dict[str, Any]], axis_weights: dict[str, Any]) -> float:
    if not axes:
        return 0.0
    weight_sum = 0.0
    weighted = 0.0
    for axis, axis_result in axes.items():
        try:
            weight = float(axis_weights.get(axis, 0) or 0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0:
            weight = 25.0
        weight_sum += weight
        weighted += weight * int(axis_result.get("score") or 0)
    return round(weighted / weight_sum, 1) if weight_sum > 0 else 0.0


def save_valuation_input_payload(state: PatentWorkflowState, name: str, payload: dict[str, Any]) -> Path | None:
    if state.user_input.get("no_save", False):
        return None
    output_dir = valuation_input_output_dir(state)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def valuation_input_output_dir(state: PatentWorkflowState) -> Path:
    artifact_dir = state.user_input.get("artifact_dir")
    if artifact_dir:
        return Path(artifact_dir) / "valuation_inputs"
    return settings.output_dir / "valuation_inputs"


def score_to_final_recommendation(average_score: float, *, threshold: float = 60) -> str:
    if average_score >= threshold:
        return "유지 권고"
    return "포기 검토"


def build_decision_rationale(
    axes: dict[str, dict[str, Any]],
    total_score: int,
    average_score: float,
    final_grade: str,
    final_indicator: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    strongest = max(axes.values(), key=lambda axis: axis.get("score", 0))
    weakest = min(axes.values(), key=lambda axis: axis.get("score", 0))
    if config and not is_default_valuation_config(config):
        weights = config.get("axisWeights") or {}
        weight_text = ", ".join(
            f"{(axes.get(axis) or {}).get('label') or axis} {weights.get(axis, 0):g}"
            for axis in ("legal", "technology", "market", "business_fit")
        )
        score_line = (
            f"4개 평가축 합산 점수는 {total_score}/400점, "
            f"가중 평균 점수는 {average_score:g}/100점(가중치 {weight_text}), 종합 등급은 {final_grade}이다."
        )
    else:
        score_line = (
            f"4개 평가축 합산 점수는 {total_score}/400점, 평균 점수는 {average_score:g}/100점, "
            f"종합 등급은 {final_grade}이다."
        )
    return [
        score_line,
        f"AI 권고 라벨은 {final_indicator}이다.",
        f"가장 강한 축은 {strongest.get('label')}({strongest.get('score')}점)이다.",
        f"보완이 필요한 축은 {weakest.get('label')}({weakest.get('score')}점)이다.",
    ]


def valuation_warnings(axes: dict[str, dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    technology_metrics = (axes.get("technology") or {}).get("technology_metrics") or {}
    warnings.extend(str(item) for item in technology_metrics.get("warnings") or [] if item)
    target_count = int(technology_metrics.get("target_count") or 0)
    comparison_count = len(technology_metrics.get("similar_patents") or [])
    if target_count > 0 and comparison_count == 0:
        warnings.append("technology_comparison_empty")
    return unique_texts(warnings)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (normalize_text(item) for item in value) if text]


def unique_texts(values: Any) -> list[str]:
    result = []
    for value in values:
        text = normalize_text(value)
        if text and text not in result:
            result.append(text)
    return result
