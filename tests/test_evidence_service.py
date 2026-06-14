import json
import threading
import time

import pytest

from schemas.evidence import Evidence
from services.evidence.api_normalizers import (
    normalize_gnews_response,
    normalize_naver_news_response,
)
from services.evidence.store_service import (
    merge_evidence_sources,
    save_evidence_collection,
    save_filtered_evidence_bundle,
    save_skax_site_search_result,
)
from services.evidence.external_search_service import (
    MAX_SEARCH_QUERIES,
    annotate_evidence_quality,
    collect_external_evidence,
    parse_query_rewrite_response,
    request_json,
    rewrite_search_queries,
)


def test_normalize_news_to_common_evidence_shape():
    naver = {
        "items": [
            {
                "title": "<b>AI</b> 자산배분 시장 성장",
                "originallink": "https://example.com/news",
                "link": "https://search.naver.com/news",
                "description": "강화학습 기반 자산배분 서비스 확대",
                "pubDate": "Tue, 05 May 2026 09:30:00 +0900",
            }
        ]
    }
    gnews = {
        "articles": [
            {
                "title": "AI portfolio products expand",
                "description": "Global wealth platforms adopt AI.",
                "url": "https://example.com/gnews",
                "publishedAt": "2026-05-04T11:00:00Z",
                "source": {"name": "Example"},
            }
        ]
    }
    merged = merge_evidence_sources(
        [
            normalize_naver_news_response(naver, query="AI 자산배분 시장"),
            normalize_gnews_response(gnews, query="AI 자산배분 시장"),
        ],
        prefix="api",
    )

    assert len(merged) == 2
    assert merged[0]["evidence_id"].startswith("api_")
    assert merged[0]["source_type"] == "news"
    assert merged[0]["title"] == "AI 자산배분 시장 성장"
    assert merged[0]["published_at"].startswith("2026-05-05T09:30:00")
    assert merged[0]["content"] == "강화학습 기반 자산배분 서비스 확대"
    assert "summary" not in merged[0]
    assert "raw_text" not in merged[0]
    assert "published_at_precision" not in merged[0]


def test_evidence_schema_content_alias_and_save(tmp_path):
    evidence = Evidence(
        evidence_id="news_001",
        source_type="news",
        source="naver_news",
        title="뉴스",
        collected_at="2026-05-05T00:00:00+09:00",
        summary="요약",
    )

    collection_path = save_evidence_collection(
        source_type="news",
        source="naver_news",
        query="테스트",
        patent_id="KR10-TEST",
        items=[evidence],
        output_dir=tmp_path / "api_evidence",
    )

    collection = json.loads(collection_path.read_text(encoding="utf-8"))

    assert evidence.content == "요약"
    assert collection["items"][0]["content"] == "요약"
    assert "summary" not in collection["items"][0]


def test_merge_deduplicates_by_url():
    first = normalize_naver_news_response(
        {
            "items": [
                {
                    "title": "중복 뉴스",
                    "originallink": "https://example.com/same",
                    "description": "A",
                }
            ]
        },
        query="q",
    )
    second = normalize_gnews_response(
        {
            "articles": [
                {
                    "title": "중복 뉴스",
                    "url": "https://example.com/same",
                    "description": "B",
                }
            ]
        },
        query="q",
    )
    merged = merge_evidence_sources([first, second], prefix="api")
    assert len(merged) == 1


