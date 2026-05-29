from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Callable
from html import unescape as html_unescape
from urllib.parse import parse_qs, quote_plus, unquote, urldefrag, urlparse
import json
import os
import re

import requests

from services.evidence.store_service import ensure_evidence_ids, now_iso


SK_AX_DOMAIN = "skax.co.kr"
SK_AX_SOURCE = "sk_ax_official"
DEFAULT_MAX_QUERIES = 5
DEFAULT_MAX_RESULTS_PER_QUERY = 5
DEFAULT_MAX_FETCH_PAGES = 5
DEFAULT_MAX_EVIDENCE_ITEMS = 3
DEFAULT_MAX_CONTENT_CHARS = 5000
DEFAULT_SEARCH_TIMEOUT_SECONDS = 5
DEFAULT_SEARCH_HTML_PREVIEW_CHARS = 800
DEFAULT_API_ERROR_BODY_PREVIEW_CHARS = 1000
GOOGLE_SEARCH_URL = "https://www.google.com/search"
GOOGLE_CUSTOM_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
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


class GoogleHtmlSearchClient(SearchClient):
    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> dict[str, Any]:
        results, diagnostics = default_html_searcher_with_diagnostics(query, max_results=max_results)
        return {
            "results": results,
            "diagnostics": diagnostics,
        }


