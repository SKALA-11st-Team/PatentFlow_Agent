from __future__ import annotations

from typing import Any

from agents.valuation_axes.common import normalize_text
from workflow.state import PatentWorkflowState


AXIS = "business_fit"
LABEL = "사업 연계성"
PROMPT_PATH = "valuation/valuation_business_fit.md"


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = runtime.build_input_payload(axis=AXIS, state=state, evidence=evidence)
    payload["business_fit_scoring_rubric"] = business_fit_scoring_rubric()
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    return runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    keywords = business_fit_keywords(state)
    direct_matches = []
    secondary_matches = []
    for item in items:
        source_type = item.get("source_type")
        if source_type in {"company_disclosure", "portfolio_context"}:
            secondary_matches.append(item)
        if source_type != "news":
            continue
        text = evidence_text(item)
        if any(keyword and keyword in text for keyword in keywords):
            direct_matches.append(item)
        else:
            secondary_matches.append(item)
    return [*direct_matches, *secondary_matches][:5]


def business_fit_keywords(state: PatentWorkflowState) -> list[str]:
    patent = state.patent_structured or {}
    metadata = ((state.kipris_api_data or {}).get("metadata") or {})
    raw_keywords: list[Any] = [
        patent.get("title_final"),
        patent.get("title_draft"),
        patent.get("related_product"),
        patent.get("technology_area"),
        patent.get("business_area"),
        patent.get("joint_applicant_name"),
        *(metadata.get("assignee") or []),
        *(metadata.get("assignee_eng") or []),
    ]
    company_context = patent.get("company_context") or state.user_input.get("company_context") or {}
    if isinstance(company_context, dict):
        raw_keywords.extend([company_context.get("company_name"), company_context.get("product_name")])
    return [normalize_text(keyword) for keyword in raw_keywords if normalize_text(keyword)]


def business_fit_scoring_rubric() -> dict[str, Any]:
    return {
        "total_score": 100,
        "components": {
            "business_connection": 40,
            "portfolio_necessity": 35,
            "practical_applicability": 15,
            "evidence_reliability": 10,
        },
        "rationale_instruction": (
            "Do not add sub_scores or any new output field. "
            "Explain the four component scores and reasoning inside the existing rationale field."
        ),
    }


def evidence_text(item: dict[str, Any]) -> str:
    values = [
        item.get("title"),
        item.get("compressed_summary"),
        item.get("content"),
        item.get("context"),
        " ".join(str(fact) for fact in item.get("key_facts", [])),
    ]
    return " ".join(normalize_text(value) for value in values if normalize_text(value))
