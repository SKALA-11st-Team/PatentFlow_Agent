from services.evidence.skax_site_search_service import (
    build_search_queries,
    collect_skax_site_evidence,
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
    def fake_default_searcher(query):
        return [
            {
                "title": "SK AX 로보어드바이저 자산배분 데이터분석",
                "snippet": "로보어드바이저 자산배분",
                "url": "https://www.skax.co.kr/financial/robo-advisor",
            }
        ]

    def fetcher(url):
        return "<html><head><title>기본 검색</title></head><body><p>로보어드바이저 사업 근거</p></body></html>"

    monkeypatch.setattr("services.evidence.skax_site_search_service.default_html_searcher", fake_default_searcher)

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        fetcher=fetcher,
        max_queries=1,
    )

    assert result["items"][0]["url"] == "https://www.skax.co.kr/financial/robo-advisor"
    assert result["stats"]["searched_result_count"] == 1
