import pytest
from fastapi.testclient import TestClient

from app.api import app
from services.observability import progress_registry
from workflow.state import PatentWorkflowState
import workflow.graph as graph_module


client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_progress_registry():
    # FR-006: 레지스트리는 모듈 전역이므로 테스트 간 상태가 누적되지 않도록 초기화한다.
    progress_registry._PROGRESS.clear()
    yield
    progress_registry._PROGRESS.clear()


def test_set_stage_records_stage_label_and_updated_at():
    progress_registry.set_stage("patent-1", "EVIDENCE_COLLECTION")

    entry = progress_registry.get("patent-1")

    assert entry is not None
    assert entry["stage"] == "EVIDENCE_COLLECTION"
    assert entry["stageLabel"] == "근거 수집"
    # updatedAt은 ISO8601(UTC) 문자열이어야 한다.
    assert entry["updatedAt"].endswith("+00:00")


def test_set_stage_ignores_unknown_stage_and_empty_patent_id():
    progress_registry.set_stage("patent-1", "NOT_A_STAGE")
    progress_registry.set_stage("", "PREPARING")
    progress_registry.set_stage(None, "PREPARING")

    assert progress_registry.get("patent-1") is None
    assert progress_registry.get("") is None


def test_clear_removes_entry():
    progress_registry.set_stage("patent-1", "DONE")
    progress_registry.clear("patent-1")

    assert progress_registry.get("patent-1") is None


def test_stage_labels_cover_workflow_stages_in_order():
    assert list(progress_registry.STAGE_LABELS) == [
        "PREPARING",
        "EVIDENCE_COLLECTION",
        "EVIDENCE_COMPRESSION",
        "VALUATION",
        "WRITING",
        "VALIDATION",
        "DONE",
    ]


def test_progress_endpoint_returns_registered_stage():
    progress_registry.set_stage("42", "VALUATION")

    response = client.get("/api/v1/ai/patents/42/evaluate/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["patentId"] == "42"
    assert body["stage"] == "VALUATION"
    assert body["stageLabel"] == "4축 평가"
    assert body["updatedAt"]


def test_progress_endpoint_returns_404_when_absent():
    response = client.get("/api/v1/ai/patents/unknown/evaluate/progress")

    assert response.status_code == 404
    assert response.json() == {"detail": "no progress"}


def test_run_workflow_marks_done_on_success(monkeypatch):
    class FakeGraph:
        def invoke(self, payload, config=None):
            return payload

    monkeypatch.setattr(graph_module, "WORKFLOW_GRAPH", FakeGraph())
    state = PatentWorkflowState(user_input={"progress_patent_id": "42"})

    graph_module.run_workflow(state)

    entry = progress_registry.get("42")
    assert entry is not None
    assert entry["stage"] == "DONE"


def test_run_workflow_clears_progress_on_failure(monkeypatch):
    class FailingGraph:
        def invoke(self, payload, config=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(graph_module, "WORKFLOW_GRAPH", FailingGraph())
    progress_registry.set_stage("42", "WRITING")
    state = PatentWorkflowState(user_input={"progress_patent_id": "42"})

    with pytest.raises(RuntimeError):
        graph_module.run_workflow(state)

    assert progress_registry.get("42") is None
