from services.observability.langsmith_service import trace
from workflow.state import PatentWorkflowState


@trace(name="valuation_agent", run_type="chain")
def run_valuation_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    evidence_ids = [item.get("evidence_id") for item in state.evidence_bundle if item.get("evidence_id")]
    fallback_evidence_ids = evidence_ids[:2] if evidence_ids else []

    def axis_payload(name: str, score: int) -> dict:
        return {
            "score": score,
            "grade": "B",
            "rationale": f"{name} 축에 대한 초기 rule-based 평가 결과입니다.",
            "evidence_ids": fallback_evidence_ids,
            "risk_factors": ["추가 정밀 분석 필요"],
            "confidence": 0.6,
        }

    state.valuation_result = {
        "legal": axis_payload("권리성", 72),
        "technology": axis_payload("기술성", 76),
        "market": axis_payload("시장성", 70),
        "economic": axis_payload("라이프사이클 경제성", 68),
        "strategy": axis_payload("전략 적합성", 73),
    }
    state.current_stage = "valuation_check"
    return state
