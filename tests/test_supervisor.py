from schemas.supervisor import SupervisorDecision
from workflow.state import PatentWorkflowState
from workflow.supervisor import (
    research_supervisor_node,
    supervisor_node,
    top_supervisor_node,
    valuation_supervisor_node,
    writing_supervisor_node,
)


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


def test_top_supervisor_routes_new_state_to_research():
    state = PatentWorkflowState()

    result = top_supervisor_node(state)

    assert result.current_team == "top"
    assert result.supervisor_decision["next_team"] == "research"
    assert result.supervisor_decision["next_action"] == "research_team"


def test_research_supervisor_routes_sufficient_evidence_to_valuation():
    state = PatentWorkflowState(
        patent_structured={
            "id": 1,
            "application_number": "10-2023-0000001",
            "registration_number": "10-2000000",
            "title_final": "테스트 특허",
            "status": "등록",
            "application_date": "2023-01-01",
            "registration_date": "2024-01-01",
        },
        preprocessed_patent={"metadata": {"title": "테스트 특허"}},
        summary_result={"title": "테스트", "plain_summary": "요약", "key_points": ["핵심"]},
        evidence_bundle=[
            {"evidence_id": "news_1", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_2", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_3", "source": "gnews", "source_type": "news", "content": "본문"},
            {"evidence_id": "rag_1", "source": "report", "source_type": "industry_report", "context": "근거"},
        ],
    )

    result = research_supervisor_node(state)

    assert result.supervisor_decision["passed"] is True
    assert result.supervisor_decision["next_team"] == "valuation"
    assert result.supervisor_decision["next_action"] == "valuation_team"


def test_research_supervisor_uses_llm_judge_after_rule_pass(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt: (
            '{"passed": false, "next_action": "query_rewriting", '
            '"missing_evidence": ["market_signal"], '
            '"issues": ["시장성 직접 근거가 부족함"], "reason": "시장 근거 보강 필요"}'
        ),
    )
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        patent_structured={
            "id": 1,
            "application_number": "10-2023-0000001",
            "registration_number": "10-2000000",
            "title_final": "테스트 특허",
            "status": "등록",
            "application_date": "2023-01-01",
            "registration_date": "2024-01-01",
        },
        preprocessed_patent={"metadata": {"title": "테스트 특허"}},
        summary_result={"title": "테스트", "plain_summary": "요약", "key_points": ["핵심"]},
        evidence_bundle=[
            {"evidence_id": "news_1", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_2", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_3", "source": "gnews", "source_type": "news", "content": "본문"},
            {"evidence_id": "rag_1", "source": "report", "source_type": "industry_report", "context": "근거"},
        ],
    )

    result = research_supervisor_node(state)

    assert result.supervisor_decision["passed"] is False
    assert result.supervisor_decision["next_team"] == "research"
    assert result.supervisor_decision["next_action"] == "query_rewriting"
    assert result.missing_evidence == ["market_signal"]


def test_supervisor_node_keeps_legacy_action_for_research_success():
    state = PatentWorkflowState(
        current_stage="evidence_check",
        patent_structured={
            "id": 1,
            "application_number": "10-2023-0000001",
            "registration_number": "10-2000000",
            "title_final": "테스트 특허",
            "status": "등록",
            "application_date": "2023-01-01",
            "registration_date": "2024-01-01",
        },
        preprocessed_patent={"metadata": {"title": "테스트 특허"}},
        summary_result={"title": "테스트", "plain_summary": "요약", "key_points": ["핵심"]},
        evidence_bundle=[
            {"evidence_id": "news_1", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_2", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_3", "source": "gnews", "source_type": "news", "content": "본문"},
            {"evidence_id": "rag_1", "source": "report", "source_type": "industry_report", "context": "근거"},
        ],
    )

    result = supervisor_node(state)

    assert result.supervisor_decision["next_team"] == "valuation"
    assert result.supervisor_decision["next_action"] == "valuation"


def test_valuation_supervisor_routes_unknown_evidence_to_research():
    state = PatentWorkflowState(
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={
            "axes": {
                axis: {
                    "score": 80,
                    "grade": "A",
                    "rationale": "근거 기반 평가",
                    "evidence_ids": ["unknown"],
                    "risk_factors": [],
                    "confidence": 0.8,
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            }
        },
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["passed"] is False
    assert result.supervisor_decision["next_team"] == "research"
    assert result.supervisor_decision["next_action"] == "research_team"


def test_supervisor_node_keeps_legacy_action_for_valuation_unknown_evidence():
    state = PatentWorkflowState(
        current_stage="valuation_check",
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={
            "axes": {
                axis: {
                    "score": 80,
                    "grade": "A",
                    "rationale": "근거 기반 평가",
                    "evidence_ids": ["unknown"],
                    "risk_factors": [],
                    "confidence": 0.8,
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            }
        },
    )

    result = supervisor_node(state)

    assert result.supervisor_decision["next_team"] == "research"
    assert result.supervisor_decision["next_action"] == "query_rewriting"


def test_valuation_supervisor_uses_llm_judge_to_retry_valuation(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt: (
            '{"passed": false, "next_action": "valuation_retry", '
            '"issues": ["권리성 점수 논리가 근거와 맞지 않음"], "reason": "평가 논리 보완 필요"}'
        ),
    )
    evidence = [{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}]
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        patent_structured={"id": 1, "title_final": "테스트 특허"},
        evidence_bundle=evidence,
        valuation_result={
            "axes": {
                axis: {
                    "score": 70,
                    "grade": "B",
                    "rationale": "known 근거 기반 평가",
                    "evidence_ids": ["known"],
                    "risk_factors": [],
                    "confidence": 0.7,
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            }
        },
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["passed"] is False
    assert result.supervisor_decision["next_team"] == "valuation"
    assert result.supervisor_decision["next_action"] == "valuation_team"


def test_writing_supervisor_routes_complete_documents_to_final():
    state = PatentWorkflowState(
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
    )

    result = writing_supervisor_node(state)

    assert result.supervisor_decision["passed"] is True
    assert result.supervisor_decision["next_team"] == "final"
    assert result.supervisor_decision["next_action"] == "final_merge"


def test_writing_supervisor_uses_llm_judge_to_retry_document(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt: (
            '{"passed": false, "next_action": "supervisor", '
            '"issues": ["AI 평가와 최종 판단 구분이 불명확함"], "reason": "문서 분리 필요"}'
        ),
    )
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
    )

    result = writing_supervisor_node(state)

    assert result.supervisor_decision["passed"] is False
    assert result.supervisor_decision["next_team"] == "writing"
    assert result.supervisor_decision["next_action"] == "writing_team"
