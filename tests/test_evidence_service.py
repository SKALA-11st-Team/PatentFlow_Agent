import json

import pytest

from schemas.evidence import Evidence
from services.evidence.api_normalizers import (
    normalize_dart_disclosures,
    normalize_gnews_response,
    normalize_naver_news_response,
)
from services.evidence.store_service import (
    merge_evidence_sources,
    save_evidence_collection,
    save_filtered_evidence_bundle,
)
from services.evidence.external_search_service import (
    MAX_SEARCH_QUERIES,
    collect_external_evidence,
    parse_query_rewrite_response,
    rewrite_search_queries,
)


def test_normalize_news_and_dart_to_common_evidence_shape():
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
    dart = {
        "list": [
            {
                "corp_code": "001",
                "corp_name": "샘플회사",
                "report_nm": "사업보고서",
                "rcept_no": "202605050001",
                "rcept_dt": "20260505",
            }
        ]
    }

    merged = merge_evidence_sources(
        [
            normalize_naver_news_response(naver, query="AI 자산배분 시장"),
            normalize_gnews_response(gnews, query="AI 자산배분 시장"),
            normalize_dart_disclosures(dart, query="AI 자산배분 시장"),
        ],
        prefix="api",
    )

    assert len(merged) == 3
    assert merged[0]["evidence_id"].startswith("api_")
    assert merged[0]["source_type"] == "news"
    assert merged[0]["title"] == "AI 자산배분 시장 성장"
    assert merged[0]["published_at"].startswith("2026-05-05T09:30:00")
    assert merged[0]["content"] == "강화학습 기반 자산배분 서비스 확대"
    assert "summary" not in merged[0]
    assert "raw_text" not in merged[0]
    assert "published_at_precision" not in merged[0]
    assert merged[2]["source_type"] == "company_disclosure"
    assert merged[2]["published_at"] == "2026-05-05"


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
            "ko": ["금융 데이터 전처리 AI", "기준금리 발표 시장 변동성", "에스케이 주식회사 금융데이터"],
            "en": ["financial data preprocessing", "market volatility AI"],
            "industry_rag": ["웰스테크 AI 에이전트 디지털 자문"],
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

    assert len(rewritten["ko"]) <= MAX_SEARCH_QUERIES
    assert any("MarketCaster" in query for query in rewritten["ko"])
    assert rewritten["industry_rag"] == ["웰스테크 AI 에이전트 디지털 자문"]
    assert rewritten["meta"]["product_query_enforced"] is True


def test_query_rewriting_parses_industry_rag_queries():
    parsed = parse_query_rewrite_response(
        json.dumps(
            {
                "ko": ["AI 투자 서비스"],
                "en": ["ai investing"],
                "industry_rag": [
                    "웰스테크 AI 에이전트 디지털 자문",
                    "로보어드바이저 자산관리 투자자문",
                ],
            },
            ensure_ascii=False,
        )
    )

    assert parsed is not None
    assert parsed["industry_rag"] == ["웰스테크 AI 에이전트 디지털 자문"]


def test_llm_query_rewriting_includes_owner_and_joint_applicant_queries(monkeypatch):
    def fake_llm_rewrite_search_queries(**kwargs):
        return {
            "ko": ["CMP 패드 자동 적재", "CMP 패드 커팅 에이징 자동화", "CMP 패드 트레이 셔틀"],
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

    assert any("에스케이 주식회사" in query for query in rewritten["ko"])
    assert any("한주반도체" in query for query in rewritten["ko"])
    assert any("CMP Pad" in query for query in rewritten["ko"])
    assert rewritten["meta"]["owner_query_enforced"] is True
    assert rewritten["meta"]["joint_applicant_query_enforced"] is True


def test_english_queries_keep_only_gnews_compatible_queries():
    from services.evidence.external_search_service import enforce_english_queries

    queries = enforce_english_queries(
        ["reinforcement learning finance", "강화학습 자산배분", "AI asset allocation", "robo-advisor market trends"],
        {
            "metadata": {
                "title": "강화학습 자산배분",
                "ipc": ["G06Q 40/06"],
            },
            "sections": {"abstract": "AI 기반 투자 포트폴리오"},
        },
        fill_to=MAX_SEARCH_QUERIES,
    )

    assert queries == ["reinforcement learning finance", "AI asset allocation", "robo advisor market trends"]


def test_collect_external_evidence_fills_gnews_queries(monkeypatch, tmp_path):
    saved_queries = []

    def fake_request_json(base_url, path, params, *, timeout=20):
        assert base_url == "http://unified.test"
        assert path == "/api/v4/search"
        query = params["q"]
        return {
            "articles": [
                {
                    "title": query,
                    "description": f"{query} description",
                    "url": f"https://example.com/{query.replace(' ', '-')}",
                    "publishedAt": "2026-05-05T00:00:00Z",
                    "source": {"name": "Example"},
                }
            ]
        }

    def fake_save_collection(**kwargs):
        saved_queries.append(kwargs["query"])
        return tmp_path / f"{kwargs['query'].replace(' ', '_')}.json"

    monkeypatch.setattr("services.evidence.external_search_service.request_json", fake_request_json)
    monkeypatch.setattr("services.evidence.external_search_service.save_evidence_collection", fake_save_collection)
    monkeypatch.setattr(
        "services.evidence.external_search_service.settings.unified_api_base_url",
        "http://unified.test",
    )
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
        ko_queries_override=[],
        en_queries_override=["reinforcement learning finance", "AI asset allocation"],
        query_limit_per_axis=MAX_SEARCH_QUERIES,
        fetch_news_full_text=False,
    )

    assert result["gnews_queries"] == ["reinforcement learning finance", "AI asset allocation"]
    assert saved_queries == result["gnews_queries"]
