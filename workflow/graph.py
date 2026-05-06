from typing import Any

from langgraph.graph import END, START, StateGraph

from services.observability.langsmith_service import trace
from workflow.nodes import (
    common_preprocess_node,
    evidence_compression_node,
    evidence_search_node,
    final_merge_node,
    patent_fetch_node,
    patent_resolve_node,
    query_rewriting_node,
    summary_node,
    validation_node,
    valuation_node,
)
from workflow.supervisor import supervisor_node
from workflow.state import PatentWorkflowState


def _as_state(payload: dict[str, Any]) -> PatentWorkflowState:
    return PatentWorkflowState.model_validate(payload)


def _run_node(payload: dict[str, Any], fn: Any) -> dict[str, Any]:
    state = _as_state(payload)
    next_state = fn(state)
    return next_state.model_dump()


def _route_after_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    decision = state.supervisor_decision or {}
    action = decision.get("next_action")

    if action in {"summary"}:
        return "summary"
    if action in {"evidence_check", "query_rewriting"}:
        if action == "query_rewriting" and state.retry_count >= 2:
            return "valuation"
        return "query_rewriting"
    if action in {"valuation", "valuation_retry"}:
        return "valuation"
    if action == "validation":
        return "validation"
    if action == "final_merge":
        return "final_merge"
    return "end"


def _build_graph() -> Any:
    graph = StateGraph(dict)
    graph.add_node("patent_resolve", lambda payload: _run_node(payload, patent_resolve_node))
    graph.add_node("patent_fetch", lambda payload: _run_node(payload, patent_fetch_node))
    graph.add_node("common_preprocess", lambda payload: _run_node(payload, common_preprocess_node))
    graph.add_node("supervisor", lambda payload: _run_node(payload, supervisor_node))
    graph.add_node("summary", lambda payload: _run_node(payload, summary_node))
    graph.add_node("query_rewriting", lambda payload: _run_node(payload, query_rewriting_node))
    graph.add_node("evidence_search", lambda payload: _run_node(payload, evidence_search_node))
    graph.add_node("evidence_compression", lambda payload: _run_node(payload, evidence_compression_node))
    graph.add_node("valuation", lambda payload: _run_node(payload, valuation_node))
    graph.add_node("validation", lambda payload: _run_node(payload, validation_node))
    graph.add_node("final_merge", lambda payload: _run_node(payload, final_merge_node))

    graph.add_edge(START, "patent_resolve")
    graph.add_edge("patent_resolve", "patent_fetch")
    graph.add_edge("patent_fetch", "common_preprocess")
    graph.add_edge("common_preprocess", "supervisor")
    graph.add_edge("summary", "supervisor")
    graph.add_edge("query_rewriting", "evidence_search")
    graph.add_edge("evidence_search", "evidence_compression")
    graph.add_edge("evidence_compression", "supervisor")
    graph.add_edge("valuation", "supervisor")
    graph.add_edge("validation", "supervisor")
    graph.add_edge("final_merge", END)

    graph.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {
            "summary": "summary",
            "query_rewriting": "query_rewriting",
            "valuation": "valuation",
            "validation": "validation",
            "final_merge": "final_merge",
            "end": END,
        },
    )
    return graph.compile()


WORKFLOW_GRAPH = _build_graph()


@trace(name="patent_valuation_workflow", run_type="chain")
def run_workflow(state: PatentWorkflowState) -> PatentWorkflowState:
    result = WORKFLOW_GRAPH.invoke(state.model_dump())
    return PatentWorkflowState.model_validate(result)
