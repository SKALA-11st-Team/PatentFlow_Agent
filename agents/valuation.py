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
from workflow.state import PatentWorkflowState


VALUATION_AXES = list(AXIS_MODULES)
AXIS_LABELS = {axis: module.LABEL for axis, module in AXIS_MODULES.items()}
# 종합 점수는 권리성·기술성·시장성 3축만 평균낸다(사업 연계성 제외).
CORE_VALUATION_AXES = ("legal", "technology", "market")
CORE_VALUATION_TOTAL_MAX = len(CORE_VALUATION_AXES) * 100
# 사업 연계성 점수가 이 값 이상이면 3축 점수와 무관하게 종합 지표를 "유지"로 본다.
BUSINESS_FIT_OVERRIDE_SCORE = 60


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
    state.valuation_result = build_final_valuation_result(ordered_axes)
    state.current_stage = "valuation_check"
    return state


def run_axis_llm_required(*, axis: str, prompt: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    raw = call_llm(prompt, model=settings.openai_valuation_model, temperature=0)
    parsed = parse_json_object(raw)
    if not parsed:
        raw = call_llm(build_json_repair_prompt(axis=axis, raw=raw))
        parsed = parse_json_object(raw)
    if not parsed:
        raise RuntimeError(f"LLM valuation response for {axis} was not valid JSON.")
    return normalize_axis_llm_result(axis, parsed, evidence=evidence)


def build_json_repair_prompt(*, axis: str, raw: str) -> str:
    return (
        "아래 LLM 응답을 특허 가치평가 축 결과 JSON 객체 하나로만 다시 출력하세요.\n"
        "설명, 마크다운 코드블록, 접두사, 접미사를 쓰지 말고 JSON만 출력하세요.\n"
        "필수 필드: score, grade, rationale, confidence, evidence_ids, risk_factors, missing_information.\n"
        "권리성 축이면 가능한 경우 prior_art_references도 포함하세요.\n"
        f"평가축: {axis}\n\n"
        "원본 응답:\n"
        f"{raw}"
    )


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
    feedback = axis_retry_feedback_block(state, axis)
    if feedback:
        prompt = f"{prompt}\n\n{feedback}"
    return prompt


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
    required_fields = ["score", "grade", "rationale", "confidence"]
    missing_fields = [field for field in required_fields if parsed.get(field) in (None, "", [])]
    if missing_fields:
        raise RuntimeError(f"LLM valuation response for {axis} is missing: {', '.join(missing_fields)}.")
    score = max(0, min(100, int(parsed["score"])))
    result = {
        "axis": axis,
        "label": AXIS_LABELS[axis],
        "score": score,
        "grade": normalize_text(parsed.get("grade")),
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
    return result


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


def build_final_valuation_result(axes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    # 종합 점수는 권리성·기술성·시장성 3축의 평균으로만 산정한다.
    # 사업 연계성(business_fit)은 합산에 넣지 않고, 일정 점수 이상이면 종합 지표를
    # 무조건 "유지"로 끌어올리는 오버라이드로만 작용한다.
    core_axes = {name: axes[name] for name in CORE_VALUATION_AXES if name in axes}
    total_score = sum(int(axis.get("score") or 0) for axis in core_axes.values())
    average_score = round(total_score / len(core_axes), 1) if core_axes else 0

    business_fit = axes.get("business_fit") or {}
    business_fit_score = int(business_fit.get("score") or 0)
    business_fit_override = business_fit_score >= BUSINESS_FIT_OVERRIDE_SCORE

    final_indicator = average_score_to_indicator(average_score)
    if business_fit_override:
        final_indicator = "유지"

    missing_information = unique_texts(
        item for axis in axes.values() for item in axis.get("missing_information", [])
    )
    required_actions = []
    if business_fit.get("missing_information"):
        required_actions.append("사업부 적용 여부 및 향후 적용 계획 확인")
    if missing_information:
        required_actions.append("부족 정보 보완 후 최종 판단 재검토")
    result = {
        "axes": axes,
        "total_score": total_score,
        "total_score_max": CORE_VALUATION_TOTAL_MAX,
        "average_score": average_score,
        "core_axes": list(core_axes.keys()),
        "business_fit_score": business_fit_score,
        "business_fit_override": business_fit_override,
        "final_indicator": final_indicator,
        "recommendation": indicator_to_recommendation(final_indicator, missing_information),
        "decision_rationale": build_decision_rationale(
            core_axes,
            total_score,
            average_score,
            final_indicator,
            business_fit_override=business_fit_override,
            business_fit_score=business_fit_score,
        ),
        "required_actions": unique_texts(required_actions),
        "missing_information": missing_information,
    }
    return result


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


def average_score_to_indicator(average_score: float) -> str:
    if average_score >= 80:
        return "유지"
    if average_score >= 60:
        return "조건부 유지"
    if average_score >= 40:
        return "포기 검토"
    return "매각 후보"


def indicator_to_recommendation(final_indicator: str, missing_information: list[str]) -> str:
    if missing_information and final_indicator in {"유지", "조건부 유지"}:
        return "추가 정보 필요"
    if final_indicator in {"유지", "조건부 유지"}:
        return "유지 권고"
    return "포기 검토"


def build_decision_rationale(
    core_axes: dict[str, dict[str, Any]],
    total_score: int,
    average_score: float,
    final_indicator: str,
    *,
    business_fit_override: bool = False,
    business_fit_score: int = 0,
) -> list[str]:
    strongest = max(core_axes.values(), key=lambda axis: axis.get("score", 0))
    weakest = min(core_axes.values(), key=lambda axis: axis.get("score", 0))
    rationale = [
        f"권리성·기술성·시장성 3개 평가축 합산 점수는 {total_score}/{CORE_VALUATION_TOTAL_MAX}점, "
        f"평균 점수는 {average_score:g}/100점이며 최종 종합 지표는 {final_indicator}이다.",
        f"가장 강한 축은 {strongest.get('label')}({strongest.get('score')}점)이다.",
        f"보완이 필요한 축은 {weakest.get('label')}({weakest.get('score')}점)이다.",
    ]
    if business_fit_override:
        rationale.append(
            f"사업 연계성 점수가 {business_fit_score}점으로 기준({BUSINESS_FIT_OVERRIDE_SCORE}점) 이상이어서, "
            f"3축 점수와 무관하게 종합 지표를 유지로 판정했다."
        )
    return rationale


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
