from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.observability.langsmith_service import trace
from services.llm.prompt_service import load_prompt
from schemas.supervisor import SupervisorDecision
from workflow.state import PatentWorkflowState


VALUATION_SUPERVISOR_RETRY_LIMIT = 1
AXIS_SUPERVISOR_RETRY_LIMIT = 1
WRITING_SUPERVISOR_RETRY_LIMIT = 1
REQUIRED_VALUATION_AXES = ["legal", "technology", "market", "business_fit"]
MAX_SUPERVISOR_EVIDENCE_SAMPLES = 5
AXIS_SUPERVISOR_PROMPTS = {
    "legal": "supervisor/supervisor_legal_check.md",
    "technology": "supervisor/supervisor_technology_check.md",
    "market": "supervisor/supervisor_market_check.md",
    "business_fit": "supervisor/supervisor_business_fit_check.md",
}
AXIS_SUPERVISOR_STATUSES = {"passed", "valuation_retry", "query_rewriting"}
# Only these axes consume external search evidence (news, industry RAG, SK AX
# official). Patent-intrinsic axes (legal/technology) get their inputs from the
# patent collection phase (KIPRIS claims/prior-art/CPC), so re-running the
# evidence search loop cannot help them — they may only request valuation_retry.
EXTERNAL_EVIDENCE_AXES = {"market", "business_fit"}


def coerce_axis_status(axis: str, status: str) -> str:
    if status == "query_rewriting" and axis not in EXTERNAL_EVIDENCE_AXES:
        return "valuation_retry"
    return status


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
    if stage in {"patent_check", "evidence_check"}:
        return _with_legacy_research_action(research_supervisor_node(state))
    if stage == "summary_check":
        return writing_supervisor_node(state)
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
    elif not state.patent_structured or not state.preprocessed_patent:
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
    axis_checks = run_axis_supervisor_checks(state)
    decision = check_valuation_result(state)
    has_unknown_evidence = any("references unknown evidence_id" in issue for issue in decision.issues)
    decision.current_team = "valuation"
    decision.stage = "valuation_check"
    if decision.passed:
        axis_decision = axis_checks_to_supervisor_decision(axis_checks)
        decision.passed = axis_decision.passed
        decision.issues = axis_decision.issues
        decision.reason = axis_decision.reason
        decision.next_team = axis_decision.next_team
        decision.next_action = axis_decision.next_action
    elif has_unknown_evidence:
        decision.next_team = "research"
        decision.next_action = "query_rewriting"
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
            "query_rewriting": ("research", "query_rewriting"),
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
    decision = apply_axis_routing_targets(state, decision, axis_checks)
    state.supervisor_decision = decision.model_dump()
    return state


def axis_retry_targets(axis_checks: dict[str, dict[str, Any]]) -> list[str]:
    return [
        axis
        for axis, check in axis_checks.items()
        if check.get("status") == "valuation_retry"
    ]


def aggregate_axis_evidence_gaps(axis_checks: dict[str, dict[str, Any]]) -> list[str]:
    gaps: list[str] = []
    for check in axis_checks.values():
        if check.get("status") != "query_rewriting":
            continue
        for issue in check.get("issues", []):
            text = str(issue).strip()
            if text and text not in gaps:
                gaps.append(text)
    return gaps


def increment_axis_retry_count(state: PatentWorkflowState, axis: str) -> int:
    team_status = dict(state.team_status or {})
    counts = dict(team_status.get("axis_retry_counts") or {})
    counts[axis] = int(counts.get(axis) or 0) + 1
    team_status["axis_retry_counts"] = counts
    state.team_status = team_status
    return counts[axis]


def reset_axis_retry_counts(state: PatentWorkflowState) -> None:
    if not state.team_status or "axis_retry_counts" not in state.team_status:
        return
    team_status = dict(state.team_status)
    team_status.pop("axis_retry_counts")
    state.team_status = team_status


def merge_axis_evidence_gaps(state: PatentWorkflowState, axis_checks: dict[str, dict[str, Any]]) -> None:
    gaps = aggregate_axis_evidence_gaps(axis_checks)
    if not gaps:
        return
    merged = list(state.missing_evidence or [])
    for gap in gaps:
        if gap not in merged:
            merged.append(gap)
    state.missing_evidence = merged


