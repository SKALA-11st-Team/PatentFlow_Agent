import requests

from services.evidence.skax_site_search_service import (
    GoogleCustomSearchClient,
    GoogleHtmlSearchClient,
    TavilySearchClient,
    build_query_generation_plan,
    build_search_queries,
    collect_skax_site_evidence,
    default_search_client,
    default_html_searcher,
    filter_search_results,
    parse_google_search_html,
)


PATENT_CONTEXT = {
    "관리번호": "P202405001-KR0",
    "발명의 명칭(최종)": "상품 트렌드 예측을 반영한 강화학습 모델을 적용한 자산배분 시스템 및 방법",
    "관련사업 분야": "Data",
    "관련기술 분야": "데이터분석",
    "관련제품": "로보어드바이저",
}


def test_build_search_queries_prioritizes_related_product_and_site_condition():
    queries = build_search_queries(PATENT_CONTEXT)

    assert 3 <= len(queries) <= 5
    assert all(query.startswith("site:skax.co.kr") for query in queries)
    assert queries[0] == "site:skax.co.kr 로보어드바이저"
    assert any("SK AX" in query and "로보어드바이저" in query for query in queries)
    assert any("news room" in query and "로보어드바이저" in query for query in queries)
    assert any("데이터분석" in query and "Data" in query for query in queries)
    assert any("금융" in query or "투자" in query for query in queries)
    assert any("AI" in query and "예측" in query for query in queries)


def test_build_query_generation_plan_uses_rewritten_skax_queries_when_provided():
    plan = build_query_generation_plan(
        PATENT_CONTEXT,
        queries_override=[
            "로보어드바이저 금융 자산관리",
            "site:skax.co.kr 디지털 금융 서비스 AI 예측",
        ],
    )

    assert plan["query_source"] == "rule_based_with_query_rewriting"
    assert plan["generated_queries"][:3] == [
        "site:skax.co.kr 로보어드바이저",
        "site:skax.co.kr SK AX 로보어드바이저",
        "site:skax.co.kr 로보어드바이저 news room",
    ]


def test_collect_skax_site_evidence_uses_rewritten_query_override():
    seen_queries = []

    def fake_searcher(query):
        seen_queries.append(query)
        return []

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        searcher=fake_searcher,
        queries_override=["디지털 금융 서비스 AI 예측"],
    )

    assert seen_queries[:3] == [
        "site:skax.co.kr 로보어드바이저",
        "site:skax.co.kr SK AX 로보어드바이저",
        "site:skax.co.kr 로보어드바이저 news room",
    ]
    assert result["query_generation_diagnostics"]["query_source"] == "rule_based_with_query_rewriting"


def test_build_search_queries_adds_finance_hints_only_when_context_supports_them():
    blockchain_context = {
        "관리번호": "P202307002-KR0",
        "발명의 명칭(최종)": "블록체인 합의 과정에서의 서명 검증 방법 및 시스템",
        "관련사업 분야": "Blockchain",
        "관련기술 분야": "Blockchain",
        "관련제품": "ChainZ",
    }

    queries = build_search_queries(blockchain_context)

    assert any("블록체인" in query or "인증" in query or "보안" in query for query in queries)
    assert not any("금융" in query or "투자" in query or "자산관리" in query for query in queries)


def test_build_search_queries_adds_manufacturing_hints_without_finance_terms():
    manufacturing_context = {
        "관리번호": "P202406001-KR0",
        "발명의 명칭(최종)": "반도체 CMP 공정 물류 자동화를 위한 설비 제어 시스템",
        "관련사업 분야": "Manufacturing",
        "관련기술 분야": "스마트팩토리",
        "관련제품": "CMP Pad",
    }

    queries = build_search_queries(manufacturing_context)

    assert any("제조" in query or "스마트팩토리" in query or "물류" in query for query in queries)
    assert not any("금융" in query or "투자" in query or "자산관리" in query for query in queries)


def test_build_search_queries_handles_empty_values():
    queries = build_search_queries({})

    assert queries == ["site:skax.co.kr"]


def test_filter_search_results_keeps_skax_non_file_urls_and_sorts_by_relevance():
    results = [
        {
            "title": "SK AX 로보어드바이저 자산배분 데이터분석",
            "snippet": "강화학습 기반 자산배분 서비스",
            "url": "https://www.skax.co.kr/financial/robo-advisor",
            "search_query": "site:skax.co.kr 로보어드바이저",
        },
        {
            "title": "외부 로보어드바이저",
            "snippet": "외부 도메인",
            "url": "https://example.com/robo-advisor",
            "search_query": "site:skax.co.kr 로보어드바이저",
        },
        {
            "title": "SK AX 브로슈어",
            "snippet": "PDF",
            "url": "https://www.skax.co.kr/financial/brochure.pdf",
            "search_query": "site:skax.co.kr 로보어드바이저",
        },
        {
            "title": "SK AX 데이터분석",
            "snippet": "데이터분석",
            "url": "https://www.skax.co.kr/data/analytics",
            "search_query": "site:skax.co.kr 로보어드바이저",
        },
        {
            "title": "SK AX 로보어드바이저 자산배분 데이터분석",
            "snippet": "중복",
            "url": "https://www.skax.co.kr/financial/robo-advisor#section",
            "search_query": "site:skax.co.kr 로보어드바이저",
        },
    ]

    filtered = filter_search_results(results, PATENT_CONTEXT)

    assert [item["url"] for item in filtered] == [
        "https://www.skax.co.kr/financial/robo-advisor",
    ]
    assert "로보어드바이저" in filtered[0]["matched_keywords"]
    assert "matched_related_product" in filtered[0]["score_reasons"]


