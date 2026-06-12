import pytest

from workflow.nodes import (
    common_preprocess_node,
    evidence_compression_node,
    evidence_search_node,
    final_merge_node,
    patent_fetch_node,
    prior_art_fulltext_node,
    query_rewriting_node,
)
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
            {"evidence_id": "disclosure_001", "source": "sk_group_owned_media", "source_type": "company_disclosure", "content": "공시 본문"},
        ]
    )

    decision = check_evidence_bundle(state)

    assert decision.passed is False
    assert decision.next_action == "query_rewriting"
    assert "minimum_news_count" in decision.missing_evidence


def test_evidence_check_fails_when_all_news_irrelevant():
    irrelevant = {
        "source": "naver_news",
        "source_type": "news",
        "content": "본문",
        "metadata": {"quality_warning": "no_patent_keyword_match", "matched_keyword_count": 0},
    }
    state = PatentWorkflowState(
        evidence_bundle=[
            {**irrelevant, "evidence_id": "news_001"},
            {**irrelevant, "evidence_id": "news_002"},
            {**irrelevant, "evidence_id": "news_003"},
        ]
    )

    decision = check_evidence_bundle(state)

    assert decision.passed is False
    assert decision.next_action == "query_rewriting"
    assert "insufficient_relevant_evidence" in decision.missing_evidence
    assert decision.metadata["relevant_evidence_count"] == 0


def test_evidence_check_passes_with_relevant_signals():
    state = PatentWorkflowState(
        evidence_bundle=[
            {"evidence_id": "news_001", "source": "naver_news", "source_type": "news", "content": "본문",
             "metadata": {"matched_keyword_count": 2}},
            {"evidence_id": "news_002", "source": "gnews", "source_type": "news", "content": "본문",
             "related_axes": ["market"]},
            {"evidence_id": "news_003", "source": "naver_news", "source_type": "news", "content": "본문",
             "metadata": {"quality_warning": "weak_patent_keyword_match"}},
            {"evidence_id": "skax_site_1", "source": "sk_ax_official", "source_type": "industry_report", "content": "본문"},
        ]
    )

    decision = check_evidence_bundle(state)

    assert decision.passed is True
    assert decision.metadata["relevant_evidence_count"] == 4


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
    captured_kwargs = {}

    def fake_foreign_fetch(patent, **kwargs):
        captured_kwargs.update(kwargs)
        return {
            "source_type": "kipris_foreign_patent",
            "metadata": {"country": "US", "application_number": patent["application_number"]},
            "claim_stats": {"active_claim_count": 1},
            "family_patents": [],
            "citation_evidence": {},
            "citation_stats": {"total_count": 0},
            "citing_stats": {"total_count": 0},
        }

    monkeypatch.setattr("workflow.nodes.fetch_foreign_patent_rights_data", fake_foreign_fetch)

    state = PatentWorkflowState(user_input={"management_number": "P202012001-US0", "collect_kipris_api": True})

    result = patent_fetch_node(state)

    assert result.kipris_api_data["source_type"] == "kipris_foreign_patent"
    assert result.kipris_api_data["metadata"]["country"] == "US"
    assert result.patent_structured["kipris_api"]["metadata"]["country"] == "US"
    assert captured_kwargs["collect_pdf"] is True


def test_patent_fetch_accepts_foreign_html_fulltext_without_pdf_path(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.get_patent",
        lambda **kwargs: {
            "id": 70,
            "management_number": "P201702001-US0",
            "country": "US",
            "application_number": "16/622,097",
            "registration_number": "11,782,432",
            "status": "등록",
        },
    )
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
            "parsed_pdf": {
                "selected_type": "GOOGLE_PATENTS_HTML_FULLTEXT",
                "pdf_path": None,
                "markdown_paths": ["/tmp/US11782432B2.md"],
                "markdown_text": "## CLAIMS\n\n1. A method comprising a processor.",
            },
        },
    )

    result = patent_fetch_node(
        PatentWorkflowState(
            user_input={"management_number": "P201702001-US0", "collect_kipris_api": True}
        )
    )

    assert result.parsed_pdf["selected_type"] == "GOOGLE_PATENTS_HTML_FULLTEXT"
    assert result.pdf_paths == []


