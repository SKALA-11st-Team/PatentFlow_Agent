# Supervisor Team Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the current single supervisor node into explicit Top, Research, Valuation, and Writing supervisors while preserving existing PatentFlow behavior.

**Architecture:** Keep existing research and valuation nodes as the first implementation layer, then add team-aware supervisor decisions around them. `workflow/supervisor.py` owns supervisor checks, `workflow/graph.py` owns routing, and `schemas/supervisor.py` defines the shared decision contract.

**Tech Stack:** Python, Pydantic, LangGraph `StateGraph`, pytest.

---

## File Structure

- Modify `schemas/supervisor.py`: add team/stage literals and optional team routing fields to `SupervisorDecision`.
- Modify `workflow/state.py`: add lightweight workflow routing fields such as `current_team` and `team_status`.
- Modify `workflow/supervisor.py`: split current single supervisor into `top_supervisor_node`, `research_supervisor_node`, `valuation_supervisor_node`, and `writing_supervisor_node`.
- Modify `workflow/graph.py`: replace the single supervisor route with explicit team supervisor nodes and conditional routing functions.
- Modify `workflow/nodes.py`: keep existing nodes, but update `final_merge_node` only if a writing output wrapper is needed.
- Modify `tests/test_nodes.py`: keep current low-level check tests green.
- Modify `tests/test_graph.py`: add routing-level tests with monkeypatched nodes so the graph behavior is testable without LLM/API calls.
- Add or modify `tests/test_supervisor.py`: focused unit tests for top/research/valuation/writing supervisor decisions.

## Task 1: Extend Supervisor Decision Schema

**Files:**
- Modify: `schemas/supervisor.py`
- Test: `tests/test_supervisor.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/test_supervisor.py` if it does not exist:

```python
from schemas.supervisor import SupervisorDecision


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_supervisor.py::test_supervisor_decision_accepts_team_routing_fields -v`

Expected: FAIL because `SupervisorDecision` does not expose the new fields.

- [ ] **Step 3: Implement minimal schema extension**

Update `schemas/supervisor.py`:

```python
from typing import Any, Literal
from pydantic import BaseModel, Field


SupervisorTeam = Literal["top", "research", "valuation", "writing", "final"]

SupervisorStage = Literal[
    "patent_check",
    "summary_check",
    "evidence_check",
    "valuation_check",
    "writing_check",
    "final_check",
]


class SupervisorDecision(BaseModel):
    passed: bool
    next_action: str
    current_team: SupervisorTeam | None = None
    next_team: SupervisorTeam | None = None
    stage: SupervisorStage | None = None
    route_reason: str = ""
    issues: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run schema test**

Run: `pytest tests/test_supervisor.py::test_supervisor_decision_accepts_team_routing_fields -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add schemas/supervisor.py tests/test_supervisor.py
git commit -m "feat: supervisor 팀 라우팅 스키마 추가"
```

## Task 2: Add Routing State Fields

**Files:**
- Modify: `workflow/state.py`
- Test: `tests/test_supervisor.py`

- [ ] **Step 1: Write failing state test**

Append to `tests/test_supervisor.py`:

```python
from workflow.state import PatentWorkflowState


