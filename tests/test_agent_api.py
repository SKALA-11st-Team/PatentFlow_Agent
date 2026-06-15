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


def test_valuation_prompt_admin_endpoints_read_and_update_md(monkeypatch, tmp_path):
    prompt_dir = tmp_path / "prompts" / "valuation"
    prompt_dir.mkdir(parents=True)
    prompt_path = prompt_dir / "valuation_legal.md"
    prompt_path.write_text(
        "# Valuation Legal Axis Prompt\n\n권리성 축입니다.\n총점은 100점입니다.\nscore grade confidence missing_information\n",
        encoding="utf-8",
    )
    for name in ("valuation_technology.md", "valuation_market.md", "valuation_business_fit.md"):
        (prompt_dir / name).write_text(
            "# Prompt\n\n기술성 시장성 사업 연계성\n총점은 100점입니다.\nscore grade confidence missing_information\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("app.api.settings.project_root", tmp_path)

    response = client.get("/api/v1/admin/valuation-criteria/prompts/legal")

    assert response.status_code == 200
    body = response.json()
    assert body["axis"] == "legal"
    assert body["path"] == "prompts/valuation/valuation_legal.md"

    updated = body["markdown"] + "\n평가 목적을 보강합니다."
    update_response = client.put(
        "/api/v1/admin/valuation-criteria/prompts/legal",
        json={"markdown": updated, "expectedChecksum": body["checksum"], "reason": "테스트"},
    )

    assert update_response.status_code == 200
    assert "평가 목적을 보강합니다." in prompt_path.read_text(encoding="utf-8")


def test_valuation_prompt_admin_update_rejects_stale_checksum(monkeypatch, tmp_path):
    prompt_dir = tmp_path / "prompts" / "valuation"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "valuation_legal.md").write_text(
        "# Prompt\n\n권리성\n총점은 100점입니다.\nscore grade confidence missing_information\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.settings.project_root", tmp_path)

    response = client.put(
        "/api/v1/admin/valuation-criteria/prompts/legal",
        json={
            "markdown": "# Prompt\n\n권리성\n총점은 100점입니다.\nscore grade confidence missing_information\n",
            "expectedChecksum": "stale",
        },
    )

    assert response.status_code == 409


