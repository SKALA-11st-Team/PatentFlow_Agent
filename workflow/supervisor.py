from __future__ import annotations

import json
from typing import Any

from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.observability.langsmith_service import trace
from services.llm.prompt_service import load_prompt
from schemas.supervisor import SupervisorDecision
from workflow.state import PatentWorkflowState


STAGE_PROMPTS = {
    "patent_check": "supervisor/supervisor_patent_check.md",
    "summary_check": "supervisor/supervisor_summary_check.md",
    "evidence_check": "supervisor/supervisor_evidence_check.md",
    "valuation_check": "supervisor/supervisor_valuation_check.md",
    "final_check": "supervisor/supervisor_final_check.md",
}

VALUATION_SUPERVISOR_RETRY_LIMIT = 2
WRITING_SUPERVISOR_RETRY_LIMIT = 1


def decide_next_step(state: PatentWorkflowState) -> str:
    if state.supervisor_decision:
        return state.supervisor_decision.get("next_action", "validation")
    if state.validation_result and state.validation_result.get("passed"):
        return "final_merge"
    if state.validation_result and state.validation_result.get("needs_more_evidence"):
        return "query_rewriting"
    if state.valuation_result is None:
        return "valuation"
    return "validation"


@trace(name="supervisor_agent", run_type="chain")
def supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    stage = state.current_stage
    if stage in {"patent_check", "summary_check", "evidence_check"}:
        return _with_legacy_research_action(research_supervisor_node(state))
    if stage == "valuation_check":
        return _with_legacy_valuation_action(valuation_supervisor_node(state))
    if stage == "final_check":
        return writing_supervisor_node(state)
    return top_supervisor_node(state)


def _with_legacy_research_action(state: PatentWorkflowState) -> PatentWorkflowState:
    decision = state.supervisor_decision or {}
    if decision.get("next_team") == "valuation" and decision.get("next_action") == "valuation_team":
        decision["next_action"] = "valuation"
        state.supervisor_decision = decision
    return state


def _with_legacy_valuation_action(state: PatentWorkflowState) -> PatentWorkflowState:
    decision = state.supervisor_decision or {}
    legacy_actions = {
        ("writing", "writing_team"): "validation",
        ("valuation", "valuation_team"): "valuation_retry",
        ("research", "research_team"): "query_rewriting",
    }
    legacy_action = legacy_actions.get((decision.get("next_team"), decision.get("next_action")))
    if legacy_action:
        decision["next_action"] = legacy_action
        state.supervisor_decision = decision
    return state


@trace(name="top_supervisor_agent", run_type="chain")
def top_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "top"
    previous = state.supervisor_decision or {}
    requested_team = previous.get("next_team")
    requested_action = previous.get("next_action")

    if requested_team in {"research", "valuation", "writing", "final"}:
        next_team = requested_team
        next_action = requested_action or f"{next_team}_team"
    elif not state.patent_structured or not state.preprocessed_patent or not state.summary_result:
        next_team = "research"
        next_action = "research_team"
    elif not state.valuation_result:
        next_team = "valuation"
        next_action = "valuation_team"
    elif not state.final_report:
        next_team = "writing"
        next_action = "writing_team"
    else:
        next_team = "final"
        next_action = "final_merge"

    state.supervisor_decision = SupervisorDecision(
        passed=True,
        current_team="top",
        next_team=next_team,
        next_action=next_action,
        route_reason=f"Top supervisor routed workflow to {next_team}.",
    ).model_dump()
    return state


@trace(name="research_supervisor_agent", run_type="chain")
def research_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "research"
    for stage, checker in [
        ("patent_check", check_patent_data),
        ("summary_check", check_summary_result),
        ("evidence_check", check_evidence_bundle),
    ]:
        decision = checker(state)
        if not decision.passed:
            decision.current_team = "research"
            decision.next_team = "research"
            decision.stage = stage
            state.current_stage = stage
            state.supervisor_decision = decision.model_dump()
            state.missing_evidence = decision.missing_evidence
            return state

    decision = SupervisorDecision(
        passed=True,
        current_team="research",
        next_team="valuation",
        stage="evidence_check",
        next_action="valuation_team",
        reason="Research outputs are ready for valuation.",
    )
    decision = run_llm_supervisor_check(
        state,
        decision,
        prompt_name="supervisor/supervisor_evidence_check.md",
        allowed_next_actions={"valuation", "query_rewriting", "industry_rag_query"},
        team_action_map={
            "valuation": ("valuation", "valuation_team"),
            "query_rewriting": ("research", "query_rewriting"),
            "industry_rag_query": ("research", "query_rewriting"),
        },
    )
    state.supervisor_decision = decision.model_dump()
    state.missing_evidence = decision.missing_evidence
    return state


