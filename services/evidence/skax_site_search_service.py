from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urldefrag, urlparse
import re

import requests

from services.evidence.store_service import ensure_evidence_ids, now_iso


SK_AX_DOMAIN = "skax.co.kr"
SK_AX_SOURCE = "sk_ax_official"
DEFAULT_MAX_QUERIES = 3
DEFAULT_MAX_RESULTS_PER_QUERY = 5
DEFAULT_MAX_FETCH_PAGES = 5
DEFAULT_MAX_CONTENT_CHARS = 5000
DEFAULT_SEARCH_TIMEOUT_SECONDS = 5
GOOGLE_SEARCH_URL = "https://www.google.com/search"
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
    "예측",
    "반영한",
    "모델",
    "시스템",
    "방법",
    "장치",
    "프로그램",
}

SearchResult = dict[str, Any]
Searcher = Callable[[str], list[SearchResult]]
Fetcher = Callable[[str], str]


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
    related_product = patent_field(patent_context, "related_product")
    title = patent_field(patent_context, "title_final") or patent_field(patent_context, "title_draft") or patent_field(patent_context, "title")
    business_area = patent_field(patent_context, "business_area")
    technology_area = patent_field(patent_context, "technology_area")
    title_keywords = extract_title_keywords(title, limit=3)

    candidates = [
        compact_query([related_product, *title_keywords[:2], technology_area]),
        compact_query([related_product, *title_keywords[:3]]),
        compact_query([business_area, technology_area, related_product]),
    ]
    if not related_product:
        candidates.extend(
            [
                compact_query([*title_keywords[:3], technology_area]),
                compact_query([business_area, technology_area, *title_keywords[:2]]),
            ]
        )

    queries: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        query = f"site:{SK_AX_DOMAIN} {candidate}"
        if query not in queries:
            queries.append(query)
        if len(queries) >= max(1, int(max_queries)):
            break
    if not queries:
        queries.append(f"site:{SK_AX_DOMAIN}")
    return queries


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
    fetcher: Fetcher,
    searcher: Searcher | None = None,
    max_queries: int = DEFAULT_MAX_QUERIES,
    max_results_per_query: int = DEFAULT_MAX_RESULTS_PER_QUERY,
    max_fetch_pages: int = DEFAULT_MAX_FETCH_PAGES,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> dict[str, Any]:
    queries = build_search_queries(patent_context, max_queries=max_queries)
    searched_result_count = 0
    search_results: list[dict[str, Any]] = []
    failed_urls: list[str] = []
    skipped_url_count = 0
    fetched_url_count = 0
    truncated_content_count = 0

    for query in queries:
        try:
            active_searcher = searcher or default_html_searcher
            results = active_searcher(query)[: max(1, int(max_results_per_query))]
        except Exception:
            results = []
        searched_result_count += len(results)
        for result in results:
            normalized = normalize_search_result(result, query)
            if normalized:
                search_results.append(normalized)

    filtered = filter_search_results(search_results, patent_context)
    skipped_url_count += max(0, len(search_results) - len(filtered))
    selected = filtered[: max(1, int(max_fetch_pages))]
    items: list[dict[str, Any]] = []

    for result in selected:
        url = result["url"]
        try:
            html = fetcher(url)
            fetched_url_count += 1
        except Exception:
            failed_urls.append(url)
            continue
        if not normalize_text(html):
            skipped_url_count += 1
            continue

        page = parse_page_html(html)
        content = page["content"]
        if not content:
            skipped_url_count += 1
            continue
        if len(content) > max_content_chars:
            truncated_content_count += 1
            content = content[:max_content_chars]
        items.append(normalize_page_evidence(patent_context, result, page["title"], content))

    evidence_items = ensure_evidence_ids(items, prefix="skax_site")
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
        "failed_urls": failed_urls,
    }


def default_html_searcher(query: str) -> list[SearchResult]:
    try:
        return parse_google_search_html(fetch_google_search_html(query))
    except Exception:
        return []


def fetch_google_search_html(
    query: str,
    *,
    timeout: int = DEFAULT_SEARCH_TIMEOUT_SECONDS,
) -> str:
    search_url = f"{GOOGLE_SEARCH_URL}?q={quote_plus(query)}"
    response = requests.get(
        search_url,
        headers={"User-Agent": SEARCH_USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def parse_google_search_html(html: str, *, max_results: int = DEFAULT_MAX_RESULTS_PER_QUERY) -> list[SearchResult]:
    if not normalize_text(html):
        return []
    parser = GoogleSearchHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []

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
    return results


def extract_google_target_url(href: str | None) -> str | None:
    if not href:
        return None
    text = normalize_text(href)
    if text.startswith("/url?") or text.startswith("https://www.google.com/url?"):
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        target = first_value(query.get("q")) or first_value(query.get("url"))
        return normalize_url(unquote(target)) if target else None
    if text.startswith("/search?") or text.startswith("#"):
        return None
    return normalize_url(text)


def filter_search_results(results: list[dict[str, Any]], patent_context: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for result in results:
        url = normalize_url(result.get("url"))
        if not url or url in seen or not is_skax_url(url) or is_file_url(url):
            continue
        seen.add(url)
        score = score_search_result(result, patent_context)
        if score["relevance_score"] <= 0:
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
    text = normalize_text(" ".join(str(result.get(field) or "") for field in ("title", "snippet", "url"))).lower()

    matched_keywords: list[str] = []
    points = 0.0
    if related_product and contains_keyword(text, related_product):
        matched_keywords.append(related_product)
        points += 0.5
    for keyword in title_keywords:
        if contains_keyword(text, keyword):
            matched_keywords.append(keyword)
            points += 0.12
    if technology_area and contains_keyword(text, technology_area):
        matched_keywords.append(technology_area)
        points += 0.14
    if business_area and contains_keyword(text, business_area):
        matched_keywords.append(business_area)
        points += 0.12

    return {
        "relevance_score": min(1.0, round(points, 3)),
        "matched_keywords": unique_texts(matched_keywords),
    }


def normalize_search_result(result: dict[str, Any], query: str) -> dict[str, Any] | None:
    url = normalize_url(result.get("url") or result.get("link"))
    if not url:
        return None
    return {
        "title": normalize_text(result.get("title")),
        "snippet": normalize_text(result.get("snippet") or result.get("description")),
        "url": url,
        "search_query": query,
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
