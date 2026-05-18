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
            "axes": {
                "legal": {"label": "권리성", "score": 70, "rationale": "권리성 근거"},
                "technology": {"label": "기술성", "score": 75, "rationale": "기술성 근거"},
                "market": {"label": "시장성", "score": 65, "rationale": "시장성 근거"},
                "business_fit": {"label": "사업 연계성", "score": 70, "rationale": "사업 연계성 근거"},
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
    assert body["summary"] == "테스트 특허 요약"
    assert len(body["scores"]) == 4
    assert body["scores"][3]["category"] == "사업 연계성"
    assert body["totalScore"] == 280
    assert body["summaryMarkdown"].startswith("# 요약")
    assert body["valuationReportMarkdown"].startswith("# 특허 가치판단 종합 보고서")
    assert body["rawMarkdown"].startswith("# 특허 가치판단 종합 보고서")


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