@trace(name="valuation_supervisor_agent", run_type="chain")
def valuation_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "valuation"
    decision = check_valuation_result(state)
    has_unknown_evidence = any("references unknown evidence_id" in issue for issue in decision.issues)
    decision.current_team = "valuation"
    decision.stage = "valuation_check"
    if decision.passed:
        decision.next_team = "writing"
        decision.next_action = "writing_team"
    elif has_unknown_evidence:
        decision.next_team = "research"
        decision.next_action = "research_team"
    else:
        decision.next_team = "valuation"
        decision.next_action = "valuation_team"
    decision = run_llm_supervisor_check(
        state,
        decision,
        prompt_name="supervisor/supervisor_valuation_check.md",
        allowed_next_actions={"validation", "query_rewriting", "valuation_retry"},
        team_action_map={
            "validation": ("writing", "writing_team"),
            "query_rewriting": ("research", "research_team"),
            "valuation_retry": ("valuation", "valuation_team"),
        },
    )
    decision = apply_supervisor_retry_limit(
        state,
        decision,
        scope="valuation",
        retry_action="valuation_team",
        retry_limit=VALUATION_SUPERVISOR_RETRY_LIMIT,
        fallback_team="writing",
        fallback_action="writing_team",
        fallback_reason="Valuation supervisor retry limit reached; continuing with structurally valid valuation result.",
        allow_fallback=check_valuation_result(state).passed,
    )
    state.supervisor_decision = decision.model_dump()
    return state


def check_writing_result(state: PatentWorkflowState) -> SupervisorDecision:
    issues = []
    summary_markdown = (state.summary_result or {}).get("summary_markdown")
    final_report_markdown = (state.valuation_result or {}).get("final_report_markdown")
    if not summary_markdown:
        issues.append("Missing summary_markdown")
    if not final_report_markdown:
        issues.append("Missing final_report_markdown")
    return SupervisorDecision(
        passed=not issues,
        current_team="writing",
        next_team="final" if not issues else "writing",
        stage="writing_check",
        next_action="final_merge" if not issues else "writing_team",
        issues=issues,
        reason="Writing output check completed.",
    )


@trace(name="writing_supervisor_agent", run_type="chain")
def writing_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "writing"
    decision = check_writing_result(state)
    decision = run_llm_supervisor_check(
        state,
        decision,
        prompt_name="supervisor/supervisor_final_check.md",
        allowed_next_actions={"final_merge", "supervisor"},
        team_action_map={
            "final_merge": ("final", "final_merge"),
            "supervisor": ("writing", "writing_team"),
        },
    )
    decision = apply_supervisor_retry_limit(
        state,
        decision,
        scope="writing",
        retry_action="writing_team",
        retry_limit=WRITING_SUPERVISOR_RETRY_LIMIT,
        fallback_team="final",
        fallback_action="final_merge",
        fallback_reason="Writing supervisor retry limit reached; final report markdown is structurally present.",
        allow_fallback=check_writing_result(state).passed,
    )
    state.supervisor_decision = decision.model_dump()
    return state


def apply_supervisor_retry_limit(
    state: PatentWorkflowState,
    decision: SupervisorDecision,
    *,
    scope: str,
    retry_action: str,
    retry_limit: int,
    fallback_team: str,
    fallback_action: str,
    fallback_reason: str,
    allow_fallback: bool,
) -> SupervisorDecision:
    if decision.next_action != retry_action:
        reset_supervisor_retry_count(state, scope)
        return decision

    retry_count = increment_supervisor_retry_count(state, scope)
    if retry_count <= retry_limit or not allow_fallback:
        return decision

    metadata = dict(decision.metadata)
    metadata["supervisor_retry_limit"] = {
        "scope": scope,
        "retry_count": retry_count,
        "retry_limit": retry_limit,
        "fallback_action": fallback_action,
    }
    return SupervisorDecision(
        passed=True,
        current_team=decision.current_team,
        next_team=fallback_team,
        stage=decision.stage,
        next_action=fallback_action,
        issues=decision.issues,
        missing_evidence=decision.missing_evidence,
        reason=fallback_reason,
        route_reason=fallback_reason,
        metadata=metadata,
    )


