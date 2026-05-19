from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import select_by_types_or_axes
from workflow.state import PatentWorkflowState


AXIS = "legal"
LABEL = "권리성"
PROMPT_PATH = "valuation/valuation_legal.md"


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = runtime.build_input_payload(axis=AXIS, state=state, evidence=evidence)
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    return runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    return select_by_types_or_axes(
        items,
        source_types={"portfolio_context", "competitor_patent", "patent_api", "prior_art", "citation"},
        axes={AXIS},
    )