def apply_axis_routing_targets(
    state: PatentWorkflowState,
    decision: SupervisorDecision,
    axis_checks: dict[str, dict[str, Any]],
) -> SupervisorDecision:
    """Translate per-axis verdicts into graph routing targets.

    - valuation_team(retry) -> re-run only the axes flagged valuation_retry,
      each bounded by a per-axis retry budget. Axes that exhaust their budget
      are dropped; if every flagged axis is exhausted, accept the current
      structurally-valid result and continue to writing.
      An empty target list lets the graph fall back to a full re-evaluation,
      bounded by the global valuation retry limit.
    - query_rewriting -> aggregate the evidence gaps of the axes that need more
      evidence into a single missing_evidence list (one shared re-search), and
      mark only the external-evidence axes for re-evaluation. The re-search only
      refreshes news / industry RAG / SK AX evidence, which is consumed solely
      by market and business_fit; legal/technology inputs come from patent
      collection and are unchanged, so their prior results are reused.
    """
    if decision.next_action == "query_rewriting":
        reset_axis_retry_counts(state)
        state.valuation_retry_axes = sorted(EXTERNAL_EVIDENCE_AXES)
        merge_axis_evidence_gaps(state, axis_checks)
        return decision
    if decision.next_action != "valuation_team":
        reset_axis_retry_counts(state)
        state.valuation_retry_axes = []
        return decision

    candidates = axis_retry_targets(axis_checks)
    if not candidates:
        # Retry direction without a specific axis -> full re-evaluation,
        # governed by the global valuation retry limit.
        state.valuation_retry_axes = []
        return decision

    eligible: list[str] = []
    exhausted: list[str] = []
    for axis in candidates:
        if increment_axis_retry_count(state, axis) <= AXIS_SUPERVISOR_RETRY_LIMIT:
            eligible.append(axis)
        else:
            exhausted.append(axis)

    if eligible:
        state.valuation_retry_axes = eligible
        return decision

    # Every flagged axis exhausted its per-axis retry budget.
    state.valuation_retry_axes = []
    if not check_valuation_result(state).passed:
        return decision
    reset_axis_retry_counts(state)
    metadata = dict(decision.metadata)
    metadata["axis_retry_budget"] = {
        "exhausted_axes": exhausted,
        "retry_limit": AXIS_SUPERVISOR_RETRY_LIMIT,
        "fallback_action": "writing_team",
    }
    return SupervisorDecision(
        passed=True,
        current_team=decision.current_team,
        next_team="writing",
        stage=decision.stage,
        next_action="writing_team",
        issues=decision.issues,
        missing_evidence=decision.missing_evidence,
        reason="축별 재평가 예산을 모두 사용해 현재 평가 결과로 진행합니다.",
        route_reason="축별 재평가 예산 소진",
        metadata=metadata,
    )


def run_axis_supervisor_checks(state: PatentWorkflowState) -> dict[str, dict[str, Any]]:
    axes = list(REQUIRED_VALUATION_AXES)
    prior = (state.valuation_result or {}).get("axis_supervisor_checks") or {}
    retry_axes = [axis for axis in (state.valuation_retry_axes or []) if axis in axes]
    reuse_axes = [axis for axis in axes if axis not in retry_axes]
    # Selective re-check: after a selective re-evaluation only the re-evaluated
    # axes changed, so reuse the prior checks for the untouched axes and only
    # re-check the ones that were re-run.
    if retry_axes and all(axis in prior for axis in reuse_axes):
        to_check = retry_axes
        checks = {axis: prior[axis] for axis in reuse_axes}
    else:
        to_check = axes
        checks = {}
    # The per-axis checks are independent LLM calls and only read from state, so
    # run them concurrently. Rule-only checks (no LLM) are instant, so only pay
    # for threads when the LLM judge is enabled.
    if state.user_input.get("use_llm_supervisor", False) and len(to_check) > 1:
        checks.update(run_axis_supervisor_checks_parallel(state, to_check))
    else:
        checks.update({axis: run_single_axis_supervisor_check(state, axis) for axis in to_check})
    ordered = {axis: checks[axis] for axis in axes}
    valuation = dict(state.valuation_result or {})
    valuation["axis_supervisor_checks"] = ordered
    state.valuation_result = valuation
    return ordered


def run_axis_supervisor_checks_parallel(
    state: PatentWorkflowState, axes: list[str]
) -> dict[str, dict[str, Any]]:
    futures = {}
    with ThreadPoolExecutor(max_workers=len(axes)) as executor:
        for axis in axes:
            # Copy the current context so the langsmith run tree propagates into
            # the worker thread and each LLM call still nests under this node.
            ctx = contextvars.copy_context()
            futures[axis] = executor.submit(ctx.run, run_single_axis_supervisor_check, state, axis)
    return {axis: futures[axis].result() for axis in axes}