def increment_supervisor_retry_count(state: PatentWorkflowState, scope: str) -> int:
    team_status = dict(state.team_status or {})
    retry_counts = dict(team_status.get("supervisor_retry_counts") or {})
    retry_counts[scope] = int(retry_counts.get(scope) or 0) + 1
    team_status["supervisor_retry_counts"] = retry_counts
    state.team_status = team_status
    return retry_counts[scope]


def reset_supervisor_retry_count(state: PatentWorkflowState, scope: str) -> None:
    team_status = dict(state.team_status or {})
    retry_counts = dict(team_status.get("supervisor_retry_counts") or {})
    if scope in retry_counts:
        retry_counts.pop(scope)
        team_status["supervisor_retry_counts"] = retry_counts
        state.team_status = team_status


def run_llm_supervisor_check(
    state: PatentWorkflowState,
    rule_decision: SupervisorDecision,
    *,
    prompt_name: str,
    allowed_next_actions: set[str],
    team_action_map: dict[str, tuple[str, str]],
) -> SupervisorDecision:
    if not state.user_input.get("use_llm_supervisor", False):
        return rule_decision

    try:
        raw = call_llm(build_supervisor_judge_prompt(state, prompt_name=prompt_name))
        parsed = parse_json_object(raw)
        if not parsed:
            raise ValueError("LLM supervisor response was not valid JSON.")
        return normalize_llm_supervisor_decision(
            parsed,
            fallback=rule_decision,
            allowed_next_actions=allowed_next_actions,
            team_action_map=team_action_map,
        )
    except Exception as exc:
        metadata = dict(rule_decision.metadata)
        metadata["supervisor_llm_warning"] = f"{exc.__class__.__name__}:{str(exc)[:200]}"
        rule_decision.metadata = metadata
        return rule_decision


def build_supervisor_judge_prompt(state: PatentWorkflowState, *, prompt_name: str) -> str:
    template = load_prompt(prompt_name).strip()
    payload = supervisor_payload(state)
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def supervisor_payload(state: PatentWorkflowState) -> dict[str, Any]:
    return {
        "user_input": state.user_input,
        "current_stage": state.current_stage,
        "current_team": state.current_team,
        "patent_structured": state.patent_structured,
        "preprocessed_patent": state.preprocessed_patent,
        "summary_result": state.summary_result,
        "query_plan": state.query_plan,
        "evidence_bundle": state.evidence_bundle,
        "valuation_result": state.valuation_result,
        "validation_result": state.validation_result,
        "missing_evidence": state.missing_evidence,
        "retry_count": state.retry_count,
    }


def normalize_llm_supervisor_decision(
    parsed: dict[str, Any],
    *,
    fallback: SupervisorDecision,
    allowed_next_actions: set[str],
    team_action_map: dict[str, tuple[str, str]],
) -> SupervisorDecision:
    requested_action = str(parsed.get("next_action") or fallback.next_action)
    if requested_action not in allowed_next_actions:
        requested_action = fallback.next_action
    next_team, next_action = team_action_map.get(
        requested_action,
        (fallback.next_team or fallback.current_team, fallback.next_action),
    )
    return SupervisorDecision(
        passed=normalize_bool(parsed.get("passed"), fallback.passed),
        next_action=next_action,
        current_team=fallback.current_team,
        next_team=next_team,
        stage=fallback.stage,
        route_reason=fallback.route_reason,
        issues=normalize_text_list(parsed.get("issues"), fallback.issues),
        missing_evidence=normalize_text_list(parsed.get("missing_evidence"), fallback.missing_evidence),
        reason=str(parsed.get("reason") or fallback.reason),
        metadata={**fallback.metadata, "supervisor_llm": {"prompt": True, "requested_action": requested_action}},
    )


def normalize_text_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    return [str(item) for item in value if str(item).strip()]


def normalize_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return fallback


