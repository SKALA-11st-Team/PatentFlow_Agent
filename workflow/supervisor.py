from __future__ import annotations

import json
from typing import Any

from app.config import settings
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
REQUIRED_VALUATION_AXES = ["legal", "technology", "market", "business_fit"]
MAX_SUPERVISOR_EVIDENCE_SAMPLES = 5


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
    if "summary" in prompt_name:
        return summary_supervisor_payload(state)
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


def summary_supervisor_payload(state: PatentWorkflowState) -> dict[str, Any]:
    summary = state.summary_result or {}
    return {
        "current_stage": state.current_stage,
        "patent": patent_metadata_payload(state),
        "summary": {
            "available": bool(summary),
            "title": summary.get("title"),
            "plain_summary_preview": preview_text(summary.get("plain_summary"), 700),
            "key_points": limit_list(summary.get("key_points"), 8),
            "has_summary_markdown": bool(summary.get("summary_markdown")),
            "summary_markdown_length": text_length(summary.get("summary_markdown")),
        },
        "preprocess_validation": preprocess_validation_payload(state.preprocessed_patent or {}),
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
    return {
        "current_stage": state.current_stage,
        "patent": patent_metadata_payload(state),
        "evidence": evidence_summary_payload(state, include_samples=True),
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
            "available": bool(state.validation_result),
            "passed": (state.validation_result or {}).get("passed"),
            "issues": limit_list((state.validation_result or {}).get("issues"), 10),
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


def evidence_summary_payload(state: PatentWorkflowState, *, include_samples: bool) -> dict[str, Any]:
    evidence_bundle = state.evidence_bundle or []
    payload = {
        "total_count": len(evidence_bundle),
        "source_type_counts": source_type_counts(evidence_bundle),
        "known_evidence_ids": sorted(known_evidence_ids(evidence_bundle)),
    }
    if include_samples:
        payload["samples"] = [
            evidence_sample(evidence)
            for evidence in evidence_bundle[:MAX_SUPERVISOR_EVIDENCE_SAMPLES]
        ]
    return payload


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


def expected_total_score(axis_payload: dict[str, dict[str, Any]]) -> float | None:
    scores = [axis.get("score") for axis in axis_payload.values() if isinstance(axis.get("score"), (int, float))]
    if len(scores) != len(REQUIRED_VALUATION_AXES):
        return None
    return round(sum(scores) / len(scores), 2)


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


def markdown_headings(markdown: Any, limit: int = 8) -> list[str]:
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
    if not state.validation_result or not state.validation_result.get("passed"):
        issues.append("Validation has not passed")
    return SupervisorDecision(
        passed=not issues,
        next_action="final_merge" if not issues else "supervisor",
        issues=issues,
        reason="Final readiness check completed.",
    )
