from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.valuation import VALUATION_AXES, finalize_valuation_axis_results, run_axis_valuation_result
from app.config import settings
from workflow.nodes import (
    common_preprocess_node,
    evidence_compression_node,
    evidence_search_node,
    final_report_node,
    final_merge_node,
    patent_fetch_node,
    portfolio_sibling_node,
    query_rewriting_node,
    report_validation_node,
    summary_node,
    summary_validation_node,
)
from workflow.supervisor import (
    research_supervisor_node,
    top_supervisor_node,
    valuation_supervisor_node,
    writing_supervisor_node,
)
from workflow.state import PatentWorkflowState


class WorkflowGraphState(TypedDict, total=False):
    user_input: dict[str, Any]
    current_stage: str | None
    current_team: str | None
    team_status: dict[str, Any]
    retry_count: int
    patent_structured: dict[str, Any] | None
    kipris_api_data: dict[str, Any] | None
    kipris_family_patents: list[dict[str, Any]]
    citation_evidence: dict[str, Any]
    pdf_paths: list[str]
    parsed_pdf: dict[str, Any] | None
    preprocessed_patent: dict[str, Any] | None
    summary_result: dict[str, Any] | None
    portfolio_evidence: list[dict[str, Any]]
    portfolio_result: dict[str, Any] | None
    query_plan: dict[str, Any] | None
    search_queries: list[str]
    evidence_bundle: list[dict[str, Any]]
    valuation_result: dict[str, Any] | None
    final_report: dict[str, Any] | None
    validation_result: dict[str, Any] | None
    summary_validation_result: dict[str, Any] | None
    report_validation_result: dict[str, Any] | None
    supervisor_decision: dict[str, Any] | None
    missing_evidence: list[str]
    valuation_axis_legal: dict[str, Any]
    valuation_axis_technology: dict[str, Any]
    valuation_axis_market: dict[str, Any]
    valuation_axis_business_fit: dict[str, Any]
    valuation_retry_axes: list[str]


def _as_state(payload: dict[str, Any]) -> PatentWorkflowState:
    return PatentWorkflowState.model_validate(payload)


def _run_node(payload: dict[str, Any], fn: Any) -> dict[str, Any]:
    state = _as_state(payload)
    next_state = fn(state)
    return next_state.model_dump()


def _run_node_partial(payload: dict[str, Any], fn: Any, keys: list[str]) -> dict[str, Any]:
    state = _as_state(payload)
    next_state = fn(state).model_dump()
    return {key: next_state.get(key) for key in keys}


def _start_valuation_axes_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    retry_axes = payload.get("valuation_retry_axes") or []
    if not retry_axes:
        # First entry or full re-evaluation: reset every axis.
        return {
            "valuation_result": None,
            "validation_result": None,
            "valuation_axis_legal": None,
            "valuation_axis_technology": None,
            "valuation_axis_market": None,
            "valuation_axis_business_fit": None,
        }
    # Selective retry: clear only the targeted axes and keep passing ones.
    reset: dict[str, Any] = {"validation_result": None}
    for axis in VALUATION_AXES:
        if axis in retry_axes:
            reset[f"valuation_axis_{axis}"] = None
    return reset


def _start_writing_reports(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    return {
        "summary_validation_result": None,
        "report_validation_result": None,
        "validation_result": None,
    }


def _join_writing_validations(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    return {}


def _route_after_writing_join(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    if state.summary_validation_result is not None and state.report_validation_result is not None:
        return "writing_supervisor"
    return "end"


def _run_valuation_axis_result_node(payload: dict[str, Any], axis: str) -> dict[str, Any]:
    retry_axes = payload.get("valuation_retry_axes") or []
    existing = payload.get(f"valuation_axis_{axis}")
    if retry_axes and axis not in retry_axes and isinstance(existing, dict):
        # Selective retry: this axis already passed, reuse it without re-calling the LLM.
        return {f"valuation_axis_{axis}": existing}
    state = _as_state(payload)
    return {f"valuation_axis_{axis}": run_axis_valuation_result(axis, state)}


def _run_valuation_axes_merge(payload: dict[str, Any]) -> dict[str, Any]:
    state = _as_state(payload)
    # On a selective retry only some axes were re-evaluated; carry over the prior
    # axis supervisor checks so the supervisor can reuse them for the untouched
    # axes instead of re-checking all four.
    prior_checks = ((payload.get("valuation_result") or {}).get("axis_supervisor_checks")) or {}
    axis_results = {
        axis: payload[f"valuation_axis_{axis}"]
        for axis in VALUATION_AXES
        if isinstance(payload.get(f"valuation_axis_{axis}"), dict)
    }
    next_state = finalize_valuation_axis_results(state, axis_results)
    result = dict(next_state.valuation_result or {})
    if prior_checks:
        result["axis_supervisor_checks"] = prior_checks
    return {"valuation_result": result}


def _route_after_top_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"research_team", "valuation_team", "writing_team", "final_merge"}:
        return action
    return "end"


def _route_after_research_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action == "query_rewriting":
        # ORCH-09: 외부 if가 이미 보장하던 중복 조건 제거.
        if state.retry_count >= settings.max_evidence_search_rounds:
            return "valuation_team"
        return action
    # ORCH-09: valuation_team 결정 시 top_supervisor 우회(superstep 1회 낭비) 대신 직접 valuation으로 라우팅.
    if action == "valuation_team":
        return "valuation_team"
    return "end"


def _route_after_valuation_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"query_rewriting", "valuation_team", "writing_team"}:
        return action
    return "end"


def _route_after_writing_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"writing_team", "summary", "final_report", "final_merge"}:
        return action
    return "end"