def test_collect_skax_site_evidence_fetches_relevant_results_and_normalizes_evidence():
    fetched_urls = []

    def searcher(query):
        return [
            {
                "title": "SK AX 로보어드바이저 자산배분 데이터분석",
                "snippet": "강화학습 기반 자산배분 솔루션",
                "url": "https://www.skax.co.kr/financial/robo-advisor",
            },
            {
                "title": "SK AX 데이터분석",
                "snippet": "데이터분석 서비스",
                "url": "https://www.skax.co.kr/data/analytics",
            },
            {
                "title": "외부 로보어드바이저",
                "snippet": "외부 결과",
                "url": "https://example.com/robo-advisor",
            },
            {
                "title": "SK AX 파일",
                "snippet": "파일 결과",
                "url": "https://www.skax.co.kr/data/file.png",
            },
        ]

    def fetcher(url):
        fetched_urls.append(url)
        return """
        <html>
          <head><title>SK AX Robo Advisor</title></head>
          <body>
            <nav>메뉴 텍스트</nav>
            <main><h1>로보어드바이저</h1><p>자산배분 데이터분석 솔루션 본문</p></main>
            <footer>푸터 텍스트</footer>
          </body>
        </html>
        """

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        searcher=searcher,
        fetcher=fetcher,
        max_queries=1,
        max_fetch_pages=1,
    )

    assert fetched_urls == ["https://www.skax.co.kr/financial/robo-advisor"]
    evidence = result["items"][0]
    assert evidence["evidence_id"].startswith("skax_site_")
    assert evidence["source_type"] == "company_disclosure"
    assert evidence["source"] == "sk_ax_official"
    assert evidence["title"] == "SK AX Robo Advisor"
    assert evidence["url"] == "https://www.skax.co.kr/financial/robo-advisor"
    assert evidence["published_at"] is None
    assert evidence["collected_at"]
    assert "로보어드바이저" in evidence["content"]
    assert "메뉴 텍스트" not in evidence["content"]
    assert "푸터 텍스트" not in evidence["content"]
    assert "business_fit" in evidence["related_axes"]
    assert evidence["management_number"] == "P202405001-KR0"
    assert evidence["related_product"] == "로보어드바이저"
    assert evidence["business_area"] == "Data"
    assert evidence["technology_area"] == "데이터분석"
    assert evidence["relevance_score"] > 0
    assert "로보어드바이저" in evidence["matched_keywords"]
    assert result["stats"]["generated_query_count"] == 1
    assert result["stats"]["searched_result_count"] == 4
    assert result["stats"]["filtered_result_count"] == 1
    assert result["stats"]["fetched_url_count"] == 1
    assert result["stats"]["collected_evidence_count"] == 1
    assert result["stats"]["skipped_url_count"] == 3
    assert result["stats"]["failed_url_count"] == 0


def test_collect_sk_related_media_requires_sk_ax_or_cnc_body_marker():
    fetched_urls = []

    def searcher(query):
        if "skcareersjournal.com" not in query:
            return []
        return [
            {
                "title": "SK AX AI 영상 분석",
                "snippet": "AI 영상 분석 서비스",
                "url": "https://www.skcareersjournal.com/2827",
            },
            {
                "title": "AI 영상 분석 일반 기사",
                "snippet": "AI 영상 분석 서비스",
                "url": "https://www.skcareersjournal.com/no-skax-marker",
            },
        ]

    def fetcher(url):
        fetched_urls.append(url)
        if url.endswith("/no-skax-marker"):
            return "<html><body><main>AI 영상 분석 서비스 소개</main></body></html>"
        return "<html><body><main>SK AX AIDEN VAS AI 영상 분석 서비스</main></body></html>"

    result = collect_skax_site_evidence(
        {
            "관리번호": "P202410001-KR0",
            "발명의 명칭(최종)": "AI 영상 분석 시스템",
            "관련사업 분야": "AI",
            "관련기술 분야": "Vision AI",
            "관련제품": "AIDEN VAS",
        },
        searcher=searcher,
        fetcher=fetcher,
        max_queries=1,
        max_fetch_pages=3,
        include_related_media=True,
    )

    assert fetched_urls == [
        "https://www.skcareersjournal.com/2827",
        "https://www.skcareersjournal.com/no-skax-marker",
    ]
    assert [item["url"] for item in result["items"]] == ["https://www.skcareersjournal.com/2827"]
    evidence = result["items"][0]
    assert evidence["source"] == "sk_group_owned_media"
    assert evidence["source_domain"] == "skcareersjournal.com"
    assert evidence["source_tier"] == "sk_related_owned_media"
    assert evidence["source_type"] == "company_disclosure"
    assert "SK AX" in evidence["content"]
    assert result["stats"]["skipped_url_count"] == 1


def test_fetch_failure_and_empty_html_do_not_fail_collection():
    def searcher(query):
        return [
            {
                "title": "SK AX 로보어드바이저",
                "snippet": "로보어드바이저 자산배분",
                "url": "https://www.skax.co.kr/financial/failure",
            },
            {
                "title": "SK AX 로보어드바이저 데이터분석",
                "snippet": "로보어드바이저 데이터분석",
                "url": "https://www.skax.co.kr/financial/empty",
            },
            {
                "title": "SK AX 로보어드바이저 강화학습 자산배분",
                "snippet": "로보어드바이저 강화학습 자산배분 데이터분석",
                "url": "https://www.skax.co.kr/financial/success",
            },
        ]

    def fetcher(url):
        if url.endswith("/failure"):
            raise RuntimeError("boom")
        if url.endswith("/empty"):
            return "  "
        return "<html><head><title>성공</title></head><body><p>성공 본문</p></body></html>"

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        searcher=searcher,
        fetcher=fetcher,
        max_queries=1,
        max_fetch_pages=3,
    )

    assert [item["url"] for item in result["items"]] == ["https://www.skax.co.kr/financial/success"]
    assert result["stats"]["failed_url_count"] == 1
    assert result["stats"]["skipped_url_count"] == 1
    assert result["stats"]["collected_evidence_count"] == 1


def test_truncates_content_and_reports_stats():
    def searcher(query):
        return [
            {
                "title": "SK AX 로보어드바이저",
                "snippet": "로보어드바이저 자산배분",
                "url": "https://www.skax.co.kr/financial/robo-advisor",
            }
        ]

    def fetcher(url):
        return f"<html><head><title>긴 본문</title></head><body><p>{'긴본문' * 20}</p></body></html>"

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        searcher=searcher,
        fetcher=fetcher,
        max_queries=1,
        max_content_chars=20,
    )

    assert len(result["items"][0]["content"]) == 20
    assert result["stats"]["truncated_content_count"] == 1