def test_workflow_state_tracks_current_team_and_status():
    state = PatentWorkflowState(current_team="research", team_status={"research": "ready"})

    assert state.current_team == "research"
    assert state.team_status == {"research": "ready"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_supervisor.py::test_workflow_state_tracks_current_team_and_status -v`

Expected: FAIL because `PatentWorkflowState` does not yet define these fields.

- [ ] **Step 3: Add fields to workflow state**

Update `workflow/state.py` near the run control fields:

```python
class PatentWorkflowState(BaseModel):
    # Run control
    user_input: dict[str, Any] = Field(default_factory=dict)
    current_stage: str | None = None
    current_team: str | None = None
    team_status: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
```

- [ ] **Step 4: Run state test**

Run: `pytest tests/test_supervisor.py::test_workflow_state_tracks_current_team_and_status -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workflow/state.py tests/test_supervisor.py
git commit -m "feat: workflow 팀 상태 필드 추가"
```

## Task 3: Split Supervisor Nodes

**Files:**
- Modify: `workflow/supervisor.py`
- Test: `tests/test_supervisor.py`

- [ ] **Step 1: Write supervisor unit tests**

Append to `tests/test_supervisor.py`:

```python
from workflow.supervisor import (
    research_supervisor_node,
    top_supervisor_node,
    valuation_supervisor_node,
    writing_supervisor_node,
)


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


def test_writing_supervisor_routes_complete_documents_to_final():
    state = PatentWorkflowState(
        summary_result={"summary_markdown": "# 특허 요약\n\n본문"},
        valuation_result={"final_report_markdown": "# 특허 가치평가 리포트\n\n본문"},
    )

    result = writing_supervisor_node(state)

    assert result.supervisor_decision["passed"] is True
    assert result.supervisor_decision["next_team"] == "final"
    assert result.supervisor_decision["next_action"] == "final_merge"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_supervisor.py -v`

Expected: FAIL because the new supervisor node functions do not exist.

- [ ] **Step 3: Implement supervisor node split**

In `workflow/supervisor.py`, keep existing check helpers and add these node functions:

```python
@trace(name="top_supervisor_agent", run_type="chain")
def top_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "top"
    previous = state.supervisor_decision or {}
    requested_team = previous.get("next_team")
    requested_action = previous.get("next_action")

    if requested_team in {"research", "valuation", "writing", "final"}:
        next_team = requested_team
        next_action = requested_action or f"{next_team}_team"
    elif not state.patent_structured or not state.preprocessed_patent or not state.summary_result:
        next_team = "research"
        next_action = "research_team"
    elif not state.valuation_result:
        next_team = "valuation"
        next_action = "valuation_team"
    elif not state.final_report:
        next_team = "writing"
        next_action = "writing_team"
    else:
        next_team = "final"
        next_action = "final_merge"

    state.supervisor_decision = SupervisorDecision(
        passed=True,
        current_team="top",
        next_team=next_team,
        next_action=next_action,
        route_reason=f"Top supervisor routed workflow to {next_team}.",
    ).model_dump()
    return state
```

Add research wrapper:

```python
@trace(name="research_supervisor_agent", run_type="chain")
def research_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "research"
    for stage, checker in [
        ("patent_check", check_patent_data),
        ("summary_check", check_summary_result),
        ("evidence_check", check_evidence_bundle),
    ]:
        decision = checker(state)
        if not decision.passed:
            decision.current_team = "research"
            decision.next_team = "research"
            decision.stage = stage
            state.supervisor_decision = decision.model_dump()
            state.missing_evidence = decision.missing_evidence
            return state

    state.supervisor_decision = SupervisorDecision(
        passed=True,
        current_team="research",
        next_team="valuation",
        stage="evidence_check",
        next_action="valuation_team",
        reason="Research outputs are ready for valuation.",
    ).model_dump()
    return state
```

Add valuation wrapper:

```python
@trace(name="valuation_supervisor_agent", run_type="chain")
def valuation_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "valuation"
    decision = check_valuation_result(state)
    has_unknown_evidence = any("references unknown evidence_id" in issue for issue in decision.issues)
    decision.current_team = "valuation"
    decision.stage = "valuation_check"
    if decision.passed:
        decision.next_team = "writing"
        decision.next_action = "writing_team"
    elif has_unknown_evidence:
        decision.next_team = "research"
        decision.next_action = "research_team"
    else:
        decision.next_team = "valuation"
        decision.next_action = "valuation_team"
    state.supervisor_decision = decision.model_dump()
    return state
```

Add writing check and wrapper:

```python
def check_writing_result(state: PatentWorkflowState) -> SupervisorDecision:
    issues = []
    summary_markdown = (state.summary_result or {}).get("summary_markdown")
    final_report_markdown = (state.valuation_result or {}).get("final_report_markdown")
    if not summary_markdown:
        issues.append("Missing summary_markdown")
    if not final_report_markdown:
        issues.append("Missing final_report_markdown")
    return SupervisorDecision(
        passed=not issues,
        current_team="writing",
        next_team="final" if not issues else "writing",
        stage="writing_check",
        next_action="final_merge" if not issues else "writing_team",
        issues=issues,
        reason="Writing output check completed.",
    )


@trace(name="writing_supervisor_agent", run_type="chain")
def writing_supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_team = "writing"
    state.supervisor_decision = check_writing_result(state).model_dump()
    return state
```

Keep `supervisor_node` as a compatibility wrapper:

```python
@trace(name="supervisor_agent", run_type="chain")
def supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    stage = state.current_stage or "patent_check"
    if stage in {"patent_check", "summary_check", "evidence_check"}:
        return research_supervisor_node(state)
    if stage == "valuation_check":
        return valuation_supervisor_node(state)
    if stage == "final_check":
        return writing_supervisor_node(state)
    return top_supervisor_node(state)
```

- [ ] **Step 4: Run supervisor tests**

Run: `pytest tests/test_supervisor.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workflow/supervisor.py tests/test_supervisor.py
git commit -m "feat: supervisor 역할별 노드 분리"
```

## Task 4: Refactor Graph Routing

**Files:**
- Modify: `workflow/graph.py`
- Test: `tests/test_graph.py`

- [ ] **Step 1: Write graph routing tests with monkeypatching**

Append to `tests/test_graph.py`:

```python
from workflow import graph as workflow_graph


def test_route_after_top_supervisor_uses_next_team():
    state = PatentWorkflowState(supervisor_decision={"next_team": "research", "next_action": "research_team"})

    assert workflow_graph._route_after_top_supervisor(state.model_dump()) == "research_team"


def test_route_after_valuation_supervisor_can_return_to_research():
    state = PatentWorkflowState(supervisor_decision={"next_team": "research", "next_action": "research_team"})

    assert workflow_graph._route_after_valuation_supervisor(state.model_dump()) == "research_team"


def test_route_after_writing_supervisor_finishes_at_final_merge():
    state = PatentWorkflowState(supervisor_decision={"next_team": "final", "next_action": "final_merge"})

    assert workflow_graph._route_after_writing_supervisor(state.model_dump()) == "final_merge"
```

- [ ] **Step 2: Run routing tests to verify they fail**

Run: `pytest tests/test_graph.py::test_route_after_top_supervisor_uses_next_team tests/test_graph.py::test_route_after_valuation_supervisor_can_return_to_research tests/test_graph.py::test_route_after_writing_supervisor_finishes_at_final_merge -v`

Expected: FAIL because the route helper functions do not exist.

- [ ] **Step 3: Update graph imports and routing helpers**

In `workflow/graph.py`, import split supervisors:

```python
from workflow.supervisor import (
    research_supervisor_node,
    top_supervisor_node,
    valuation_supervisor_node,
    writing_supervisor_node,
)
```

Add route helpers:

```python
def _route_after_top_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"research_team", "valuation_team", "writing_team", "final_merge"}:
        return action
    return "end"


