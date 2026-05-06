from services.observability.langsmith_service import trace
from services.llm.prompt_service import load_prompt
from schemas.supervisor import SupervisorDecision
from workflow.state import PatentWorkflowState


STAGE_PROMPTS = {
    "patent_check": "supervisor_patent_check.md",
    "summary_check": "supervisor_summary_check.md",
    "evidence_check": "supervisor_evidence_check.md",
    "valuation_check": "supervisor_valuation_check.md",
    "final_check": "supervisor_final_check.md",
}


def decide_next_step(state: PatentWorkflowState) -> str:
    if state.supervisor_decision:
        return state.supervisor_decision.get("next_action", "validation")
    if state.validation_result and state.validation_result.get("passed"):
        return "final_merge"
    if state.validation_result and state.validation_result.get("needs_more_evidence"):
        return "query_rewriting"
    if state.valuation_result is None:
        return "valuation"
    return "validation"


@trace(name="supervisor_agent", run_type="chain")
def supervisor_node(state: PatentWorkflowState) -> PatentWorkflowState:
    stage = state.current_stage or "patent_check"
    prompt_name = STAGE_PROMPTS.get(stage, "supervisor_final_check.md")
    prompt = load_prompt(prompt_name)

    rule_decision = run_rule_based_check(state, stage)
    if not rule_decision.passed:
        state.supervisor_decision = rule_decision.model_dump()
        state.missing_evidence = rule_decision.missing_evidence
        return state

    # TODO: Pass `prompt` and state into the LLM supervisor when llm_service is implemented.
    state.supervisor_decision = SupervisorDecision(
        passed=True,
        next_action=rule_decision.next_action,
        reason=f"Rule-based checks passed. Loaded prompt: {prompt_name}",
        metadata={"prompt_preview": prompt[:120]},
    ).model_dump()
    return state


def run_rule_based_check(state: PatentWorkflowState, stage: str) -> SupervisorDecision:
    if stage == "patent_check":
        return check_patent_data(state)
    if stage == "summary_check":
        return check_summary_result(state)
    if stage == "evidence_check":
        return check_evidence_bundle(state)
    if stage == "valuation_check":
        return check_valuation_result(state)
    return check_final_ready(state)


def check_patent_data(state: PatentWorkflowState) -> SupervisorDecision:
    patent = state.patent_structured or {}
    required_fields = [
        "id",
        "application_number",
        "registration_number",
        "title_final",
        "status",
        "application_date",
        "registration_date",
    ]
    missing = [field for field in required_fields if not patent.get(field)]
    preprocessed = state.preprocessed_patent
    issues = [f"Missing patent field: {field}" for field in missing]
    if preprocessed:
        validation = preprocessed.get("validation", {})
        warnings = list(validation.get("missing_fields", [])) + list(validation.get("warnings", []))
    else:
        warnings = []
    return SupervisorDecision(
        passed=not issues,
        next_action="summary" if preprocessed and not missing else "common_preprocess" if not missing else "patent_fetch",
        issues=issues,
        reason="Patent metadata check completed.",
        metadata={"warnings": warnings},
    )


def check_summary_result(state: PatentWorkflowState) -> SupervisorDecision:
    summary = state.summary_result or {}
    required_fields = ["title", "plain_summary", "key_points"]
    missing = [field for field in required_fields if not summary.get(field)]
    return SupervisorDecision(
        passed=not missing,
        next_action="evidence_check" if not missing else "summary",
        issues=[f"Missing summary field: {field}" for field in missing],
        reason="Summary structure check completed.",
    )


def check_evidence_bundle(state: PatentWorkflowState) -> SupervisorDecision:
    evidence_bundle = state.evidence_bundle
    issues: list[str] = []
    missing_evidence: list[str] = []
    if len(evidence_bundle) < 3:
        missing_evidence.append("minimum_evidence_count")
    required_fields = ["evidence_id", "source"]
    for index, evidence in enumerate(evidence_bundle):
        for field in required_fields:
            if not evidence.get(field):
                issues.append(f"Evidence #{index + 1} missing {field}")
        if not evidence.get("content") and not evidence.get("context") and not evidence.get("compressed_summary"):
            issues.append(f"Evidence #{index + 1} missing content/context/compressed_summary")

    passed = not issues and not missing_evidence
    return SupervisorDecision(
        passed=passed,
        next_action="valuation" if passed else "query_rewriting",
        issues=issues,
        missing_evidence=missing_evidence,
        reason="Evidence bundle structure check completed.",
    )


def check_valuation_result(state: PatentWorkflowState) -> SupervisorDecision:
    valuation = state.valuation_result or {}
    evidence_ids = {evidence.get("evidence_id") for evidence in state.evidence_bundle}
    required_axes = ["legal", "technology", "market", "economic", "strategy"]
    issues: list[str] = []

    for axis in required_axes:
        axis_result = valuation.get(axis)
        if not axis_result:
            issues.append(f"Missing valuation axis: {axis}")
            continue
        for field in ["score", "grade", "rationale", "evidence_ids", "risk_factors", "confidence"]:
            if axis_result.get(field) in (None, "", []):
                issues.append(f"{axis} missing {field}")
        for evidence_id in axis_result.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                issues.append(f"{axis} references unknown evidence_id: {evidence_id}")

    passed = not issues
    return SupervisorDecision(
        passed=passed,
        next_action="validation" if passed else "valuation_retry",
        issues=issues,
        reason="Valuation structure and evidence-id check completed.",
    )


def check_final_ready(state: PatentWorkflowState) -> SupervisorDecision:
    issues = []
    if not state.summary_result:
        issues.append("Missing summary_result")
    if not state.valuation_result:
        issues.append("Missing valuation_result")
    if not state.validation_result or not state.validation_result.get("passed"):
        issues.append("Validation has not passed")
    return SupervisorDecision(
        passed=not issues,
        next_action="final_merge" if not issues else "supervisor",
        issues=issues,
        reason="Final readiness check completed.",
    )