def test_parse_google_search_html_extracts_skax_urls_and_removes_duplicates():
    html = """
    <html>
      <body>
        <a href="/url?q=https%3A%2F%2Fwww.skax.co.kr%2Ffinancial%2Frobo-advisor%23section&sa=U">
          SK AX 로보어드바이저
        </a>
        <a href="/url?q=https%3A%2F%2Fexample.com%2Fexternal&sa=U">외부</a>
        <a href="/url?q=https%3A%2F%2Fwww.skax.co.kr%2Ffinancial%2Fbrochure.pdf&sa=U">PDF</a>
        <a href="https://www.skax.co.kr/financial/robo-advisor">중복</a>
        <a href="https://www.skax.co.kr/data/analytics">SK AX 데이터분석</a>
      </body>
    </html>
    """

    results = parse_google_search_html(html)

    assert results == [
        {
            "title": "SK AX 로보어드바이저",
            "url": "https://www.skax.co.kr/financial/robo-advisor",
            "snippet": "",
        },
        {
            "title": "SK AX 데이터분석",
            "url": "https://www.skax.co.kr/data/analytics",
            "snippet": "",
        },
    ]


def test_parse_google_search_html_extracts_escaped_skax_urls_without_anchor_href():
    html = """
    <html>
      <body>
        <script>
          var result = "https%3A%2F%2Fwww.skax.co.kr%2Fai%2Fagent-platform%3Futm_source%3Dgoogle";
          var external = "https%3A%2F%2Fnews.example.com%2Fskax%2Fai";
        </script>
      </body>
    </html>
    """

    results = parse_google_search_html(html)

    assert results == [
        {
            "title": "https://www.skax.co.kr/ai/agent-platform?utm_source=google",
            "url": "https://www.skax.co.kr/ai/agent-platform?utm_source=google",
            "snippet": "",
        }
    ]


def test_parse_google_search_html_extracts_js_escaped_skax_urls():
    html = """
    <html>
      <body>
        <script>
          window.result = "https:\\/\\/www.skax.co.kr\\/digital-based-financial-service";
          window.external = "https:\\/\\/news.example.com\\/skax\\/financial";
        </script>
      </body>
    </html>
    """

    results = parse_google_search_html(html)

    assert results == [
        {
            "title": "https://www.skax.co.kr/digital-based-financial-service",
            "url": "https://www.skax.co.kr/digital-based-financial-service",
            "snippet": "",
        }
    ]


def test_parse_google_search_html_allows_only_skax_domain_and_subdomains():
    html = """
    <html>
      <body>
        <a href="https://www.skax.co.kr/financial/robo-advisor">www SK AX</a>
        <a href="https://skax.co.kr/data/analytics">root SK AX</a>
        <a href="https://www.sk.co.kr/news/robo-advisor">SK group</a>
        <a href="https://news.example.com/skax/robo-advisor">news mirror</a>
        <a href="https://blog.example.com/skax/robo-advisor">blog mirror</a>
      </body>
    </html>
    """

    results = parse_google_search_html(html)

    assert [result["url"] for result in results] == [
        "https://www.skax.co.kr/financial/robo-advisor",
        "https://skax.co.kr/data/analytics",
    ]


def test_collect_returns_empty_when_search_has_no_skax_domain_results():
    fetched_urls = []

    def searcher(query):
        return [
            {
                "title": "SK 그룹 로보어드바이저",
                "snippet": "다른 공식 도메인",
                "url": "https://www.sk.co.kr/news/robo-advisor",
            },
            {
                "title": "뉴스 로보어드바이저",
                "snippet": "뉴스 미러링",
                "url": "https://news.example.com/skax/robo-advisor",
            },
            {
                "title": "블로그 로보어드바이저",
                "snippet": "블로그 미러링",
                "url": "https://blog.example.com/skax/robo-advisor",
            },
        ]

    def fetcher(url):
        fetched_urls.append(url)
        return "<html><body><p>외부 문서</p></body></html>"

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        searcher=searcher,
        fetcher=fetcher,
        max_queries=1,
    )

    assert result["items"] == []
    assert fetched_urls == []
    assert result["stats"]["filtered_result_count"] == 0
    assert result["stats"]["fetched_url_count"] == 0
    assert result["stats"]["collected_evidence_count"] == 0


def test_default_html_searcher_uses_fetch_google_search_html(monkeypatch):
    captured = {}

    def fake_fetch(query):
        captured["query"] = query
        return """
        <html>
          <body>
            <a href="/url?q=https%3A%2F%2Fwww.skax.co.kr%2Ffinancial%2Frobo-advisor&sa=U">
              SK AX 로보어드바이저
            </a>
          </body>
        </html>
        """

    monkeypatch.setattr("services.evidence.skax_site_search_service.fetch_google_search_html", fake_fetch)

    results = default_html_searcher("site:skax.co.kr 로보어드바이저")

    assert captured["query"] == "site:skax.co.kr 로보어드바이저"
    assert results[0]["url"] == "https://www.skax.co.kr/financial/robo-advisor"


def test_default_html_searcher_returns_empty_list_on_fetch_failure(monkeypatch):
    def fake_fetch(query):
        raise RuntimeError("blocked")

    monkeypatch.setattr("services.evidence.skax_site_search_service.fetch_google_search_html", fake_fetch)

    assert default_html_searcher("site:skax.co.kr 로보어드바이저") == []


def test_collect_uses_default_searcher_when_searcher_is_not_provided(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_CX", raising=False)

    def fake_google_response(query):
        return {
            "status_code": 200,
            "url": "https://www.google.com/search?q=site%3Askax.co.kr+robo",
            "html": """
            <html>
              <body>
                <a href="/url?q=https%3A%2F%2Fwww.skax.co.kr%2Ffinancial%2Frobo-advisor&sa=U">
                  SK AX 로보어드바이저 자산배분 데이터분석
                </a>
              </body>
            </html>
            """,
        }

    def fetcher(url):
        return "<html><head><title>기본 검색</title></head><body><p>로보어드바이저 사업 근거</p></body></html>"

    monkeypatch.setattr(
        "services.evidence.skax_site_search_service.fetch_google_search_response",
        fake_google_response,
    )

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        fetcher=fetcher,
        max_queries=1,
    )

    assert result["items"][0]["url"] == "https://www.skax.co.kr/financial/robo-advisor"
    assert result["stats"]["searched_result_count"] == 1