def run_single_axis_supervisor_check(state: PatentWorkflowState, axis: str) -> dict[str, Any]:
    fallback = build_rule_axis_supervisor_check(state, axis)
    if not state.user_input.get("use_llm_supervisor", False):
        return fallback

    try:
        raw = call_llm(
            build_axis_supervisor_check_prompt(state, axis=axis),
            model=settings.openai_supervisor_model,
        )
        parsed = parse_json_object(raw)
        if not parsed:
            raise ValueError("Axis supervisor response was not valid JSON.")
        return normalize_axis_supervisor_check(axis, parsed, fallback=fallback)
    except Exception as exc:
        return {
            **fallback,
            "metadata": {
                **dict(fallback.get("metadata") or {}),
                "supervisor_llm_warning": f"{exc.__class__.__name__}:{str(exc)[:200]}",
            },
        }


def build_axis_supervisor_check_prompt(state: PatentWorkflowState, *, axis: str) -> str:
    prompt_name = AXIS_SUPERVISOR_PROMPTS[axis]
    template = load_prompt(prompt_name).strip()
    payload = axis_supervisor_payload(state, axis=axis)
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def axis_supervisor_payload(state: PatentWorkflowState, *, axis: str) -> dict[str, Any]:
    valuation = state.valuation_result or {}
    axes = valuation.get("axes") or {}
    axis_result = axes.get(axis) or {}
    known_ids = known_evidence_ids(state.evidence_bundle)
    cited_ids = [str(item) for item in axis_result.get("evidence_ids", []) if str(item).strip()]
    payload = {
        "current_stage": state.current_stage,
        "axis": axis,
        "patent": patent_metadata_payload(state),
        "evidence": evidence_summary_payload(state, include_samples=True, priority_evidence_ids=cited_ids),
        "valuation_axis": valuation_axis_payload(axis, axis_result, known_ids),
        "axis_result": compact_axis_result_for_supervisor(axis_result),
    }
    if axis == "legal":
        # Prior art lives in the patent record (citation_evidence), not the
        # evidence_bundle. Surface what the evaluation cited vs what the input
        # actually provided so the supervisor can verify grounding instead of
        # flagging it as "missing from known_evidence_ids".
        payload["prior_art_context"] = {
            "cited_in_evaluation": [
                str(item) for item in axis_result.get("prior_art_references", []) if str(item).strip()
            ],
            "available_in_input": available_prior_art_identifiers(state),
        }
    return payload


def available_prior_art_identifiers(state: PatentWorkflowState) -> list[str]:
    identifiers: list[str] = []
    citation = state.citation_evidence or {}
    for key in ("kr_citation_documents", "foreign_citation_documents"):
        for doc in citation.get(key) or []:
            if not isinstance(doc, dict):
                continue
            for field in (
                "registration_number",
                "application_number",
                "publication_number",
                "document_number",
                "display_number",
            ):
                value = str(doc.get(field) or "").strip()
                if value:
                    identifiers.append(value)
    for source in (
        (state.preprocessed_patent or {}).get("metadata") or {},
        (state.kipris_api_data or {}).get("metadata") or {},
        state.patent_structured or {},
    ):
        prior_art = source.get("prior_art") or []
        if isinstance(prior_art, str):
            prior_art = [prior_art]
        for value in prior_art:
            text = str(value or "").strip()
            if text:
                identifiers.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in identifiers:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def compact_axis_result_for_supervisor(axis_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "axis": axis_result.get("axis"),
        "label": axis_result.get("label"),
        "score": axis_result.get("score"),
        "grade": axis_result.get("grade"),
        "rationale_preview": preview_text(axis_result.get("rationale"), 600),
        "evidence_ids": limit_list(axis_result.get("evidence_ids"), 20),
        "risk_factors": limit_list(axis_result.get("risk_factors"), 10),
        "missing_information": limit_list(axis_result.get("missing_information"), 10),
        "confidence": axis_result.get("confidence"),
        "subscores": supervisor_subscores_payload(axis_result.get("subscores")),
    }