def _route_after_research_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"patent_fetch", "common_preprocess", "summary", "query_rewriting"}:
        if action == "query_rewriting" and state.retry_count >= settings.max_evidence_search_rounds:
            return "top_supervisor"
        return action
    if action == "valuation_team":
        return "top_supervisor"
    return "end"


def _route_after_valuation_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"research_team", "valuation_team", "writing_team"}:
        return action
    return "end"


def _route_after_writing_supervisor(payload: dict[str, Any]) -> str:
    state = _as_state(payload)
    action = (state.supervisor_decision or {}).get("next_action")
    if action in {"writing_team", "final_merge"}:
        return action
    return "end"
```

- [ ] **Step 4: Update graph nodes and edges**

In `_build_graph()`, add supervisor nodes:

```python
graph.add_node("top_supervisor", lambda payload: _run_node(payload, top_supervisor_node))
graph.add_node("research_supervisor", lambda payload: _run_node(payload, research_supervisor_node))
graph.add_node("valuation_supervisor", lambda payload: _run_node(payload, valuation_supervisor_node))
graph.add_node("writing_supervisor", lambda payload: _run_node(payload, writing_supervisor_node))
```

Use existing nodes as the first research/valuation/writing team implementation:

```python
graph.add_edge(START, "top_supervisor")
graph.add_edge("patent_resolve", "patent_fetch")
graph.add_edge("patent_fetch", "portfolio_sibling")
graph.add_edge("portfolio_sibling", "common_preprocess")
graph.add_edge("common_preprocess", "summary")
graph.add_edge("summary", "query_rewriting")
graph.add_edge("query_rewriting", "evidence_search")
graph.add_edge("evidence_search", "evidence_compression")
graph.add_edge("evidence_compression", "research_supervisor")
graph.add_edge("valuation", "valuation_supervisor")
graph.add_edge("validation", "writing_supervisor")
graph.add_edge("final_merge", END)
```

Add conditional edges:

```python
graph.add_conditional_edges(
    "top_supervisor",
    _route_after_top_supervisor,
    {
        "research_team": "patent_resolve",
        "valuation_team": "valuation",
        "writing_team": "validation",
        "final_merge": "final_merge",
        "end": END,
    },
)
graph.add_conditional_edges(
    "research_supervisor",
    _route_after_research_supervisor,
    {
        "patent_fetch": "patent_fetch",
        "common_preprocess": "common_preprocess",
        "summary": "summary",
        "query_rewriting": "query_rewriting",
        "top_supervisor": "top_supervisor",
        "end": END,
    },
)
graph.add_conditional_edges(
    "valuation_supervisor",
    _route_after_valuation_supervisor,
    {
        "research_team": "patent_resolve",
        "valuation_team": "valuation",
        "writing_team": "validation",
        "end": END,
    },
)
graph.add_conditional_edges(
    "writing_supervisor",
    _route_after_writing_supervisor,
    {
        "writing_team": "validation",
        "final_merge": "final_merge",
        "end": END,
    },
)
```

- [ ] **Step 5: Run graph routing tests**

Run: `pytest tests/test_graph.py -v`

Expected: PASS. If `test_run_workflow_returns_state` now exercises LLM/API paths, monkeypatch the graph nodes in that test or narrow the assertion to helper routing.

- [ ] **Step 6: Commit**

```bash
git add workflow/graph.py tests/test_graph.py
git commit -m "feat: supervisor 팀 기반 그래프 라우팅 적용"
```

## Task 5: Preserve Existing Node and Valuation Tests

**Files:**
- Modify only if tests reveal a real compatibility issue:
  - `workflow/nodes.py`
  - `workflow/supervisor.py`
  - `tests/test_nodes.py`
  - `tests/test_valuation.py`

- [ ] **Step 1: Run focused existing tests**

Run:

```bash
pytest tests/test_nodes.py tests/test_valuation.py tests/test_agent_api.py -v
```

Expected: PASS or failures directly caused by the new supervisor decision shape.

- [ ] **Step 2: Fix only compatibility failures**

If tests expect `next_action == "validation"` from `check_valuation_result`, keep `check_valuation_result` unchanged and do team routing only in `valuation_supervisor_node`.

If tests expect `supervisor_node` to exist, keep the compatibility wrapper from Task 3.

- [ ] **Step 3: Re-run focused tests**

Run:

```bash
pytest tests/test_nodes.py tests/test_valuation.py tests/test_agent_api.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit if code changed**

```bash
git add workflow/nodes.py workflow/supervisor.py tests/test_nodes.py tests/test_valuation.py tests/test_agent_api.py
git commit -m "fix: supervisor 라우팅 기존 테스트 호환성 보완"
```

Skip the commit if no files changed.

## Task 6: End-to-End Verification

**Files:**
- No planned code changes.

- [ ] **Step 1: Run supervisor and graph tests**

Run:

```bash
pytest tests/test_supervisor.py tests/test_graph.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
pytest -v
```

Expected: PASS. If external API tests require credentials or network, record the exact skipped or failed tests and run the maximal local subset.

- [ ] **Step 3: Inspect git status**

Run:

```bash
git status --short
```

Expected: clean working tree after commits, or only intentional unstaged files the user asked to leave.

## Self-Review

- Spec coverage: covered updated axes, top supervisor routing, research checks, valuation checks, writing checks, graph shape, and routing tests.
- Placeholder scan: the plan uses concrete file paths, commands, and code snippets for each implementation step.
- Type consistency: `SupervisorDecision.current_team`, `SupervisorDecision.next_team`, `SupervisorDecision.stage`, and `PatentWorkflowState.current_team` are introduced before use in supervisor and graph tasks.