def test_collect_reports_google_search_diagnostics_with_mock_html(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_CX", raising=False)

    html = """
    <html>
      <body>
        <a href="/url?q=https%3A%2F%2Fwww.skax.co.kr%2Fdigital-based-financial-service&sa=U">
          SK AX AI 디지털 금융 서비스
        </a>
      </body>
    </html>
    """

    def fake_google_response(query):
        return {
            "status_code": 200,
            "url": "https://www.google.com/search?q=site%3Askax.co.kr+AI",
            "html": html,
        }

    def fetcher(url):
        return "<html><head><title>SK AX 금융</title></head><body><p>AI 데이터분석 로보어드바이저 서비스</p></body></html>"

    monkeypatch.setattr(
        "services.evidence.skax_site_search_service.fetch_google_search_response",
        fake_google_response,
    )

    result = collect_skax_site_evidence(
        {
            "management_number": "TEST",
            "title_final": "",
            "business_area": "AI",
            "technology_area": "AI",
            "related_product": "AI",
        },
        fetcher=fetcher,
        max_queries=1,
    )

    diagnostics = result["search_diagnostics"][0]
    assert result["stats"]["searched_result_count"] == 1
    assert result["items"][0]["url"] == "https://www.skax.co.kr/digital-based-financial-service"
    assert diagnostics["search_status_code"] == 200
    assert diagnostics["search_html_length"] == len(html)
    assert 0 < len(diagnostics["search_html_preview"]) <= 800
    assert diagnostics["parsed_link_count"] == 1
    assert diagnostics["parsed_result_count"] == 1
    assert diagnostics["search_failure_reason"] is None


def test_collect_reports_google_consent_page_when_search_results_are_zero(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_CX", raising=False)

    html = """
    <html>
      <head><title>Before you continue to Google Search</title></head>
      <body>Before you continue consent.google.com</body>
    </html>
    """

    def fake_google_response(query):
        return {
            "status_code": 200,
            "url": "https://consent.google.com/",
            "html": html,
        }

    def fetcher(url):
        raise AssertionError("No SK AX URL should be fetched from a consent page.")

    monkeypatch.setattr(
        "services.evidence.skax_site_search_service.fetch_google_search_response",
        fake_google_response,
    )

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        fetcher=fetcher,
        max_queries=1,
    )

    diagnostics = result["search_diagnostics"][0]
    assert result["items"] == []
    assert result["stats"]["searched_result_count"] == 0
    assert diagnostics["search_status_code"] == 200
    assert diagnostics["search_html_length"] == len(html)
    assert diagnostics["parsed_link_count"] == 0
    assert diagnostics["parsed_result_count"] == 0
    assert diagnostics["search_failure_reason"] == "google_consent_page"


def test_collect_reports_google_requires_javascript_for_enablejs_retry_html(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_CX", raising=False)

    html = """
    <html>
      <head><title>Google Search</title></head>
      <body>
        <script nonce="abc">location.href='/httpservice/retry/enablejs?sei=abc'</script>
        <noscript>몇 초 안에 이동하지 않는 경우 여기를 클릭하세요.</noscript>
      </body>
    </html>
    """

    def fake_google_response(query):
        return {
            "status_code": 200,
            "url": "https://www.google.com/search?q=site%3Askax.co.kr+AI",
            "html": html,
        }

    monkeypatch.setattr(
        "services.evidence.skax_site_search_service.fetch_google_search_response",
        fake_google_response,
    )

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        fetcher=lambda url: "<html></html>",
        max_queries=1,
    )

    diagnostics = result["search_diagnostics"][0]
    assert result["items"] == []
    assert diagnostics["search_status_code"] == 200
    assert diagnostics["search_html_length"] == len(html)
    assert diagnostics["parsed_result_count"] == 0
    assert diagnostics["search_failure_reason"] == "google_requires_javascript"


def test_collect_reports_google_requires_javascript_for_noscript_enablejs_html(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_CX", raising=False)

    html = """
    <html>
      <body>
        <noscript>
          JavaScript를 사용 설정하거나 enablejs 링크로 이동해야 합니다.
        </noscript>
      </body>
    </html>
    """

    def fake_google_response(query):
        return {
            "status_code": 200,
            "url": "https://www.google.com/search?q=site%3Askax.co.kr+AI",
            "html": html,
        }

    monkeypatch.setattr(
        "services.evidence.skax_site_search_service.fetch_google_search_response",
        fake_google_response,
    )

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        fetcher=lambda url: "<html></html>",
        max_queries=1,
    )

    diagnostics = result["search_diagnostics"][0]
    assert result["items"] == []
    assert diagnostics["parsed_result_count"] == 0
    assert diagnostics["search_failure_reason"] == "google_requires_javascript"


def test_collect_uses_search_client_and_normalizes_skax_evidence():
    class MockSearchClient:
        def search(self, query, *, max_results=5):
            return {
                "results": [
                    {
                        "title": "SK AX 로보어드바이저 자산배분",
                        "snippet": "데이터분석 서비스",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                    }
                ],
                "diagnostics": {
                    "query": query,
                    "parsed_link_count": 1,
                    "parsed_result_count": 1,
                },
            }

    def fetcher(url):
        return "<html><head><title>SK AX 금융</title></head><body><p>로보어드바이저 데이터분석 사업 근거</p></body></html>"

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=MockSearchClient(),
        fetcher=fetcher,
        max_queries=1,
    )

    assert result["items"][0]["source"] == "sk_ax_official"
    assert result["items"][0]["source_type"] == "company_disclosure"
    assert result["items"][0]["url"] == "https://www.skax.co.kr/finance/digital-based-financial-service"
    assert result["stats"]["searched_result_count"] == 1
    assert result["search_diagnostics"][0]["parsed_result_count"] == 1