def supervisor_subscores_payload(subscores: Any) -> dict[str, Any]:
    if not isinstance(subscores, dict):
        return {}
    payload: dict[str, Any] = {}
    for key, item in subscores.items():
        if not isinstance(item, dict):
            continue
        payload[str(key)] = {
            "label": item.get("label"),
            "score": item.get("score"),
            "max_score": item.get("max_score"),
            "details": item.get("details") if isinstance(item.get("details"), dict) else {},
            "rationale_preview": preview_text(item.get("rationale"), 300),
        }
    return payload


def build_rule_axis_supervisor_check(state: PatentWorkflowState, axis: str) -> dict[str, Any]:
    valuation = state.valuation_result or {}
    axis_result = (valuation.get("axes") or {}).get(axis) or {}
    known_ids = known_evidence_ids(state.evidence_bundle)
    axis_payload = valuation_axis_payload(axis, axis_result, known_ids)
    issues: list[str] = []
    status = "passed"

    if not axis_payload["exists"]:
        status = "valuation_retry"
        issues.append(f"Missing valuation axis: {axis}")
    elif axis_payload["unknown_evidence_ids"]:
        status = "query_rewriting"
        issues.append(f"{axis} references unknown evidence_id: {', '.join(axis_payload['unknown_evidence_ids'])}")
    else:
        for field in ("score", "grade", "confidence"):
            if axis_result.get(field) in (None, "", []):
                status = "valuation_retry"
                issues.append(f"{axis} missing {field}")
        if not axis_payload["has_rationale"]:
            status = "valuation_retry"
            issues.append(f"{axis} missing rationale")

    return axis_supervisor_check_result(
        axis=axis,
        status=status,
        issues=issues,
        reason="축별 평가 구조와 근거 연결을 rule 기반으로 확인했습니다.",
        source="rule",
    )


def normalize_axis_supervisor_check(axis: str, parsed: dict[str, Any], *, fallback: dict[str, Any]) -> dict[str, Any]:
    status = normalize_axis_supervisor_status(parsed, fallback.get("status", "passed"))
    issues = normalize_text_list(parsed.get("issues"), list(fallback.get("issues") or []))
    reason = str(parsed.get("reason") or fallback.get("reason") or "")
    return axis_supervisor_check_result(
        axis=axis,
        status=status,
        issues=issues,
        reason=reason,
        source="llm",
        metadata={"supervisor_llm": {"prompt": True}},
    )


def normalize_axis_supervisor_status(parsed: dict[str, Any], fallback: str) -> str:
    raw_status = str(parsed.get("status") or "").strip()
    if raw_status in AXIS_SUPERVISOR_STATUSES:
        return raw_status
    requested_action = str(parsed.get("next_action") or "").strip()
    if requested_action in {"valuation_retry", "query_rewriting"}:
        return requested_action
    if normalize_bool(parsed.get("passed"), fallback == "passed"):
        return "passed"
    return fallback if fallback in AXIS_SUPERVISOR_STATUSES else "passed"