def _build_graph() -> Any:
    graph = StateGraph(WorkflowGraphState)
    graph.add_node("top_supervisor", lambda payload: _run_node(payload, top_supervisor_node))
    graph.add_node("patent_context_collect", lambda payload: _run_node(payload, patent_fetch_node))
    graph.add_node("portfolio_sibling", lambda payload: _run_node(payload, portfolio_sibling_node))
    graph.add_node("common_preprocess", lambda payload: _run_node(payload, common_preprocess_node))
    graph.add_node("research_supervisor", lambda payload: _run_node(payload, research_supervisor_node))
    graph.add_node("summary", lambda payload: _run_node_partial(payload, summary_node, ["summary_result"]))
    graph.add_node("query_rewriting", lambda payload: _run_node(payload, query_rewriting_node))
    graph.add_node("evidence_search", lambda payload: _run_node(payload, evidence_search_node))
    graph.add_node("evidence_compression", lambda payload: _run_node(payload, evidence_compression_node))
    graph.add_node("valuation_axes_analyze", _start_valuation_axes_analysis)
    graph.add_node("valuation_legal", lambda payload: _run_valuation_axis_result_node(payload, "legal"))
    graph.add_node("valuation_technology", lambda payload: _run_valuation_axis_result_node(payload, "technology"))
    graph.add_node("valuation_market", lambda payload: _run_valuation_axis_result_node(payload, "market"))
    graph.add_node("valuation_business_fit", lambda payload: _run_valuation_axis_result_node(payload, "business_fit"))
    graph.add_node("valuation_axes_merge", lambda payload: _run_valuation_axes_merge(payload))
    graph.add_node("valuation_supervisor", lambda payload: _run_node(payload, valuation_supervisor_node))
    graph.add_node("writing_start", _start_writing_reports)
    graph.add_node("final_report", lambda payload: _run_node_partial(payload, final_report_node, ["valuation_result"]))
    graph.add_node(
        "report_validation",
        lambda payload: _run_node_partial(payload, report_validation_node, ["report_validation_result"]),
    )
    graph.add_node(
        "summary_validation",
        lambda payload: _run_node_partial(payload, summary_validation_node, ["summary_validation_result"]),
    )
    graph.add_node("writing_join", _join_writing_validations)
    graph.add_node("writing_supervisor", lambda payload: _run_node(payload, writing_supervisor_node))
    graph.add_node("final_merge", lambda payload: _run_node(payload, final_merge_node))

    # Entry and research flow
    graph.add_edge(START, "top_supervisor")
    graph.add_edge("patent_context_collect", "portfolio_sibling")
    graph.add_edge("portfolio_sibling", "common_preprocess")
    graph.add_edge("common_preprocess", "research_supervisor")
    graph.add_edge("query_rewriting", "evidence_search")
    graph.add_edge("evidence_search", "evidence_compression")
    graph.add_edge("evidence_compression", "research_supervisor")

    # Valuation flow
    graph.add_edge("valuation_axes_analyze", "valuation_legal")
    graph.add_edge("valuation_axes_analyze", "valuation_technology")
    graph.add_edge("valuation_axes_analyze", "valuation_market")
    graph.add_edge("valuation_axes_analyze", "valuation_business_fit")
    graph.add_edge(
        ["valuation_legal", "valuation_technology", "valuation_market", "valuation_business_fit"],
        "valuation_axes_merge",
    )
    graph.add_edge("valuation_axes_merge", "valuation_supervisor")

    # Writing flow
    graph.add_edge("writing_start", "summary")
    graph.add_edge("writing_start", "final_report")
    graph.add_edge("summary", "summary_validation")
    graph.add_edge("final_report", "report_validation")
    graph.add_edge("summary_validation", "writing_join")
    graph.add_edge("report_validation", "writing_join")

    # Final output assembly
    graph.add_edge("final_merge", END)

    # Team-level routing
    graph.add_conditional_edges(
        "top_supervisor",
        _route_after_top_supervisor,
        {
            "research_team": "patent_context_collect",
            "valuation_team": "valuation_axes_analyze",
            "writing_team": "writing_start",
            "final_merge": "final_merge",
            "end": END,
        },
    )

    # Research reroute
    graph.add_conditional_edges(
        "research_supervisor",
        _route_after_research_supervisor,
        {
            "query_rewriting": "query_rewriting",
            "valuation_team": "valuation_axes_analyze",
            "top_supervisor": "top_supervisor",
            "end": END,
        },
    )

    # Valuation reroute
    graph.add_conditional_edges(
        "valuation_supervisor",
        _route_after_valuation_supervisor,
        {
            "query_rewriting": "query_rewriting",
            "valuation_team": "valuation_axes_analyze",
            "writing_team": "writing_start",
            "end": END,
        },
    )

    # Writing reroute
    graph.add_conditional_edges(
        "writing_join",
        _route_after_writing_join,
        {
            "writing_supervisor": "writing_supervisor",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "writing_supervisor",
        _route_after_writing_supervisor,
        {
            "writing_team": "writing_start",
            "summary": "summary",
            "final_report": "final_report",
            "final_merge": "final_merge",
            "end": END,
        },
    )
    return graph.compile()


WORKFLOW_GRAPH = _build_graph()


def run_workflow(state: PatentWorkflowState) -> PatentWorkflowState:
    # LangGraph traces the whole run as a single nested tree (one root named
    # below) when LANGSMITH_TRACING is enabled; node spans and wrap_openai LLM
    # calls nest under it. See services.observability.langsmith_service.
    result = WORKFLOW_GRAPH.invoke(
        state.model_dump(),
        config={
            "run_name": "patent_valuation_workflow",
            "recursion_limit": settings.workflow_recursion_limit,
        },
    )
    return PatentWorkflowState.model_validate(result)
