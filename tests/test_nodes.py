from workflow.nodes import evidence_compression_node, evidence_search_node, final_merge_node, patent_fetch_node, query_rewriting_node
from workflow.supervisor import check_evidence_bundle
from workflow.state import PatentWorkflowState


def test_final_merge_node_sets_final_report():
    state = PatentWorkflowState(summary_result={}, valuation_result={})
    result = final_merge_node(state)
    assert result.final_report is not None


def test_evidence_check_accepts_rag_context_field():
    state = PatentWorkflowState(
        evidence_bundle=[
            {"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스 본문"},
            {"evidence_id": "news_002", "source": "naver_news", "source_type": "news", "content": "뉴스 본문"},
            {"evidence_id": "news_003", "source": "gnews", "source_type": "news", "content": "뉴스 본문"},
            {"evidence_id": "rag_001", "source": "industry_report.pdf", "source_type": "industry_report", "context": "산업 보고서 청크"},
        ]
    )

    decision = check_evidence_bundle(state)

    assert decision.passed is True


def test_evidence_check_requires_at_least_three_news_items():
    state = PatentWorkflowState(
        evidence_bundle=[
            {"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스 본문"},
            {"evidence_id": "rag_001", "source": "industry_report.pdf", "source_type": "industry_report", "context": "산업 보고서 청크"},
            {"evidence_id": "dart_001", "source": "dart", "source_type": "company_disclosure", "content": "공시 본문"},
        ]
    )

    decision = check_evidence_bundle(state)

    assert decision.passed is False
    assert decision.next_action == "query_rewriting"
    assert "minimum_news_count" in decision.missing_evidence


def test_patent_fetch_continues_when_kipris_pdf_is_missing(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.get_patent",
        lambda **kwargs: {
            "id": 45,
            "application_number": "18020829",
            "status": "등록",
        },
    )
    monkeypatch.setattr(
        "workflow.nodes.fetch_kipris_bibliography",
        lambda application_number: {
            "source_type": "kipris_bibliography_detail",
            "metadata": {"application_number": application_number},
            "claim_stats": {},
            "family_patents": [{"country_code": "US", "registration_number": "1234567"}],
            "citation_evidence": {
                "kr_citation_documents": [{"application_number": "1020200012345"}],
                "kr_citing_documents": [],
                "foreign_citation_documents": [],
                "foreign_claim_lookup_candidates": [],
                "warnings": [],
            },
            "citation_stats": {"total_count": 1},
            "citing_stats": {"total_count": 0},
        },
    )

    def fake_download_and_parse_patent_pdf(*args, **kwargs):
        raise RuntimeError("Could not find KIPRIS fulltext PDF path")

    monkeypatch.setattr("workflow.nodes.download_and_parse_patent_pdf", fake_download_and_parse_patent_pdf)

    state = PatentWorkflowState(user_input={"patent_id": 45, "collect_kipris_api": True, "collect_pdf": True})

    result = patent_fetch_node(state)

    assert result.kipris_api_data is not None
    assert result.kipris_family_patents == [{"country_code": "US", "registration_number": "1234567"}]
    assert result.citation_evidence["kr_citation_documents"][0]["application_number"] == "1020200012345"
    assert result.patent_structured["kipris_api"]["citation_stats"] == {"total_count": 1}
    assert result.parsed_pdf is None
    assert result.patent_structured["pdf"]["warning"].startswith("pdf_fetch_failed:RuntimeError")


def test_query_rewriting_node_stores_industry_rag_queries(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.rewrite_search_queries",
        lambda **kwargs: {
            "ko": ["AI 투자 서비스"],
            "en": ["ai investing"],
            "industry_rag": ["웰스테크 AI 에이전트 디지털 자문"],
            "meta": {"rewrite_source": "llm"},
        },
    )

    state = PatentWorkflowState(preprocessed_patent={"metadata": {}, "sections": {}})

    result = query_rewriting_node(state)

    assert result.query_plan["industry_rag_queries"] == ["웰스테크 AI 에이전트 디지털 자문"]
    assert "웰스테크 AI 에이전트 디지털 자문" in result.search_queries


def test_evidence_search_node_appends_skax_official_evidence(monkeypatch):
    configure_evidence_search_mocks(
        monkeypatch,
        external_items=[{"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스"}],
        news_kept=[{"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스"}],
        skax_result={
            "items": [
                {
                    "evidence_id": "skax_001",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "url": "https://www.skax.co.kr/finance/service",
                    "title": "금융 서비스",
                }
            ],
            "queries": ["site:skax.co.kr 로보어드바이저 금융"],
            "stats": {"collected_evidence_count": 1},
            "failed_urls": [],
        },
    )
    captured_context = {}

    def fake_collect_skax(patent_context):
        captured_context.update(patent_context)
        return {
            "items": [
                {
                    "evidence_id": "skax_001",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "url": "https://www.skax.co.kr/finance/service",
                    "title": "금융 서비스",
                }
            ],
            "queries": ["site:skax.co.kr 로보어드바이저 금융"],
            "stats": {"collected_evidence_count": 1},
            "failed_urls": [],
        }

    monkeypatch.setattr("workflow.nodes.collect_skax_site_evidence", fake_collect_skax)
    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={
            "id": 1,
            "관리번호": "P202405001-KR0",
            "발명의 명칭(최종)": "상품 트렌드 예측을 반영한 자산배분 시스템",
            "관련 사업 분야": "Data",
            "관련 기술 분야": "데이터분석",
            "관련제품": "로보어드바이저",
        },
        preprocessed_patent={"metadata": {}, "sections": {}},
    )

    result = evidence_search_node(state)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["news_001", "skax_001"]
    assert captured_context["management_number"] == "P202405001-KR0"
    assert captured_context["title_final"] == "상품 트렌드 예측을 반영한 자산배분 시스템"
    assert captured_context["related_product"] == "로보어드바이저"
    assert result.query_plan["skax_site_search"]["item_count"] == 1
    assert result.query_plan["skax_site_search"]["queries"] == ["site:skax.co.kr 로보어드바이저 금융"]


