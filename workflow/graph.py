from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import settings
from services.observability.langsmith_service import trace
from workflow.nodes import (
    common_preprocess_node,
    evidence_compression_node,
    evidence_search_node,
    final_report_node,
    final_merge_node,
    patent_fetch_node,
    patent_resolve_node,
    portfolio_sibling_node,
    query_rewriting_node,
    summary_node,
    validation_node,
    valuation_business_fit_node,
    valuation_finalize_node,
    valuation_legal_node,
    valuation_market_node,
    valuation_technology_node,
)
from workflow.supervisor import (
    research_supervisor_node,
    top_supervisor_node,
    valuation_supervisor_node,
    writing_supervisor_node,
)
from workflow.state import PatentWorkflowState


def _as_state(payload: dict[str, Any]) -> PatentWorkflowState:
    return PatentWorkflowState.model_validate(payload)


def _run_node(payload: dict[str, Any], fn: Any) -> dict[str, Any]:
    state = _as_state(payload)
    next_state = fn(state)
    return next_state.model_dump()


def _route_after_top_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"research_team", "valuation_team", "writing_team", "final_merge"}:
        return action
    return "end"


def _route_after_research_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action == "common_preprocess" and not state.parsed_pdf and not state.kipris_api_data:
        return "end"
    if action in {"patent_fetch", "common_preprocess", "query_rewriting"}:
        if action == "query_rewriting" and state.retry_count >= settings.max_evidence_search_rounds:
            return "valuation_team"
        return action
    if action == "valuation_team":
        return "top_supervisor"
    return "end"


def _route_after_valuation_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"research_team", "valuation_team", "writing_team"}:
        return action
    return "end"


def _route_after_writing_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"writing_team", "final_merge"}:
        return action
    return "end"


def _build_graph() -> Any:
    graph = StateGraph(dict)
    graph.add_node("top_supervisor", lambda payload: _run_node(payload, top_supervisor_node))
    graph.add_node("patent_resolve", lambda payload: _run_node(payload, patent_resolve_node))
    graph.add_node("patent_fetch", lambda payload: _run_node(payload, patent_fetch_node))
    graph.add_node("portfolio_sibling", lambda payload: _run_node(payload, portfolio_sibling_node))
    graph.add_node("common_preprocess", lambda payload: _run_node(payload, common_preprocess_node))
    graph.add_node("research_supervisor", lambda payload: _run_node(payload, research_supervisor_node))
    graph.add_node("summary", lambda payload: _run_node(payload, summary_node))
    graph.add_node("query_rewriting", lambda payload: _run_node(payload, query_rewriting_node))
    graph.add_node("evidence_search", lambda payload: _run_node(payload, evidence_search_node))
    graph.add_node("evidence_compression", lambda payload: _run_node(payload, evidence_compression_node))
    graph.add_node("valuation_legal", lambda payload: _run_node(payload, valuation_legal_node))
    graph.add_node("valuation_technology", lambda payload: _run_node(payload, valuation_technology_node))
    graph.add_node("valuation_market", lambda payload: _run_node(payload, valuation_market_node))
    graph.add_node("valuation_business_fit", lambda payload: _run_node(payload, valuation_business_fit_node))
    graph.add_node("valuation_finalize", lambda payload: _run_node(payload, valuation_finalize_node))
    graph.add_node("valuation_supervisor", lambda payload: _run_node(payload, valuation_supervisor_node))
    graph.add_node("final_report", lambda payload: _run_node(payload, final_report_node))
    graph.add_node("validation", lambda payload: _run_node(payload, validation_node))
    graph.add_node("writing_supervisor", lambda payload: _run_node(payload, writing_supervisor_node))
    graph.add_node("final_merge", lambda payload: _run_node(payload, final_merge_node))

    graph.add_edge(START, "top_supervisor")
    graph.add_edge("patent_resolve", "patent_fetch")
    graph.add_edge("patent_fetch", "portfolio_sibling")
    graph.add_edge("portfolio_sibling", "common_preprocess")
    graph.add_edge("common_preprocess", "research_supervisor")
    graph.add_edge("summary", "final_report")
    graph.add_edge("final_report", "writing_supervisor")
    graph.add_edge("query_rewriting", "evidence_search")
    graph.add_edge("evidence_search", "evidence_compression")
    graph.add_edge("evidence_compression", "research_supervisor")
    graph.add_edge("valuation_legal", "valuation_technology")
    graph.add_edge("valuation_technology", "valuation_market")
    graph.add_edge("valuation_market", "valuation_business_fit")
    graph.add_edge("valuation_business_fit", "valuation_finalize")
    graph.add_edge("valuation_finalize", "valuation_supervisor")
    graph.add_edge("validation", "writing_supervisor")
    graph.add_edge("final_merge", END)

    graph.add_conditional_edges(
        "top_supervisor",
        _route_after_top_supervisor,
        {
            "research_team": "patent_resolve",
            "valuation_team": "valuation_legal",
            "writing_team": "summary",
            "final_merge": "final_merge",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "research_supervisor",
        _route_after_research_supervisor,
        {
            "patent_fetch": "patent_fetch",
            "common_preprocess": "common_preprocess",
            "query_rewriting": "query_rewriting",
            "valuation_team": "valuation_legal",
            "top_supervisor": "top_supervisor",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "valuation_supervisor",
        _route_after_valuation_supervisor,
        {
            "research_team": "patent_resolve",
            "valuation_team": "valuation_legal",
            "writing_team": "summary",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "writing_supervisor",
        _route_after_writing_supervisor,
        {
            "writing_team": "summary",
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