def test_collect_excludes_external_urls_from_search_client():
    fetched_urls = []

    class MockSearchClient:
        def search(self, query, *, max_results=5):
            return {
                "results": [
                    {
                        "title": "외부 로보어드바이저",
                        "snippet": "외부 뉴스",
                        "url": "https://news.example.com/skax/robo-advisor",
                    }
                ],
                "diagnostics": {"query": query, "parsed_result_count": 1},
            }

    def fetcher(url):
        fetched_urls.append(url)
        return "<html><body><p>외부 문서</p></body></html>"

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=MockSearchClient(),
        fetcher=fetcher,
        max_queries=1,
    )

    assert result["items"] == []
    assert fetched_urls == []
    assert result["stats"]["filtered_result_count"] == 0
    assert result["stats"]["fetched_url_count"] == 0


def test_collect_returns_empty_with_empty_search_client_results():
    class MockSearchClient:
        def search(self, query, *, max_results=5):
            return {
                "results": [],
                "diagnostics": {"query": query, "parsed_link_count": 0, "parsed_result_count": 0},
            }

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=MockSearchClient(),
        max_queries=1,
    )

    assert result["items"] == []
    assert result["stats"]["searched_result_count"] == 0
    assert result["search_diagnostics"][0]["parsed_result_count"] == 0


def test_collect_records_search_client_exception_in_diagnostics():
    class FailingSearchClient:
        def search(self, query, *, max_results=5):
            raise RuntimeError("search api down")

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=FailingSearchClient(),
        max_queries=1,
    )

    assert result["items"] == []
    assert result["stats"]["searched_result_count"] == 0
    assert result["search_diagnostics"][0]["search_failure_reason"] == "fetch_error:RuntimeError"


def test_google_html_search_client_returns_compatible_structure(monkeypatch):
    html = """
    <html>
      <body>
        <a href="/url?q=https%3A%2F%2Fwww.skax.co.kr%2Ffinancial%2Frobo-advisor&sa=U">
          SK AX 로보어드바이저
        </a>
      </body>
    </html>
    """

    def fake_google_response(query):
        return {
            "status_code": 200,
            "url": "https://www.google.com/search?q=site%3Askax.co.kr+robo",
            "html": html,
        }

    monkeypatch.setattr(
        "services.evidence.skax_site_search_service.fetch_google_search_response",
        fake_google_response,
    )

    result = GoogleHtmlSearchClient().search("site:skax.co.kr 로보어드바이저")

    assert result["results"][0]["url"] == "https://www.skax.co.kr/financial/robo-advisor"
    assert result["diagnostics"]["search_status_code"] == 200
    assert result["diagnostics"]["parsed_result_count"] == 1


def test_google_custom_search_client_extracts_only_skax_results(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "title": "SK AX 로보어드바이저",
                        "link": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "snippet": "로보어드바이저 데이터분석",
                    },
                    {
                        "title": "외부 뉴스",
                        "link": "https://news.example.com/skax/robo-advisor",
                        "snippet": "외부",
                    },
                    {
                        "title": "중복",
                        "link": "https://www.skax.co.kr/finance/digital-based-financial-service#section",
                        "snippet": "중복",
                    },
                ]
            }

    def fake_get(url, *, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.get", fake_get)

    result = GoogleCustomSearchClient(api_key="key", cx="cx").search(
        "site:skax.co.kr 로보어드바이저",
        max_results=10,
    )

    assert captured["url"] == "https://www.googleapis.com/customsearch/v1"
    assert captured["params"]["key"] == "key"
    assert captured["params"]["cx"] == "cx"
    assert captured["params"]["q"] == "site:skax.co.kr 로보어드바이저"
    assert captured["params"]["num"] == 10
    assert captured["params"]["hl"] == "ko"
    assert captured["params"]["gl"] == "kr"
    assert "siteSearch" not in captured["params"]
    assert "siteSearchFilter" not in captured["params"]
    assert result["results"] == [
        {
            "title": "SK AX 로보어드바이저",
            "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
            "snippet": "로보어드바이저 데이터분석",
        }
    ]
    assert result["diagnostics"]["search_provider"] == "google_custom_search_json"
    assert result["diagnostics"]["parsed_link_count"] == 3
    assert result["diagnostics"]["parsed_result_count"] == 1
    assert result["diagnostics"]["search_failure_reason"] is None


def test_google_custom_search_client_reports_missing_config():
    result = GoogleCustomSearchClient(api_key="", cx="").search("site:skax.co.kr AI")

    assert result["results"] == []
    assert result["diagnostics"]["search_provider"] == "google_custom_search_json"
    assert result["diagnostics"]["missing_config"] is True
    assert result["diagnostics"]["search_failure_reason"] == "missing_config"


def test_google_custom_search_client_handles_api_failure(monkeypatch):
    def fake_get(url, *, params, timeout):
        raise RuntimeError("api down")

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.get", fake_get)

    result = GoogleCustomSearchClient(api_key="key", cx="cx").search("site:skax.co.kr AI")

    assert result["results"] == []
    assert result["diagnostics"]["search_status_code"] is None
    assert result["diagnostics"]["search_failure_reason"] == "fetch_error:RuntimeError"


def test_google_custom_search_client_reports_http_error_body_without_key(monkeypatch):
    class FakeResponse:
        status_code = 403
        text = '{"error":{"code":403,"message":"Custom Search API has not been used in project. key=secret-key","status":"PERMISSION_DENIED","errors":[{"reason":"accessNotConfigured","message":"Custom Search API has not been used in project."}]}}'

        def raise_for_status(self):
            raise requests.HTTPError("403 Client Error", response=self)

        def json(self):
            return {
                "error": {
                    "code": 403,
                    "message": "Custom Search API has not been used in project.",
                    "status": "PERMISSION_DENIED",
                    "errors": [
                        {
                            "reason": "accessNotConfigured",
                            "message": "Custom Search API has not been used in project.",
                        }
                    ],
                }
            }

    def fake_get(url, *, params, timeout):
        assert params["key"] == "secret-key"
        return FakeResponse()

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.get", fake_get)

    result = GoogleCustomSearchClient(api_key="secret-key", cx="cx").search("site:skax.co.kr AI")
    diagnostics = result["diagnostics"]

    assert result["results"] == []
    assert diagnostics["search_request_url"] == "https://www.googleapis.com/customsearch/v1"
    assert "secret-key" not in diagnostics["search_request_url"]
    assert "secret-key" not in diagnostics["api_error_body_preview"]
    assert "[REDACTED]" in diagnostics["api_error_body_preview"]
    assert diagnostics["search_status_code"] == 403
    assert diagnostics["search_failure_reason"] == "fetch_error:HTTPError"
    assert diagnostics["api_error_code"] == 403
    assert diagnostics["api_error_status"] == "PERMISSION_DENIED"
    assert diagnostics["api_error_message"] == "Custom Search API has not been used in project."
    assert diagnostics["api_error_reason"] == "accessNotConfigured"


def test_collect_handles_custom_search_http_error_without_raising(monkeypatch):
    class FakeResponse:
        status_code = 403
        text = '{"error":{"code":403,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED","errors":[{"reason":"dailyLimitExceeded","message":"Quota exceeded"}]}}'

        def raise_for_status(self):
            raise requests.HTTPError("403 Client Error", response=self)

        def json(self):
            return {
                "error": {
                    "code": 403,
                    "message": "Quota exceeded",
                    "status": "RESOURCE_EXHAUSTED",
                    "errors": [{"reason": "dailyLimitExceeded", "message": "Quota exceeded"}],
                }
            }

    def fake_get(url, *, params, timeout):
        return FakeResponse()

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.get", fake_get)

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=GoogleCustomSearchClient(api_key="key", cx="cx"),
        max_queries=1,
    )
    diagnostics = result["search_diagnostics"][0]

    assert result["items"] == []
    assert result["stats"]["searched_result_count"] == 0
    assert diagnostics["api_error_code"] == 403
    assert diagnostics["api_error_status"] == "RESOURCE_EXHAUSTED"
    assert diagnostics["api_error_reason"] == "dailyLimitExceeded"
    assert "Quota exceeded" in diagnostics["api_error_body_preview"]


