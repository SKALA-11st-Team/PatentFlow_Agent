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


def test_valuation_team_runs_sequential_axis_nodes(monkeypatch):
    calls = []

    def axis_node(name):
        def _node(state):
            calls.append(name)
            state.valuation_result = {
                **(state.valuation_result or {}),
                "axes": {
                    **((state.valuation_result or {}).get("axes") or {}),
                    name: {"score": 70},
                },
            }
            return state

        return _node

    def finalize_node(state):
        calls.append("finalize")
        state.valuation_result = {
            "axes": {
                "legal": {"score": 70},
                "technology": {"score": 70},
                "market": {"score": 70},
                "business_fit": {"score": 70},
            }
        }
        return state

    def supervisor_node(state):
        calls.append("valuation_supervisor")
        state.supervisor_decision = {"next_action": "end"}
        return state

    monkeypatch.setattr(workflow_graph, "valuation_legal_node", axis_node("legal"))
    monkeypatch.setattr(workflow_graph, "valuation_technology_node", axis_node("technology"))
    monkeypatch.setattr(workflow_graph, "valuation_market_node", axis_node("market"))
    monkeypatch.setattr(workflow_graph, "valuation_business_fit_node", axis_node("business_fit"))
    monkeypatch.setattr(workflow_graph, "valuation_finalize_node", finalize_node)
    monkeypatch.setattr(workflow_graph, "valuation_supervisor_node", supervisor_node)

    state = PatentWorkflowState(
        patent_structured={"id": 1},
        preprocessed_patent={"patent_id": "P1"},
        summary_result={"summary_markdown": "요약"},
    )

    run_workflow(state)

    assert calls == ["legal", "technology", "market", "business_fit", "finalize", "valuation_supervisor"]


def test_writing_team_runs_summary_before_writing_supervisor(monkeypatch):
    calls = []

    def summary_node(state):
        calls.append("summary")
        state.summary_result = {"summary_markdown": "# 특허 요약\n\n본문"}
        return state

    def writing_supervisor_node(state):
        calls.append("writing_supervisor")
        state.supervisor_decision = {"next_action": "final_merge"}
        return state

    monkeypatch.setattr(workflow_graph, "summary_node", summary_node)
    monkeypatch.setattr(workflow_graph, "writing_supervisor_node", writing_supervisor_node)

    state = PatentWorkflowState(
        patent_structured={"id": 1},
        preprocessed_patent={"patent_id": "P1"},
        valuation_result={"final_report_markdown": "# 가치평가 리포트\n\n본문"},
    )

    result = run_workflow(state)

    assert calls == ["summary", "writing_supervisor"]
    assert result.final_report["summary"]["summary_markdown"].startswith("# 특허 요약")
