# Supervisor Team Routing Design

## Context

PatentFlow will move from a single stage-checking supervisor into a team-based supervisor workflow. The target shape is:

```text
Top Supervisor
→ Research Team
→ Research Supervisor
→ Valuation Team
→ Valuation Supervisor
→ Writing Team
→ Writing Supervisor
→ Final Output
```

The current code already has these useful building blocks:

- `workflow/graph.py`: LangGraph workflow with a single `supervisor` node.
- `workflow/supervisor.py`: rule-based checks for patent, summary, evidence, valuation, and final readiness.
- `agents/summary.py`: patent summary generation.
- `agents/valuation.py`: four-axis valuation loop.
- `prompts/supervisor/*`: stage-specific supervisor prompts.
- `schemas/supervisor.py`: supervisor decision schema.

The current implementation does not yet clearly separate top-level routing from team-level quality control.

## Updated Evaluation Axes

The current team decision is that PatentFlow scoring uses these four axes:

- 권리성
- 기술성
- 시장성
- 사업 연계성

Code, prompts, schemas, tests, and shared docs should align to `business_fit` / `사업 연계성`.

## Supervisor Responsibilities

### Top Supervisor

The Top Supervisor decides which team owns the next step. It should not perform detailed quality review.

Inputs:

- `current_team`
- `team_status`
- `research_result`
- `valuation_result`
- `writing_result`
- latest team supervisor decision

Outputs:

- `next_team`: `research`, `valuation`, `writing`, or `final`
- `next_action`: concrete graph node or team entry node
- `reason`

Primary decisions:

- Send incomplete patent understanding or evidence work to Research.
- Send sufficient research output to Valuation.
- Send reliable valuation output to Writing.
- Send approved writing output to Final.
- Route back from Valuation to Research when valuation cannot proceed because evidence is insufficient.

### Research Supervisor

The Research Supervisor validates whether the patent understanding and evidence package are ready for valuation.

Owns checks for:

- Patent metadata and source availability.
- Preprocessed patent content.
- Research summary for internal downstream use.
- External evidence count and relevance.
- Portfolio evidence and industry RAG evidence.
- Missing evidence categories.

Possible next actions:

- `patent_fetch`
- `common_preprocess`
- `summary`
- `query_rewriting`
- `evidence_search`
- `evidence_compression`
- `valuation_team`

### Valuation Supervisor

The Valuation Supervisor validates the quality and consistency of axis-level evaluation.

Owns checks for:

- All four axes exist: `legal`, `technology`, `market`, `business_fit`.
- Each axis has score, grade, rationale, evidence ids, risk factors, missing information, and confidence.
- Evidence ids referenced by valuation outputs exist in `evidence_bundle`.
- High scores have enough supporting evidence.
- `business_fit` is supported by product, business area, company context, or portfolio evidence.
- Valuation does not present AI output as final recorded decision.

Possible next actions:

- `valuation_team`
- `research_team`
- `writing_team`

If evidence is missing, route to Research. If evidence is enough but the reasoning or structure is poor, route back to Valuation.

### Writing Supervisor

The Writing Supervisor validates user-facing documents.

Owns checks for:

- Final patent summary exists and is readable.
- Final valuation report exists.
- AI patent evaluation report, final decision, business opinion, and evaluation evidence are clearly separated.
- Recommendation labels use the approved labels: `유지 권고`, `포기 검토`, `추가 정보 필요`.
- Missing information uses approved copy such as `정보 부족 있음`, `추가 확인 필요`, or `N/A` only when source data is missing, insufficient, or not applicable.
- The document does not imply that AI made the final legal or business decision.

Possible next actions:

- `writing_team`
- `final`

## Proposed Graph Shape

```text
START
→ top_supervisor
→ research_team
→ research_supervisor
→ top_supervisor
→ valuation_team
→ valuation_supervisor
→ top_supervisor
→ writing_team
→ writing_supervisor
→ top_supervisor
→ final_merge
→ END
```

Team nodes may initially wrap existing nodes instead of rewriting all agents at once.

## Incremental Implementation Plan

1. Update shared domain references so `business_fit` is official and `lifecycle_economics` is not used as a current scoring axis.
2. Extend supervisor schemas with team-level routing fields while keeping backward compatibility with existing `next_action`.
3. Split `workflow/supervisor.py` into clear functions for:
   - top routing
   - research checks
   - valuation checks
   - writing checks
4. Refactor `workflow/graph.py` to expose explicit supervisor nodes:
   - `top_supervisor`
   - `research_supervisor`
   - `valuation_supervisor`
   - `writing_supervisor`
5. Keep existing research nodes initially:
   - `patent_resolve`
   - `patent_fetch`
   - `portfolio_sibling`
   - `common_preprocess`
   - `summary`
   - `query_rewriting`
   - `evidence_search`
   - `evidence_compression`
6. Keep existing valuation execution initially, but make the supervisor validate axis-level outputs explicitly.
7. Introduce writing team wrappers after the routing layer is stable.
8. Add focused tests for routing outcomes:
   - insufficient research evidence loops to Research.
   - valuation structure errors loop to Valuation.
   - valuation evidence gaps loop to Research.
   - approved writing routes to final output.

## Related FR/UI

- `FR-005`: 특허 내용 요약 생성
- `FR-006`: AI 기반 특허 가치 재평가 수행
- `FR-007`: 평가 근거 제공
- `FR-008`: 종합 권고안 생성
- `FR-011`: AI 특허 평가 레포트와 최종 판단 분리 조회/수정
- `UI-005`: 특허상세
- `UI-009`: 레포트

## Open Integration Notes

- The existing `docs/` reference set mentioned in `AGENTS.md` is not present in this repository checkout. This spec becomes the local managed reference for the supervisor restructuring until those docs are restored or added.
- Current code already uses `business_fit`; the main mismatch is the older shared guide text and any remaining references to lifecycle economics.
- The first implementation should preserve existing API behavior unless a route requires a new explicit status field.
