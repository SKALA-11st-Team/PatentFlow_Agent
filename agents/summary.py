from services.observability.langsmith_service import trace
from workflow.state import PatentWorkflowState


@trace(name="summary_agent", run_type="chain")
def run_summary_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    patent = state.preprocessed_patent or {}
    metadata = patent.get("metadata") or {}
    sections = patent.get("sections") or {}
    title = metadata.get("title") or metadata.get("title_eng") or "Untitled patent"
    abstract = sections.get("abstract") or ""
    claim_count = (patent.get("claim_stats") or {}).get("active_claim_count") or len(patent.get("claims") or [])

    plain_summary = abstract or f"{title} 관련 특허입니다."
    state.summary_result = {
        "title": title,
        "plain_summary": plain_summary,
        "key_points": [
            f"특허명: {title}",
            f"활성 청구항 수: {claim_count}"],
        "notes": [],
    }
    state.current_stage = "summary_check"
    return state
