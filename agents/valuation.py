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
    DEFAULT_MAINTAIN_THRESHOLD,
    DEFAULT_SUBSCORE_WEIGHTS,
    is_default_valuation_config,
    resolve_valuation_config,
    validate_axis_result,
    validate_valuation_result,
)
from workflow.state import PatentWorkflowState


VALUATION_AXES = list(AXIS_MODULES)
AXIS_LABELS = {axis: module.LABEL for axis, module in AXIS_MODULES.items()}
# 종합 점수는 권리성·기술성·시장성 3축만 평균낸다(사업 연계성 제외).
CORE_VALUATION_AXES = ("legal", "technology", "market")
CORE_VALUATION_TOTAL_MAX = len(CORE_VALUATION_AXES) * 100
# 사업 연계성 점수가 이 값 이상이면 3축 점수와 무관하게 AI 권고 라벨을 "유지 권고"로 본다.
# 이는 운영 설정이 아닌 고정 제품 정책이며, 평균점 기준의 '유지 권고' 임계(valuationConfig.maintainThreshold)
# 와는 별개 개념이다(기본값이 둘 다 60이라 우연히 겹치지만, maintainThreshold는 3축 평균에,
# 이 값은 사업 연계성 단일 축에 적용된다). 오버라이드 발동 시 decision_rationale에 사유를 남긴다.
# 운영 설정 valuationConfig.businessFitOverrideThreshold로 조정 가능(미설정 시 아래 기본값).
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
    # seed 미지원(기본 gpt-5)일 때는 VALUATION_ENSEMBLE_RUNS 앙상블 중앙값 run을 채택해 점수 분산을 줄인다.
    # 주의: 각 축은 이후 reconcile_*/apply_*에서 채택된 run의 subscores로 최종 score를 결정론적으로
    # 재산정하므로, combine_axis_ensemble이 덮어쓰는 평균 top-level score 자체는 최종 결과에 남지 않는다
    # (앙상블 효과는 '중앙값 run의 subscores 선택'으로 실현된다). 분산 축소는 ensemble_runs>1일 때만 적용된다.
    ensemble_runs = max(1, int(settings.valuation_ensemble_runs or 1))
    if ensemble_runs > 1:
        results = [run_axis_llm_once(axis=axis, prompt=prompt, evidence=evidence) for _ in range(ensemble_runs)]
        return combine_axis_ensemble(axis, results)
    return run_axis_llm_once(axis=axis, prompt=prompt, evidence=evidence)