def test_google_custom_search_client_parses_http_error_text_when_json_method_fails(monkeypatch):
    class FakeResponse:
        status_code = 403
        text = '{"error":{"code":403,"message":"API key not valid.","status":"INVALID_ARGUMENT","errors":[{"reason":"keyInvalid","message":"API key not valid."}]}}'

        def raise_for_status(self):
            raise requests.HTTPError("403 Client Error", response=self)

        def json(self):
            raise ValueError("not json")

    def fake_get(url, *, params, timeout):
        return FakeResponse()

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.get", fake_get)

    result = GoogleCustomSearchClient(api_key="key", cx="cx").search("site:skax.co.kr AI")
    diagnostics = result["diagnostics"]

    assert diagnostics["api_error_code"] == 403
    assert diagnostics["api_error_status"] == "INVALID_ARGUMENT"
    assert diagnostics["api_error_message"] == "API key not valid."
    assert diagnostics["api_error_reason"] == "keyInvalid"


def test_collect_uses_custom_search_client_and_fetches_page(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "title": "SK AX 로보어드바이저 자산배분 데이터분석",
                        "link": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "snippet": "로보어드바이저 데이터분석",
                    }
                ]
            }

    def fake_get(url, *, params, timeout):
        return FakeResponse()

    def fetcher(url):
        return "<html><head><title>SK AX 금융</title></head><body><p>로보어드바이저 데이터분석 사업 근거</p></body></html>"

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.get", fake_get)

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=GoogleCustomSearchClient(api_key="key", cx="cx"),
        fetcher=fetcher,
        max_queries=1,
    )

    evidence = result["items"][0]
    diagnostics = result["search_diagnostics"][0]
    assert evidence["source"] == "sk_ax_official"
    assert evidence["source_type"] == "company_disclosure"
    assert evidence["url"] == "https://www.skax.co.kr/finance/digital-based-financial-service"
    assert diagnostics["search_provider"] == "google_custom_search_json"
    assert diagnostics["parsed_result_count"] == 1


def test_tavily_search_client_extracts_only_skax_results(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 로보어드바이저",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "content": "로보어드바이저 데이터분석 서비스",
                    },
                    {
                        "title": "외부 뉴스",
                        "url": "https://news.example.com/skax/robo-advisor",
                        "content": "외부",
                    },
                    {
                        "title": "중복",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service#section",
                        "content": "중복",
                    },
                ]
            }

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", fake_post)

    result = TavilySearchClient(api_key="tavily-key").search("site:skax.co.kr 로보어드바이저", max_results=5)

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["json"]["api_key"] == "tavily-key"
    assert captured["json"]["query"] == "site:skax.co.kr 로보어드바이저"
    assert captured["json"]["include_domains"] == ["skax.co.kr", "skcareersjournal.com", "openapi.sk.com"]
    assert captured["json"]["include_raw_content"] is True
    assert captured["json"]["search_depth"] == "basic"
    assert captured["json"]["max_results"] == 3
    assert result["results"] == [
        {
            "title": "SK AX 로보어드바이저",
            "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
            "snippet": "로보어드바이저 데이터분석 서비스",
            "content": "로보어드바이저 데이터분석 서비스",
        }
    ]
    assert result["diagnostics"]["search_provider"] == "tavily_search"
    assert result["diagnostics"]["raw_content_included"] is True
    assert result["diagnostics"]["parsed_link_count"] == 3
    assert result["diagnostics"]["parsed_result_count"] == 1
    assert result["diagnostics"]["candidate_results"] == [
        {
            "title": "SK AX 로보어드바이저",
            "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
            "normalized_url": "https://www.skax.co.kr/finance/digital-based-financial-service",
            "accepted": True,
            "domain_accepted": True,
            "final_selected": False,
            "skip_reason": None,
            "final_skip_reason": None,
            "candidate_relevance_score": 0.0,
            "score_reasons": [],
        },
        {
            "title": "외부 뉴스",
            "url": "https://news.example.com/skax/robo-advisor",
            "normalized_url": "https://news.example.com/skax/robo-advisor",
            "accepted": False,
            "domain_accepted": False,
            "final_selected": False,
            "skip_reason": "external_domain",
            "final_skip_reason": None,
            "candidate_relevance_score": 0.0,
            "score_reasons": [],
        },
        {
            "title": "중복",
            "url": "https://www.skax.co.kr/finance/digital-based-financial-service#section",
            "normalized_url": "https://www.skax.co.kr/finance/digital-based-financial-service",
            "accepted": False,
            "domain_accepted": False,
            "final_selected": False,
            "skip_reason": "duplicate_url",
            "final_skip_reason": None,
            "candidate_relevance_score": 0.0,
            "score_reasons": [],
        },
    ]
    assert "content" not in result["diagnostics"]["candidate_results"][0]
    assert "tavily-key" not in str(result["diagnostics"]["candidate_results"])


