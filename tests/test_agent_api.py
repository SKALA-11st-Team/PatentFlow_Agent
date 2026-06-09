from fastapi.testclient import TestClient

from workflow.state import PatentWorkflowState
from app.api import app


client = TestClient(app)


def test_root_and_health_endpoints():
    root_response = client.get("/")
    assert root_response.status_code == 200
    assert root_response.json()["service"] == "PatentFlow Agent API"

    health_response = client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "UP"}


def test_evaluate_patent_runs_workflow_and_returns_report(monkeypatch):
    def fake_run_workflow(state):
        state.summary_result = {
            "title": "테스트 특허",
            "plain_summary": "테스트 특허 요약",
            "summary_markdown": "# 요약\n\n테스트 특허 요약",
        }
        state.valuation_result = {
            "recommendation": "유지 권고",
            "total_score": 280,
            "average_score": 70.0,
            "final_indicator": "조건부 유지",
            "axes": {
                "legal": {"label": "권리성", "score": 70, "grade": "B", "rationale": "권리성 근거"},
                "technology": {"label": "기술성", "score": 75, "grade": "B", "rationale": "기술성 근거"},
                "market": {"label": "시장성", "score": 65, "grade": "B", "rationale": "시장성 근거"},
                "business_fit": {"label": "사업 연계성", "score": 70, "grade": "B", "rationale": "사업 연계성 근거"},
            },
            "final_report_markdown": "# 특허 가치판단 종합 보고서\n\n본문",
        }
        state.final_report = {
            "summary": state.summary_result,
            "valuation": state.valuation_result,
            "evidence": [],
        }
        return state

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})

    response = client.post(
        "/api/v1/ai/patents/PAT-TEST/evaluate",
        json={"managementNumber": "P202405001-KR0", "title": "테스트 특허"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["patentId"] == "PAT-TEST"
    assert body["recommendation"] == "유지 권고"
    assert "summary" not in body
    assert len(body["scores"]) == 4
    assert body["scores"][3]["category"] == "사업 연계성"
    assert body["scores"][0]["grade"] == "B"
    assert body["totalScore"] == 280
    assert body["averageScore"] == 70.0
    assert body["finalGrade"] == "B"
    assert body["finalIndicator"] == "조건부 유지"
    assert body["summaryMarkdown"].startswith("# 요약")
    assert body["valuationReportMarkdown"].startswith("# 특허 가치판단 종합 보고서")
    assert "rawMarkdown" not in body


def test_degraded_reflects_technology_comparison_warning():
    from app.api import failure_reason, is_degraded, workflow_warnings

    state = PatentWorkflowState(
        evidence_bundle=[{"evidence_id": "e1"}],
        valuation_result={
            "warnings": ["technology_comparison_empty"],
            "axes": {
                "legal": {"label": "권리성", "score": 70, "grade": "B", "rationale": "r"},
                "technology": {"label": "기술성", "score": 70, "grade": "B", "rationale": "r"},
                "market": {"label": "시장성", "score": 70, "grade": "B", "rationale": "r"},
                "business_fit": {"label": "사업 연계성", "score": 70, "grade": "B", "rationale": "r"},
            },
        },
    )

    assert "technology_comparison_empty" in workflow_warnings(state)
    assert is_degraded(state, state.valuation_result) is True
    assert failure_reason(state, state.valuation_result).startswith("기술성 비교군")


def test_evaluate_patent_builds_patent_id_input(monkeypatch):
    captured = {}

    def fake_run_workflow(state: PatentWorkflowState):
        captured.update(state.user_input)
        state.summary_result = {"plain_summary": "요약"}
        state.valuation_result = {"axes": {}, "final_report_markdown": "보고서"}
        return state

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})

    response = client.post("/api/v1/ai/patents/1/evaluate", json={"noSave": True})

    assert response.status_code == 200
    assert captured["patent_id"] == 1
    assert captured["collect_pdf"] is True
    assert captured["collect_kipris_api"] is True
    assert captured["use_llm_supervisor"] is True
    assert captured["no_save"] is True


def test_evaluate_patent_does_not_use_shared_db_fallback_by_default(monkeypatch):
    captured = {}

    def fake_run_workflow(state: PatentWorkflowState):
        captured.update(state.user_input)
        state.summary_result = {"plain_summary": "요약"}
        state.valuation_result = {"axes": {}, "final_report_markdown": "보고서"}
        return state

    def fake_get_patent_identifiers(patent_id):
        raise AssertionError("shared DB fallback should be disabled by default")

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})
    monkeypatch.setattr("app.api.get_patent_identifiers", fake_get_patent_identifiers)
    monkeypatch.setattr("app.api.settings.enable_shared_db_fallback", False)

    response = client.post("/api/v1/ai/patents/1/evaluate", json={"noSave": True})

    assert response.status_code == 200
    assert captured["patent_id"] == 1


def test_evaluate_patent_can_use_shared_db_fallback_when_enabled(monkeypatch):
    captured = {}

    def fake_run_workflow(state: PatentWorkflowState):
        captured.update(state.user_input)
        state.summary_result = {"plain_summary": "요약"}
        state.valuation_result = {"axes": {}, "final_report_markdown": "보고서"}
        return state

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})
    monkeypatch.setattr("app.api.settings.enable_shared_db_fallback", True)
    monkeypatch.setattr(
        "app.api.get_patent_identifiers",
        lambda patent_id: {
            "management_number": "P202405001-KR0",
            "application_number": "10-2024-0115774",
            "registration_number": "10-2932891",
        },
    )

    response = client.post("/api/v1/ai/patents/123/evaluate", json={"noSave": True})

    assert response.status_code == 200
    assert captured["management_number"] == "P202405001-KR0"
    assert "patent_id" not in captured