def test_save_filtered_evidence_bundle_groups_news_and_industry(tmp_path):
    path = save_filtered_evidence_bundle(
        patent_id=1,
        news_items=[
            {
                "evidence_id": "news_1",
                "source_type": "news",
                "source": "naver_news",
                "title": "뉴스",
                "collected_at": "2026-05-05T00:00:00+09:00",
                "content": "뉴스 본문",
            }
        ],
        industry_items=[
            {
                "evidence_id": "chunk_1",
                "source_type": "industry_report",
                "source": "pgvector",
                "title": "산업 청크",
                "collected_at": "2026-05-05T00:00:00+09:00",
                "content": "청크 본문",
            }
        ],
        output_dir=tmp_path / "filtered_evidence",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["stats"]["news_count"] == 1
    assert payload["stats"]["industry_report_count"] == 1
    assert len(payload["items"]) == 2
    assert payload["industry_report"][0]["evidence_id"] == "chunk_1"


def test_query_rewriting_fails_when_llm_is_disabled():
    with pytest.raises(RuntimeError, match="use_llm is disabled"):
        rewrite_search_queries(
            {
                "metadata": {
                    "title": "강화학습 자산배분",
                    "title_eng": "Asset allocation with reinforcement learning",
                    "ipc": ["G06Q 40/06"],
                },
                "sections": {"abstract": "AI 기반 투자 포트폴리오"},
            },
            missing_evidence=["market evidence 부족", "competitor evidence 부족"],
            use_llm=False,
        )


def test_llm_query_rewriting_keeps_one_related_product_query(monkeypatch):
    def fake_llm_rewrite_search_queries(**kwargs):
        return {
            "domestic": ["금융 데이터 전처리 AI", "기준금리 발표 시장 변동성", "에스케이 주식회사 금융데이터"],
            "en": ["financial data preprocessing", "market volatility AI"],
            "industry_rag": ["웰스테크 AI 에이전트 디지털 자문"],
            "skax_site": ["로보어드바이저 금융 자산관리"],
        }

    monkeypatch.setattr(
        "services.evidence.external_search_service.llm_rewrite_search_queries",
        fake_llm_rewrite_search_queries,
    )

    rewritten = rewrite_search_queries(
        {
            "metadata": {
                "title": "금융시장 데이터 전처리",
                "related_product": "MarketCaster",
            },
            "sections": {"abstract": "금융 데이터 이상치를 탐지하고 전처리하는 기술"},
        },
        use_llm=True,
    )

    assert len(rewritten["domestic"]) <= MAX_SEARCH_QUERIES
    assert any("MarketCaster" in query for query in rewritten["domestic"])
    assert rewritten["industry_rag"] == ["웰스테크 AI 에이전트 디지털 자문"]
    # skax_site는 LLM 변형만 담는다(제품명 그대로 검색어는 build_query_generation_plan의
    # rule-based 후보가 담당하므로 여기서 따로 주입하지 않는다).
    assert rewritten["skax_site"] == [
        "site:skax.co.kr 로보어드바이저 금융 자산관리",
    ]
    assert rewritten["meta"]["product_query_enforced"] is True


def test_llm_query_rewriting_keeps_product_query_even_when_previous_queries_exhaust_ko(monkeypatch):
    def fake_llm_rewrite_search_queries(**kwargs):
        return {
            "domestic": [],
            "en": ["robo advisor asset allocation"],
            "industry_rag": [],
            "skax_site": [],
        }

    monkeypatch.setattr(
        "services.evidence.external_search_service.llm_rewrite_search_queries",
        fake_llm_rewrite_search_queries,
    )

    rewritten = rewrite_search_queries(
        {
            "metadata": {
                "title": "강화학습 기반 자산배분",
                "related_product": "로보어드바이저",
            },
            "sections": {"abstract": "강화학습 기반 투자전략 생성"},
        },
        previous_queries=[
            "로보어드바이저 시장 동향",
            "로보어드바이저 기술 적용",
            "로보어드바이저 기업 동향",
            "로보어드바이저",
        ],
        use_llm=True,
    )

    assert rewritten["domestic"] == ["로보어드바이저 시장 동향"]
    assert rewritten["meta"]["product_query_enforced"] is True


def test_query_rewriting_parses_industry_rag_queries():
    parsed = parse_query_rewrite_response(
        json.dumps(
            {
                "domestic": ["AI 투자 서비스"],
                "en": ["ai investing"],
                "industry_rag": [
                    "웰스테크 AI 에이전트 디지털 자문",
                    "로보어드바이저 자산관리 투자자문",
                ],
                "skax_site": [
                    "로보어드바이저 금융 자산관리",
                    "site:skax.co.kr 디지털 금융 서비스 AI 예측",
                ],
            },
            ensure_ascii=False,
        )
    )

    assert parsed is not None
    assert parsed["industry_rag"] == ["웰스테크 AI 에이전트 디지털 자문"]
    assert parsed["skax_site"] == [
        "site:skax.co.kr 로보어드바이저 금융 자산관리",
        "site:skax.co.kr 디지털 금융 서비스 AI 예측",
    ]


def test_llm_query_rewriting_includes_owner_and_joint_applicant_queries(monkeypatch):
    def fake_llm_rewrite_search_queries(**kwargs):
        return {
            "domestic": ["CMP 패드 자동 적재", "CMP 패드 커팅 에이징 자동화", "CMP 패드 트레이 셔틀"],
            "en": ["CMP pad automatic loading", "wafer polishing pad handling"],
        }

    monkeypatch.setattr(
        "services.evidence.external_search_service.llm_rewrite_search_queries",
        fake_llm_rewrite_search_queries,
    )

    rewritten = rewrite_search_queries(
        {
            "metadata": {
                "title": "CMP Pad의 자동 적재 시스템",
                "assignee": ["에스케이 주식회사", "(주)한주하이텍"],
                "related_product": "CMP Pad Press Cutting, Aging",
                "joint_application": 1,
                "joint_applicant_name": "한주반도체",
            },
            "sections": {"abstract": "CMP Pad 커팅 및 에이징 공정 자동 적재 기술"},
        },
        use_llm=True,
    )

    assert any("에스케이 주식회사" in query for query in rewritten["domestic"])
    assert any("한주반도체" in query for query in rewritten["domestic"])
    assert any("CMP Pad" in query for query in rewritten["domestic"])
    assert rewritten["meta"]["owner_query_enforced"] is True
    assert rewritten["meta"]["joint_applicant_query_enforced"] is True


def test_english_queries_keep_only_gnews_compatible_queries():
    from services.evidence.external_search_service import enforce_english_queries

    # EVID-14: preprocessed_patent·fill_to는 무시되던 데드 파라미터라 시그니처에서 제거됨.
    queries = enforce_english_queries(
        ["reinforcement learning finance", "강화학습 자산배분", "AI asset allocation", "robo-advisor market trends"],
    )

    assert queries == ["reinforcement learning finance", "AI asset allocation", "robo advisor market trends"]


def test_collect_external_evidence_fills_gnews_queries(monkeypatch, tmp_path):
    saved_queries = []
    tavily_queries = []

    def fake_tavily_news(query, *, max_results):
        tavily_queries.append((query, max_results))
        return {
            "results": [
                {
                    "title": query,
                    "url": f"https://example.com/{query.replace(' ', '-')}",
                    "content": f"{query} description",
                    "published_date": "2026-05-05T00:00:00Z",
                }
            ]
        }

    def fake_save_collection(**kwargs):
        saved_queries.append(kwargs["query"])
        return tmp_path / f"{kwargs['query'].replace(' ', '_')}.json"

    monkeypatch.setattr("services.evidence.external_search_service.search_global_news_via_tavily", fake_tavily_news)
    monkeypatch.setattr("services.evidence.external_search_service.save_evidence_collection", fake_save_collection)
    result = collect_external_evidence(
        preprocessed_patent={
            "metadata": {
                "title": "강화학습 자산배분",
                "ipc": ["G06Q 40/06"],
            },
            "sections": {"abstract": "AI 기반 투자 포트폴리오"},
        },
        patent_id=1,
        include_naver=False,
        include_gnews=True,
        include_kipris=False,
        domestic_queries_override=[],
        en_queries_override=["reinforcement learning finance", "AI asset allocation"],
        query_limit_per_axis=MAX_SEARCH_QUERIES,
        fetch_news_full_text=False,
    )

    assert result["gnews_queries"] == ["reinforcement learning finance", "AI asset allocation"]
    assert sorted(query for query, _ in tavily_queries) == sorted(result["gnews_queries"])
    assert sorted(saved_queries) == sorted(result["gnews_queries"])


def test_collect_external_evidence_searches_news_queries_concurrently(monkeypatch):
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_request_json(base_url, path, params, *, timeout=20):
        nonlocal active, max_active
        del base_url, timeout
        assert path == "/api/news/search"
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        query = params["query"]
        return {
            "items": [
                {
                    "title": query,
                    "originallink": f"https://example.com/{query}",
                    "description": "뉴스 본문",
                    "pubDate": "Tue, 05 May 2026 09:30:00 +0900",
                }
            ]
        }

    monkeypatch.setattr("services.evidence.external_search_service.request_json", fake_request_json)

    result = collect_external_evidence(
        preprocessed_patent={"metadata": {"title": "스마트팩토리"}, "sections": {"abstract": "공장 자동화"}},
        patent_id=1,
        include_naver=True,
        include_gnews=False,
        include_kipris=False,
        domestic_queries_override=["스마트팩토리 레이아웃", "제조 자동화"],
        en_queries_override=[],
        query_limit_per_axis=2,
        fetch_news_full_text=False,
        save=False,
    )

    assert max_active > 1
    assert result["queries"] == ["스마트팩토리 레이아웃", "제조 자동화"]
    assert [item["title"] for item in result["items"]] == ["스마트팩토리 레이아웃", "제조 자동화"]


def test_collect_external_evidence_uses_configured_news_results_per_query(monkeypatch, tmp_path):
    observed = []
    tavily_calls = []

    def fake_request_json(base_url, path, params, *, timeout=20):
        del base_url, timeout
        observed.append((path, dict(params)))
        assert path == "/api/news/search"
        assert params["display"] == 3
        return {"items": []}

    def fake_tavily_news(query, *, max_results):
        tavily_calls.append((query, max_results))
        return {"results": []}

    monkeypatch.setattr("services.evidence.external_search_service.request_json", fake_request_json)
    monkeypatch.setattr("services.evidence.external_search_service.search_global_news_via_tavily", fake_tavily_news)
    monkeypatch.setattr("services.evidence.external_search_service.save_evidence_collection", lambda **kwargs: tmp_path / "x.json")
    monkeypatch.setattr("services.evidence.external_search_service.settings.news_results_per_query", 3, raising=False)

    collect_external_evidence(
        preprocessed_patent={"metadata": {"title": "챗봇"}, "sections": {"abstract": "AI 챗봇"}},
        patent_id=1,
        include_naver=True,
        include_gnews=True,
        domestic_queries_override=["대화형 AI 챗봇"],
        en_queries_override=["conversational AI chatbot"],
        query_limit_per_axis=1,
        fetch_news_full_text=False,
    )

    # 네이버는 통합 서버, 글로벌 뉴스(gnews 자리)는 Tavily로 호출된다.
    assert [path for path, _ in observed] == ["/api/news/search"]
    assert tavily_calls == [("conversational AI chatbot", 3)]


def test_request_json_sends_unified_api_key_header(monkeypatch):
    observed = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_get(url, params, headers, timeout):
        observed["url"] = url
        observed["params"] = params
        observed["headers"] = headers
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setenv("UNIFIED_API_KEY", "gateway-secret")
    monkeypatch.setattr("services.evidence.external_search_service.requests.get", fake_get)

    assert request_json("http://unified.test", "/api/news/search", {"query": "AI"}) == {"ok": True}
    assert observed["headers"] == {"X-API-Key": "gateway-secret"}


def test_request_json_blocks_metadata_service_base_url():
    with pytest.raises(Exception, match="metadata"):
        request_json("http://169.254.169.254", "/latest/meta-data", {})


def test_validate_unified_api_base_url_allows_private_and_loopback_gateways():
    from services.evidence.external_search_service import validate_unified_api_base_url

    # 게이트웨이는 사설망/로컬에 정상 배치되므로 사설·loopback IP·localhost는 허용한다.
    validate_unified_api_base_url("http://10.0.0.5:8080")
    validate_unified_api_base_url("http://192.168.1.10:8080")
    validate_unified_api_base_url("http://127.0.0.1:8080")
    validate_unified_api_base_url("http://localhost:8080")


def test_validate_unified_api_base_url_blocks_link_local_literal_ip():
    import requests
    from services.evidence.external_search_service import validate_unified_api_base_url

    with pytest.raises(requests.RequestException, match="metadata"):
        validate_unified_api_base_url("http://169.254.169.254:8080")


def test_validate_unified_api_base_url_blocks_domain_resolving_to_metadata_ip(monkeypatch):
    import requests
    from services.evidence import external_search_service

    # DNS rebinding: 도메인이 메타데이터 IP로 해석되면 차단한다.
    monkeypatch.setattr(
        external_search_service.socket,
        "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))],
    )
    with pytest.raises(requests.RequestException, match="metadata"):
        external_search_service.validate_unified_api_base_url("http://evil.example.com:8080")


def test_validate_unified_api_base_url_allows_domain_resolving_to_private_ip(monkeypatch):
    from services.evidence import external_search_service

    # 도메인이 사설 IP로 해석되면(예: 쿠버네티스 서비스) 허용한다.
    monkeypatch.setattr(
        external_search_service.socket,
        "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("10.1.2.3", 0))],
    )
    external_search_service.validate_unified_api_base_url("http://unified-api:8080")


def test_annotate_evidence_quality_surfaces_low_relevance_warning():
    items = [
        {
            "evidence_id": "api_001",
            "source_type": "news",
            "source": "gnews",
            "title": "Unrelated retail trend",
            "content": "Fashion store expansion and consumer goods logistics.",
            "metadata": {},
        },
        {
            "evidence_id": "api_002",
            "source_type": "news",
            "source": "gnews",
            "title": "AI portfolio market expands",
            "content": "Robo advisor and portfolio analytics adoption grows.",
            "metadata": {},
        },
    ]

    result = annotate_evidence_quality(
        items,
        preprocessed_patent={
            "metadata": {"title": "AI portfolio robo advisor", "related_product": "portfolio analytics"},
            "sections": {"abstract": "AI portfolio analytics for robo advisor services"},
        },
    )

    assert any("api_001:evidence_quality_low" in warning for warning in result["warnings"])
    assert items[0]["metadata"]["quality_warning"] == "no_patent_keyword_match"
    assert items[1]["metadata"]["matched_keyword_count"] > 0


def test_collect_external_evidence_hard_surfaces_gateway_failure(monkeypatch, tmp_path):
    import requests

    def failing_request_json(base_url, path, params, *, timeout=20):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("services.evidence.external_search_service.request_json", failing_request_json)
    monkeypatch.setattr(
        "services.evidence.external_search_service.save_evidence_collection",
        lambda **kwargs: tmp_path / "x.json",
    )

    result = collect_external_evidence(
        preprocessed_patent={"metadata": {"title": "AI 챗봇"}, "sections": {"abstract": "대화형 AI"}},
        patent_id=1,
        include_naver=True,
        include_gnews=True,
        include_kipris=False,
        domestic_queries_override=["대화형 AI 챗봇"],
        en_queries_override=["conversational AI chatbot"],
        query_limit_per_axis=1,
        fetch_news_full_text=False,
        save=False,
    )

    # EXT-03: 모든 게이트웨이 호출 실패 + 증거 0건 → 조용한 통과가 아니라 hard-surface.
    assert result["items"] == []
    assert result["attempted_calls"] > 0
    assert result["failed_calls"] == result["attempted_calls"]
    assert result["gateway_unreachable"] is True
    assert result["missing_reason"].startswith("external_gateway_failed:")
    assert any("external_evidence_unavailable" in warning for warning in result["warnings"])


def test_collect_external_evidence_empty_results_not_flagged_as_gateway_failure(monkeypatch, tmp_path):
    def empty_request_json(base_url, path, params, *, timeout=20):
        # 예외 없이 빈 결과를 반환(정상적인 '근거 없음').
        return {"items": []} if path == "/api/news/search" else {"articles": []}

    monkeypatch.setattr("services.evidence.external_search_service.request_json", empty_request_json)
    monkeypatch.setattr(
        "services.evidence.external_search_service.save_evidence_collection",
        lambda **kwargs: tmp_path / "x.json",
    )

    result = collect_external_evidence(
        preprocessed_patent={"metadata": {"title": "AI 챗봇"}, "sections": {"abstract": "대화형 AI"}},
        patent_id=1,
        include_naver=True,
        include_gnews=True,
        include_kipris=False,
        domestic_queries_override=["대화형 AI 챗봇"],
        en_queries_override=["conversational AI chatbot"],
        query_limit_per_axis=1,
        fetch_news_full_text=False,
        save=False,
    )

    # 정상 경로의 0건은 게이트웨이 실패로 오인하지 않는다(false-positive 방지).
    assert result["items"] == []
    assert result["gateway_unreachable"] is False
    assert result["missing_reason"] is None
    assert not any("external_evidence_unavailable" in warning for warning in result["warnings"])


def test_save_skax_site_search_result_persists_queries_and_diagnostics(tmp_path):
    # collect_skax_site_evidence 반환 형태를 그대로 저장한다.
    skax_result = {
        "items": [
            {
                "evidence_id": "skax_site_001",
                "title": "AI 모델 서빙 - SK AX",
                "url": "https://www.skax.co.kr/solution/ai",
                "search_query": "site:skax.co.kr AccuInsight+ Runtime",
            }
        ],
        "queries": [
            "site:skax.co.kr AccuInsight+ Runtime",
            "site:skax.co.kr 모델 서빙 플랫폼",
        ],
        "stats": {"collected_evidence_count": 1, "searched_result_count": 4},
        "query_generation_diagnostics": {"query_source": "rule_based_with_query_rewriting"},
        "search_diagnostics": [
            {"query": "site:skax.co.kr AccuInsight+ Runtime", "tavily_effective_query": "AccuInsight+ Runtime"},
        ],
        "failed_urls": ["https://www.skax.co.kr/broken"],
        "warning": None,
    }

    path = save_skax_site_search_result(
        patent_id="P123",
        skax_result=skax_result,
        output_dir=tmp_path,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    # 실제 전송 쿼리·후보 진단이 그대로 보존돼 디버깅에 쓸 수 있어야 한다.
    assert saved["queries"] == skax_result["queries"]
    assert saved["search_diagnostics"][0]["tavily_effective_query"] == "AccuInsight+ Runtime"
    assert saved["failed_urls"] == ["https://www.skax.co.kr/broken"]
    assert saved["items"][0]["evidence_id"] == "skax_site_001"
    assert saved["patent_id"] == "P123"
    assert path.name == "P123_skax_site_search.json"


# AG-02: LLM 쿼리 재작성이 실패해도 평가가 500으로 죽지 않고 메타데이터 기반 폴백으로 진행된다.
def test_rewrite_search_queries_falls_back_when_llm_raises(monkeypatch):
    def broken_llm_rewrite(**kwargs):
        raise RuntimeError("LLM query rewriting response was not valid JSON.")

    monkeypatch.setattr(
        "services.evidence.external_search_service.llm_rewrite_search_queries",
        broken_llm_rewrite,
    )

    rewritten = rewrite_search_queries(
        {
            "metadata": {
                "title": "금융시장 데이터 전처리",
                "related_product": "MarketCaster",
            },
            "sections": {"abstract": "금융 데이터 이상치를 탐지하고 전처리하는 기술"},
        },
        use_llm=True,
    )

    assert rewritten["meta"]["rewrite_source"] == "fallback"
    assert "RuntimeError" in rewritten["meta"]["llm_error"]
    # ensure_* 인젝터가 제품명 기반 결정적 쿼리를 채워 degraded 수집이 가능해야 한다.
    assert any("MarketCaster" in query for query in rewritten["domestic"])


# AG-03: 외부 응답 형태 드리프트(normalize 단계 TypeError 등)가 수집 실패로 집계될 뿐
# collect_external_evidence 전체를 던지게 하지 않는다.
def test_collect_external_evidence_survives_malformed_source_payload(monkeypatch):
    monkeypatch.setattr(
        "services.evidence.external_search_service.request_json",
        lambda *args, **kwargs: {"items": None},
    )

    def broken_normalize(raw, *, query):
        raise TypeError("'NoneType' object is not iterable")

    monkeypatch.setattr(
        "services.evidence.external_search_service.normalize_naver_news_response",
        broken_normalize,
    )

    result = collect_external_evidence(
        preprocessed_patent={"metadata": {"title": "테스트 특허"}, "sections": {"abstract": "초록"}},
        domestic_queries_override=["테스트 쿼리"],
        en_queries_override=[],
        include_gnews=False,
        include_kipris=False,
        save=False,
    )

    assert result["items"] == []
    assert any("naver_news call failed" in warning and "TypeError" in warning for warning in result["warnings"])