def axis_supervisor_check_result(
    *,
    axis: str,
    status: str,
    issues: list[str],
    reason: str,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = coerce_axis_status(axis, status)
    return {
        "axis": axis,
        "status": status,
        "passed": status == "passed",
        "needs_retry": status == "valuation_retry",
        "needs_more_evidence": status == "query_rewriting",
        "issues": issues,
        "reason": reason,
        "source": source,
        "metadata": metadata or {},
    }


def axis_checks_to_supervisor_decision(axis_checks: dict[str, dict[str, Any]]) -> SupervisorDecision:
    issues = [
        f"{axis}: {issue}"
        for axis, check in axis_checks.items()
        for issue in check.get("issues", [])
    ]
    if any(check.get("status") == "query_rewriting" for check in axis_checks.values()):
        return SupervisorDecision(
            passed=False,
            current_team="valuation",
            next_team="research",
            stage="valuation_check",
            next_action="query_rewriting",
            issues=issues,
            reason="축별 평가 검증에서 근거 재수집이 필요한 항목이 확인되었습니다.",
        )
    if any(check.get("status") == "valuation_retry" for check in axis_checks.values()):
        return SupervisorDecision(
            passed=False,
            current_team="valuation",
            next_team="valuation",
            stage="valuation_check",
            next_action="valuation_team",
            issues=issues,
            reason="축별 평가 검증에서 평가 논리 재작성 또는 재평가가 필요한 항목이 확인되었습니다.",
        )
    return SupervisorDecision(
        passed=True,
        current_team="valuation",
        next_team="writing",
        stage="valuation_check",
        next_action="writing_team",
        issues=issues,
        reason="축별 평가 검증을 통과했습니다.",
    )


def check_writing_result(state: PatentWorkflowState) -> SupervisorDecision:
    issues = []
    summary_markdown = (state.summary_result or {}).get("summary_markdown")
    final_report_markdown = (state.valuation_result or {}).get("final_report_markdown")
    summary_validation = state.summary_validation_result or {}
    report_validation = state.report_validation_result or {}
    summary_failed = False
    report_failed = False
    if not summary_markdown:
        issues.append("Missing summary_markdown")
        summary_failed = True
    if state.summary_validation_result and not summary_validation.get("passed"):
        issues.extend(summary_validation.get("issues") or ["Summary validation has not passed"])
        summary_failed = True
    if not final_report_markdown:
        issues.append("Missing final_report_markdown")
        report_failed = True
    if state.report_validation_result and not report_validation.get("passed"):
        issues.extend(report_validation.get("issues") or ["Report validation has not passed"])
        report_failed = True
    if summary_failed and report_failed:
        next_action = "writing_team"
    elif summary_failed:
        next_action = "summary"
    elif report_failed:
        next_action = "final_report"
    else:
        next_action = "final_merge"
    return SupervisorDecision(
        passed=not issues,
        current_team="writing",
        next_team="final" if not issues else "writing",
        stage="writing_check",
        next_action=next_action,
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
        allowed_next_actions={"final_merge", "summary", "final_report", "writing_team"},
        team_action_map={
            "final_merge": ("final", "final_merge"),
            "summary": ("writing", "summary"),
            "final_report": ("writing", "final_report"),
            "writing_team": ("writing", "writing_team"),
        },
    )
    decision = apply_supervisor_retry_limit(
        state,
        decision,
        scope="writing",
        retry_action={"summary", "final_report", "writing_team"},
        retry_limit=WRITING_SUPERVISOR_RETRY_LIMIT,
        fallback_team="final",
        fallback_action="final_merge",
        fallback_reason="Writing supervisor retry limit reached; final report markdown is structurally present.",
        allow_fallback=check_writing_result(state).passed,
    )
    state.validation_result = {
        "passed": decision.passed,
        "issues": decision.issues,
        "summary": state.summary_validation_result,
        "report": state.report_validation_result,
    }
    state.supervisor_decision = decision.model_dump()
    return state


def apply_supervisor_retry_limit(
    state: PatentWorkflowState,
    decision: SupervisorDecision,
    *,
    scope: str,
    retry_action: str | set[str],
    retry_limit: int,
    fallback_team: str,
    fallback_action: str,
    fallback_reason: str,
    allow_fallback: bool,
) -> SupervisorDecision:
    retry_actions = retry_action if isinstance(retry_action, set) else {retry_action}
    if decision.next_action not in retry_actions:
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
        raw = call_llm(
            build_supervisor_judge_prompt(state, prompt_name=prompt_name),
            model=settings.openai_supervisor_model,
        )
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
    payload = supervisor_payload(state, prompt_name=prompt_name)
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def supervisor_payload(state: PatentWorkflowState, *, prompt_name: str) -> dict[str, Any]:
    if "evidence" in prompt_name:
        return evidence_supervisor_payload(state)
    if "valuation" in prompt_name:
        return valuation_supervisor_payload(state)
    if "final" in prompt_name:
        return final_supervisor_payload(state)
    if "patent" in prompt_name:
        return patent_supervisor_payload(state)
    return common_supervisor_payload(state)


def common_supervisor_payload(state: PatentWorkflowState) -> dict[str, Any]:
    return {
        "current_stage": state.current_stage,
        "current_team": state.current_team,
        "patent": patent_metadata_payload(state),
        "has_preprocessed_patent": bool(state.preprocessed_patent),
        "has_summary_result": bool(state.summary_result),
        "evidence": evidence_summary_payload(state, include_samples=False),
        "has_valuation_result": bool(state.valuation_result),
        "has_validation_result": bool(state.validation_result),
        "missing_evidence": state.missing_evidence,
        "retry_count": state.retry_count,
    }


def patent_supervisor_payload(state: PatentWorkflowState) -> dict[str, Any]:
    preprocessed = state.preprocessed_patent or {}
    parsed_pdf = state.parsed_pdf or {}
    selected_pdf = parsed_pdf.get("selected_file") or parsed_pdf.get("selected_path")
    return {
        "current_stage": state.current_stage,
        "patent": patent_metadata_payload(state),
        "kipris": {
            "available": bool(state.kipris_api_data),
            "metadata_available": bool((state.kipris_api_data or {}).get("metadata") or state.kipris_api_data),
            "abstract_available": bool((state.kipris_api_data or {}).get("abstract")),
            "claim_count": safe_len((state.kipris_api_data or {}).get("claims")),
            "family_patent_count": len(state.kipris_family_patents),
            "warnings": normalize_text_list((state.kipris_api_data or {}).get("warnings"), []),
        },
        "pdf": {
            "requested": bool(state.pdf_paths),
            "available": bool(parsed_pdf),
            "selected_type": parsed_pdf.get("selected_type"),
            "selected_file": selected_pdf,
            "markdown_file_count": safe_len(parsed_pdf.get("markdown_files")),
            "warning": parsed_pdf.get("warning"),
        },
        "preprocess_validation": preprocess_validation_payload(preprocessed),
        "retry_count": state.retry_count,
    }


def evidence_supervisor_payload(state: PatentWorkflowState) -> dict[str, Any]:
    return {
        "current_stage": state.current_stage,
        "patent": patent_metadata_payload(state),
        "query_plan": query_plan_payload(state.query_plan or {}),
        "evidence": evidence_summary_payload(state, include_samples=True),
        "missing_evidence": state.missing_evidence,
        "retry_count": state.retry_count,
    }


def valuation_supervisor_payload(state: PatentWorkflowState) -> dict[str, Any]:
    valuation = state.valuation_result or {}
    axes = valuation.get("axes") or {}
    known_ids = known_evidence_ids(state.evidence_bundle)
    deprecated_axes = [axis for axis in axes if axis not in REQUIRED_VALUATION_AXES]
    axis_payload = {
        axis: valuation_axis_payload(axis, axes.get(axis) or {}, known_ids)
        for axis in REQUIRED_VALUATION_AXES
    }
    cited_ids = [
        str(item)
        for axis in REQUIRED_VALUATION_AXES
        for item in (axes.get(axis) or {}).get("evidence_ids", [])
        if str(item).strip()
    ]
    return {
        "current_stage": state.current_stage,
        "patent": patent_metadata_payload(state),
        "evidence": evidence_summary_payload(state, include_samples=True, priority_evidence_ids=cited_ids),
        "valuation": {
            "available": bool(valuation),
            "axis_count": len(axes),
            "required_axes": REQUIRED_VALUATION_AXES,
            "missing_axes": [axis for axis in REQUIRED_VALUATION_AXES if axis not in axes],
            "deprecated_axes": deprecated_axes,
            "axes": axis_payload,
            "total_score": valuation.get("total_score"),
            "expected_total_score": expected_total_score(axis_payload),
            "recommendation": valuation.get("recommendation"),
            "decision_rationale_exists": bool(valuation.get("decision_rationale")),
            "decision_rationale_preview": preview_text(valuation.get("decision_rationale"), 300),
            "axis_supervisor_checks": valuation.get("axis_supervisor_checks") or {},
            "required_actions_count": safe_len(valuation.get("required_actions")),
            "has_final_report_markdown": bool(valuation.get("final_report_markdown")),
            "final_report_markdown_length": text_length(valuation.get("final_report_markdown")),
        },
        "retry_count": state.retry_count,
    }


def final_supervisor_payload(state: PatentWorkflowState) -> dict[str, Any]:
    summary = state.summary_result or {}
    valuation = state.valuation_result or {}
    final_report_markdown = valuation.get("final_report_markdown")
    summary_markdown = summary.get("summary_markdown")
    return {
        "current_stage": state.current_stage,
        "patent": patent_metadata_payload(state),
        "summary": {
            "available": bool(summary),
            "has_summary_markdown": bool(summary_markdown),
            "summary_markdown_length": text_length(summary_markdown),
            "summary_markdown_headings": markdown_headings(summary_markdown),
            "plain_summary_preview": preview_text(summary.get("plain_summary"), 500),
        },
        "valuation": {
            "available": bool(valuation),
            "axis_count": len((valuation.get("axes") or {})),
            "total_score": valuation.get("total_score"),
            "recommendation": valuation.get("recommendation"),
            "has_final_report_markdown": bool(final_report_markdown),
            "final_report_markdown_length": text_length(final_report_markdown),
            "final_report_headings": markdown_headings(final_report_markdown),
        },
        "validation": {
            "available": bool(state.validation_result or state.summary_validation_result or state.report_validation_result),
            "passed": (state.validation_result or {}).get("passed"),
            "issues": limit_list((state.validation_result or {}).get("issues"), 10),
            "summary_passed": (state.summary_validation_result or {}).get("passed"),
            "summary_issues": limit_list((state.summary_validation_result or {}).get("issues"), 10),
            "report_passed": (state.report_validation_result or {}).get("passed"),
            "report_issues": limit_list((state.report_validation_result or {}).get("issues"), 10),
            "report_warnings": limit_list((state.report_validation_result or {}).get("warnings"), 10),
            "missing_evidence": limit_list((state.validation_result or {}).get("missing_evidence"), 10),
        },
        "evidence": evidence_summary_payload(state, include_samples=False),
        "retry_count": state.retry_count,
    }


def patent_metadata_payload(state: PatentWorkflowState) -> dict[str, Any]:
    patent = state.patent_structured or {}
    metadata = (state.preprocessed_patent or {}).get("metadata") or {}
    return {
        "id": patent.get("id"),
        "management_number": patent.get("management_number"),
        "application_number": patent.get("application_number"),
        "registration_number": patent.get("registration_number"),
        "title": patent.get("title") or metadata.get("title"),
        "title_final": patent.get("title_final"),
        "status": patent.get("status"),
        "application_date": patent.get("application_date"),
        "registration_date": patent.get("registration_date"),
        "expected_expiration_date": patent.get("expected_expiration_date"),
        "business_area": patent.get("business_area"),
        "technology_area": patent.get("technology_area"),
        "related_product": patent.get("related_product"),
    }


def preprocess_validation_payload(preprocessed: dict[str, Any]) -> dict[str, Any]:
    validation = preprocessed.get("validation") or {}
    return {
        "available": bool(preprocessed),
        "is_valid": validation.get("is_valid"),
        "missing_fields": limit_list(validation.get("missing_fields"), 20),
        "warnings": limit_list(validation.get("warnings"), 20),
    }


def query_plan_payload(query_plan: dict[str, Any]) -> dict[str, Any]:
    search_queries = query_plan.get("search_queries") or {}
    industry_rag = query_plan.get("industry_rag") or {}
    compressed = query_plan.get("compressed_evidence") or {}
    news_filter = query_plan.get("news_filter") or {}
    ko_queries = search_queries.get("ko") or search_queries.get("korean") or []
    en_queries = search_queries.get("en") or search_queries.get("english") or []
    return {
        "available": bool(query_plan),
        "ko_query_count": safe_len(ko_queries),
        "en_query_count": safe_len(en_queries),
        "selected_ko_queries": limit_list(ko_queries, 5),
        "selected_en_queries": limit_list(en_queries, 5),
        "search_warnings": limit_list(search_queries.get("warnings"), 10),
        "news_filter": {
            "enabled": news_filter.get("enabled"),
            "kept_count": news_filter.get("kept_count"),
            "dropped_count": news_filter.get("dropped_count"),
            "warnings": limit_list(news_filter.get("warnings"), 10),
        },
        "industry_rag": {
            "has_results": bool(industry_rag.get("results")),
            "result_count": safe_len(industry_rag.get("results")),
            "warning": industry_rag.get("warning"),
        },
        "compressed_evidence": {
            "count": safe_len(compressed.get("items") or compressed.get("results")),
            "warnings": limit_list(compressed.get("warnings"), 10),
        },
    }


def evidence_summary_payload(
    state: PatentWorkflowState,
    *,
    include_samples: bool,
    priority_evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    evidence_bundle = state.evidence_bundle or []
    payload = {
        "total_count": len(evidence_bundle),
        "source_type_counts": source_type_counts(evidence_bundle),
        "known_evidence_ids": sorted(known_evidence_ids(evidence_bundle)),
    }
    if include_samples:
        payload["samples"] = supervisor_evidence_samples(evidence_bundle, priority_evidence_ids)
    return payload


def supervisor_evidence_samples(
    evidence_bundle: list[dict[str, Any]],
    priority_evidence_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build evidence previews for a supervisor check.

    The evidence the rationale actually cited (priority_evidence_ids) is included
    in full so the supervisor can content-verify it, then a few non-cited items
    are added for context. Without priorities this keeps the legacy behaviour of
    the first MAX_SUPERVISOR_EVIDENCE_SAMPLES items.
    """
    priority = [str(item) for item in (priority_evidence_ids or []) if str(item).strip()]
    priority_set = set(priority)
    cited = [
        evidence
        for evidence in evidence_bundle
        if str(evidence.get("evidence_id")) in priority_set
    ]
    others = [
        evidence
        for evidence in evidence_bundle
        if str(evidence.get("evidence_id")) not in priority_set
    ]
    selected = cited + others[:MAX_SUPERVISOR_EVIDENCE_SAMPLES]
    return [evidence_sample(evidence) for evidence in selected]


def evidence_sample(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": evidence.get("evidence_id"),
        "source_type": evidence.get("source_type"),
        "source": evidence.get("source"),
        "title": evidence.get("title"),
        "url": evidence.get("url"),
        "has_content": bool(evidence.get("content")),
        "has_context": bool(evidence.get("context")),
        "has_compressed_summary": bool(evidence.get("compressed_summary")),
        "summary_preview": preview_text(
            evidence.get("compressed_summary") or evidence.get("summary") or evidence.get("title"),
            250,
        ),
        "related_axes": limit_list(evidence.get("related_axes"), 6),
    }


def valuation_axis_payload(axis: str, axis_result: dict[str, Any], known_ids: set[str]) -> dict[str, Any]:
    evidence_ids = [str(item) for item in axis_result.get("evidence_ids", []) if str(item).strip()]
    score = axis_result.get("score")
    return {
        "exists": bool(axis_result),
        "score": score,
        "grade": axis_result.get("grade"),
        "has_rationale": bool(axis_result.get("rationale")),
        "rationale_length": text_length(axis_result.get("rationale")),
        "rationale_preview": preview_text(axis_result.get("rationale"), 300),
        "evidence_ids": evidence_ids,
        "unknown_evidence_ids": [evidence_id for evidence_id in evidence_ids if evidence_id not in known_ids],
        "risk_factor_count": safe_len(axis_result.get("risk_factors")),
        "missing_information_count": safe_len(axis_result.get("missing_information")),
        "confidence": axis_result.get("confidence"),
    }


def source_type_counts(evidence_bundle: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for evidence in evidence_bundle:
        source_type = str(evidence.get("source_type") or "unknown")
        counts[source_type] = counts.get(source_type, 0) + 1
    return counts


def known_evidence_ids(evidence_bundle: list[dict[str, Any]]) -> set[str]:
    return {str(evidence.get("evidence_id")) for evidence in evidence_bundle if evidence.get("evidence_id")}


def expected_total_score(axis_payload: dict[str, dict[str, Any]]) -> int | None:
    # total_score in the valuation result is the SUM of the 4 axis scores
    # (0-400). Return the sum here too so it can be cross-checked against
    # total_score; previously this returned the average, which never matched.
    scores = [axis.get("score") for axis in axis_payload.values() if isinstance(axis.get("score"), (int, float))]
    if len(scores) != len(REQUIRED_VALUATION_AXES):
        return None
    return int(sum(scores))


def preview_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def text_length(value: Any) -> int:
    if value is None:
        return 0
    return len(str(value))


def limit_list(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def safe_len(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict, str)):
        return len(value)
    return 0


def markdown_headings(markdown: Any, limit: int = 20) -> list[str]:
    if not markdown:
        return []
    headings = []
    for line in str(markdown).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.append(stripped[:120])
        if len(headings) >= limit:
            break
    return headings


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
    if not preprocessed:
        issues.append("Missing preprocessed_patent")
    if preprocessed:
        validation = preprocessed.get("validation", {})
        warnings = list(validation.get("missing_fields", [])) + list(validation.get("warnings", []))
    else:
        warnings = []
    return SupervisorDecision(
        passed=not issues,
        next_action="query_rewriting" if not issues else "end",
        issues=issues,
        reason="Patent metadata check completed.",
        metadata={"warnings": warnings},
    )


def check_evidence_bundle(state: PatentWorkflowState) -> SupervisorDecision:
    evidence_bundle = state.evidence_bundle
    issues: list[str] = []
    missing_evidence: list[str] = []
    if len(evidence_bundle) < 3:
        missing_evidence.append("minimum_evidence_count")
    if not state.user_input.get("skip_news_evidence"):
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
    issues: list[str] = []

    for axis in REQUIRED_VALUATION_AXES:
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
    if not state.summary_validation_result or not state.summary_validation_result.get("passed"):
        issues.append("Summary validation has not passed")
    if not state.report_validation_result or not state.report_validation_result.get("passed"):
        issues.append("Report validation has not passed")
    return SupervisorDecision(
        passed=not issues,
        next_action="final_merge" if not issues else "supervisor",
        issues=issues,
        reason="Final readiness check completed.",
    )
