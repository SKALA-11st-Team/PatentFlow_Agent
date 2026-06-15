import threading
import time

import requests

from services.evidence.skax_site_search_service import (
    EmptySearchClient,
    TavilySearchClient,
    build_query_generation_plan,
    build_search_queries,
    collect_skax_site_evidence,
    default_search_client,
    filter_search_results,
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
    # query rewriting(override) 검색어가 rule-based보다 우선 배치된다.
    assert plan["generated_queries"][:2] == [
        "site:skax.co.kr 로보어드바이저 금융 자산관리",
        "site:skax.co.kr 디지털 금융 서비스 AI 예측",
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

    # override 검색어가 1순위로 실제 검색에 나간다(rule-based보다 먼저).
    assert seen_queries[0] == "site:skax.co.kr 디지털 금융 서비스 AI 예측"
    assert result["query_generation_diagnostics"]["query_source"] == "rule_based_with_query_rewriting"


def test_collect_skax_site_evidence_searches_queries_concurrently():
    active = 0
    max_active = 0
    lock = threading.Lock()

    class SlowSearchClient:
        def search(self, query, *, max_results=5):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return {
                "results": [
                    {
                        "title": query,
                        "url": f"https://www.skax.co.kr/finance/{query.rsplit(' ', 1)[-1]}",
                        "snippet": "로보어드바이저 데이터분석 금융",
                        "content": "로보어드바이저 데이터분석 금융",
                    }
                ],
                "diagnostics": {"search_provider": "fake"},
            }

    result = collect_skax_site_evidence(
        PATENT_CONTEXT,
        search_client=SlowSearchClient(),
        queries_override=["로보어드바이저 one", "로보어드바이저 two"],
        max_queries=2,
        max_results_per_query=1,
    )

    assert max_active > 1
    # override 검색어가 우선 배치되어 max_queries=2 슬롯을 채운다.
    assert result["queries"][:2] == [
        "site:skax.co.kr 로보어드바이저 one",
        "site:skax.co.kr 로보어드바이저 two",
    ]
    assert [diagnostic["query"] for diagnostic in result["search_diagnostics"][:2]] == result["queries"][:2]


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

    # 관련성 점수로 더는 버리지 않는다. skax.co.kr 비파일 URL은 모두 통과하고,
    # 점수가 높은 항목이 앞에 오도록 정렬만 한다(외부 도메인/PDF/중복은 여전히 제외).
    assert [item["url"] for item in filtered] == [
        "https://www.skax.co.kr/financial/robo-advisor",
        "https://www.skax.co.kr/data/analytics",
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
    assert evidence["management_number"] == "P202405001-KR0"
    assert evidence["related_product"] == "로보어드바이저"
    assert evidence["business_area"] == "Data"
    assert evidence["technology_area"] == "데이터분석"
    assert evidence["relevance_score"] > 0
    assert "로보어드바이저" in evidence["matched_keywords"]
    assert result["stats"]["generated_query_count"] == 1
    assert result["stats"]["searched_result_count"] == 4
    # 관련성으로 더는 버리지 않으므로 skax 비파일 URL 2건이 모두 통과한다(외부/PNG 제외).
    assert result["stats"]["filtered_result_count"] == 2
    assert result["stats"]["fetched_url_count"] == 1
    assert result["stats"]["collected_evidence_count"] == 1
    # data/analytics가 이제 필터를 통과하므로(외부/PNG만 제외) 덜 버려진다.
    assert result["stats"]["skipped_url_count"] == 2
    assert result["stats"]["failed_url_count"] == 0


def test_collect_sk_related_media_requires_sk_ax_or_cnc_body_marker():
    fetched_urls = []

    def searcher(query):
        # 관련매체 변형 쿼리('SK AX ...')에만 결과를 준다(도메인 제한은 include_domains가 처리).
        if not query.startswith("SK AX"):
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
    # Tavily는 site: 연산자를 지원하지 않으므로 도메인 제한은 include_domains로만 하고,
    # 쿼리 텍스트에서는 site:<domain> 토큰을 제거해 키워드만 보낸다.
    assert captured["json"]["query"] == "로보어드바이저"
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


def test_blockchain_related_page_ranks_first_unrelated_page_still_kept():
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

    # 관련성 점수로 더는 버리지 않는다(실제 관련성 판단은 압축 단계로 이관).
    # 두 skax 페이지 모두 통과하되, 대상 특허와 직접 관련된 ChainZ 페이지가
    # 더 높은 점수로 맨 앞에 온다.
    assert filtered[0]["url"] == "https://www.skax.co.kr/security/chainz"
    assert "matched_related_product" in filtered[0]["score_reasons"]
    assert "matched_strong_term:블록체인" in filtered[0]["score_reasons"]
    assert "https://www.skax.co.kr/finance/payment-convenience-improvement" in [
        item["url"] for item in filtered
    ]


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

    assert isinstance(default_search_client(), TavilySearchClient)


def test_default_search_client_returns_empty_client_without_config(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    assert isinstance(default_search_client(), EmptySearchClient)


def test_empty_search_client_reports_missing_config():
    result = EmptySearchClient().search("site:skax.co.kr AI")

    assert result["results"] == []
    assert result["diagnostics"]["search_provider"] == "no_search_provider"
    assert result["diagnostics"]["missing_config"] is True
    assert result["diagnostics"]["search_failure_reason"] == "missing_config"


def test_default_search_uses_tavily_results_without_fallback(monkeypatch):
    from services.evidence.skax_site_search_service import search_with_default_fallback

    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setattr(
        TavilySearchClient,
        "search",
        lambda self, query, max_results: {
            "results": [{"title": "SK AX 제조", "url": "https://www.skax.co.kr/manufacturing"}],
            "diagnostics": {
                "search_provider": "tavily_search",
                "search_failure_reason": None,
            },
        },
    )

    result = search_with_default_fallback("site:skax.co.kr 제조", max_results=3)

    assert len(result["results"]) == 1
    assert result["diagnostics"]["fallback_used"] is False
    assert [item["search_provider"] for item in result["diagnostics"]["fallback_attempts"]] == [
        "tavily_search",
    ]


def test_default_search_returns_empty_when_tavily_unset(monkeypatch):
    from services.evidence.skax_site_search_service import search_with_default_fallback

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = search_with_default_fallback("site:skax.co.kr 제조", max_results=3)

    assert result["results"] == []
    assert result["diagnostics"]["fallback_used"] is False
    assert result["diagnostics"]["search_failure_reason"] == "missing_config"
    assert [item["search_provider"] for item in result["diagnostics"]["fallback_attempts"]] == [
        "no_search_provider",
    ]
