from workflow import graph as workflow_graph
from workflow.graph import run_workflow
from workflow.state import PatentWorkflowState


def test_run_workflow_returns_state():
    state = PatentWorkflowState(user_input={"patent_id": 1})
    result = run_workflow(state)
    assert result.patent_structured is not None
    assert result.supervisor_decision is not None


def test_route_after_top_supervisor_uses_next_team():
    state = PatentWorkflowState(supervisor_decision={"next_team": "research", "next_action": "research_team"})

    assert workflow_graph._route_after_top_supervisor(state.model_dump()) == "research_team"


def test_route_after_valuation_supervisor_can_return_to_research():
    state = PatentWorkflowState(supervisor_decision={"next_team": "research", "next_action": "research_team"})

    assert workflow_graph._route_after_valuation_supervisor(state.model_dump()) == "research_team"


def test_route_after_writing_supervisor_finishes_at_final_merge():
    state = PatentWorkflowState(supervisor_decision={"next_team": "final", "next_action": "final_merge"})

    assert workflow_graph._route_after_writing_supervisor(state.model_dump()) == "final_merge"
