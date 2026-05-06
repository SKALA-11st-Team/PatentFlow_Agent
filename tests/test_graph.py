from workflow.graph import run_workflow
from workflow.state import PatentWorkflowState


def test_run_workflow_returns_state():
    state = PatentWorkflowState(user_input={"patent_id": 1})
    result = run_workflow(state)
    assert result.patent_structured is not None
    assert result.supervisor_decision is not None
