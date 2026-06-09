from schemas.supervisor import SupervisorDecision
from workflow.state import PatentWorkflowState
from workflow.supervisor import (
    build_supervisor_judge_prompt,
    research_supervisor_node,
    run_axis_supervisor_checks,
    top_supervisor_node,
    valuation_supervisor_node,
    writing_supervisor_node,
)


def test_axis_supervisor_criteria_files_are_non_routing_guides():
    from pathlib import Path

    files = [
        "supervisor_legal_check.md",
        "supervisor_technology_check.md",
        "supervisor_market_check.md",
        "supervisor_business_fit_check.md",
    ]

    for filename in files:
        text = Path("prompts/supervisor", filename).read_text(encoding="utf-8")
        assert "라우팅을 결정하는 Supervisor 프롬프트가 아니며" in text
        assert "`next_action`을 출력하지 않습니다" in text
        assert "최종 라우팅은 4개 축의 status를 결정적으로 집계해 수행하며, 별도의 라우팅 LLM은 없습니다." in text



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


def test_research_supervisor_stops_when_patent_preprocess_is_missing():
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
    )

    result = research_supervisor_node(state)

    assert result.supervisor_decision["passed"] is False
    assert result.supervisor_decision["next_action"] == "end"
    assert "Missing preprocessed_patent" in result.supervisor_decision["issues"]


def test_research_supervisor_does_not_require_summary_result():
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
        evidence_bundle=[
            {"evidence_id": "news_1", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_2", "source": "naver", "source_type": "news", "content": "본문"},
            {"evidence_id": "news_3", "source": "gnews", "source_type": "news", "content": "본문"},
        ],
    )

    result = research_supervisor_node(state)

    assert result.supervisor_decision["passed"] is True
    assert result.supervisor_decision["next_action"] == "valuation_team"


