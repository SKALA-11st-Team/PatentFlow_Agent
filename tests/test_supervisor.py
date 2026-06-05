from schemas.supervisor import SupervisorDecision
from workflow.state import PatentWorkflowState
from workflow.supervisor import (
    build_supervisor_judge_prompt,
    research_supervisor_node,
    run_axis_supervisor_checks,
    supervisor_node,
    top_supervisor_node,
    valuation_supervisor_payload,
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
        assert "최종 라우팅은 `supervisor_valuation_check.md`에서 수행합니다" in text


def test_valuation_supervisor_references_axis_quality_criteria():
    from pathlib import Path

    text = Path("prompts/supervisor/supervisor_valuation_check.md").read_text(encoding="utf-8")

    assert "`supervisor_legal_check.md`: 권리성 평가 기준" in text
    assert "`supervisor_technology_check.md`: 기술성 평가 기준" in text
    assert "`supervisor_market_check.md`: 시장성 평가 기준" in text
    assert "`supervisor_business_fit_check.md`: 사업 연계성 평가 기준" in text
    assert "축별 기준서가 별도 `next_action`을 결정한다고 가정하지 마세요." in text


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
    assert result.supervisor_decision["next_action"] == "query_rewriting"


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
                    "risk_factors": [],
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
    assert result.supervisor_decision["next_team"] == "research"
    assert result.supervisor_decision["next_action"] == "query_rewriting"


def test_run_axis_supervisor_checks_defaults_to_passed_without_llm():
    state = PatentWorkflowState(
        user_input={"use_llm_supervisor": False},
        evidence_bundle=[{"evidence_id": "known", "source": "naver", "source_type": "news", "content": "본문"}],
        valuation_result={
            "axes": {
                axis: {
                    "score": 70,
                    "grade": "B",
                    "rationale": "known 근거 기반 평가",
                    "evidence_ids": ["known"],
                    "risk_factors": [],
                    "missing_information": [],
                    "confidence": 0.7,
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            }
        },
    )

    checks = run_axis_supervisor_checks(state)

    assert set(checks) == {"legal", "technology", "market", "business_fit"}
    assert all(item["status"] == "passed" for item in checks.values())
    assert state.valuation_result["axis_supervisor_checks"] == checks


def test_valuation_supervisor_payload_includes_axis_supervisor_checks():
    checks = {
        "legal": {"status": "passed", "issues": [], "reason": "권리성 정상"},
        "technology": {"status": "valuation_retry", "issues": ["기술성 재검토"], "reason": "근거 보완"},
    }
    state = PatentWorkflowState(
        valuation_result={
            "axis_supervisor_checks": checks,
            "axes": {
                axis: {
                    "score": 70,
                    "grade": "B",
                    "rationale": "known 근거 기반 평가",
                    "evidence_ids": [],
                    "risk_factors": [],
                    "missing_information": [],
                }
                for axis in ["legal", "technology", "market", "business_fit"]
            },
        },
    )

    payload = valuation_supervisor_payload(state)

    assert payload["valuation"]["axis_supervisor_checks"] == checks


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
    monkeypatch.setattr(
        "workflow.supervisor.call_llm",
        lambda prompt, **kwargs: (
            '{"passed": false, "next_action": "final_report", '
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
    assert result.supervisor_decision["next_action"] == "final_report"


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


def test_summary_supervisor_prompt_excludes_full_preprocessed_text():
    state = PatentWorkflowState(
        patent_structured={"title_final": "요약 대상 특허", "application_number": "10-2024-0000001"},
        preprocessed_patent={
            "cleaned_markdown": "SECRET_FULL_PATENT_MARKDOWN",
            "sections": {"description": "SECRET_FULL_DESCRIPTION"},
            "validation": {"is_valid": True, "warnings": ["확인 필요"]},
        },
        summary_result={
            "title": "요약 대상 특허",
            "plain_summary": "특허와 직접 관련된 요약입니다.",
            "key_points": ["핵심 기술"],
            "summary_markdown": "# 특허 요약\n\nSECRET_FULL_SUMMARY_MARKDOWN",
        },
    )

    prompt = build_supervisor_judge_prompt(state, prompt_name="supervisor/supervisor_summary_check.md")

    assert "SECRET_FULL_PATENT_MARKDOWN" not in prompt
    assert "SECRET_FULL_DESCRIPTION" not in prompt
    assert "SECRET_FULL_SUMMARY_MARKDOWN" not in prompt
    assert "특허와 직접 관련된 요약입니다." in prompt
    assert "summary_markdown_length" in prompt


def test_valuation_supervisor_prompt_uses_evidence_previews_not_raw_content():
    state = PatentWorkflowState(
        patent_structured={"title_final": "평가 대상 특허"},
        evidence_bundle=[
            {
                "evidence_id": "known",
                "source": "industry_report",
                "source_type": "industry_report",
                "title": "산업 근거",
                "content": "SECRET_RAW_EVIDENCE_CONTENT",
                "context": "SECRET_RAW_EVIDENCE_CONTEXT",
                "compressed_summary": "시장 성장 근거 요약",
                "related_axes": ["market", "business_fit"],
            }
        ],
        valuation_result={
            "axes": {
                "legal": {
                    "score": 70,
                    "grade": "B",
                    "rationale": "known 근거 기반 권리성 평가",
                    "evidence_ids": ["known"],
                    "risk_factors": [],
                    "confidence": 0.7,
                },
                "technology": {
                    "score": 75,
                    "grade": "B+",
                    "rationale": "known 근거 기반 기술성 평가",
                    "evidence_ids": ["known"],
                    "risk_factors": [],
                    "confidence": 0.7,
                },
                "market": {
                    "score": 80,
                    "grade": "A",
                    "rationale": "known 근거 기반 시장성 평가",
                    "evidence_ids": ["known"],
                    "risk_factors": [],
                    "confidence": 0.8,
                },
                "business_fit": {
                    "score": 65,
                    "grade": "B",
                    "rationale": "unknown_ref 근거를 잘못 참조한 평가",
                    "evidence_ids": ["unknown_ref"],
                    "risk_factors": [],
                    "confidence": 0.6,
                },
            },
            "final_report_markdown": "# 가치평가 리포트\n\nSECRET_FULL_FINAL_REPORT",
        },
    )

    prompt = build_supervisor_judge_prompt(state, prompt_name="supervisor/supervisor_valuation_check.md")

    assert "SECRET_RAW_EVIDENCE_CONTENT" not in prompt
    assert "SECRET_RAW_EVIDENCE_CONTEXT" not in prompt
    assert "SECRET_FULL_FINAL_REPORT" not in prompt
    assert "시장 성장 근거 요약" in prompt
    assert "unknown_evidence_ids" in prompt
    assert "unknown_ref" in prompt


def test_final_supervisor_prompt_includes_headings_not_full_markdown():
    state = PatentWorkflowState(
        summary_result={
            "plain_summary": "최종 문서에 들어갈 요약입니다.",
            "summary_markdown": "# 특허 요약\n\nSECRET_SUMMARY_BODY",
        },
        valuation_result={
            "axes": {axis: {"score": 70} for axis in ["legal", "technology", "market", "business_fit"]},
            "total_score": 70,
            "recommendation": "추가 정보 필요",
            "final_report_markdown": "# 가치평가 리포트\n\n## 시장성\n\nSECRET_REPORT_BODY",
        },
        validation_result={"passed": True, "issues": []},
    )

    prompt = build_supervisor_judge_prompt(state, prompt_name="supervisor/supervisor_final_check.md")

    assert "SECRET_SUMMARY_BODY" not in prompt
    assert "SECRET_REPORT_BODY" not in prompt
    assert "# 특허 요약" in prompt
    assert "## 시장성" in prompt
    assert "최종 문서에 들어갈 요약입니다." in prompt


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
    assert result.valuation_retry_axes == []
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