def run_axis_llm_once(*, axis: str, prompt: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    # 재현성: seed 지원 모델 + temperature=0 + 고정 seed로 동일 입력→동일 점수를 보장한다.
    raw = call_llm(
        prompt,
        model=settings.openai_valuation_model or settings.valuation_model,
        temperature=0.0,
        seed=valuation_seed(),
        reasoning_effort=settings.openai_valuation_reasoning_effort,
        timeout=settings.openai_valuation_timeout_seconds,
    )
    parsed = parse_json_object(raw)
    if not parsed:
        raw = call_llm(build_json_repair_prompt(axis=axis, raw=raw))
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
    try:
        score = max(0, min(100, int(float(parsed["score"]))))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"LLM valuation response for {axis} has non-numeric score.") from exc
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
    # 종합 점수는 권리성·기술성·시장성 3축의 평균으로만 산정한다.
    # 사업 연계성(business_fit)은 합산에 넣지 않고, 일정 점수 이상이면 AI 권고 라벨을
    # 무조건 "유지 권고"로 끌어올리는 오버라이드로만 작용한다.
    core_axes = {name: validated_axes[name] for name in CORE_VALUATION_AXES if name in validated_axes}
    total_score = sum(int(axis.get("score") or 0) for axis in core_axes.values())
    # total_score는 3축 단순 합(0~300)을 유지하고, 운영 설정 가중치는 average_score에만 반영한다.
    average_score = weighted_average_score(core_axes, axis_weights)
    final_grade = grade_for_score(average_score, cutoffs)

    business_fit = validated_axes.get("business_fit") or {}
    business_fit_score = int(business_fit.get("score") or 0)
    # 오버라이드 기준점은 운영 설정(valuationConfig.businessFitOverrideThreshold)을 따른다(미설정 시 기본 60).
    business_fit_override = business_fit_score >= business_fit_override_cutoff(applied_config)

    missing_information = unique_texts(
        item for axis in validated_axes.values() for item in axis.get("missing_information", [])
    )
    # 부족 정보(missing_information)는 거의 모든 특허에 늘 존재하는 보완 자료라, AI 검토 의견을 강등하는
    # 트리거로 쓰지 않는다. 대신 담당 팀별 확인사항으로 분류해 보고서 하단에 체크리스트로 제공한다.
    review_checklist = build_review_checklist(validated_axes)
    warnings = valuation_warnings(validated_axes)
    required_actions = []
    if business_fit.get("missing_information"):
        required_actions.append("사업부 적용 여부 및 향후 적용 계획 확인")
    # '유지 권고' 임계 평균 점수는 운영 설정(valuationConfig.maintainThreshold, BE 단일 출처)을 따른다.
    # 설정이 없으면 schemas의 기본값(60)으로 resolve된 값이 들어온다.
    recommendation = score_to_final_recommendation(
        average_score,
        maintain_cutoff=maintain_threshold_cutoff(applied_config),
    )
    if business_fit_override:
        recommendation = "유지 권고"
    result = {
        "axes": validated_axes,
        "total_score": total_score,
        "total_score_max": CORE_VALUATION_TOTAL_MAX,
        "average_score": average_score,
        "core_axes": list(core_axes.keys()),
        "business_fit_score": business_fit_score,
        "business_fit_override": business_fit_override,
        "final_grade": final_grade,
        "recommendation": recommendation,
        "decision_rationale": build_decision_rationale(
            core_axes,
            total_score,
            average_score,
            final_grade,
            recommendation,
            business_fit_override=business_fit_override,
            business_fit_score=business_fit_score,
            config=applied_config,
        ),
        "required_actions": unique_texts(required_actions),
        "missing_information": missing_information,
        "review_checklist": review_checklist,
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


# AI 검토 의견(recommendation) 점수 컷오프(평균점 기준):
#   ≥유지임계 유지 권고 · 그 미만이고 ≥조건부임계 조건부 유지 · <조건부임계 포기 검토.
# '유지 권고' 임계(maintain_cutoff)는 운영 설정 valuationConfig.maintainThreshold(BE 단일 출처,
# 기본 60)로 결정된다. 설정 미주입 경로의 폴백 기본값도 schemas의 DEFAULT_MAINTAIN_THRESHOLD에 맞춘다.
MAINTAIN_RECOMMENDATION_CUTOFF = DEFAULT_MAINTAIN_THRESHOLD
CONDITIONAL_RECOMMENDATION_CUTOFF = 50.0


def maintain_threshold_cutoff(config: dict[str, Any] | None) -> float:
    """resolve된 valuationConfig에서 '유지 권고' 임계 평균 점수를 꺼낸다.
    누락/비수치 시 기본값(DEFAULT_MAINTAIN_THRESHOLD)으로 폴백한다."""
    try:
        return float((config or {}).get("maintainThreshold", MAINTAIN_RECOMMENDATION_CUTOFF))
    except (TypeError, ValueError):
        return MAINTAIN_RECOMMENDATION_CUTOFF


def business_fit_override_cutoff(config: dict[str, Any] | None) -> float:
    """resolve된 valuationConfig에서 사업 연계성 오버라이드 기준점을 꺼낸다.
    누락/비수치 시 기본값(BUSINESS_FIT_OVERRIDE_SCORE)으로 폴백한다."""
    try:
        return float((config or {}).get("businessFitOverrideThreshold", BUSINESS_FIT_OVERRIDE_SCORE))
    except (TypeError, ValueError):
        return BUSINESS_FIT_OVERRIDE_SCORE


def score_to_final_recommendation(
    average_score: float,
    *,
    maintain_cutoff: float = MAINTAIN_RECOMMENDATION_CUTOFF,
    conditional_cutoff: float = CONDITIONAL_RECOMMENDATION_CUTOFF,
) -> str:
    if average_score >= maintain_cutoff:
        return "유지 권고"
    if average_score >= conditional_cutoff:
        return "조건부 유지"
    return "포기 검토"


# 부족 정보(missing_information)를 담당 팀별 확인사항으로 분류한다.
# 권리성·기술성은 권리/선행기술 검토라 법무·특허팀, 시장성·사업연계성은 사업부서 몫으로 본다.
REVIEW_TEAM_BY_AXIS = {
    "legal": "법무·특허팀 확인사항",
    "technology": "법무·특허팀 확인사항",
    "market": "사업부서 확인사항",
    "business_fit": "사업부서 확인사항",
}
REVIEW_TEAMS = ("사업부서 확인사항", "법무·특허팀 확인사항")


def build_review_checklist(validated_axes: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {team: [] for team in REVIEW_TEAMS}
    for axis_name, axis_result in validated_axes.items():
        team = REVIEW_TEAM_BY_AXIS.get(axis_name, "사업부서 확인사항")
        for item in axis_result.get("missing_information") or []:
            text = str(item or "").strip()
            if text:
                buckets[team].append(text)
    return {team: unique_texts(items) for team, items in buckets.items() if items}


def build_decision_rationale(
    core_axes: dict[str, dict[str, Any]],
    total_score: int,
    average_score: float,
    final_grade: str,
    recommendation: str,
    *,
    business_fit_override: bool = False,
    business_fit_score: int = 0,
    config: dict[str, Any] | None = None,
) -> list[str]:
    strongest = max(core_axes.values(), key=lambda axis: axis.get("score", 0))
    weakest = min(core_axes.values(), key=lambda axis: axis.get("score", 0))
    if config and not is_default_valuation_config(config):
        weights = config.get("axisWeights") or {}
        weight_text = ", ".join(
            f"{(core_axes.get(axis) or {}).get('label') or axis} {weights.get(axis, 0):g}"
            for axis in CORE_VALUATION_AXES
        )
        score_line = (
            f"권리성·기술성·시장성 3개 평가축 합산 점수는 {total_score}/{CORE_VALUATION_TOTAL_MAX}점, "
            f"가중 평균 점수는 {average_score:g}/100점(가중치 {weight_text}), 종합 등급은 {final_grade}, "
            f"AI 검토 의견은 {recommendation}이다."
        )
    else:
        score_line = (
            f"권리성·기술성·시장성 3개 평가축 합산 점수는 {total_score}/{CORE_VALUATION_TOTAL_MAX}점, "
            f"평균 점수는 {average_score:g}/100점, 종합 등급은 {final_grade}이며 AI 검토 의견은 {recommendation}이다."
        )
    rationale = [
        score_line,
        f"가장 강한 축은 {strongest.get('label')}({strongest.get('score')}점)이다.",
        f"보완이 필요한 축은 {weakest.get('label')}({weakest.get('score')}점)이다.",
    ]
    if business_fit_override:
        rationale.append(
            f"사업 연계성 점수가 {business_fit_score}점으로 기준({int(business_fit_override_cutoff(config))}점) 이상이어서, "
            f"3축 점수와 무관하게 AI 검토 의견을 유지 권고로 보정했다."
        )
    return rationale


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
