from workflow.nodes import final_merge_node
from workflow.supervisor import check_evidence_bundle
from workflow.state import PatentWorkflowState


def test_final_merge_node_sets_final_report():
    state = PatentWorkflowState(summary_result={}, valuation_result={})
    result = final_merge_node(state)
    assert result.final_report is not None


def test_evidence_check_accepts_rag_context_field():
    state = PatentWorkflowState(
        evidence_bundle=[
            {"evidence_id": "rag_001", "source": "industry_report.pdf", "context": "산업 보고서 청크"},
            {"evidence_id": "news_001", "source": "naver_news", "content": "뉴스 본문"},
            {"evidence_id": "dart_001", "source": "dart", "content": "공시 본문"},
        ]
    )

    decision = check_evidence_bundle(state)

    assert decision.passed is True