def test_tavily_candidate_results_record_file_urls(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 파일",
                        "url": "https://www.skax.co.kr/files/brochure.pdf",
                        "content": "파일",
                    }
                ]
            }

    monkeypatch.setattr(
        "services.evidence.skax_site_search_service.requests.post",
        lambda url, *, json, timeout: FakeResponse(),
    )

    result = TavilySearchClient(api_key="tavily-key").search("site:skax.co.kr 로보어드바이저")

    assert result["results"] == []
    assert result["diagnostics"]["candidate_results"][0]["skip_reason"] == "file_url"
    assert "penalty_file_url" in result["diagnostics"]["candidate_results"][0]["score_reasons"]


def test_tavily_search_client_reports_missing_config():
    result = TavilySearchClient(api_key="").search("site:skax.co.kr AI")

    assert result["results"] == []
    assert result["diagnostics"]["search_provider"] == "tavily_search"
    assert result["diagnostics"]["missing_config"] is True
    assert result["diagnostics"]["search_failure_reason"] == "missing_config"


def test_tavily_search_client_handles_api_failure(monkeypatch):
    def fake_post(url, *, json, timeout):
        raise RuntimeError("tavily down")

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", fake_post)

    result = TavilySearchClient(api_key="tavily-key").search("site:skax.co.kr AI")

    assert result["results"] == []
    assert result["diagnostics"]["search_status_code"] is None
    assert result["diagnostics"]["search_failure_reason"] == "fetch_error:RuntimeError"


def test_collect_uses_tavily_content_without_fetching_page(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 로보어드바이저 자산배분 데이터분석",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "content": "로보어드바이저 데이터분석 사업 근거",
                    }
                ]
            }

    def fake_post(url, *, json, timeout):
        return FakeResponse()

    def fetcher(url):
        raise AssertionError("Tavily content should avoid page fetch.")

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", fake_post)

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=TavilySearchClient(api_key="tavily-key"),
        fetcher=fetcher,
        max_queries=1,
    )

    evidence = result["items"][0]
    diagnostics = result["search_diagnostics"][0]
    assert evidence["source"] == "sk_ax_official"
    assert evidence["source_type"] == "company_disclosure"
    assert evidence["url"] == "https://www.skax.co.kr/finance/digital-based-financial-service"
    assert evidence["content"] == "로보어드바이저 데이터분석 사업 근거"
    assert result["stats"]["fetched_url_count"] == 0
    assert diagnostics["search_provider"] == "tavily_search"
    query_diagnostics = result["query_generation_diagnostics"]
    assert query_diagnostics["selected_features"]["related_product"] == "로보어드바이저"
    assert query_diagnostics["title_keywords"]
    assert query_diagnostics["domain_hints"][0]["name"] == "finance"
    assert query_diagnostics["generated_queries"] == result["queries"]
    candidate = diagnostics["candidate_results"][0]
    assert candidate["domain_accepted"] is True
    assert candidate["final_selected"] is True
    assert candidate["final_skip_reason"] is None
    assert candidate["candidate_relevance_score"] > 0
    assert "matched_related_product" in candidate["score_reasons"]


def test_collect_prefers_tavily_raw_content_and_truncates_before_evidence(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 로보어드바이저",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "content": "짧은 스니펫",
                        "raw_content": "원문본문" * 20,
                    }
                ]
            }

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", lambda url, *, json, timeout: FakeResponse())

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=TavilySearchClient(api_key="tavily-key", max_content_chars=5000),
        max_queries=1,
        max_content_chars=24,
    )

    evidence = result["items"][0]
    diagnostics = result["search_diagnostics"][0]
    assert evidence["content"] == ("원문본문" * 20)[:24]
    assert "짧은 스니펫" not in evidence["content"]
    assert result["stats"]["truncated_content_count"] == 1
    assert diagnostics["raw_content_included"] is True
    assert "raw_content" not in diagnostics["candidate_results"][0]
    assert "content" not in diagnostics["candidate_results"][0]


def test_collect_truncates_tavily_content(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 로보어드바이저",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "content": "긴본문" * 20,
                    }
                ]
            }

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", lambda url, *, json, timeout: FakeResponse())

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=TavilySearchClient(api_key="tavily-key", max_content_chars=5000),
        max_queries=1,
        max_content_chars=20,
    )

    assert len(result["items"][0]["content"]) == 20
    assert result["stats"]["truncated_content_count"] == 1


def test_collect_fetches_page_when_tavily_content_is_empty(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 로보어드바이저 자산배분 데이터분석",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "content": "",
                    }
                ]
            }

    def fetcher(url):
        return "<html><head><title>SK AX 금융</title></head><body><p>페이지 fetch 본문</p></body></html>"

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", lambda url, *, json, timeout: FakeResponse())

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=TavilySearchClient(api_key="tavily-key"),
        fetcher=fetcher,
        max_queries=1,
    )

    assert result["items"][0]["content"] == "페이지 fetch 본문"
    assert result["stats"]["fetched_url_count"] == 1