def test_research_supervisor_uses_llm_judge_after_rule_pass(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt, **kwargs: (
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


def test_supervisor_llm_uses_dedicated_model_setting(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, **kwargs):
        captured.update(kwargs)
        return '{"passed": true, "next_action": "valuation", "issues": [], "reason": "통과"}'

    monkeypatch.setattr("workflow.supervisor.settings.openai_supervisor_model", "gpt-5-nano")
    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
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

    research_supervisor_node(state)

    assert captured["model"] == "gpt-5-nano"


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
    assert result.supervisor_decision["next_action"] == "query_rewriting"


def test_valuation_supervisor_uses_llm_judge_to_retry_valuation(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt, **kwargs: (
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
                    "risk_factors": ["리스크"],
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


def test_axis_supervisor_checks_are_stored_and_route_retry_or_research(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        if "Legal Axis Quality Check Criteria" in prompt:
            return '{"status": "passed", "issues": [], "reason": "권리성 정상"}'
        if "Technology Axis Quality Check Criteria" in prompt:
            return '{"status": "valuation_retry", "issues": ["기술성 근거 재작성 필요"], "reason": "평가 논리 보완"}'
        if "Market Axis Quality Check Criteria" in prompt:
            return '{"status": "query_rewriting", "issues": ["시장성 근거 부족"], "reason": "근거 재수집 필요"}'
        if "Business Fit Axis Quality Check Criteria" in prompt:
            return '{"status": "passed", "issues": [], "reason": "사업 연계성 정상"}'
        return '{"passed": false, "next_action": "query_rewriting", "issues": ["축별 근거 부족"], "reason": "근거 재수집"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
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
                    "risk_factors": ["리스크"],
                    "missing_information": [],
                    "confidence": 0.7,
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            }
        },
    )

    result = valuation_supervisor_node(state)
    checks = result.valuation_result["axis_supervisor_checks"]

    assert checks["legal"]["status"] == "passed"
    assert checks["technology"]["status"] == "valuation_retry"
    assert checks["market"]["status"] == "query_rewriting"
    assert checks["business_fit"]["status"] == "passed"
    # Deterministic aggregation: query_rewriting wins over valuation_retry.
    assert result.supervisor_decision["next_team"] == "research"
    assert result.supervisor_decision["next_action"] == "query_rewriting"


def test_run_axis_supervisor_checks_defaults_to_passed_without_llm():
    business_fit_subscores = {
        "official_business_evidence": {"score": 24},
        "product_function_direct_match": {"score": 24},
        "business_context_fit": {"score": 18},
    }
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": False},
        evidence_bundle=[
            {
                "evidence_id": "known",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "url": "https://www.skax.co.kr/manufacturing/example",
                "content": "SK AX 제조 솔루션",
            }
        ],
        valuation_result={
            "axes": {
                axis: {
                    "score": 66 if axis == "business_fit" else 70,
                    "grade": "B",
                    "rationale": "known 근거 기반 평가",
                    "evidence_ids": ["known"],
                    "risk_factors": [],
                    "missing_information": [],
                    "confidence": 0.7,
                    **({"subscores": business_fit_subscores} if axis == "business_fit" else {}),
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            }
        },
    )

    checks = run_axis_supervisor_checks(state)

    assert set(checks) == {"legal", "technology", "market", "business_fit"}
    assert all(item["status"] == "passed" for item in checks.values())
    assert state.valuation_result["axis_supervisor_checks"] == checks



def test_valuation_supervisor_retry_limit_continues_when_rule_check_passes(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt, **kwargs: (
            '{"passed": false, "next_action": "valuation_retry", '
            '"issues": ["평가 논리 재검토 필요"], "reason": "재시도 요청"}'
        ),
    )
    evidence = [{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}]
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        team_status={"supervisor_retry_counts": {"valuation": 2}},
        patent_structured={"id": 1, "title_final": "테스트 특허"},
        evidence_bundle=evidence,
        valuation_result={
            "axes": {
                axis: {
                    "score": 70,
                    "grade": "B",
                    "rationale": "known 근거 기반 평가",
                    "evidence_ids": ["known"],
                    "risk_factors": ["리스크"],
                    "confidence": 0.7,
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            }
        },
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["passed"] is True
    assert result.supervisor_decision["next_team"] == "writing"
    assert result.supervisor_decision["next_action"] == "writing_team"
    assert result.supervisor_decision["metadata"]["supervisor_retry_limit"]["scope"] == "valuation"


def test_writing_supervisor_routes_complete_documents_to_final():
    state = PatentWorkflowState(
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
        summary_validation_result={"passed": True, "issues": []},
        report_validation_result={"passed": True, "issues": []},
    )

    result = writing_supervisor_node(state)

    assert result.supervisor_decision["passed"] is True
    assert result.supervisor_decision["next_team"] == "final"
    assert result.supervisor_decision["next_action"] == "final_merge"


def test_writing_supervisor_retries_only_failed_summary():
    state = PatentWorkflowState(
        summary_result={"summary_markdown": ""},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
        summary_validation_result={"passed": False, "issues": ["Missing summary_markdown"]},
        report_validation_result={"passed": True, "issues": []},
    )

    result = writing_supervisor_node(state)

    assert result.supervisor_decision["next_team"] == "writing"
    assert result.supervisor_decision["next_action"] == "summary"


def test_writing_supervisor_retries_only_failed_report():
    state = PatentWorkflowState(
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": ""},
        summary_validation_result={"passed": True, "issues": []},
        report_validation_result={"passed": False, "issues": ["Missing final_report_markdown"]},
    )

    result = writing_supervisor_node(state)

    assert result.supervisor_decision["next_team"] == "writing"
    assert result.supervisor_decision["next_action"] == "final_report"


def test_writing_supervisor_uses_llm_judge_to_retry_document(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        if "Supervisor Summary Check" in prompt:
            return '{"passed": true, "issues": [], "reason": "요약 정상"}'
        return '{"passed": false, "issues": ["보고서 내용 보완 필요"], "reason": "보고서 품질"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
    )

    result = writing_supervisor_node(state)

    # Summary passed its dedicated check, report failed -> retry only the report.
    assert result.supervisor_decision["passed"] is False
    assert result.supervisor_decision["next_team"] == "writing"
    assert result.supervisor_decision["next_action"] == "final_report"


def test_writing_supervisor_retries_summary_when_its_content_check_fails(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        if "Supervisor Summary Check" in prompt:
            return '{"passed": false, "issues": ["핵심 내용 설명 부족"], "reason": "요약 품질"}'
        return '{"passed": true, "issues": [], "reason": "보고서 정상"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
    )

    result = writing_supervisor_node(state)

    # Dedicated summary check failed -> retry only the summary.
    assert result.supervisor_decision["next_action"] == "summary"


def test_summary_check_prompt_evaluates_summary_content():
    from pathlib import Path

    text = Path("prompts/supervisor/supervisor_summary_check.md").read_text(encoding="utf-8")
    assert "이 체크는 요약만 평가합니다" in text
    assert "summary_markdown" in text


def test_writing_supervisor_retry_limit_finishes_when_outputs_exist(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt, **kwargs: (
            '{"passed": false, "next_action": "final_report", '
            '"issues": ["문서 보완 필요"], "reason": "재검토 요청"}'
        ),
    )
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        team_status={"supervisor_retry_counts": {"writing": 1}},
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
    )

    result = writing_supervisor_node(state)

    assert result.supervisor_decision["passed"] is True
    assert result.supervisor_decision["next_team"] == "final"
    assert result.supervisor_decision["next_action"] == "final_merge"
    assert result.supervisor_decision["metadata"]["supervisor_retry_limit"]["scope"] == "writing"



def test_final_supervisor_prompt_includes_report_body_for_content_check():
    state = PatentWorkflowState(
        summary_result={
            "plain_summary": "최종 문서에 들어갈 요약입니다.",
            "summary_markdown": "# 특허 요약\n\nSUMMARY_BODY_TEXT",
        },
        valuation_result={
            "axes": {axis: {"score": 70, "grade": "B"} for axis in ["legal", "technology", "market", "business_fit"]},
            "total_score": 70,
            "recommendation": "추가 정보 필요",
            "final_report_markdown": "# 가치평가 리포트\n\n## 시장성\n\nREPORT_BODY_TEXT",
        },
        validation_result={"passed": True, "issues": []},
    )

    prompt = build_supervisor_judge_prompt(state, prompt_name="supervisor/supervisor_final_check.md")

    # Body is now included so the supervisor can verify content (not just format).
    assert "REPORT_BODY_TEXT" in prompt
    assert "SUMMARY_BODY_TEXT" in prompt
    # Per-axis scores are surfaced for faithfulness checks.
    assert "axis_scores" in prompt


def _axes_result(**overrides):
    base = {
        axis: {
            "score": 70,
            "grade": "B",
            "rationale": "known 근거 기반 평가",
            "evidence_ids": ["known"],
            "risk_factors": ["리스크"],
            "missing_information": [],
            "confidence": 0.7,
        }
        for axis in ["legal", "technology", "market", "business_fit"]
    }
    base.update(overrides)
    return base


def test_valuation_supervisor_stores_only_failing_retry_axis(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        if "Technology Axis Quality Check Criteria" in prompt:
            return '{"status": "valuation_retry", "issues": ["기술성 논리 보완"], "reason": "재평가"}'
        if "Axis Quality Check Criteria" in prompt:
            return '{"status": "passed", "issues": [], "reason": "정상"}'
        # Final valuation routing prompt
        return '{"passed": false, "next_action": "valuation_retry", "issues": [], "reason": "재평가"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        patent_structured={"id": 1, "title_final": "테스트 특허"},
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={"axes": _axes_result()},
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["next_action"] == "valuation_team"
    assert result.valuation_retry_axes == ["technology"]


def test_valuation_supervisor_clears_retry_axes_when_passing(monkeypatch):
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt, **kwargs: '{"status": "passed", "issues": [], "reason": "정상"}'
        if "Axis Quality Check Criteria" in prompt
        else '{"passed": true, "next_action": "validation", "issues": [], "reason": "통과"}',
    )
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        valuation_retry_axes=["technology"],
        patent_structured={"id": 1, "title_final": "테스트 특허"},
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={"axes": _axes_result()},
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["next_action"] == "writing_team"
    assert result.valuation_retry_axes == []


def test_valuation_supervisor_aggregates_query_rewriting_gaps(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        if "Market Axis Quality Check Criteria" in prompt:
            return '{"status": "query_rewriting", "issues": ["시장 성장 근거 부족"], "reason": "근거 부족"}'
        if "Business Fit Axis Quality Check Criteria" in prompt:
            return '{"status": "query_rewriting", "issues": ["사업 적용 근거 부족"], "reason": "근거 부족"}'
        if "Axis Quality Check Criteria" in prompt:
            return '{"status": "passed", "issues": [], "reason": "정상"}'
        return '{"passed": false, "next_action": "query_rewriting", "issues": [], "reason": "근거 재수집"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        patent_structured={"id": 1, "title_final": "테스트 특허"},
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={"axes": _axes_result()},
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["next_action"] == "query_rewriting"
    # Only external-evidence axes re-evaluate after re-search; legal/technology
    # keep their prior results.
    assert result.valuation_retry_axes == ["business_fit", "market"]
    assert "시장 성장 근거 부족" in result.missing_evidence
    assert "사업 적용 근거 부족" in result.missing_evidence


def test_valuation_supervisor_drops_axis_after_retry_budget_exhausted(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        if "Technology Axis Quality Check Criteria" in prompt:
            return '{"status": "valuation_retry", "issues": ["기술성 논리 보완"], "reason": "재평가"}'
        if "Axis Quality Check Criteria" in prompt:
            return '{"status": "passed", "issues": [], "reason": "정상"}'
        return '{"passed": false, "next_action": "valuation_retry", "issues": [], "reason": "재평가"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        team_status={"axis_retry_counts": {"technology": 1}},
        patent_structured={"id": 1, "title_final": "테스트 특허"},
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={"axes": _axes_result()},
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["next_action"] == "writing_team"
    assert result.valuation_retry_axes == []
    assert result.supervisor_decision["metadata"]["axis_retry_budget"]["exhausted_axes"] == ["technology"]


def test_valuation_supervisor_allows_first_axis_retry_within_budget(monkeypatch):
    def fake_call_llm(prompt, **kwargs):
        if "Technology Axis Quality Check Criteria" in prompt:
            return '{"status": "valuation_retry", "issues": ["기술성 논리 보완"], "reason": "재평가"}'
        if "Axis Quality Check Criteria" in prompt:
            return '{"status": "passed", "issues": [], "reason": "정상"}'
        return '{"passed": false, "next_action": "valuation_retry", "issues": [], "reason": "재평가"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        patent_structured={"id": 1, "title_final": "테스트 특허"},
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={"axes": _axes_result()},
    )

    result = valuation_supervisor_node(state)

    assert result.supervisor_decision["next_action"] == "valuation_team"
    assert result.valuation_retry_axes == ["technology"]
    assert result.team_status["axis_retry_counts"]["technology"] == 1


def test_self_contained_axis_query_rewriting_is_coerced_to_retry():
    from workflow.supervisor import axis_supervisor_check_result

    for axis in ["legal", "technology"]:
        result = axis_supervisor_check_result(
            axis=axis, status="query_rewriting", issues=["x"], reason="r", source="llm"
        )
        assert result["status"] == "valuation_retry"
        assert result["needs_more_evidence"] is False
        assert result["needs_retry"] is True


def test_external_evidence_axis_keeps_query_rewriting():
    from workflow.supervisor import axis_supervisor_check_result

    for axis in ["market", "business_fit"]:
        result = axis_supervisor_check_result(
            axis=axis, status="query_rewriting", issues=["x"], reason="r", source="llm"
        )
        assert result["status"] == "query_rewriting"
        assert result["needs_more_evidence"] is True


def test_legal_technology_prompts_no_longer_offer_query_rewriting():
    from pathlib import Path

    for filename in ["supervisor_legal_check.md", "supervisor_technology_check.md"]:
        text = Path("prompts/supervisor", filename).read_text(encoding="utf-8")
        assert '"status": "passed" | "valuation_retry"' in text
        assert '| "query_rewriting"' not in text


def test_supervisor_samples_prioritize_cited_evidence_beyond_first_five():
    from workflow.supervisor import supervisor_evidence_samples

    bundle = [
        {"evidence_id": f"e{i}", "source": "naver", "source_type": "news",
         "compressed_summary": f"요약 {i}"}
        for i in range(12)
    ]
    # An axis cites e9 / e11, which fall outside the first 5 of the bundle.
    samples = supervisor_evidence_samples(bundle, priority_evidence_ids=["e9", "e11"])
    ids = [s["evidence_id"] for s in samples]

    assert "e9" in ids and "e11" in ids
    # Cited items come first, then context fill.
    assert ids[0] == "e9" and ids[1] == "e11"


def test_supervisor_samples_without_priority_keep_legacy_first_n():
    from workflow.supervisor import supervisor_evidence_samples, MAX_SUPERVISOR_EVIDENCE_SAMPLES

    bundle = [
        {"evidence_id": f"e{i}", "source": "naver", "source_type": "news"}
        for i in range(12)
    ]
    samples = supervisor_evidence_samples(bundle, priority_evidence_ids=None)

    assert len(samples) == MAX_SUPERVISOR_EVIDENCE_SAMPLES
    assert [s["evidence_id"] for s in samples] == [f"e{i}" for i in range(MAX_SUPERVISOR_EVIDENCE_SAMPLES)]


def test_axis_check_prompts_have_evidence_existence_note():
    from pathlib import Path

    for filename in [
        "supervisor_legal_check.md",
        "supervisor_technology_check.md",
        "supervisor_market_check.md",
        "supervisor_business_fit_check.md",
    ]:
        text = Path("prompts/supervisor", filename).read_text(encoding="utf-8")
        assert "근거의 존재 여부는 evidence.samples가 아니라 known_evidence_ids로 판단" in text


def test_axis_supervisor_checks_run_concurrently(monkeypatch):
    import threading
    import time

    barrier = threading.Barrier(4, timeout=5)
    seen_threads = set()

    def fake_call_llm(prompt, **kwargs):
        seen_threads.add(threading.get_ident())
        # If the 4 checks run concurrently, all reach the barrier together.
        barrier.wait()
        return '{"status": "passed", "issues": [], "reason": "정상"}'

    monkeypatch.setattr("workflow.supervisor.call_llm", fake_call_llm)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={"axes": _axes_result()},
    )

    checks = run_axis_supervisor_checks(state)

    assert set(checks) == {"legal", "technology", "market", "business_fit"}
    assert all(c["status"] == "passed" for c in checks.values())
    # The barrier only releases if all four checks were in flight simultaneously,
    # and they ran on distinct worker threads.
    assert len(seen_threads) == 4



def test_legal_axis_payload_includes_prior_art_context():
    from workflow.supervisor import axis_supervisor_payload

    state = PatentWorkflowState(
        citation_evidence={
            "kr_citation_documents": [
                {"registration_number": "KR10-0948760"},
                {"registration_number": "KR10-2014-0072547"},
            ]
        },
        valuation_result={
            "axes": {
                "legal": {
                    "score": 67, "grade": "B", "rationale": "근거",
                    "evidence_ids": [], "risk_factors": ["x"], "confidence": 0.68,
                    "prior_art_references": ["KR10-0948760"],
                }
            }
        },
    )

    payload = axis_supervisor_payload(state, axis="legal")

    ctx = payload["prior_art_context"]
    assert ctx["cited_in_evaluation"] == ["KR10-0948760"]
    assert "KR10-0948760" in ctx["available_in_input"]
    assert "KR10-2014-0072547" in ctx["available_in_input"]


def test_non_legal_axis_payload_has_no_prior_art_context():
    from workflow.supervisor import axis_supervisor_payload

    state = PatentWorkflowState(
        valuation_result={"axes": {"market": {"score": 40, "grade": "C", "rationale": "x",
                                              "evidence_ids": [], "risk_factors": ["r"], "confidence": 0.5}}},
    )

    payload = axis_supervisor_payload(state, axis="market")

    assert "prior_art_context" not in payload


def test_business_fit_axis_payload_includes_official_evidence_context():
    from workflow.supervisor import axis_supervisor_payload

    state = PatentWorkflowState(
        evidence_bundle=[
            {
                "evidence_id": "official",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "SK AX 제조 솔루션",
                "url": "https://www.skax.co.kr/manufacturing/example",
                "content": "SK AX 제조 공정 관리 솔루션",
            }
        ],
        valuation_result={
            "axes": {
                "business_fit": {
                    "score": 54,
                    "grade": "C",
                    "rationale": "공식 사업 문맥과 연결",
                    "evidence_ids": ["official"],
                    "risk_factors": ["핵심 기능 직접 매칭 제한"],
                    "missing_information": [],
                    "confidence": 0.6,
                    "subscores": {
                        "official_business_evidence": {"score": 16},
                        "product_function_direct_match": {"score": 20},
                        "business_context_fit": {"score": 18},
                    },
                }
            }
        },
    )

    payload = axis_supervisor_payload(state, axis="business_fit")

    context = payload["business_fit_context"]
    assert context["skax_official_evidence"][0]["evidence_id"] == "official"
    assert context["cited_evidence"][0]["is_sk_ax_official"] is True


def test_business_fit_rule_rejects_general_news_as_direct_evidence():
    from workflow.supervisor import build_rule_axis_supervisor_check

    state = PatentWorkflowState(
        evidence_bundle=[
            {
                "evidence_id": "official",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "url": "https://www.skax.co.kr/manufacturing/example",
                "content": "SK AX 제조 솔루션",
            },
            {
                "evidence_id": "external_news",
                "source": "naver_news",
                "source_type": "news",
                "url": "https://example.com/news",
                "content": "외부 제조 기사",
            },
        ],
        valuation_result={
            "axes": {
                "business_fit": {
                    "score": 54,
                    "grade": "C",
                    "rationale": "외부 기사를 직접 사업 근거로 사용",
                    "evidence_ids": ["external_news"],
                    "risk_factors": ["직접 매칭 제한"],
                    "missing_information": [],
                    "confidence": 0.6,
                    "subscores": {
                        "official_business_evidence": {"score": 24},
                        "product_function_direct_match": {"score": 12},
                        "business_context_fit": {"score": 18},
                    },
                }
            }
        },
    )

    result = build_rule_axis_supervisor_check(state, "business_fit")

    assert result["status"] == "valuation_retry"
    assert any("일반 뉴스/외부 자료" in issue for issue in result["issues"])


def test_business_fit_rule_requests_search_when_sk_evidence_is_absent():
    from workflow.supervisor import build_rule_axis_supervisor_check

    state = PatentWorkflowState(
        evidence_bundle=[
            {
                "evidence_id": "external_news",
                "source": "naver_news",
                "source_type": "news",
                "content": "외부 제조 기사",
            }
        ],
        valuation_result={
            "axes": {
                "business_fit": {
                    "score": 0,
                    "grade": "D",
                    "rationale": "공식 근거 미확인",
                    "evidence_ids": [],
                    "risk_factors": ["공식 근거 미확인"],
                    "missing_information": ["SK AX 공식 페이지"],
                    "confidence": 0.3,
                    "subscores": {
                        "official_business_evidence": {"score": 0},
                        "product_function_direct_match": {"score": 0},
                        "business_context_fit": {"score": 0},
                    },
                }
            }
        },
    )

    result = build_rule_axis_supervisor_check(state, "business_fit")

    assert result["status"] == "query_rewriting"
    assert any("SK AX 공식" in issue for issue in result["issues"])


def test_business_fit_rule_accepts_owned_media_tier_without_text_marker():
    from workflow.supervisor import build_rule_axis_supervisor_check

    state = PatentWorkflowState(
        evidence_bundle=[
            {
                "evidence_id": "owned_without_marker",
                "source": "sk_group_owned_media",
                "source_type": "news",
                "source_domain": "skcareersjournal.com",
                "source_tier": "sk_related_owned_media",
                "content": "다른 SK 계열사의 일반 채용 소식",
            }
        ],
        valuation_result={
            "axes": {
                "business_fit": {
                    "score": 0,
                    "grade": "D",
                    "rationale": "SK AX 사업 근거 미확인",
                    "evidence_ids": [],
                    "risk_factors": ["공식 근거 미확인"],
                    "missing_information": ["SK AX 공식 페이지"],
                    "confidence": 0.3,
                    "subscores": {
                        "official_business_evidence": {"score": 0},
                        "product_function_direct_match": {"score": 0},
                        "business_context_fit": {"score": 0},
                    },
                }
            }
        },
    )

    result = build_rule_axis_supervisor_check(state, "business_fit")

    assert result["status"] == "valuation_retry"
    assert any("evidence_ids" in issue for issue in result["issues"])



def test_axis_supervisor_checks_reuse_prior_for_non_retried_axes(monkeypatch):
    calls = []

    def fake_single(state, axis):
        calls.append(axis)
        return {"axis": axis, "status": "passed", "passed": True, "needs_retry": False,
                "needs_more_evidence": False, "issues": [], "reason": "fresh", "source": "llm", "metadata": {}}

    monkeypatch.setattr("workflow.supervisor.run_single_axis_supervisor_check", fake_single)
    prior = {
        axis: {"axis": axis, "status": "passed", "passed": True, "needs_retry": False,
               "needs_more_evidence": False, "issues": [], "reason": "prior", "source": "llm", "metadata": {}}
        for axis in ["legal", "technology", "market", "business_fit"]
    }
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        valuation_retry_axes=["technology"],
        valuation_result={"axes": _axes_result(), "axis_supervisor_checks": prior},
    )

    checks = run_axis_supervisor_checks(state)

    # Only the re-evaluated axis is re-checked; the rest reuse prior results.
    assert calls == ["technology"]
    assert checks["technology"]["reason"] == "fresh"
    assert checks["legal"]["reason"] == "prior"
    assert checks["market"]["reason"] == "prior"
    assert checks["business_fit"]["reason"] == "prior"


def test_axis_supervisor_checks_full_when_no_retry_axes(monkeypatch):
    calls = []

    def fake_single(state, axis):
        calls.append(axis)
        return {"axis": axis, "status": "passed", "passed": True, "needs_retry": False,
                "needs_more_evidence": False, "issues": [], "reason": "fresh", "source": "llm", "metadata": {}}

    monkeypatch.setattr("workflow.supervisor.run_single_axis_supervisor_check", fake_single)
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": True},
        valuation_result={"axes": _axes_result()},
    )

    run_axis_supervisor_checks(state)

    assert sorted(calls) == ["business_fit", "legal", "market", "technology"]