def run_rule_based_check(state: PatentWorkflowState, stage: str) -> SupervisorDecision:
    if stage == "patent_check":
        return check_patent_data(state)
    if stage == "summary_check":
        return check_summary_result(state)
    if stage == "evidence_check":
        return check_evidence_bundle(state)
    if stage == "valuation_check":
        return check_valuation_result(state)
    return check_final_ready(state)


def check_patent_data(state: PatentWorkflowState) -> SupervisorDecision:
    patent = state.patent_structured or {}
    required_fields = [
        "id",
        "application_number",
        "registration_number",
        "title_final",
        "status",
        "application_date",
        "registration_date",
    ]
    missing = [field for field in required_fields if not patent.get(field)]
    preprocessed = state.preprocessed_patent
    issues = [f"Missing patent field: {field}" for field in missing]
    if preprocessed:
        validation = preprocessed.get("validation", {})
        warnings = list(validation.get("missing_fields", [])) + list(validation.get("warnings", []))
    else:
        warnings = []
    return SupervisorDecision(
        passed=not issues,
        next_action="summary" if preprocessed and not missing else "common_preprocess" if not missing else "patent_fetch",
        issues=issues,
        reason="Patent metadata check completed.",
        metadata={"warnings": warnings},
    )


def check_summary_result(state: PatentWorkflowState) -> SupervisorDecision:
    summary = state.summary_result or {}
    required_fields = ["title", "plain_summary", "key_points"]
    missing = [field for field in required_fields if not summary.get(field)]
    return SupervisorDecision(
        passed=not missing,
        next_action="evidence_check" if not missing else "summary",
        issues=[f"Missing summary field: {field}" for field in missing],
        reason="Summary structure check completed.",
    )


def check_evidence_bundle(state: PatentWorkflowState) -> SupervisorDecision:
    evidence_bundle = state.evidence_bundle
    issues: list[str] = []
    missing_evidence: list[str] = []
    if len(evidence_bundle) < 3:
        missing_evidence.append("minimum_evidence_count")
    news_count = sum(1 for evidence in evidence_bundle if evidence.get("source_type") == "news")
    if news_count < 3:
        missing_evidence.append("minimum_news_count")
    required_fields = ["evidence_id", "source"]
    for index, evidence in enumerate(evidence_bundle):
        for field in required_fields:
            if not evidence.get(field):
                issues.append(f"Evidence #{index + 1} missing {field}")
        if not evidence.get("content") and not evidence.get("context") and not evidence.get("compressed_summary"):
            issues.append(f"Evidence #{index + 1} missing content/context/compressed_summary")

    passed = not issues and not missing_evidence
    return SupervisorDecision(
        passed=passed,
        next_action="valuation" if passed else "query_rewriting",
        issues=issues,
        missing_evidence=missing_evidence,
        reason="Evidence bundle structure check completed.",
    )


def check_valuation_result(state: PatentWorkflowState) -> SupervisorDecision:
    valuation = state.valuation_result or {}
    evidence_ids = {evidence.get("evidence_id") for evidence in state.evidence_bundle}
    axes = valuation.get("axes") or {}
    required_axes = ["legal", "technology", "market", "business_fit"]
    issues: list[str] = []

    for axis in required_axes:
        axis_result = axes.get(axis)
        if not axis_result:
            issues.append(f"Missing valuation axis: {axis}")
            continue
        for field in ["score", "grade", "rationale", "evidence_ids", "risk_factors", "confidence"]:
            if field == "evidence_ids":
                continue
            if axis_result.get(field) in (None, "", []):
                issues.append(f"{axis} missing {field}")
        for evidence_id in axis_result.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                issues.append(f"{axis} references unknown evidence_id: {evidence_id}")
    if "strategy" in axes:
        issues.append("Deprecated valuation axis present: strategy")

    passed = not issues
    return SupervisorDecision(
        passed=passed,
        next_action="validation" if passed else "valuation_retry",
        issues=issues,
        reason="Valuation structure and evidence-id check completed.",
    )


def check_final_ready(state: PatentWorkflowState) -> SupervisorDecision:
    issues = []
    if not state.summary_result:
        issues.append("Missing summary_result")
    if not state.valuation_result:
        issues.append("Missing valuation_result")
    if not state.validation_result or not state.validation_result.get("passed"):
        issues.append("Validation has not passed")
    return SupervisorDecision(
        passed=not issues,
        next_action="final_merge" if not issues else "supervisor",
        issues=issues,
        reason="Final readiness check completed.",
    )