def test_collect_marks_tavily_candidate_final_skip_reason(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 금융 운영 효율화 로보어드바이저 자산배분 데이터분석",
                        "url": "https://www.skax.co.kr/finance/operational-efficiency-improvement",
                        "content": "로보어드바이저 자산배분 데이터분석 AI 예측 금융 서비스",
                    },
                    {
                        "title": "SK AX 디지털 금융 서비스",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "content": "금융 데이터 서비스",
                    },
                    {
                        "title": "SK AX AICC",
                        "url": "https://www.skax.co.kr/finance/aicc",
                        "content": "AICC 고객센터 서비스",
                    },
                ]
            }

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", lambda url, *, json, timeout: FakeResponse())

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=TavilySearchClient(api_key="tavily-key"),
        max_queries=1,
        max_fetch_pages=1,
    )

    assert [item["url"] for item in result["items"]] == [
        "https://www.skax.co.kr/finance/operational-efficiency-improvement"
    ]
    candidates = {
        candidate["normalized_url"]: candidate
        for candidate in result["search_diagnostics"][0]["candidate_results"]
    }
    digital_candidate = candidates["https://www.skax.co.kr/finance/digital-based-financial-service"]
    assert digital_candidate["domain_accepted"] is True
    assert digital_candidate["final_selected"] is False
    assert digital_candidate["final_skip_reason"] == "lower_relevance_than_selected"
    assert digital_candidate["candidate_relevance_score"] > 0
    assert "matched_domain_hint:금융" in digital_candidate["score_reasons"]
    assert "matched_preferred_path:/finance" in digital_candidate["score_reasons"]
    assert "content" not in digital_candidate
    assert "tavily-key" not in str(candidates)


def test_collect_can_create_top_three_tavily_evidence(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "SK AX 로보어드바이저 자산배분 데이터분석",
                        "url": "https://www.skax.co.kr/finance/operational-efficiency-improvement",
                        "content": "로보어드바이저 자산배분 데이터분석",
                    },
                    {
                        "title": "SK AX 디지털 금융 로보어드바이저",
                        "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
                        "content": "로보어드바이저 금융 데이터분석",
                    },
                    {
                        "title": "SK AX Finance",
                        "url": "https://www.skax.co.kr/finance",
                        "content": "금융 투자 AI 예측 데이터 서비스",
                    },
                ]
            }

    monkeypatch.setattr("services.evidence.skax_site_search_service.requests.post", lambda url, *, json, timeout: FakeResponse())

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=TavilySearchClient(api_key="tavily-key"),
        max_queries=1,
    )

    assert len(result["items"]) == 3
    candidates = result["search_diagnostics"][0]["candidate_results"]
    assert all(candidate["final_selected"] for candidate in candidates)
    assert all(candidate["final_skip_reason"] is None for candidate in candidates)


def test_aicc_scores_lower_than_finance_ai_candidate():
    filtered = filter_search_results(
        [
            {
                "title": "SK AX AICC",
                "snippet": "AICC 고객센터 로보어드바이저 투자 AI 서비스",
                "url": "https://www.skax.co.kr/finance/aicc",
            },
            {
                "title": "SK AX 금융 AI 예측 데이터 서비스",
                "snippet": "로보어드바이저 자산배분 데이터분석",
                "url": "https://www.skax.co.kr/finance/digital-based-financial-service",
            },
        ],
        PATENT_CONTEXT,
    )

    assert filtered[0]["url"] == "https://www.skax.co.kr/finance/digital-based-financial-service"
    aicc = next((item for item in filtered if item["url"].endswith("/aicc")), None)
    assert aicc is None or aicc["relevance_score"] < filtered[0]["relevance_score"]
    assert aicc is None or "penalty_unrelated_keyword:AICC" in aicc["score_reasons"]
    assert "matched_preferred_path:/finance" in filtered[0]["score_reasons"]


def test_blockchain_broad_hints_alone_do_not_select_unrelated_finance_pages():
    context = {
        "management_number": "P202307002-KR0",
        "title_final": "블록체인 합의 과정에서의 서명 검증 방법 및 시스템",
        "business_area": "Blockchain",
        "technology_area": "Blockchain",
        "related_product": "ChainZ",
    }

    filtered = filter_search_results(
        [
            {
                "title": "고객 결제 편의 개선",
                "snippet": "블록체인 인증 보안 디지털 자산 문구가 일부 포함된 금융 결제 서비스",
                "url": "https://www.skax.co.kr/finance/payment-convenience-improvement",
            },
            {
                "title": "ChainZ 블록체인 서명 검증",
                "snippet": "ChainZ 블록체인 합의 서명 검증 서비스",
                "url": "https://www.skax.co.kr/security/chainz",
            },
        ],
        context,
    )

    assert [item["url"] for item in filtered] == ["https://www.skax.co.kr/security/chainz"]
    assert "matched_related_product" in filtered[0]["score_reasons"]
    assert "matched_strong_term:블록체인" in filtered[0]["score_reasons"]


def test_manufacturing_query_generation_includes_mcs_hint_without_finance_terms():
    queries = build_search_queries(
        {
            "title_final": "CMP Pad의 물류 관리 시스템",
            "business_area": "제조",
            "technology_area": "CMP Pad 물류 기술",
            "related_product": "CMP Pad 물류 시스템",
        }
    )

    joined = " ".join(queries)
    assert "MCS" in joined
    assert "스마트팩토리" in joined
    assert "금융" not in joined
    assert "투자" not in joined


def test_default_search_client_prefers_tavily_when_config_exists(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setenv("GOOGLE_CUSTOM_SEARCH_API_KEY", "google-key")
    monkeypatch.setenv("GOOGLE_CUSTOM_SEARCH_CX", "cx")

    assert isinstance(default_search_client(), TavilySearchClient)


def test_default_search_client_prefers_custom_search_when_config_exists(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_CUSTOM_SEARCH_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_CUSTOM_SEARCH_CX", "cx")

    assert isinstance(default_search_client(), GoogleCustomSearchClient)


def test_default_search_client_falls_back_to_google_html_without_config(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_CX", raising=False)

    assert isinstance(default_search_client(), GoogleHtmlSearchClient)