def test_evidence_search_node_keeps_existing_evidence_when_skax_empty(monkeypatch):
    configure_evidence_search_mocks(
        monkeypatch,
        external_items=[{"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스"}],
        news_kept=[{"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스"}],
        skax_result={"items": [], "queries": [], "stats": {"collected_evidence_count": 0}, "failed_urls": []},
    )
    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={"title_final": "자산배분 시스템", "related_product": "로보어드바이저"},
        preprocessed_patent={"metadata": {}, "sections": {}},
    )

    result = evidence_search_node(state)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["news_001"]
    assert result.query_plan["skax_site_search"]["item_count"] == 0


def test_evidence_search_node_keeps_existing_evidence_when_skax_fails(monkeypatch):
    configure_evidence_search_mocks(
        monkeypatch,
        external_items=[{"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스"}],
        news_kept=[{"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "뉴스"}],
    )

    def raise_skax_error(patent_context):
        raise RuntimeError("network blocked")

    monkeypatch.setattr("workflow.nodes.collect_skax_site_evidence", raise_skax_error)
    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={"title_final": "자산배분 시스템", "related_product": "로보어드바이저"},
        preprocessed_patent={"metadata": {}, "sections": {}},
    )

    result = evidence_search_node(state)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["news_001"]
    assert result.query_plan["skax_site_search"]["warning"].startswith("skax_site_search_failed:RuntimeError")


def test_evidence_search_node_deduplicates_skax_official_evidence(monkeypatch):
    configure_evidence_search_mocks(
        monkeypatch,
        external_items=[
            {
                "evidence_id": "existing_001",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "url": "https://www.skax.co.kr/finance/service",
                "title": "금융 서비스",
            }
        ],
        news_kept=[],
        skax_result={
            "items": [
                {
                    "evidence_id": "skax_duplicate_url",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "url": "https://www.skax.co.kr/finance/service",
                    "title": "금융 서비스 상세",
                },
                {
                    "evidence_id": "existing_001",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "url": "https://www.skax.co.kr/finance/other",
                    "title": "다른 금융 서비스",
                },
                {
                    "evidence_id": "skax_002",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "url": "https://www.skax.co.kr/finance/new",
                    "title": "신규 금융 서비스",
                },
            ],
            "queries": ["site:skax.co.kr 금융"],
            "stats": {"collected_evidence_count": 3},
            "failed_urls": [],
        },
    )
    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={"title_final": "자산배분 시스템", "related_product": "로보어드바이저"},
        preprocessed_patent={"metadata": {}, "sections": {}},
    )

    result = evidence_search_node(state)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["existing_001", "skax_002"]


def test_evidence_compression_merges_portfolio_evidence(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.compress_evidence_items",
        lambda items, **kwargs: {
            "items": [
                {
                    "evidence_id": "news_001",
                    "source": "naver_news",
                    "source_type": "news",
                    "compressed_summary": "뉴스 요약",
                }
            ],
            "warnings": [],
            "stats": {"compressed_count": 1},
        },
    )

    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={"id": 1},
        preprocessed_patent={"metadata": {}, "sections": {}},
        evidence_bundle=[{"evidence_id": "raw_001", "source_type": "news", "content": "뉴스"}],
        portfolio_evidence=[
            {
                "evidence_id": "portfolio_001",
                "source": "kipris_api",
                "source_type": "portfolio_context",
                "compressed_summary": "포트폴리오 요약",
            }
        ],
    )

    result = evidence_compression_node(state)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["news_001", "portfolio_001"]
    assert result.query_plan["compressed_evidence"]["stats"]["portfolio_evidence_count"] == 1


def configure_evidence_search_mocks(
    monkeypatch,
    *,
    external_items=None,
    news_kept=None,
    industry_items=None,
    skax_result=None,
):
    monkeypatch.setattr(
        "workflow.nodes.collect_external_evidence",
        lambda **kwargs: {
            "items": external_items or [],
            "queries": ["외부 검색어"],
            "gnews_queries": [],
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "workflow.nodes.filter_news_safely",
        lambda **kwargs: {
            "kept": news_kept or [],
            "rejected": [],
            "stats": {"kept_count": len(news_kept or [])},
            "output_path": None,
            "warning": None,
        },
    )
    monkeypatch.setattr(
        "workflow.nodes.search_industry_rag_safely",
        lambda **kwargs: {
            "query": None,
            "queries": [],
            "items": industry_items or [],
            "output_path": None,
            "warning": None,
        },
    )
    monkeypatch.setattr("workflow.nodes.save_filtered_evidence_safely", lambda **kwargs: None)
    monkeypatch.setattr(
        "workflow.nodes.collect_skax_site_evidence",
        lambda patent_context: skax_result
        or {
            "items": [],
            "queries": [],
            "stats": {"collected_evidence_count": 0},
            "failed_urls": [],
        },
    )
