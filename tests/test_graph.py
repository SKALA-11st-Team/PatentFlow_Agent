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


def test_valuation_team_analyzes_axis_nodes_then_merges_results(monkeypatch):
    calls = []

    def axis_result_node(name):
        def _node(payload):
            calls.append(name)
            return {f"valuation_axis_{name}": {"axis": name, "score": 70}}

        return _node

    def finalize_node(payload):
        calls.append("finalize")
        return {
            **payload,
            "valuation_result": {
                "axes": {
                    "legal": {"score": 70},
                    "technology": {"score": 70},
                    "market": {"score": 70},
                    "business_fit": {"score": 70},
                }
            },
        }

    def supervisor_node(state):
        calls.append("valuation_supervisor")
        state.supervisor_decision = {"next_action": "end"}
        return state

    monkeypatch.setattr(workflow_graph, "_run_valuation_axis_result_node", lambda payload, axis: axis_result_node(axis)(payload))
    monkeypatch.setattr(workflow_graph, "_run_valuation_axes_merge", finalize_node)
    monkeypatch.setattr(workflow_graph, "valuation_supervisor_node", supervisor_node)

    state = PatentWorkflowState(
        patent_structured={"id": 1},
        preprocessed_patent={"patent_id": "P1"},
        summary_result={"summary_markdown": "요약"},
    )

    run_workflow(state)

    assert set(calls[:4]) == {"legal", "technology", "market", "business_fit"}
    assert calls[-2:] == ["finalize", "valuation_supervisor"]


def test_valuation_axes_analyze_clears_previous_valuation_state():
    payload = {
        "valuation_result": {"axes": {"legal": {"score": 5}}},
        "validation_result": {"passed": True},
        "valuation_axis_legal": {"score": 5},
        "valuation_axis_technology": {"score": 5},
        "valuation_axis_market": {"score": 5},
        "valuation_axis_business_fit": {"score": 5},
    }

    result = workflow_graph._start_valuation_axes_analysis(payload)

    assert result["valuation_result"] is None
    assert result["validation_result"] is None
    assert result["valuation_axis_legal"] is None
    assert result["valuation_axis_technology"] is None
    assert result["valuation_axis_market"] is None
    assert result["valuation_axis_business_fit"] is None


def test_writing_team_runs_summary_final_report_validation_then_writing_supervisor(monkeypatch):
    calls = []

    def summary_node(state):
        calls.append("summary")
        state.summary_result = {"summary_markdown": "# 특허 요약\n\n본문"}
        return state

    def final_report_node(state):
        calls.append("final_report")
        state.valuation_result = {
            **(state.valuation_result or {}),
            "final_report_markdown": "# 가치평가 리포트\n\n본문",
        }
        return state

    def validation_node(state):
        calls.append("validation")
        state.validation_result = {"passed": True, "issues": []}
        return state

    def writing_supervisor_node(state):
        calls.append("writing_supervisor")
        state.supervisor_decision = {"next_action": "final_merge"}
        return state

    monkeypatch.setattr(workflow_graph, "summary_node", summary_node)
    monkeypatch.setattr(workflow_graph, "final_report_node", final_report_node)
    monkeypatch.setattr(workflow_graph, "validation_node", validation_node)
    monkeypatch.setattr(workflow_graph, "writing_supervisor_node", writing_supervisor_node)

    state = PatentWorkflowState(
        patent_structured={"id": 1},
        preprocessed_patent={"patent_id": "P1"},
        valuation_result={"axes": {}},
    )

    result = run_workflow(state)

    assert calls == ["summary", "final_report", "validation", "writing_supervisor"]
    assert result.final_report["summary"]["summary_markdown"].startswith("# 특허 요약")
    assert result.final_report["valuation"]["final_report_markdown"].startswith("# 가치평가 리포트")
