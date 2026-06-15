from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urldefrag, urlparse
import json
import logging
import os
import re

import requests

from app.config import settings

_logger = logging.getLogger("patentflow.skax_search")

from services.evidence.store_service import ensure_evidence_ids, now_iso


SK_AX_DOMAIN = "skax.co.kr"
SK_AX_SOURCE = "sk_ax_official"
SK_GROUP_OWNED_MEDIA_SOURCE = "sk_group_owned_media"
SK_RELATED_MEDIA_DOMAINS = ("skcareersjournal.com", "openapi.sk.com")
SK_OWNED_DOMAINS = (SK_AX_DOMAIN, *SK_RELATED_MEDIA_DOMAINS)
SK_RELATED_MEDIA_REQUIRED_MARKERS = (
    "sk ax",
    "sk c&c",
    "sk㈜ ax",
    "sk주식회사 ax",
    "sk주식회사 c&c",
)
DEFAULT_MAX_QUERIES = 5
DEFAULT_MAX_RESULTS_PER_QUERY = 5
DEFAULT_MAX_FETCH_PAGES = 5
DEFAULT_MAX_EVIDENCE_ITEMS = 3
DEFAULT_MAX_CONTENT_CHARS = 5000
DEFAULT_SEARCH_WORKERS = 4
DEFAULT_SEARCH_TIMEOUT_SECONDS = settings.skax_search_timeout_seconds
DEFAULT_API_ERROR_BODY_PREVIEW_CHARS = 1000
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SKIPPED_FILE_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".zip",
}
TITLE_STOPWORDS = {
    "및",
    "또는",
    "으로",
    "에서",
    "에게",
    "하는",
    "위한",
    "통한",
    "기반",
    "적용한",
    "상품",
    "트렌드",
    "반영한",
    "모델",
    "시스템",
    "방법",
    "장치",
    "프로그램",
}
DOMAIN_HINT_PROFILES = [
    {
        "name": "finance",
        "triggers": ("로보어드바이저", "자산배분", "투자", "포트폴리오", "금융", "예측"),
        "hints": ("금융", "투자", "자산관리", "AI", "예측", "데이터 서비스"),
        "preferred_paths": ("/finance",),
    },
    {
        "name": "blockchain_security",
        "triggers": ("블록체인", "chainz", "합의", "서명", "인증"),
        "hints": ("블록체인", "인증", "보안", "디지털 자산"),
        "preferred_paths": ("/blockchain", "/security", "/trust"),
    },
    {
        "name": "manufacturing_smart_factory",
        "triggers": ("cmp", "반도체", "제조", "공정", "물류", "설비", "agv", "컨베이어", "스마트팩토리"),
        "hints": ("제조", "스마트팩토리", "MCS", "공정", "물류", "자동화", "설비", "설비 제어"),
        "preferred_paths": ("/manufacturing", "/industry", "/smart-factory", "/enterprise"),
    },
]

SearchResult = dict[str, Any]
Searcher = Callable[[str], list[SearchResult]]
Fetcher = Callable[[str], str]


class SearchClient:
    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> dict[str, Any]:
        raise NotImplementedError


class EmptySearchClient(SearchClient):
    """SK AX 사이트 검색 키(Tavily)가 없을 때 쓰는 무동작 클라이언트.

    과거에는 Google CSE/HTML 폴백을 썼으나 모두 제거됐다. 키가 없으면 검색을
    수행하지 못하므로, 'missing_config'를 진단에 남기고 빈 결과를 반환한다.
    """

    provider = "no_search_provider"

    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> dict[str, Any]:
        del max_results
        diagnostics = build_empty_search_diagnostics(query)
        diagnostics.update(
            {
                "search_provider": self.provider,
                "missing_config": True,
                "search_failure_reason": "missing_config",
            }
        )
        return {"results": [], "diagnostics": diagnostics}