def test_common_preprocess_enriches_foreign_pdf_prior_art(monkeypatch):
    monkeypatch.setattr(
        "workflow.nodes.resolve_foreign_prior_art_evidence",
        lambda prior_art: {
            "foreign_claim_lookup_candidates": [{"display_number": prior_art[0]}],
            "foreign_citation_documents": [
                {
                    "display_number": prior_art[0],
                    "representative_claims": [{"claim_no": 1, "text": "Prior art claim"}],
                }
            ],
            "foreign_identifier_only_documents": [],
            "prior_art_collection": {
                "candidate_count": 1,
                "comparison_ready_count": 1,
                "identifier_only_count": 0,
                "comparison_status": "comparison_ready",
            },
            "warnings": [],
        },
    )
    state = PatentWorkflowState(
        patent_structured={
            "country": "CN",
            "application_number": "201880038342.9",
            "registration_number": "CN110770661B",
            "title_final": "测量控制方法和系统",
        },
        kipris_api_data={
            "metadata": {"country": "CN"},
            "claims": [{"claim_no": 1, "text": "Target claim", "is_independent": True}],
            "claim_stats": {"active_claim_count": 1},
            "citation_evidence": {},
        },
        parsed_pdf={
            "markdown_text": "(56)对比文件 US 2010241261 A1\n技术领域\n半导体测量技术\n发明内容\n动态测量。",
            "markdown_paths": ["/tmp/CN110770661B.md"],
            "pdf_path": "/tmp/CN110770661B.pdf",
        },
    )

    result = common_preprocess_node(state)

    assert result.citation_evidence["foreign_citation_documents"][0]["representative_claims"][0]["text"] == "Prior art claim"
    assert result.citation_evidence["prior_art_collection"]["comparison_ready_count"] == 1


