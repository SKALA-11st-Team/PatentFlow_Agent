from agents.valuation import run_valuation_agent
from workflow.state import PatentWorkflowState


def test_run_valuation_agent_sets_result():
    state = PatentWorkflowState()
    result = run_valuation_agent(state)
    assert result.valuation_result is not None

