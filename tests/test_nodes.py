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


def test_patent_fetch_uses_foreign_rights_data_for_non_kr_patent(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.get_patent",
        lambda **kwargs: {
            "id": 45,
            "management_number": "P202012001-US0",
            "country": "US",
            "application_number": "18/020,829",
            "registration_number": "12,417,849",
            "status": "등록",
        },
    )

    def fail_domestic_fetch(application_number):
        raise AssertionError("domestic KIPRIS fetch should not be used for foreign patents")

    monkeypatch.setattr("workflow.nodes.fetch_kipris_bibliography", fail_domestic_fetch)
    monkeypatch.setattr(
        "workflow.nodes.fetch_foreign_patent_rights_data",
        lambda patent, **kwargs: {
            "source_type": "kipris_foreign_patent",
            "metadata": {"country": "US", "application_number": patent["application_number"]},
            "claim_stats": {"active_claim_count": 1},
            "family_patents": [],
            "citation_evidence": {},
            "citation_stats": {"total_count": 0},
            "citing_stats": {"total_count": 0},
        },
    )

    state = PatentWorkflowState(user_input={"management_number": "P202012001-US0", "collect_kipris_api": True})

    result = patent_fetch_node(state)

    assert result.kipris_api_data["source_type"] == "kipris_foreign_patent"
    assert result.kipris_api_data["metadata"]["country"] == "US"
    assert result.patent_structured["kipris_api"]["metadata"]["country"] == "US"


def test_query_rewriting_node_stores_industry_rag_queries(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.rewrite_search_queries",
        lambda **kwargs: {
            "ko": ["AI 투자 서비스"],
            "en": ["ai investing"],
            "industry_rag": ["웰스테크 AI 에이전트 디지털 자문"],
            "skax_site": ["site:skax.co.kr 로보어드바이저 금융"],
            "meta": {"rewrite_source": "llm"},
        },
    )

    state = PatentWorkflowState(preprocessed_patent={"metadata": {}, "sections": {}})

    result = query_rewriting_node(state)

    assert result.query_plan["industry_rag_queries"] == ["웰스테크 AI 에이전트 디지털 자문"]
    assert result.query_plan["skax_site_queries"] == ["site:skax.co.kr 로보어드바이저 금융"]
    assert "웰스테크 AI 에이전트 디지털 자문" in result.search_queries
    assert "site:skax.co.kr 로보어드바이저 금융" in result.search_queries


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
    captured_kwargs = {}

    def fake_collect_skax(patent_context, **kwargs):
        captured_context.update(patent_context)
        captured_kwargs.update(kwargs)
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
        query_plan={"skax_site_queries": ["site:skax.co.kr 로보어드바이저 금융"]},
    )

    result = evidence_search_node(state)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["news_001", "skax_001"]
    assert captured_context["management_number"] == "P202405001-KR0"
    assert captured_context["title_final"] == "상품 트렌드 예측을 반영한 자산배분 시스템"
    assert captured_context["related_product"] == "로보어드바이저"
    assert captured_kwargs["queries_override"] == ["site:skax.co.kr 로보어드바이저 금융"]
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

    def raise_skax_error(patent_context, **kwargs):
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


def test_evidence_compression_preserves_skax_official_evidence(monkeypatch):
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
            "stats": {"compressed_count": 1, "input_count": len(items)},
        },
    )
    skax_content = "SK AX 공식 금융 서비스 근거 본문"
    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={"id": 1, "related_product": "로보어드바이저"},
        preprocessed_patent={"metadata": {}, "sections": {}},
        evidence_bundle=[
            {"evidence_id": "raw_news", "source_type": "news", "content": "뉴스"},
            {
                "evidence_id": "skax_site_001",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "SK AX 금융 서비스",
                "url": "https://www.skax.co.kr/finance/service",
                "content": skax_content,
                "related_axes": ["business_fit"],
            },
        ],
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

    by_id = {item["evidence_id"]: item for item in result.evidence_bundle}
    assert list(by_id) == ["news_001", "skax_site_001", "portfolio_001"]
    assert by_id["skax_site_001"]["content"] == skax_content
    assert by_id["skax_site_001"]["title"] == "SK AX 금융 서비스"
    assert by_id["skax_site_001"]["url"] == "https://www.skax.co.kr/finance/service"
    assert by_id["skax_site_001"]["source"] == "sk_ax_official"
    assert by_id["skax_site_001"]["source_type"] == "company_disclosure"
    assert by_id["skax_site_001"]["related_axes"] == ["business_fit"]
    assert result.query_plan["compressed_evidence"]["stats"]["skax_official_evidence_count"] == 1


