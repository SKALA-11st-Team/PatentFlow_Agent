from schemas.supervisor import SupervisorDecision
from workflow.state import PatentWorkflowState


def test_supervisor_decision_accepts_team_routing_fields():
    decision = SupervisorDecision(
        passed=True,
        next_action="valuation_team",
        current_team="research",
        next_team="valuation",
        stage="evidence_check",
        route_reason="Research evidence is ready for valuation.",
    )

    assert decision.current_team == "research"
    assert decision.next_team == "valuation"
    assert decision.stage == "evidence_check"
    assert decision.route_reason == "Research evidence is ready for valuation."


def test_workflow_state_tracks_current_team_and_status():
    state = PatentWorkflowState(current_team="research", team_status={"research": "ready"})

    assert state.current_team == "research"
    assert state.team_status == {"research": "ready"}
