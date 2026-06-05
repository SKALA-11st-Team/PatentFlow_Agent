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


def test_route_after_valuation_supervisor_returns_to_evidence_search():
    state = PatentWorkflowState(supervisor_decision={"next_team": "research", "next_action": "query_rewriting"})

    assert workflow_graph._route_after_valuation_supervisor(state.model_dump()) == "query_rewriting"


def test_valuation_supervisor_no_longer_reroutes_to_patent_collection():
    state = PatentWorkflowState(supervisor_decision={"next_team": "research", "next_action": "research_team"})

    assert workflow_graph._route_after_valuation_supervisor(state.model_dump()) == "end"


def test_research_supervisor_no_longer_reroutes_to_collection_or_preprocess():
    for action in ["patent_fetch", "patent_context_collect", "common_preprocess"]:
        state = PatentWorkflowState(supervisor_decision={"next_team": "research", "next_action": action})

        assert workflow_graph._route_after_research_supervisor(state.model_dump()) == "end"


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


def test_writing_team_runs_summary_and_final_report_in_parallel_then_validates(monkeypatch):
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

    def summary_validation_node(state):
        calls.append("summary_validation")
        state.summary_validation_result = {"passed": True, "issues": []}
        return state

    def report_validation_node(state):
        calls.append("report_validation")
        state.report_validation_result = {"passed": True, "issues": []}
        return state

    def writing_supervisor_node(state):
        calls.append("writing_supervisor")
        state.supervisor_decision = {"next_action": "final_merge"}
        return state

    monkeypatch.setattr(workflow_graph, "summary_node", summary_node)
    monkeypatch.setattr(workflow_graph, "final_report_node", final_report_node)
    monkeypatch.setattr(workflow_graph, "summary_validation_node", summary_validation_node)
    monkeypatch.setattr(workflow_graph, "report_validation_node", report_validation_node)
    monkeypatch.setattr(workflow_graph, "writing_supervisor_node", writing_supervisor_node)

    state = PatentWorkflowState(
        patent_structured={"id": 1},
        preprocessed_patent={"patent_id": "P1"},
        valuation_result={"axes": {}},
    )

    result = run_workflow(state)

    assert set(calls[:2]) == {"summary", "final_report"}
    assert set(calls[2:4]) == {"summary_validation", "report_validation"}
    assert calls[-1] == "writing_supervisor"
    assert result.final_report["summary"]["summary_markdown"].startswith("# 특허 요약")
    assert result.final_report["valuation"]["final_report_markdown"].startswith("# 가치평가 리포트")


def test_writing_supervisor_can_retry_only_failed_document():
    summary_failed = PatentWorkflowState(
        summary_validation_result={"passed": False, "issues": ["summary"]},
        report_validation_result={"passed": True, "issues": []},
        supervisor_decision={"next_action": "summary"},
    )
    report_failed = PatentWorkflowState(
        summary_validation_result={"passed": True, "issues": []},
        report_validation_result={"passed": False, "issues": ["report"]},
        supervisor_decision={"next_action": "final_report"},
    )

    assert workflow_graph._route_after_writing_supervisor(summary_failed.model_dump()) == "summary"
    assert workflow_graph._route_after_writing_supervisor(report_failed.model_dump()) == "final_report"


def test_writing_retry_reuses_existing_document_nodes(monkeypatch):
    calls = []
    counters = {"summary_validation": 0, "writing_supervisor": 0}

    def top_supervisor_node(state):
        state.supervisor_decision = {"next_action": "writing_team"}
        return state

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

    def summary_validation_node(state):
        calls.append("summary_validation")
        counters["summary_validation"] += 1
        passed = counters["summary_validation"] > 1
        state.summary_validation_result = {"passed": passed, "issues": [] if passed else ["요약문 보완 필요"]}
        return state

    def report_validation_node(state):
        calls.append("report_validation")
        state.report_validation_result = {"passed": True, "issues": []}
        return state

    def writing_supervisor_node(state):
        calls.append("writing_supervisor")
        counters["writing_supervisor"] += 1
        state.supervisor_decision = {
            "next_action": "summary" if counters["writing_supervisor"] == 1 else "final_merge"
        }
        return state

    monkeypatch.setattr(workflow_graph, "top_supervisor_node", top_supervisor_node)
    monkeypatch.setattr(workflow_graph, "summary_node", summary_node)
    monkeypatch.setattr(workflow_graph, "final_report_node", final_report_node)
    monkeypatch.setattr(workflow_graph, "summary_validation_node", summary_validation_node)
    monkeypatch.setattr(workflow_graph, "report_validation_node", report_validation_node)
    monkeypatch.setattr(workflow_graph, "writing_supervisor_node", writing_supervisor_node)

    result = run_workflow(PatentWorkflowState(valuation_result={"axes": {}}))

    assert calls.count("summary") == 2
    assert calls.count("final_report") == 1
    assert calls.count("writing_supervisor") == 2
    assert result.final_report["summary"]["summary_markdown"].startswith("# 특허 요약")


def test_writing_graph_reuses_report_nodes_for_retry():
    graph_nodes = workflow_graph.WORKFLOW_GRAPH.get_graph().nodes

    assert "summary_retry" not in graph_nodes
    assert "summary_validation_retry" not in graph_nodes
    assert "final_report_retry" not in graph_nodes
    assert "report_validation_retry" not in graph_nodes


def test_start_valuation_axes_analysis_full_reset_when_no_targets():
    reset = workflow_graph._start_valuation_axes_analysis(
        PatentWorkflowState(valuation_retry_axes=[]).model_dump()
    )

    assert reset["valuation_axis_legal"] is None
    assert reset["valuation_axis_technology"] is None
    assert reset["valuation_axis_market"] is None
    assert reset["valuation_axis_business_fit"] is None
    assert reset["valuation_result"] is None


def test_start_valuation_axes_analysis_selective_reset_keeps_passing_axes():
    reset = workflow_graph._start_valuation_axes_analysis(
        PatentWorkflowState(valuation_retry_axes=["technology"]).model_dump()
    )

    assert reset["valuation_axis_technology"] is None
    # Passing axes must not be reset (absent keys keep their existing payload).
    assert "valuation_axis_legal" not in reset
    assert "valuation_axis_market" not in reset
    assert "valuation_axis_business_fit" not in reset
    assert "valuation_result" not in reset


def test_selective_retry_reuses_passing_axis_without_llm(monkeypatch):
    calls = []

    def fake_run_axis(axis, state):
        calls.append(axis)
        return {"axis": axis, "score": 50, "grade": "C"}

    monkeypatch.setattr(workflow_graph, "run_axis_valuation_result", fake_run_axis)

    payload = PatentWorkflowState(
        valuation_retry_axes=["technology"],
        patent_structured={"id": 1, "title_final": "테스트 특허"},
    ).model_dump()
    payload["valuation_axis_legal"] = {"axis": "legal", "score": 80, "grade": "A"}

    legal = workflow_graph._run_valuation_axis_result_node(payload, "legal")
    technology = workflow_graph._run_valuation_axis_result_node(payload, "technology")

    # legal already passed -> reused as-is, no re-evaluation
    assert legal["valuation_axis_legal"]["score"] == 80
    assert "legal" not in calls
    # technology is targeted -> re-evaluated
    assert "technology" in calls
    assert technology["valuation_axis_technology"]["axis"] == "technology"