def test_evidence_compression_deduplicates_preserved_skax_official_evidence(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.compress_evidence_items",
        lambda items, **kwargs: {
            "items": [
                {
                    "evidence_id": "compressed_skax_duplicate",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "title": "SK AX 금융 서비스",
                    "url": "https://www.skax.co.kr/finance/service",
                    "compressed_summary": "압축 중복",
                }
            ],
            "warnings": [],
            "stats": {"compressed_count": 1},
        },
    )
    state = PatentWorkflowState(
        user_input={"no_save": True},
        preprocessed_patent={"metadata": {}, "sections": {}},
        evidence_bundle=[
            {
                "evidence_id": "skax_site_001",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "SK AX 금융 서비스",
                "url": "https://www.skax.co.kr/finance/service",
                "content": "원문 보존",
                "related_axes": ["business_fit"],
            }
        ],
        portfolio_evidence=[
            {
                "evidence_id": "portfolio_dup",
                "source": "sk_ax_official",
                "source_type": "portfolio_context",
                "title": "SK AX 금융 서비스",
                "url": "https://www.skax.co.kr/finance/service",
            }
        ],
    )

    result = evidence_compression_node(state)

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["compressed_skax_duplicate"]


def test_evidence_compression_preserved_skax_reaches_business_fit_input(monkeypatch):
    from agents.valuation_axes.business_fit import build_input_payload

    monkeypatch.setattr(
        "workflow.nodes.compress_evidence_items",
        lambda items, **kwargs: {"items": [], "warnings": [], "stats": {"compressed_count": 0}},
    )
    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={"title_final": "자산배분 특허", "related_product": "로보어드바이저"},
        preprocessed_patent={"metadata": {}, "sections": {}},
        evidence_bundle=[
            {
                "evidence_id": "skax_site_001",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "SK AX 금융 서비스",
                "url": "https://www.skax.co.kr/finance/service",
                "content": "로보어드바이저와 금융 서비스 공식 근거",
                "related_axes": ["business_fit"],
            }
        ],
    )

    compressed_state = evidence_compression_node(state)
    payload = build_input_payload(state=compressed_state, evidence=compressed_state.evidence_bundle)

    official = payload["business_fit_context"]["skax_official_evidence"]
    assert len(official) == 1
    assert official[0]["evidence_id"] == "skax_site_001"
    assert official[0]["content_excerpt"] == "로보어드바이저와 금융 서비스 공식 근거"


def test_evidence_compression_failure_still_preserves_skax_official_evidence(monkeypatch):
    def raise_compression_error(items, **kwargs):
        raise RuntimeError("compression failed")

    monkeypatch.setattr("workflow.nodes.compress_evidence_items", raise_compression_error)
    state = PatentWorkflowState(
        user_input={"no_save": True},
        preprocessed_patent={"metadata": {}, "sections": {}},
        evidence_bundle=[
            {
                "evidence_id": "skax_site_001",
                "source": "sk_ax_official",
                "source_type": "company_disclosure",
                "title": "SK AX 금융 서비스",
                "url": "https://www.skax.co.kr/finance/service",
                "content": "공식 근거",
                "related_axes": ["business_fit"],
            },
            {"evidence_id": "raw_news", "source_type": "news", "content": "뉴스"},
        ],
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

    assert [item["evidence_id"] for item in result.evidence_bundle] == ["skax_site_001", "portfolio_001"]
    assert result.query_plan["compressed_evidence"]["warnings"][0].startswith("evidence_compression_failed:RuntimeError")


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
        lambda patent_context, **kwargs: skax_result
        or {
            "items": [],
            "queries": [],
            "stats": {"collected_evidence_count": 0},
            "failed_urls": [],
        },
    )