def test_evaluate_patent_can_disable_llm_supervisor(monkeypatch):
    captured = {}

    def fake_run_workflow(state: PatentWorkflowState):
        captured.update(state.user_input)
        state.summary_result = {"plain_summary": "요약"}
        state.valuation_result = {"axes": {}, "final_report_markdown": "보고서"}
        return state

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})

    response = client.post(
        "/api/v1/ai/patents/P202405001-KR0/evaluate",
        json={"noSave": True, "useLlmSupervisor": False},
    )

    assert response.status_code == 200
    assert captured["management_number"] == "P202405001-KR0"
    assert captured["use_llm_supervisor"] is False


def test_evaluate_patent_ignores_swagger_placeholder_identifiers(monkeypatch):
    captured = {}

    def fake_run_workflow(state: PatentWorkflowState):
        captured.update(state.user_input)
        state.summary_result = {"plain_summary": "요약"}
        state.valuation_result = {"axes": {}, "final_report_markdown": "보고서"}
        return state

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})

    response = client.post(
        "/api/v1/ai/patents/P202405001-KR0/evaluate",
        json={
            "managementNumber": "string",
            "applicationNumber": "string",
            "registrationNumber": "string",
            "title": "string",
            "useLlmSupervisor": False,
        },
    )

    assert response.status_code == 200
    assert captured["management_number"] == "P202405001-KR0"
    assert "application_number" not in captured
    assert "registration_number" not in captured


def test_recommend_fields_endpoint_returns_business_and_technology_only(monkeypatch):
    monkeypatch.setattr(
        "app.api.recommend_fields",
        lambda **kwargs: {
            "businessArea": "Data",
            "technologyArea": "데이터분석",
            "confidence": 0.82,
            "confidenceText": "높음",
            "reason": "특허명과 기술 내용이 데이터 분석 분류와 가장 가깝습니다.",
        },
    )

    response = client.post(
        "/api/v1/ai/patents/PAT-TEST/recommend-fields",
        json={
            "title": "강화학습 기반 자산배분 시스템",
            "businessArea": "",
            "technologyArea": "",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "businessArea": "Data",
        "technologyArea": "데이터분석",
        "confidence": 0.82,
        "confidenceText": "높음",
        "reason": "특허명과 기술 내용이 데이터 분석 분류와 가장 가깝습니다.",
    }
    assert "productName" not in body


def test_evaluate_patent_passes_through_rich_evidence(monkeypatch):
    # ORCH-06/AIREPORT-02: 워크플로가 산출한 축별 출처·리포트 레벨 리치 근거가 API로 풀스루되는지 검증.
    def fake_run_workflow(state):
        state.summary_result = {"summary_markdown": "# 요약"}
        state.evidence_bundle = [
            {
                "evidence_id": "ev-news-1",
                "source_type": "news",
                "source": "한국경제",
                "title": "AI 반도체 시장 급성장",
                "url": "https://example.com/news/1",
            },
            {
                "evidence_id": "ev-ind-1",
                "source_type": "industry_report",
                "source": "KIET",
                "title": "반도체 산업 전망",
                "url": None,
            },
        ]
        state.valuation_result = {
            "recommendation": "유지 권고",
            "total_score": 300,
            "average_score": 75.0,
            "final_grade": "B",
            "final_indicator": "유지 권고",
            "axes": {
                "legal": {"label": "권리성", "score": 72, "grade": "B", "rationale": "권리성 근거",
                          "evidence_ids": []},
                "technology": {"label": "기술성", "score": 80, "grade": "A", "rationale": "기술성 근거",
                               "evidence_ids": ["ev-news-1"]},
                "market": {"label": "시장성", "score": 70, "grade": "B", "rationale": "시장성 근거",
                           "evidence_ids": ["ev-news-1", "ev-ind-1"]},
                "business_fit": {"label": "사업 연계성", "score": 78, "grade": "B", "rationale": "사업 연계성 근거",
                                 "evidence_ids": []},
            },
            "decision_rationale": ["합산 300/400, 평균 75/100, 등급 B.", "기술성이 가장 강하다."],
            "required_actions": ["사업부 적용 계획 확인"],
            "missing_information": ["상용화 매출 실적"],
            "final_report_markdown": "# 보고서",
        }
        state.final_report = {"valuation": state.valuation_result, "evidence": []}
        return state

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})

    response = client.post("/api/v1/ai/patents/PAT-RICH/evaluate", json={"noSave": True})
    assert response.status_code == 200
    body = response.json()

    # 리포트 레벨 리치 근거
    assert body["missingInformation"] == ["상용화 매출 실적"]
    assert body["judgementGrounds"] == ["합산 300/400, 평균 75/100, 등급 B.", "기술성이 가장 강하다."]
    assert body["businessCheckRequests"] == ["사업부 적용 계획 확인"]
    # keyEvidence = 최고 점수 축(기술성 80)의 근거
    assert body["keyEvidence"] == "기술성: 기술성 근거"
    # externalSources = 사용된 근거의 제목/URL(중복 제거)
    titles = {src["title"] for src in body["externalSources"]}
    assert "AI 반도체 시장 급성장" in titles
    assert "반도체 산업 전망" in titles
    # 축별 evidenceDetails(클릭형 출처)
    market_axis = next(s for s in body["scores"] if s["category"] == "시장성")
    assert len(market_axis["evidenceDetails"]) == 2
    news_detail = next(d for d in market_axis["evidenceDetails"] if d["text"] == "AI 반도체 시장 급성장")
    assert news_detail["source"]["url"] == "https://example.com/news/1"
    legal_axis = next(s for s in body["scores"] if s["category"] == "권리성")
    assert legal_axis["evidenceDetails"] == []