class TavilySearchClient(SearchClient):
    provider = "tavily_search"
    default_max_results = 3
    include_raw_content = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
        max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY")
        self.timeout = timeout
        self.max_content_chars = max_content_chars

    @classmethod
    def has_config(cls) -> bool:
        return bool(os.getenv("TAVILY_API_KEY"))

    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> dict[str, Any]:
        max_results = min(self.default_max_results, max(1, int(max_results)))
        diagnostics = build_empty_search_diagnostics(query)
        diagnostics.update(
            {
                "search_provider": self.provider,
                "search_request_url": TAVILY_SEARCH_URL,
                "missing_config": False,
                "raw_content_included": self.include_raw_content,
                "max_results": max_results,
                "max_content_chars": self.max_content_chars,
            }
        )
        if not self.api_key:
            diagnostics["missing_config"] = True
            diagnostics["search_failure_reason"] = "missing_config"
            return {"results": [], "diagnostics": diagnostics}

        # Tavily는 구글식 `site:` 연산자를 이해하지 못하고, 도메인 제한은 이미
        # include_domains로 처리한다. 공유 쿼리에 붙은 `site:<domain>` 토큰은
        # Tavily에서는 노이즈가 되어 시맨틱 매칭을 떨어뜨리므로 제거한다.
        tavily_query = strip_site_operators(query)
        diagnostics["tavily_effective_query"] = tavily_query
        if not tavily_query:
            diagnostics["search_failure_reason"] = "empty_query_after_site_strip"
            return {"results": [], "diagnostics": diagnostics}

        try:
            response = requests.post(
                TAVILY_SEARCH_URL,
                json={
                    "api_key": self.api_key,
                    "query": tavily_query,
                    "search_depth": "basic",
                    "include_domains": list(SK_OWNED_DOMAINS),
                    "include_raw_content": self.include_raw_content,
                    "max_results": max_results,
                },
                timeout=self.timeout,
            )
            diagnostics["search_status_code"] = response.status_code
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            diagnostics["search_failure_reason"] = f"fetch_error:{exc.__class__.__name__}"
            response = getattr(exc, "response", None)
            if response is not None:
                diagnostics["api_error_body_preview"] = sanitize_sensitive_text(
                    normalize_text(getattr(response, "text", "")),
                    [self.api_key],
                )[:DEFAULT_API_ERROR_BODY_PREVIEW_CHARS]
            return {"results": [], "diagnostics": diagnostics}

        results, candidate_results = parse_tavily_search_json_with_candidates(
            payload,
            max_results=max_results,
            max_content_chars=self.max_content_chars,
        )
        diagnostics["candidate_results"] = candidate_results
        diagnostics["parsed_link_count"] = len(candidate_results)
        diagnostics["parsed_result_count"] = len(results)
        if not results:
            diagnostics["search_failure_reason"] = "no_skax_results"
        return {"results": results, "diagnostics": diagnostics}


class PageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.capture_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.capture_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self.capture_title = False

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = normalize_text(data)
        if not text:
            return
        if self.capture_title:
            self.title_parts.append(text)
        else:
            self.text_parts.append(text)

    @property
    def title(self) -> str | None:
        title = normalize_text(" ".join(self.title_parts))
        return title or None

    @property
    def content(self) -> str:
        return normalize_text(" ".join(self.text_parts))


