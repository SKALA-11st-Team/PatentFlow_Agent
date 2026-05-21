import requests

from services.evidence.skax_site_search_service import (
    GoogleCustomSearchClient,
    GoogleHtmlSearchClient,
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

    assert len(queries) == 3
    assert all(query.startswith("site:skax.co.kr") for query in queries)
    assert all("로보어드바이저" in query for query in queries)
    assert "데이터분석" in queries[0]
    assert "Data" in queries[2]
    assert any("강화학습" in query or "자산배분" in query for query in queries)


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
        "https://www.skax.co.kr/data/analytics",
    ]
    assert filtered[0]["relevance_score"] > filtered[1]["relevance_score"]
    assert "로보어드바이저" in filtered[0]["matched_keywords"]


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
    assert result["stats"]["filtered_result_count"] == 2
    assert result["stats"]["fetched_url_count"] == 1
    assert result["stats"]["collected_evidence_count"] == 1
    assert result["stats"]["skipped_url_count"] == 2
    assert result["stats"]["failed_url_count"] == 0


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
                        "url": "https://www.skax.co.kr/digital-based-financial-service",
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
    assert result["items"][0]["url"] == "https://www.skax.co.kr/digital-based-financial-service"
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
                        "link": "https://www.skax.co.kr/digital-based-financial-service",
                        "snippet": "로보어드바이저 데이터분석",
                    },
                    {
                        "title": "외부 뉴스",
                        "link": "https://news.example.com/skax/robo-advisor",
                        "snippet": "외부",
                    },
                    {
                        "title": "중복",
                        "link": "https://www.skax.co.kr/digital-based-financial-service#section",
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
    assert captured["params"]["siteSearch"] == "skax.co.kr"
    assert captured["params"]["siteSearchFilter"] == "i"
    assert result["results"] == [
        {
            "title": "SK AX 로보어드바이저",
            "url": "https://www.skax.co.kr/digital-based-financial-service",
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
                        "link": "https://www.skax.co.kr/digital-based-financial-service",
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
    assert evidence["url"] == "https://www.skax.co.kr/digital-based-financial-service"
    assert diagnostics["search_provider"] == "google_custom_search_json"
    assert diagnostics["parsed_result_count"] == 1


def test_default_search_client_prefers_custom_search_when_config_exists(monkeypatch):
    monkeypatch.setenv("GOOGLE_CUSTOM_SEARCH_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_CUSTOM_SEARCH_CX", "cx")

    assert isinstance(default_search_client(), GoogleCustomSearchClient)


def test_default_search_client_falls_back_to_google_html_without_config(monkeypatch):
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CUSTOM_SEARCH_CX", raising=False)

    assert isinstance(default_search_client(), GoogleHtmlSearchClient)