def test_prior_art_fulltext_node_merges_pdf_claims_into_citation_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "workflow.nodes.build_prior_art_patent_context",
        lambda **kwargs: {
            "comparison_mode": "prior-art",
            "candidate_count": 2,
            "similar_patents": [],
            "prior_art_patents": [
                {
                    "display_number": "US 20100249974 A1",
                    "country_code": "US",
                    "document_number": "20100249974",
                    "kind_code": "A1",
                    "title": "Advanced process control",
                    "abstract": "Sampling rate based on process capability.",
                    "representative_claims": [
                        {
                            "claim_no": 1,
                            "is_independent": True,
                            "dependency": None,
                            "text": "A semiconductor manufacturing method comprising determining a sampling rate.",
                        }
                    ],
                    "lookup_status": "resolved",
                    "lookup_source": "prior_art_pdf_fulltext",
                    "comparison_status": "claim_comparison_ready",
                }
            ],
            "warnings": [],
        },
    )
    state = PatentWorkflowState(
        user_input={"artifact_dir": str(tmp_path)},
        preprocessed_patent={
            "metadata": {
                "country": "JP",
                "prior_art": ["US20100249974 A1", "JP1999345752 A"],
            }
        },
        citation_evidence={
            "foreign_claim_lookup_candidates": [
                {"display_number": "US 20100249974 A1"},
                {"display_number": "JP 1999345752 A"},
            ],
            "foreign_citation_documents": [
                {
                    "display_number": "US20100249974 A1",
                    "abstract": "기존 KIPRIS 초록",
                    "representative_claims": [],
                }
            ],
        },
    )

    result = prior_art_fulltext_node(state)

    document = result.citation_evidence["foreign_citation_documents"][0]
    assert len(result.citation_evidence["foreign_citation_documents"]) == 1
    assert document["lookup_source"] == "prior_art_pdf_fulltext"
    assert document["representative_claims"][0]["claim_no"] == 1
    assert result.citation_evidence["prior_art_collection"] == {
        "candidate_count": 2,
        "comparison_ready_count": 1,
        "claim_comparison_ready_count": 1,
        "abstract_only_count": 0,
        "fulltext_claims_unparsed_count": 0,
        "identifier_only_count": 1,
        "comparison_status": "claim_comparison_ready",
    }


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
    # SK AX 공식 근거도 이제 압축 단계를 거치되 식별 필드(source/source_type/url/
    # related_axes/content)는 그대로 보존된다(normalize_company_disclosure_compression).
    skax_content = "SK AX 공식 금융 서비스 근거 본문"
    monkeypatch.setattr(
        "workflow.nodes.compress_evidence_items",
        lambda items, **kwargs: {
            "items": [
                {
                    "evidence_id": "news_001",
                    "source": "naver_news",
                    "source_type": "news",
                    "compressed_summary": "뉴스 요약",
                },
                {
                    "evidence_id": "skax_site_001",
                    "source": "sk_ax_official",
                    "source_type": "company_disclosure",
                    "title": "SK AX 금융 서비스",
                    "url": "https://www.skax.co.kr/finance/service",
                    "content": skax_content,
                    "related_axes": ["business_fit"],
                    "compressed_summary": "SK AX 금융 서비스 요약",
                    "sk_ax_relevant": True,
                },
            ],
            "warnings": [],
            "stats": {"compressed_count": 2, "input_count": len(items)},
        },
    )
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

    # skax는 이제 압축 단계를 통과하므로, 압축 결과에 식별 필드를 보존한 채 담겨
    # business_fit 입력의 skax_official_evidence로 도달한다.
    monkeypatch.setattr(
        "workflow.nodes.compress_evidence_items",
        lambda items, **kwargs: {
            "items": [
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
            "warnings": [],
            "stats": {"compressed_count": 1},
        },
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


def _report_state(markdown, total_score=223):
    from workflow.state import PatentWorkflowState
    axes = {a: {"score": 50} for a in ["legal", "technology", "market", "business_fit"]}
    return PatentWorkflowState(
        valuation_result={"axes": axes, "total_score": total_score, "final_report_markdown": markdown}
    )


def test_report_validation_passes_well_formed_report():
    from workflow.nodes import report_validation_node

    md = "\n".join(f"## {i}. 섹션" for i in range(1, 7)) + "\n종합 점수 223/300점, 평균 74.3/100점"
    result = report_validation_node(_report_state(md)).report_validation_result

    assert result["passed"] is True
    assert result["issues"] == []


def test_report_validation_flags_missing_sections_and_score_mismatch():
    from workflow.nodes import report_validation_node

    md = "## 1. 한눈에 보는 검토 결과\n## 2. 평가대상\n종합 점수 999/300점"
    result = report_validation_node(_report_state(md, total_score=223)).report_validation_result

    assert result["passed"] is False
    assert any("missing required sections" in i for i in result["issues"])
    assert any("total score" in i for i in result["issues"])


def _capture_collect_external_evidence(monkeypatch):
    configure_evidence_search_mocks(monkeypatch, external_items=[], news_kept=[])
    captured = {}

    def capturing(**kwargs):
        captured.update(kwargs)
        return {"items": [], "queries": ["q"], "gnews_queries": [], "warnings": []}

    monkeypatch.setattr("workflow.nodes.collect_external_evidence", capturing)
    return captured


def test_evidence_search_node_enables_kipris_by_default(monkeypatch):
    # EVID-02: KIPRIS 경쟁근거 기본 ON.
    captured = _capture_collect_external_evidence(monkeypatch)
    state = PatentWorkflowState(
        user_input={"no_save": True},
        patent_structured={"application_number": "10-2024-0000001"},
        preprocessed_patent={"metadata": {}, "sections": {}},
        query_plan={},
    )

    evidence_search_node(state)

    assert captured["include_kipris"] is True


def test_evidence_search_node_kipris_can_be_disabled(monkeypatch):
    captured = _capture_collect_external_evidence(monkeypatch)
    state = PatentWorkflowState(
        user_input={"no_save": True, "include_kipris_competitor": False},
        patent_structured={"application_number": "10-2024-0000001"},
        preprocessed_patent={"metadata": {}, "sections": {}},
        query_plan={},
    )

    evidence_search_node(state)

    assert captured["include_kipris"] is False


def test_report_validation_flags_recommendation_mismatch():
    from workflow.nodes import report_validation_node

    md = (
        "\n".join(f"## {i}. 섹션" for i in range(1, 7))
        + "\n| 종합 검토 의견 | 조건부 유지 |\n종합 점수 223/400점"
    )
    state = _report_state(md)
    state.valuation_result["recommendation"] = "추가 정보 필요"

    result = report_validation_node(state).report_validation_result

    assert result["passed"] is False
    assert any("recommendation" in issue for issue in result["issues"])


@pytest.mark.parametrize("country", ["CN", "JP", "TW", "AE"])
def test_report_validation_rejects_domestic_wording_and_drawing_block_for_foreign_patent(country):
    from workflow.nodes import report_validation_node

    md = (
        "\n".join(f"## {i}. 섹션" for i in range(1, 7))
        + "\n종합 점수 223/300점\n등록된 국내권\n**권리범위 참고도 및 이해**"
    )
    state = _report_state(md)
    state.patent_structured = {"country": country}

    result = report_validation_node(state).report_validation_result

    assert result["passed"] is False
    assert any("domestic-patent wording" in issue for issue in result["issues"])
    assert any("representative-drawing" in issue for issue in result["issues"])
