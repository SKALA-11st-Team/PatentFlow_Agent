"""해외특허 domestic 뉴스 현지화(Tavily country + 현지어 쿼리) 테스트."""
import pytest

from agents.valuation_axes.market import build_market_evidence_groups
from services.evidence.api_normalizers import normalize_tavily_news_response
from services.evidence.external_search_service import (
    collect_external_evidence,
    rewrite_search_queries,
    search_news_via_tavily,
)
from services.evidence.news_localization import (
    is_foreign_country,
    resolve_domestic_locale,
)


def test_resolve_domestic_locale_maps_known_countries():
    assert resolve_domestic_locale("JP") == ("japan", "Japanese")
    assert resolve_domestic_locale("US") == ("united states", "English")
    assert resolve_domestic_locale("CN") == ("china", "Chinese")
    assert resolve_domestic_locale("de") == ("germany", "German")


def test_resolve_domestic_locale_falls_back_for_ep_and_unknown():
    # EP(유럽특허청)·미매핑 국가는 country 미지정 + 영어 폴백.
    assert resolve_domestic_locale("EP") == (None, "English")
    assert resolve_domestic_locale("ZZ") == (None, "English")


def test_resolve_domestic_locale_kr_is_korean_default():
    assert resolve_domestic_locale("KR") == (None, "Korean")
    assert resolve_domestic_locale("") == (None, "Korean")


def test_is_foreign_country():
    assert is_foreign_country("US") is True
    assert is_foreign_country("jp") is True
    assert is_foreign_country("KR") is False
    assert is_foreign_country("") is False
    assert is_foreign_country(None) is False


def test_search_news_via_tavily_adds_country_only_when_given(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr("services.evidence.external_search_service.requests.post", fake_post)

    search_news_via_tavily("japanese ai factory", max_results=3, country="japan")
    assert captured["payload"]["country"] == "japan"
    # country는 Tavily에서 topic=general일 때만 동작하므로 country 지정 시 general로 호출한다.
    assert captured["payload"]["topic"] == "general"
    assert "days" not in captured["payload"]

    captured.clear()
    search_news_via_tavily("global ai factory", max_results=3, country=None)
    assert "country" not in captured["payload"]
    assert captured["payload"]["topic"] == "news"
    assert "days" in captured["payload"]


def test_normalize_tavily_news_response_tags_domestic_source_and_country():
    raw = {"results": [{"title": "t", "url": "https://e.com/a", "content": "c"}]}
    items = normalize_tavily_news_response(
        raw, query="q", source="domestic_news", country="japan"
    )
    assert items[0]["source"] == "domestic_news"
    assert items[0]["metadata"]["country"] == "japan"

    # 기본 호출은 그대로 global_news.
    default_items = normalize_tavily_news_response(raw, query="q")
    assert default_items[0]["source"] == "global_news"
    assert default_items[0]["metadata"]["country"] is None


def test_collect_external_evidence_foreign_routes_domestic_to_tavily(monkeypatch, tmp_path):
    tavily_calls = []
    gateway_called = []

    def fake_tavily(query, *, max_results, country=None):
        tavily_calls.append((query, country))
        return {
            "results": [
                {"title": query, "url": f"https://e.com/{query}", "content": f"{query} body"}
            ]
        }

    def fake_request_json(*args, **kwargs):
        gateway_called.append(args)
        return {"items": []}

    monkeypatch.setattr("services.evidence.external_search_service.search_news_via_tavily", fake_tavily)
    monkeypatch.setattr("services.evidence.external_search_service.request_json", fake_request_json)

    result = collect_external_evidence(
        preprocessed_patent={"metadata": {"title": "ai factory"}, "sections": {"abstract": "x"}},
        patent_id=1,
        include_naver=True,
        include_gnews=False,
        include_kipris=False,
        is_foreign=True,
        domestic_country="japan",
        ko_queries_override=["スマート工場 ai"],
        en_queries_override=[],
        query_limit_per_axis=1,
        fetch_news_full_text=False,
        save=False,
    )

    # 해외특허: 게이트웨이(naver) 미호출, domestic 채널이 Tavily(country=japan)로 대체됨.
    assert gateway_called == []
    assert tavily_calls == [("スマート工場 ai", "japan")]
    assert [item["source"] for item in result["items"]] == ["domestic_news"]


def test_collect_external_evidence_domestic_kr_still_uses_gateway(monkeypatch):
    gateway_paths = []

    def fake_request_json(base_url, path, params, *, timeout=20):
        del base_url, timeout
        gateway_paths.append(path)
        return {"items": [{"title": params["query"], "originallink": "https://e.com/x", "description": "본문"}]}

    monkeypatch.setattr("services.evidence.external_search_service.request_json", fake_request_json)

    result = collect_external_evidence(
        preprocessed_patent={"metadata": {"title": "스마트팩토리"}, "sections": {"abstract": "공장 자동화"}},
        patent_id=1,
        include_naver=True,
        include_gnews=False,
        include_kipris=False,
        is_foreign=False,
        domestic_country=None,
        ko_queries_override=["스마트팩토리 자동화"],
        en_queries_override=[],
        query_limit_per_axis=1,
        fetch_news_full_text=False,
        save=False,
    )

    assert gateway_paths == ["/api/news/search"]
    assert [item["source"] for item in result["items"]] == ["naver_news"]


def test_rewrite_search_queries_foreign_skips_korean_postprocessing(monkeypatch):
    captured = {}

    def fake_llm_rewrite(**kwargs):
        captured.update(kwargs)
        return {
            "ko": ["スマート工場 予知保全", "製造 自動化 ai"],
            "en": ["smart factory", "predictive maintenance"],
            "industry_rag": [],
            "skax_site": [],
        }

    monkeypatch.setattr(
        "services.evidence.external_search_service.llm_rewrite_search_queries",
        fake_llm_rewrite,
    )

    rewritten = rewrite_search_queries(
        {
            "metadata": {"title": "smart factory", "related_product": "FactoryCaster", "assignee": ["Toyota"]},
            "sections": {"abstract": "predictive maintenance"},
        },
        use_llm=True,
        domestic_language="Japanese",
        is_foreign=True,
    )

    # 현지어 라벨이 LLM에 전달된다.
    assert captured["domestic_language"] == "Japanese"
    # 한국어 전용 후처리(제품명 `… 시장 동향`, 회사명 쿼리)는 스킵 → ko가 LLM 출력 그대로.
    assert rewritten["ko"] == ["スマート工場 予知保全", "製造 自動化 ai"]
    assert rewritten["meta"]["product_query_enforced"] is False
    assert rewritten["meta"]["owner_query_enforced"] is False


def test_build_market_evidence_groups_recognizes_domestic_news():
    evidence = [
        {"evidence_id": "e1", "source": "domestic_news", "source_type": "news"},
        {"evidence_id": "e2", "source": "global_news", "source_type": "news"},
        {"evidence_id": "e3", "source": "naver_news", "source_type": "news"},
    ]
    groups = build_market_evidence_groups(evidence)
    assert "e1" in groups["naver_news_evidence_ids"]
    assert "e3" in groups["naver_news_evidence_ids"]
    assert "e1" in groups["competition_evidence_ids"]
    assert "e2" in groups["global_news_evidence_ids"]