class GoogleCustomSearchClient(SearchClient):
    provider = "google_custom_search_json"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        cx: str | None = None,
        timeout: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY")
        self.cx = cx if cx is not None else os.getenv("GOOGLE_CUSTOM_SEARCH_CX")
        self.timeout = timeout

    @classmethod
    def has_config(cls) -> bool:
        return bool(os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY") and os.getenv("GOOGLE_CUSTOM_SEARCH_CX"))

    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> dict[str, Any]:
        diagnostics = build_empty_search_diagnostics(query)
        diagnostics.update(
            {
                "search_provider": self.provider,
                "search_request_url": GOOGLE_CUSTOM_SEARCH_URL,
                "missing_config": False,
            }
        )
        if not self.api_key or not self.cx:
            diagnostics["missing_config"] = True
            diagnostics["search_failure_reason"] = "missing_config"
            return {"results": [], "diagnostics": diagnostics}

        try:
            response = requests.get(
                GOOGLE_CUSTOM_SEARCH_URL,
                params={
                    "key": self.api_key,
                    "cx": self.cx,
                    "q": query,
                    "num": min(10, max(1, int(max_results))),
                    "hl": "ko",
                    "gl": "kr",
                    "siteSearch": SK_AX_DOMAIN,
                    "siteSearchFilter": "i",
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
                diagnostics.update(parse_google_api_error_response(response, sensitive_values=[self.api_key, self.cx]))
            return {"results": [], "diagnostics": diagnostics}

        results = parse_custom_search_json(payload, max_results=max_results)
        diagnostics["parsed_link_count"] = len(payload.get("items", [])) if isinstance(payload, dict) else 0
        diagnostics["parsed_result_count"] = len(results)
        if not results:
            diagnostics["search_failure_reason"] = "no_skax_results"
        return {"results": results, "diagnostics": diagnostics}


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

        try:
            response = requests.post(
                TAVILY_SEARCH_URL,
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": "basic",
                    "include_domains": [SK_AX_DOMAIN],
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


class GoogleSearchHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.current_href: str | None = None
        self.current_text_parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.current_href = href
                self.current_text_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag in {"script", "style", "noscript", "svg"}:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "a" and self.current_href:
            self.links.append(
                {
                    "href": self.current_href,
                    "text": normalize_text(" ".join(self.current_text_parts)),
                }
            )
            self.current_href = None
            self.current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.current_href:
            return
        text = normalize_text(data)
        if text:
            self.current_text_parts.append(text)


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
    strong_terms = strong_search_terms(patent_context, title_keywords)
    domain_profiles = business_domain_profiles(patent_context)
    domain_hints = business_domain_hints(patent_context)
    if queries_override is not None:
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
            "generated_queries": normalize_skax_site_queries(queries_override, max_queries=max_queries),
            "dropped_duplicate_queries": [],
            "query_source": "query_rewriting",
        }

    candidates = [
        compact_query([related_product, business_area, technology_area]),
        compact_query([related_product, *title_keywords[:2], technology_area]),
        compact_query([related_product, *title_keywords[:3]]),
        compact_query([business_area, technology_area, related_product]),
    ]
    if domain_hints:
        candidates.extend(
            [
                compact_query([*domain_hints[:5]]),
                compact_query([related_product, *domain_hints[:2], technology_area]),
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
    for candidate in candidates:
        if not candidate:
            continue
        query = f"site:{SK_AX_DOMAIN} {candidate}"
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
        "query_source": "rule_based",
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
) -> dict[str, Any]:
    query_generation_diagnostics = build_query_generation_plan(
        patent_context,
        max_queries=max_queries,
        queries_override=queries_override,
    )
    queries = query_generation_diagnostics["generated_queries"]
    searched_result_count = 0
    search_results: list[dict[str, Any]] = []
    search_diagnostics: list[dict[str, Any]] = []
    failed_urls: list[str] = []
    skipped_url_count = 0
    fetched_url_count = 0
    truncated_content_count = 0

    for query in queries:
        if search_client:
            try:
                search_response = search_client.search(query, max_results=max_results_per_query)
                results = list(search_response.get("results", []))[: max(1, int(max_results_per_query))]
                search_diagnostics.append(
                    normalize_search_diagnostics(
                        query,
                        search_response.get("diagnostics"),
                        results,
                    )
                )
            except Exception as exc:
                results = []
                search_diagnostics.append(build_search_error_diagnostics(query, exc))
        elif searcher:
            try:
                results = searcher(query)[: max(1, int(max_results_per_query))]
                search_diagnostics.append(build_injected_search_diagnostics(query, results))
            except Exception as exc:
                results = []
                search_diagnostics.append(build_search_error_diagnostics(query, exc))
        else:
            search_response = default_search_client().search(query, max_results=max_results_per_query)
            results = list(search_response.get("results", []))[: max(1, int(max_results_per_query))]
            search_diagnostics.append(
                normalize_search_diagnostics(
                    query,
                    search_response.get("diagnostics"),
                    results,
                )
            )
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


def default_search_client() -> SearchClient:
    if TavilySearchClient.has_config():
        return TavilySearchClient()
    if GoogleCustomSearchClient.has_config():
        return GoogleCustomSearchClient()
    return GoogleHtmlSearchClient()


def default_html_searcher(query: str) -> list[SearchResult]:
    try:
        return parse_google_search_html(fetch_google_search_html(query))
    except Exception:
        return []


def default_html_searcher_with_diagnostics(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY,
) -> tuple[list[SearchResult], dict[str, Any]]:
    diagnostics = build_empty_search_diagnostics(query)
    try:
        response = fetch_google_search_response(query)
    except Exception as exc:
        diagnostics.update(build_search_error_diagnostics(query, exc))
        return [], diagnostics

    html = response["html"]
    diagnostics.update(
        {
            "search_status_code": response["status_code"],
            "search_final_url": response["url"],
            "search_html_length": len(html),
            "search_html_preview": html[:DEFAULT_SEARCH_HTML_PREVIEW_CHARS],
            "search_failure_reason": detect_google_search_failure(
                html,
                status_code=response["status_code"],
                final_url=response["url"],
            ),
        }
    )
    results, parse_diagnostics = parse_google_search_html_with_diagnostics(html, max_results=max_results)
    diagnostics.update(parse_diagnostics)
    if (
        not diagnostics["search_failure_reason"]
        and diagnostics["parsed_result_count"] == 0
        and looks_like_google_requires_javascript(html)
    ):
        diagnostics["search_failure_reason"] = "google_requires_javascript"
    if not diagnostics["search_failure_reason"] and not results and diagnostics["parsed_link_count"] == 0:
        diagnostics["search_failure_reason"] = "no_parseable_google_links"
    return results, diagnostics


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


def fetch_google_search_html(
    query: str,
    *,
    timeout: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
) -> str:
    return fetch_google_search_response(query, timeout=timeout)["html"]


def fetch_google_search_response(
    query: str,
    *,
    timeout: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    search_url = f"{GOOGLE_SEARCH_URL}?q={quote_plus(query)}"
    response = requests.get(
        search_url,
        headers={"User-Agent": SEARCH_USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return {
        "status_code": response.status_code,
        "url": response.url,
        "html": response.text,
    }


def parse_google_search_html(html: str, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> list[SearchResult]:
    results, _ = parse_google_search_html_with_diagnostics(html, max_results=max_results)
    return results


def parse_google_search_html_with_diagnostics(
    html: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY,
) -> tuple[list[SearchResult], dict[str, Any]]:
    diagnostics = {
        "parsed_link_count": 0,
        "parsed_result_count": 0,
    }
    if not normalize_text(html):
        return [], diagnostics
    parser = GoogleSearchHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        diagnostics["search_failure_reason"] = "html_parse_error"
        return [], diagnostics
    diagnostics["parsed_link_count"] = len(parser.links)

    results: list[SearchResult] = []
    seen: set[str] = set()
    for link in parser.links:
        url = extract_google_target_url(link.get("href"))
        if not url or url in seen or not is_skax_url(url) or is_file_url(url):
            continue
        seen.add(url)
        results.append(
            {
                "title": link.get("text") or url,
                "url": url,
                "snippet": "",
            }
        )
        if len(results) >= max(1, int(max_results)):
            break

    if len(results) < max(1, int(max_results)):
        for url in extract_skax_urls_from_text(html):
            if url in seen or is_file_url(url):
                continue
            seen.add(url)
            results.append(
                {
                    "title": url,
                    "url": url,
                    "snippet": "",
                }
            )
            if len(results) >= max(1, int(max_results)):
                break
    diagnostics["parsed_result_count"] = len(results)
    return results, diagnostics


def parse_custom_search_json(payload: Any, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> list[SearchResult]:
    if not isinstance(payload, dict):
        return []
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in payload.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        url = normalize_url(item.get("link"))
        if not url or url in seen or not is_skax_url(url) or is_file_url(url):
            continue
        seen.add(url)
        results.append(
            {
                "title": normalize_text(item.get("title")) or url,
                "url": url,
                "snippet": normalize_text(item.get("snippet")),
            }
        )
        if len(results) >= max(1, int(max_results)):
            break
    return results


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


def parse_google_api_error_response(
    response: Any,
    *,
    sensitive_values: list[str | None] | None = None,
) -> dict[str, Any]:
    body_text = sanitize_sensitive_text(normalize_text(getattr(response, "text", "")), sensitive_values or [])
    payload: Any
    try:
        payload = response.json()
    except Exception:
        try:
            payload = json.loads(body_text)
        except Exception:
            payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    details = error.get("errors", []) if isinstance(error, dict) else []
    first_detail = details[0] if details and isinstance(details[0], dict) else {}
    return {
        "api_error_body_preview": body_text[:DEFAULT_API_ERROR_BODY_PREVIEW_CHARS],
        "api_error_code": error.get("code") if isinstance(error, dict) else None,
        "api_error_status": error.get("status") if isinstance(error, dict) else None,
        "api_error_message": error.get("message") if isinstance(error, dict) else None,
        "api_error_reason": first_detail.get("reason"),
    }


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


def detect_google_search_failure(
    html: str,
    *,
    status_code: int | None,
    final_url: str | None,
) -> str | None:
    if status_code and status_code >= 400:
        return f"http_status_{status_code}"
    haystack = normalize_text(f"{final_url or ''} {html}").lower()
    if looks_like_google_requires_javascript(html, final_url=final_url):
        return "google_requires_javascript"
    if "consent.google" in haystack or "before you continue" in haystack:
        return "google_consent_page"
    if "sorry/index" in haystack or "unusual traffic" in haystack:
        return "google_blocked_or_captcha"
    if "captcha" in haystack:
        return "captcha_page"
    return None


def looks_like_google_requires_javascript(html: str, *, final_url: str | None = None) -> bool:
    haystack = normalize_text(f"{final_url or ''} {html}").lower()
    return any(
        marker in haystack
        for marker in (
            "/httpservice/retry/enablejs",
            "enablejs",
            "<noscript",
            "몇 초 안에 이동하지 않는 경우",
        )
    )


def extract_google_target_url(href: str | None) -> str | None:
    if not href:
        return None
    text = normalize_text(href).replace("\\/", "/")
    if text.startswith("/url?") or text.startswith("https://www.google.com/url?"):
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        target = first_value(query.get("q")) or first_value(query.get("url"))
        return normalize_url(unquote(target)) if target else None
    if text.startswith("/search?") or text.startswith("#"):
        return None
    return normalize_url(text)


def extract_skax_urls_from_text(text: str) -> list[str]:
    decoded = html_unescape(unquote(normalize_text(text))).replace("\\/", "/")
    candidates = re.findall(r"https?://(?:[A-Za-z0-9-]+\.)*skax\.co\.kr[^\s\"'<>)]*", decoded)
    urls: list[str] = []
    for candidate in candidates:
        url = normalize_url(strip_google_tail_params(candidate).rstrip(".,;:"))
        if url and is_skax_url(url) and url not in urls:
            urls.append(url)
    return urls


def strip_google_tail_params(url: str) -> str:
    return re.split(r"&(sa|ved|usg|ei|source)=", url, maxsplit=1)[0]


def filter_search_results(results: list[dict[str, Any]], patent_context: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for result in results:
        url = normalize_url(result.get("url"))
        if not url or url in seen or not is_skax_url(url) or is_file_url(url):
            continue
        seen.add(url)
        score = score_search_result(result, patent_context)
        if score["relevance_score"] <= 0 or not score["is_relevant"]:
            continue
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
    return {
        "evidence_id": None,
        "source_type": "company_disclosure",
        "source": SK_AX_SOURCE,
        "title": page_title or result.get("title") or result["url"],
        "url": result["url"],
        "published_at": None,
        "collected_at": now_iso(),
        "content": content,
        "related_axes": ["business_fit"],
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
    return netloc == SK_AX_DOMAIN or netloc.endswith(f".{SK_AX_DOMAIN}")


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


def first_value(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]