def build_search_queries(
    patent_context: dict[str, Any],
    *,
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> list[str]:
    return build_query_generation_plan(patent_context, max_queries=max_queries)["generated_queries"]


def build_query_generation_plan(
    patent_context: dict[str, Any],
    *,
    max_queries: int = DEFAULT_MAX_QUERIES,
    queries_override: list[str] | None = None,
) -> dict[str, Any]:
    related_product = patent_field(patent_context, "related_product")
    title = patent_field(patent_context, "title_final") or patent_field(patent_context, "title_draft") or patent_field(patent_context, "title")
    business_area = patent_field(patent_context, "business_area")
    technology_area = patent_field(patent_context, "technology_area")
    title_keywords = extract_title_keywords(title, limit=4)
    domain_profiles = business_domain_profiles(patent_context)
    domain_hints = business_domain_hints(patent_context)

    product_anchor_candidates = (
        [
            compact_query([related_product]),
            compact_query(["SK AX", related_product]),
        ]
        if related_product
        else []
    )
    candidates = [
        *product_anchor_candidates,
        compact_query([related_product, business_area, technology_area]),
    ]
    if domain_hints:
        candidates.append(compact_query([*domain_hints[:5]]))
    candidates.extend(
        [
            compact_query([related_product, *title_keywords[:2], technology_area]),
            compact_query([related_product, *title_keywords[:3]]),
            compact_query([business_area, technology_area, related_product]),
        ]
    )
    if not related_product:
        candidates.extend(
            [
                compact_query([*title_keywords[:3], technology_area]),
                compact_query([business_area, technology_area, *title_keywords[:2]]),
            ]
        )

    queries: list[str] = []
    dropped_duplicate_queries: list[str] = []
    base_candidates = [candidate for candidate in candidates if candidate]
    override_queries = normalize_skax_site_queries(queries_override or [], max_queries=max_queries)
    # query rewriting(LLM)이 만든 검색어를 우선 배치하고, 남는 슬롯을 rule-based 후보로 채운다.
    # (예전엔 rule-based를 먼저 넣어 max_queries를 다 채워, LLM 검색어가 한 건도 안 쓰이고 버려졌다.)
    raw_queries = list(override_queries)
    raw_queries.extend(f"site:{SK_AX_DOMAIN} {candidate}" for candidate in base_candidates)
    for query in raw_queries:
        query = " ".join(str(query or "").split())
        if not query:
            continue
        if query not in queries:
            queries.append(query)
        else:
            dropped_duplicate_queries.append(query)
        if len(queries) >= max(1, int(max_queries)):
            break
    if not queries:
        queries.append(f"site:{SK_AX_DOMAIN}")
    return {
        "selected_features": {
            "related_product": related_product,
            "business_area": business_area,
            "technology_area": technology_area,
            "title_final": patent_field(patent_context, "title_final"),
            "title_draft": patent_field(patent_context, "title_draft"),
        },
        "title_keywords": title_keywords,
        "domain_hints": [
            {
                "name": profile["name"],
                "hints": list(profile["hints"]),
                "preferred_paths": list(profile["preferred_paths"]),
            }
            for profile in domain_profiles
        ],
        "generated_queries": queries,
        "dropped_duplicate_queries": dropped_duplicate_queries,
        "query_source": "rule_based_with_query_rewriting" if queries_override is not None else "rule_based",
    }


def normalize_skax_site_queries(queries: list[str], *, max_queries: int = DEFAULT_MAX_QUERIES) -> list[str]:
    normalized_queries = []
    for query in queries:
        value = " ".join(str(query or "").split())
        if not value:
            continue
        value = re.sub(r"^site\s*:\s*skax\.co\.kr\s*", "", value, flags=re.IGNORECASE).strip()
        normalized_queries.append(f"site:{SK_AX_DOMAIN} {value}".strip())
    return unique_texts(normalized_queries)[: max(1, int(max_queries))]


def business_domain_hints(patent_context: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for profile in business_domain_profiles(patent_context):
        hints.extend(profile["hints"])
    return unique_texts(hints)


def business_domain_profiles(patent_context: dict[str, Any]) -> list[dict[str, Any]]:
    text = normalize_text(
        " ".join(
            [
                patent_field(patent_context, "related_product"),
                patent_field(patent_context, "title_final"),
                patent_field(patent_context, "title_draft"),
                patent_field(patent_context, "business_area"),
                patent_field(patent_context, "technology_area"),
            ]
        )
    ).lower()
    return [
        profile
        for profile in DOMAIN_HINT_PROFILES
        if any(normalize_text(trigger).lower() in text for trigger in profile["triggers"])
    ]


def extract_title_keywords(title: Any, *, limit: int = 4) -> list[str]:
    text = re.sub(r"[^0-9a-zA-Z가-힣\s]", " ", str(title or ""))
    tokens = re.findall(r"[0-9a-zA-Z가-힣]{2,}", text)
    keywords: list[str] = []
    for token in tokens:
        normalized = token.strip()
        if not normalized or normalized in TITLE_STOPWORDS:
            continue
        if normalized in keywords:
            continue
        keywords.append(normalized)
        if len(keywords) >= limit:
            break
    return keywords


def collect_skax_site_evidence(
    patent_context: dict[str, Any],
    *,
    fetcher: Fetcher | None = None,
    searcher: Searcher | None = None,
    search_client: SearchClient | None = None,
    queries_override: list[str] | None = None,
    max_queries: int = DEFAULT_MAX_QUERIES,
    max_results_per_query: int = DEFAULT_MAX_RESULTS_PER_QUERY,
    max_fetch_pages: int = DEFAULT_MAX_FETCH_PAGES,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    include_related_media: bool = False,
) -> dict[str, Any]:
    query_generation_diagnostics = build_query_generation_plan(
        patent_context,
        max_queries=max_queries,
        queries_override=queries_override,
    )
    base_queries = query_generation_diagnostics["generated_queries"]
    related_media_queries = (
        build_related_media_queries(base_queries, max_queries=max_queries)
        if include_related_media
        else []
    )
    # base와 관련매체 쿼리가 겹치면(예: 둘 다 'SK AX {제품}') 중복 검색을 막는다.
    queries = unique_texts([*base_queries, *related_media_queries])
    searched_result_count = 0
    search_results: list[dict[str, Any]] = []
    search_diagnostics: list[dict[str, Any]] = []
    failed_urls: list[str] = []
    skipped_url_count = 0
    fetched_url_count = 0
    truncated_content_count = 0

    active_search_client = search_client or (None if searcher else default_search_client())
    for query, results, diagnostics in search_queries_concurrently(
        queries,
        search_client=active_search_client,
        searcher=searcher,
        max_results_per_query=max_results_per_query,
    ):
        search_diagnostics.append(diagnostics)
        searched_result_count += len(results)
        for result in results:
            normalized = normalize_search_result(result, query)
            if normalized:
                search_results.append(normalized)

    filtered = filter_search_results(search_results, patent_context)
    skipped_url_count += max(0, len(search_results) - len(filtered))
    selected_limit = min(DEFAULT_MAX_EVIDENCE_ITEMS, max(1, int(max_fetch_pages)))
    selected = filtered[:selected_limit]
    items: list[dict[str, Any]] = []

    for result in selected:
        url = result["url"]
        content = normalize_text(result.get("content"))
        page_title = None
        if not content:
            try:
                active_fetcher = fetcher or fetch_skax_page_html
                html = active_fetcher(url)
                fetched_url_count += 1
            except Exception:
                failed_urls.append(url)
                continue
            if not normalize_text(html):
                skipped_url_count += 1
                continue

            page = parse_page_html(html)
            content = page["content"]
            page_title = page["title"]
        if not content:
            skipped_url_count += 1
            continue
        if is_related_media_url(url) and not contains_sk_ax_or_cnc_marker(content):
            skipped_url_count += 1
            continue
        if len(content) > max_content_chars:
            truncated_content_count += 1
            content = content[:max_content_chars]
        items.append(normalize_page_evidence(patent_context, result, page_title, content))

    evidence_items = ensure_evidence_ids(items, prefix="skax_site")
    annotate_candidate_final_selection(search_diagnostics, filtered, selected, evidence_items)
    return {
        "items": evidence_items,
        "stats": {
            "generated_query_count": len(queries),
            "searched_result_count": searched_result_count,
            "filtered_result_count": len(filtered),
            "fetched_url_count": fetched_url_count,
            "collected_evidence_count": len(evidence_items),
            "skipped_url_count": skipped_url_count,
            "failed_url_count": len(failed_urls),
            "truncated_content_count": truncated_content_count,
        },
        "queries": queries,
        "query_generation_diagnostics": query_generation_diagnostics,
        "failed_urls": failed_urls,
        "search_diagnostics": search_diagnostics,
    }


def search_queries_concurrently(
    queries: list[str],
    *,
    search_client: SearchClient | None,
    searcher: Searcher | None,
    max_results_per_query: int,
) -> list[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    if not queries:
        return []

    result_limit = max(1, int(max_results_per_query))

    def search_query(query: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        if search_client:
            try:
                search_response = search_client.search(query, max_results=result_limit)
                results = list(search_response.get("results", []))[:result_limit]
                diagnostics = normalize_search_diagnostics(
                    query,
                    search_response.get("diagnostics"),
                    results,
                )
                return query, results, diagnostics
            except Exception as exc:
                return query, [], build_search_error_diagnostics(query, exc)
        if searcher:
            try:
                results = searcher(query)[:result_limit]
                return query, results, build_injected_search_diagnostics(query, results)
            except Exception as exc:
                return query, [], build_search_error_diagnostics(query, exc)
        return query, [], build_empty_search_diagnostics(query)

    max_workers = min(DEFAULT_SEARCH_WORKERS, len(queries))
    if max_workers <= 1:
        return [search_query(query) for query in queries]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(search_query, queries))


def default_search_client() -> SearchClient:
    if TavilySearchClient.has_config():
        return TavilySearchClient()
    # SK AX 사이트 검색은 Tavily 단독으로 수행한다(과거 Google CSE/HTML 폴백은 제거됨).
    # 키가 없으면 검색이 불가능하므로, 'SK AX 근거 없음'이 조용한 비기능이 아니라
    # 키 미설정 때문임을 운영자가 인지하도록 경고를 남기고 빈 결과 클라이언트를 쓴다.
    _logger.warning(
        "SK AX 사이트 검색 키(TAVILY_API_KEY) 미설정 — SK AX 근거 0건으로 진행합니다."
    )
    return EmptySearchClient()


def search_with_default_fallback(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY,
) -> dict[str, Any]:
    client = default_search_client()
    try:
        response = client.search(query, max_results=max_results)
    except Exception as exc:
        response = {"results": [], "diagnostics": build_search_error_diagnostics(query, exc)}
    results = list(response.get("results", []))
    diagnostics = dict(response.get("diagnostics") or {})
    diagnostics["fallback_attempts"] = [
        {
            "search_provider": diagnostics.get("search_provider") or client.__class__.__name__,
            "result_count": len(results),
            "search_failure_reason": diagnostics.get("search_failure_reason"),
        }
    ]
    diagnostics["fallback_used"] = False
    return {"results": results, "diagnostics": diagnostics}


def build_related_media_queries(base_queries: list[str], *, max_queries: int = DEFAULT_MAX_QUERIES) -> list[str]:
    # 관련매체(skcareersjournal.com·openapi.sk.com)는 검색 단계의 include_domains(SK_OWNED_DOMAINS)가
    # 이미 모두 커버한다. 예전엔 도메인마다 site:<domain>을 붙여 base당 도메인 수만큼(×2) 쿼리를 만들었지만,
    # site: 토큰은 Tavily 전에 제거되어 동일 키워드 쿼리가 도메인 수만큼 중복 생성됐다(15개 중 10개 중복).
    # base당 'SK AX {키워드}' 한 건만 만들어 SK AX 언급을 가산 검색한다.
    related_queries: list[str] = []
    for base_query in base_queries[: max(1, int(max_queries))]:
        body = re.sub(r"^site\s*:\s*skax\.co\.kr\s*", "", base_query, flags=re.IGNORECASE).strip()
        if not re.match(r"(?i)^sk\s*ax\b", body):  # 이미 'SK AX'로 시작하면 접두 중복(SK AX SK AX) 방지
            body = compact_query(["SK AX", body])
        if body:
            related_queries.append(body)
    return unique_texts(related_queries)


def fetch_skax_page_html(
    url: str,
    *,
    timeout: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": SEARCH_USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def parse_tavily_search_json(
    payload: Any,
    *,
    max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> list[SearchResult]:
    results, _ = parse_tavily_search_json_with_candidates(
        payload,
        max_results=max_results,
        max_content_chars=max_content_chars,
    )
    return results


def parse_tavily_search_json_with_candidates(
    payload: Any,
    *,
    max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> tuple[list[SearchResult], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return [], []
    results: list[SearchResult] = []
    candidate_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        original_url = normalize_text(item.get("url"))
        normalized_url = normalize_url(original_url)
        candidate = {
            "title": normalize_text(item.get("title")),
            "url": original_url,
            "normalized_url": normalized_url,
            "accepted": False,
            "domain_accepted": False,
            "final_selected": False,
            "skip_reason": None,
            "final_skip_reason": None,
            "candidate_relevance_score": 0.0,
            "score_reasons": [],
        }
        if not original_url:
            candidate["skip_reason"] = "empty_url"
            candidate_results.append(candidate)
            continue
        if not normalized_url:
            candidate["skip_reason"] = "invalid_url"
            candidate_results.append(candidate)
            continue
        if not is_skax_url(normalized_url):
            candidate["skip_reason"] = "external_domain"
            candidate_results.append(candidate)
            continue
        if is_file_url(normalized_url):
            candidate["skip_reason"] = "file_url"
            candidate["score_reasons"] = ["penalty_file_url"]
            candidate_results.append(candidate)
            continue
        if normalized_url in seen:
            candidate["skip_reason"] = "duplicate_url"
            candidate_results.append(candidate)
            continue
        seen.add(normalized_url)
        content = normalize_text(item.get("raw_content") or item.get("content") or item.get("snippet"))
        if len(content) > max_content_chars:
            content = content[:max_content_chars]
        candidate["accepted"] = True
        candidate["domain_accepted"] = True
        candidate_results.append(candidate)
        results.append(
            {
                "title": normalize_text(item.get("title")) or normalized_url,
                "url": normalized_url,
                "snippet": content,
                "content": content,
            }
        )
        if len(results) >= max(1, int(max_results)):
            break
    return results, candidate_results


def annotate_candidate_final_selection(
    diagnostics: list[dict[str, Any]],
    filtered: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> None:
    filtered_scores = {item["url"]: item.get("relevance_score", 0.0) for item in filtered}
    filtered_reasons = {item["url"]: item.get("score_reasons", []) for item in filtered}
    selected_urls = {item["url"] for item in selected}
    evidence_urls = {item.get("url") for item in evidence_items}
    selected_score_floor = min((item.get("relevance_score", 0.0) for item in selected), default=0.0)

    for diagnostic in diagnostics:
        for candidate in diagnostic.get("candidate_results", []) or []:
            normalized_url = candidate.get("normalized_url")
            if candidate.get("skip_reason"):
                candidate["final_skip_reason"] = candidate["skip_reason"]
                continue

            score = filtered_scores.get(normalized_url, 0.0)
            candidate["candidate_relevance_score"] = score
            candidate["score_reasons"] = filtered_reasons.get(normalized_url, candidate.get("score_reasons", []))
            if normalized_url in evidence_urls:
                candidate["final_selected"] = True
                candidate["final_skip_reason"] = None
            elif normalized_url not in filtered_scores:
                candidate["final_skip_reason"] = "insufficient_specific_match"
            elif normalized_url not in selected_urls:
                candidate["final_skip_reason"] = (
                    "lower_relevance_than_selected"
                    if score <= selected_score_floor
                    else "max_evidence_limit"
                )
            else:
                candidate["final_skip_reason"] = "empty_content"


def sanitize_sensitive_text(text: str, sensitive_values: list[str | None]) -> str:
    sanitized = text
    for value in sensitive_values:
        secret = normalize_text(value)
        if len(secret) >= 6:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def build_empty_search_diagnostics(query: str) -> dict[str, Any]:
    return {
        "query": query,
        "search_status_code": None,
        "search_final_url": None,
        "search_html_length": 0,
        "search_html_preview": "",
        "parsed_link_count": 0,
        "parsed_result_count": 0,
        "search_failure_reason": None,
        "api_error_body_preview": None,
        "api_error_code": None,
        "api_error_status": None,
        "api_error_message": None,
        "api_error_reason": None,
    }


def build_injected_search_diagnostics(query: str, results: list[SearchResult]) -> dict[str, Any]:
    diagnostics = build_empty_search_diagnostics(query)
    diagnostics.update(
        {
            "parsed_link_count": len(results),
            "parsed_result_count": len(results),
        }
    )
    return diagnostics


def normalize_search_diagnostics(
    query: str,
    diagnostics: Any,
    results: list[SearchResult],
) -> dict[str, Any]:
    normalized = build_empty_search_diagnostics(query)
    if isinstance(diagnostics, dict):
        normalized.update(diagnostics)
    normalized["query"] = query
    if normalized.get("parsed_link_count") in {None, 0} and results:
        normalized["parsed_link_count"] = len(results)
    if normalized.get("parsed_result_count") in {None, 0} and results:
        normalized["parsed_result_count"] = len(results)
    return normalized


def build_search_error_diagnostics(query: str, exc: Exception) -> dict[str, Any]:
    diagnostics = build_empty_search_diagnostics(query)
    diagnostics["search_failure_reason"] = f"fetch_error:{exc.__class__.__name__}"
    return diagnostics


def filter_search_results(results: list[dict[str, Any]], patent_context: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for result in results:
        url = normalize_url(result.get("url"))
        if not url or url in seen or not is_skax_url(url) or is_file_url(url):
            continue
        seen.add(url)
        # 관련성 점수는 정렬(우선순위)용으로만 쓰고, 여기서 버리지 않는다.
        # skax.co.kr 공식 도메인 페이지는 제품명 정확매칭이 없어도(예: 제품을
        # AIOps Platform 같은 일반 표현으로 소개) 일단 근거 후보로 올린 뒤,
        # 실제 관련성 판단·요약은 뒤의 압축 단계(LLM)에서 처리한다.
        score = score_search_result(result, patent_context)
        filtered.append({**result, "url": url, **score})
    filtered.sort(key=lambda item: item["relevance_score"], reverse=True)
    return filtered


def score_search_result(result: dict[str, Any], patent_context: dict[str, Any]) -> dict[str, Any]:
    related_product = patent_field(patent_context, "related_product")
    title = patent_field(patent_context, "title_final") or patent_field(patent_context, "title_draft") or patent_field(patent_context, "title")
    business_area = patent_field(patent_context, "business_area")
    technology_area = patent_field(patent_context, "technology_area")
    title_keywords = extract_title_keywords(title, limit=4)
    strong_terms = strong_search_terms(patent_context, title_keywords)
    domain_profiles = business_domain_profiles(patent_context)
    domain_hints = business_domain_hints(patent_context)
    url = normalize_text(result.get("url"))
    path = urlparse(url).path.lower()
    text = normalize_text(" ".join(str(result.get(field) or "") for field in ("title", "snippet", "content", "url"))).lower()

    matched_keywords: list[str] = []
    matched_strong_terms: list[str] = []
    score_reasons: list[str] = []
    points = 0.0
    related_product_matched = False
    if related_product and contains_keyword(text, related_product):
        matched_keywords.append(related_product)
        matched_strong_terms.append(related_product)
        score_reasons.append("matched_related_product")
        points += 0.5
        related_product_matched = True
    for keyword in strong_terms:
        if contains_keyword(text, keyword):
            matched_keywords.append(keyword)
            matched_strong_terms.append(keyword)
            score_reasons.append(f"matched_strong_term:{keyword}")
            points += 0.16
    if technology_area and contains_keyword(text, technology_area):
        matched_keywords.append(technology_area)
        score_reasons.append("matched_technology_area")
        points += 0.14
    if business_area and contains_keyword(text, business_area):
        matched_keywords.append(business_area)
        score_reasons.append("matched_business_area")
        points += 0.12
    for hint in domain_hints:
        if contains_keyword(text, hint):
            matched_keywords.append(hint)
            score_reasons.append(f"matched_domain_hint:{hint}")
            points += 0.08
    for profile in domain_profiles:
        matched_path = next((path_hint for path_hint in profile["preferred_paths"] if path.startswith(path_hint)), None)
        if matched_path:
            score_reasons.append(f"matched_preferred_path:{matched_path}")
            points += 0.1
    if any(profile["name"] == "finance" for profile in domain_profiles):
        patent_text = normalize_text(" ".join([related_product, title, business_area, technology_area])).lower()
        if "aicc" in path and "aicc" not in patent_text:
            score_reasons.append("penalty_unrelated_keyword:AICC")
            points -= 0.5

    domain_hint_matches = [reason for reason in score_reasons if reason.startswith("matched_domain_hint:")]
    preferred_path_matched = any(reason.startswith("matched_preferred_path:") for reason in score_reasons)
    is_relevant = (
        related_product_matched
        or len(unique_texts(matched_strong_terms)) >= 2
        or (len(unique_texts(matched_strong_terms)) >= 1 and preferred_path_matched)
        or (preferred_path_matched and len(domain_hint_matches) >= 2)
    )
    if not is_relevant:
        score_reasons.append("insufficient_specific_match")
        points = min(points, 0.0)

    return {
        "relevance_score": min(1.0, max(0.0, round(points, 3))),
        "matched_keywords": unique_texts(matched_keywords),
        "matched_strong_terms": unique_texts(matched_strong_terms),
        "score_reasons": unique_texts(score_reasons),
        "is_relevant": is_relevant,
    }


def strong_search_terms(patent_context: dict[str, Any], title_keywords: list[str]) -> list[str]:
    terms = [
        patent_field(patent_context, "related_product"),
        *title_keywords,
        patent_field(patent_context, "technology_area"),
    ]
    broad_terms = {"ai", "data", "cloud", "서비스", "플랫폼", "자동화", "보안", "인증"}
    return [term for term in unique_texts(terms) if term and term.lower() not in broad_terms][:8]


def normalize_search_result(result: dict[str, Any], query: str) -> dict[str, Any] | None:
    url = normalize_url(result.get("url") or result.get("link"))
    if not url:
        return None
    return {
        "title": normalize_text(result.get("title")),
        "snippet": normalize_text(result.get("snippet") or result.get("description")),
        "url": url,
        "search_query": query,
        "content": normalize_text(result.get("content")),
    }


def parse_page_html(html: str) -> dict[str, str | None]:
    parser = PageHTMLParser()
    parser.feed(html)
    parser.close()
    return {
        "title": parser.title,
        "content": parser.content,
    }


def normalize_page_evidence(
    patent_context: dict[str, Any],
    result: dict[str, Any],
    page_title: str | None,
    content: str,
) -> dict[str, Any]:
    source_domain = evidence_source_domain(result["url"])
    return {
        "evidence_id": None,
        "source_type": "company_disclosure",
        "source": evidence_source_for_url(result["url"]),
        "source_domain": source_domain,
        "source_tier": evidence_source_tier(result["url"]),
        "title": page_title or result.get("title") or result["url"],
        "url": result["url"],
        "published_at": None,
        "collected_at": now_iso(),
        "content": content,
        "search_query": result.get("search_query"),
        "matched_keywords": result.get("matched_keywords", []),
        "relevance_score": result.get("relevance_score"),
        "management_number": patent_field(patent_context, "management_number"),
        "related_product": patent_field(patent_context, "related_product"),
        "business_area": patent_field(patent_context, "business_area"),
        "technology_area": patent_field(patent_context, "technology_area"),
    }


def patent_field(patent_context: dict[str, Any], field: str) -> str:
    aliases = {
        "management_number": ["management_number", "관리번호"],
        "title": ["title", "발명의 명칭"],
        "title_draft": ["title_draft", "발명의 명칭(가제)"],
        "title_final": ["title_final", "발명의 명칭(최종)"],
        "business_area": ["business_area", "관련사업 분야"],
        "technology_area": ["technology_area", "관련기술 분야"],
        "related_product": ["related_product", "관련제품"],
    }
    for key in aliases[field]:
        value = normalize_text(patent_context.get(key))
        if value:
            return value
    return ""


def strip_site_operators(query: str) -> str:
    """쿼리에서 `site:<domain>` 연산자 토큰을 제거한다.

    `site:`는 Google HTML/Custom Search용 연산자다. Tavily는 이를 지원하지 않고
    도메인 제한을 include_domains로 처리하므로, Tavily에 보낼 때는 노이즈가 되는
    이 토큰을 벗겨내고 키워드만 남긴다.
    """
    tokens = [token for token in normalize_text(query).split() if not token.lower().startswith("site:")]
    return " ".join(tokens).strip()


def compact_query(parts: list[Any]) -> str:
    values = []
    for part in parts:
        text = normalize_text(part)
        if text and text not in values:
            values.append(text)
    return " ".join(values)


def normalize_url(url: Any) -> str | None:
    text = normalize_text(url)
    if not text or text.startswith(("mailto:", "tel:", "javascript:")):
        return None
    without_fragment = urldefrag(text)[0]
    parsed = urlparse(without_fragment)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path or "/"
    return parsed._replace(path=path, fragment="").geturl()


def is_skax_url(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return any(netloc == domain or netloc.endswith(f".{domain}") for domain in SK_OWNED_DOMAINS)


def is_related_media_url(url: str) -> bool:
    domain = evidence_source_domain(url)
    return domain in SK_RELATED_MEDIA_DOMAINS


def evidence_source_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    for domain in SK_OWNED_DOMAINS:
        if netloc == domain or netloc.endswith(f".{domain}"):
            return domain
    return netloc


def evidence_source_for_url(url: str) -> str:
    return SK_GROUP_OWNED_MEDIA_SOURCE if is_related_media_url(url) else SK_AX_SOURCE


def evidence_source_tier(url: str) -> str:
    return "sk_related_owned_media" if is_related_media_url(url) else "sk_ax_official_site"


def contains_sk_ax_or_cnc_marker(content: str) -> bool:
    haystack = normalize_text(content).lower()
    return any(marker in haystack for marker in SK_RELATED_MEDIA_REQUIRED_MARKERS)


def is_file_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(extension) for extension in SKIPPED_FILE_EXTENSIONS)


def contains_keyword(text: str, keyword: str) -> bool:
    return normalize_text(keyword).lower() in text


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def unique_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = normalize_text(value)
        if text and text not in result:
            result.append(text)
    return result