def test_markdown_headings_captures_all_report_sections():
    from workflow.supervisor import markdown_headings

    md = "\n".join([
        "### 제목", "## 특허 기본 정보",
        "## 1. 한눈에 보는 검토 결과", "## 2. 평가대상 및 범위", "## 3. 판단 근거",
        "## 4. 평가축별 상세 근거", "### 4.1 권리성", "### 4.2 기술성",
        "### 4.3 시장성", "### 4.4 사업 연계성",
        "## 5. 사업부 확인 필요 사항", "## 6. 최종 검토 의견",
    ])
    headings = markdown_headings(md)

    # Sections 5 and 6 must survive (previously truncated at 8 by the 4.x subsections).
    assert any("## 5." in h for h in headings)
    assert any("## 6." in h for h in headings)


def test_final_check_prompt_checks_format_and_content():
    from pathlib import Path

    text = Path("prompts/supervisor/supervisor_final_check.md").read_text(encoding="utf-8")
    # Format: structure/score is judged by the deterministic report_validation.
    assert "report_validation 결과(report_passed/report_issues)로 판단" in text
    # Content: faithfulness to scores + no fabricated facts are now checked.
    assert "axis_scores의 점수·등급과 모순되지 않는가" in text
    assert "입력에 없는 사실을 단정하지 않았는가" in text