def test_evaluate_patent_runs_workflow_and_returns_report(monkeypatch):
    def fake_run_workflow(state):
        state.summary_result = {
            "title": "테스트 특허",
            "plain_summary": "테스트 특허 요약",
            "summary_markdown": "# 요약\n\n테스트 특허 요약",
            "summary_brief": {"one_line_summary": "한 줄 요약", "key_components": ["A", "B", "C"]},
        }
        state.valuation_result = {
            "recommendation": "유지 권고",
            "total_score": 280,
            "average_score": 70.0,
            "axes": {
                "legal": {"label": "권리성", "score": 70, "grade": "B", "rationale": "권리성 근거"},
                "technology": {"label": "기술성", "score": 75, "grade": "B", "rationale": "기술성 근거"},
                "market": {"label": "시장성", "score": 65, "grade": "B", "rationale": "시장성 근거"},
                "business_fit": {"label": "사업 연계성", "score": 70, "grade": "B", "rationale": "사업 연계성 근거"},
            },
            "review_checklist": {
                "사업부서 확인사항": ["시장 규모 자료"],
                "법무·특허팀 확인사항": ["해외 패밀리 등록 상태"],
            },
            "final_report_markdown": (
                "# 특허 가치판단 종합 보고서\n\n"
                "## 1. 한눈에 보는 검토 결과\n표\n\n"
                "## 2. 평가대상 및 범위\n범위 본문\n\n"
                "## 3. 판단 근거\n근거 본문\n\n"
                "## 4. 평가축별 상세 근거\n### 4.1 권리성\n권리성 본문\n\n"
                "## 5. 역할별 확인 사항\n### 5.1 사업부 확인 사항\n- 사업부 줄글\n\n"
                "## 6. 최종 검토 의견\n최종 의견 줄글\n"
            ),
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
    assert "finalIndicator" not in body
    assert body["summaryMarkdown"].startswith("# 요약")
    # FE 카드용 구조화 요약본이 그대로 전달된다.
    assert body["summaryBrief"]["one_line_summary"] == "한 줄 요약"
    assert body["valuationReportMarkdown"].startswith("# 특허 가치판단 종합 보고서")
    # 보고서 2~6번 섹션이 헤더 제외 본문으로 분리되어 전달된다(1번은 구조화 필드로 대체).
    assert set(body["reportSections"]) == {
        "evaluationScope",
        "judgmentBasis",
        "axisDetails",
        "roleChecklist",
        "finalOpinion",
    }
    assert body["reportSections"]["evaluationScope"] == "범위 본문"
    assert "4.1 권리성" in body["reportSections"]["axisDetails"]
    assert body["reportSections"]["finalOpinion"] == "최종 의견 줄글"
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


def test_failure_reason_names_missing_search_api_key():
    from app.api import failure_reason

    # 증거 0건 + 검색 키 미설정 신호 → 일반 문구 대신 키 미설정 원인을 명시한다.
    state = PatentWorkflowState(
        evidence_bundle=[],
        query_plan={
            "search_warnings": [
                "global_news_call_failed:query='x': RequestException: TAVILY_API_KEY is not set"
            ],
        },
        valuation_result={"axes": {}},
    )

    reason = failure_reason(state, state.valuation_result)
    assert "외부 검색 API 키" in reason


def test_failure_reason_falls_back_to_generic_empty_evidence():
    from app.api import failure_reason

    # 진단 신호가 없으면 기존 일반 문구를 유지한다(정상적으로 외부 결과가 없는 경우).
    state = PatentWorkflowState(evidence_bundle=[], valuation_result={"axes": {}})

    assert failure_reason(state, state.valuation_result) == (
        "외부 근거 수집 결과가 없어 AI 평가 신뢰도가 낮습니다."
    )


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


def test_recommend_fields_survives_non_numeric_confidence(monkeypatch):
    # LLM이 confidence 자리에 숫자가 아닌 라벨(예: "높음")을 넣어도 float() 예외로 500이
    # 나지 않고, 다른 파싱 실패처럼 graceful하게 0.0으로 폴백해야 한다.
    import json as _json

    from agents import field_recommendation

    monkeypatch.setattr(field_recommendation, "load_prompt", lambda _name: "prompt")
    monkeypatch.setattr(
        field_recommendation,
        "call_llm",
        lambda _prompt: _json.dumps(
            {
                "businessArea": "Data",
                "technologyArea": "데이터분석",
                "confidence": "높음",
                "reason": "분류 근거.",
            }
        ),
    )

    result = field_recommendation.recommend_fields(
        title="강화학습 기반 자산배분 시스템",
        abstract="요약 본문",
        taxonomy={"businessArea": ["Data"], "technologyArea": ["데이터분석"]},
    )

    assert 0.0 <= result["confidence"] <= 1.0
    assert result["confidence"] == 0.0
    assert result["businessArea"] == "Data"
    assert result["technologyArea"] == "데이터분석"


def test_recommend_fields_normalizes_non_string_fields(monkeypatch):
    # LLM이 str 필드(reason 등)에 비-str 값을 주더라도 str로 정규화해 Pydantic 응답
    # 모델(str)에서 500이 나지 않게 한다.
    import json as _json

    from agents import field_recommendation

    monkeypatch.setattr(field_recommendation, "load_prompt", lambda _name: "prompt")
    monkeypatch.setattr(
        field_recommendation,
        "call_llm",
        lambda _prompt: _json.dumps(
            {
                "businessArea": "Data",
                "technologyArea": "데이터분석",
                "confidence": 0.82,
                "reason": 123,
            }
        ),
    )

    result = field_recommendation.recommend_fields(
        title="강화학습 기반 자산배분 시스템",
        abstract="요약 본문",
        taxonomy={"businessArea": ["Data"], "technologyArea": ["데이터분석"]},
    )

    assert isinstance(result["reason"], str)
    assert result["reason"] == "123"
    assert result["confidence"] == 0.82


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


def test_degraded_evaluation_defaults_recommendation_to_review_again(monkeypatch):
    # 평가 미산출/degraded(축 점수 없음)로 recommendation이 비면 '포기 검토'(ABANDON)로 단정하지 않고
    # 중립값 '추가 정보 필요'(BE Recommendation.REVIEW_AGAIN)를 기본값으로 둔다.
    def fake_run_workflow(state):
        state.summary_result = {"plain_summary": "요약"}
        state.evidence_bundle = []
        state.valuation_result = {"axes": {}, "final_report_markdown": "보고서"}
        return state

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", lambda state: {})

    response = client.post("/api/v1/ai/patents/PAT-DEGRADED/evaluate", json={"noSave": True})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["recommendation"] == "추가 정보 필요"


def test_valuation_average_score_fallback_divides_by_three_core_axes():
    from app.api import valuation_average_score

    # average_score가 없고 total_score(3축 합, max 300)만 있으면 핵심 축 수(3)로 나눠 평균을 환산한다.
    assert valuation_average_score({"total_score": 300}) == 100.0
    assert valuation_average_score({"total_score": 210}) == 70.0
    # average_score가 있으면 그대로 사용한다(폴백 미적용).
    assert valuation_average_score({"total_score": 300, "average_score": 75.0}) == 75.0


def test_evaluate_patent_survives_save_outputs_failure(monkeypatch):
    # 영속화 실패가 이미 계산된 평가 응답을 폐기하지 않고, warnings로만 표면화되는지 검증.
    def fake_run_workflow(state):
        state.summary_result = {"plain_summary": "요약"}
        state.valuation_result = {
            "recommendation": "유지 권고",
            "axes": {
                "legal": {"label": "권리성", "score": 70, "grade": "B", "rationale": "r"},
                "technology": {"label": "기술성", "score": 70, "grade": "B", "rationale": "r"},
                "market": {"label": "시장성", "score": 70, "grade": "B", "rationale": "r"},
                "business_fit": {"label": "사업 연계성", "score": 70, "grade": "B", "rationale": "r"},
            },
            "final_report_markdown": "보고서",
        }
        return state

    def fake_save_outputs(state):
        raise OSError("disk full")

    monkeypatch.setattr("app.api.run_workflow", fake_run_workflow)
    monkeypatch.setattr("app.api.save_outputs", fake_save_outputs)

    response = client.post("/api/v1/ai/patents/PAT-SAVE-FAIL/evaluate", json={"title": "테스트"})

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"] == "유지 권고"
    assert any(warning.startswith("artifact_save_failed:") for warning in body["warnings"])


def test_inbound_auth_passes_through_when_key_unset(monkeypatch):
    # SEC-01: AGENT_INBOUND_API_KEY 미설정 시 인바운드 인증 없이 통과(기존 배포 호환).
    monkeypatch.delenv("AGENT_INBOUND_API_KEY", raising=False)
    assert client.get("/health").status_code == 200


def test_inbound_auth_required_when_key_set(monkeypatch):
    # 키 설정 시 보호 경로는 X-API-Key 없으면 401, /health는 면제.
    monkeypatch.setenv("AGENT_INBOUND_API_KEY", "agent-secret")
    assert client.get("/health").status_code == 200
    unauthorized = client.post("/api/v1/ai/patents/PAT-X/evaluate", json={"noSave": True})
    assert unauthorized.status_code == 401
    wrong_key = client.post(
        "/api/v1/ai/patents/PAT-X/evaluate",
        json={"noSave": True},
        headers={"X-API-Key": "wrong"},
    )
    assert wrong_key.status_code == 401
