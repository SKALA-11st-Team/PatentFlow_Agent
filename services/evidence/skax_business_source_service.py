from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urldefrag, urljoin, urlparse
import re

import requests

from services.evidence.store_service import ensure_evidence_ids, now_iso


DEFAULT_MAX_DEPTH = 1
DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_CONTENT_CHARS = 5000
DEFAULT_TIMEOUT_SECONDS = 5
SK_AX_SOURCE = "sk_ax_official"
SKIPPED_FILE_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".zip",
}

FetchHtml = Callable[[str], str]


class PageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.capture_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.capture_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag in {"script", "style", "noscript", "svg"}:
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


def collect_skax_business_sources(
    seed_urls: list[str],
    *,
    extra_urls: list[str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    fetcher: FetchHtml | None = None,
) -> dict[str, Any]:
    fetch_html = fetcher or fetch_url
    seed_rules = [build_seed_rule(url) for url in seed_urls if normalize_url(url)]
    queue: list[tuple[str, int, str | None]] = [
        (rule["url"], 0, None)
        for rule in seed_rules
    ]
    for extra_url in extra_urls or []:
        normalized = normalize_url(extra_url)
        if normalized:
            queue.append((normalized, 0, None))

    visited: set[str] = set()
    items: list[dict[str, Any]] = []
    skipped_urls: list[str] = []
    failed_urls: list[str] = []
    truncated_content_count = 0
    attempted_url_count = 0

    while queue and len(visited) < max(1, int(max_pages)):
        url, depth, parent_url = queue.pop(0)
        if url in visited:
            continue
        if not is_allowed_url(url, seed_rules):
            skipped_urls.append(url)
            continue

        visited.add(url)
        attempted_url_count += 1
        try:
            html = fetch_html(url)
        except Exception:
            failed_urls.append(url)
            continue
        if not normalize_text(html):
            skipped_urls.append(url)
            continue

        parsed = parse_page_html(html)
        content = parsed["content"]
        if not content:
            skipped_urls.append(url)
            continue
        truncated = len(content) > max_content_chars
        if truncated:
            truncated_content_count += 1
            content = content[:max_content_chars]
        items.append(normalize_page_evidence(url, parsed["title"], content, depth, parent_url))

        if depth >= max(0, int(max_depth)):
            continue
        for link in parsed["links"]:
            next_url = normalize_url(urljoin(url, link))
            if not next_url or next_url in visited:
                continue
            if not is_allowed_url(next_url, seed_rules):
                skipped_urls.append(next_url)
                continue
            queue.append((next_url, depth + 1, url))

    evidence_items = ensure_evidence_ids(items, prefix="skax_business")
    return {
        "items": evidence_items,
        "stats": {
            "attempted_url_count": attempted_url_count,
            "collected_evidence_count": len(evidence_items),
            "skipped_url_count": len(skipped_urls),
            "failed_url_count": len(failed_urls),
            "truncated_content_count": truncated_content_count,
            "max_depth": max_depth,
            "max_pages": max_pages,
        },
        "skipped_urls": skipped_urls,
        "failed_urls": failed_urls,
    }


def fetch_url(url: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_page_html(html: str) -> dict[str, Any]:
    parser = PageHTMLParser()
    parser.feed(html)
    parser.close()
    return {
        "title": parser.title,
        "content": parser.content,
        "links": parser.links,
    }


def normalize_page_evidence(
    url: str,
    title: str | None,
    content: str,
    crawl_depth: int,
    parent_url: str | None,
) -> dict[str, Any]:
    return {
        "evidence_id": None,
        "source_type": "company_disclosure",
        "source": SK_AX_SOURCE,
        "title": title or business_domain_from_url(url) or url,
        "url": url,
        "published_at": None,
        "collected_at": now_iso(),
        "content": content,
        "related_axes": ["business_fit"],
        "business_domain": business_domain_from_url(url),
        "crawl_depth": crawl_depth,
        "parent_url": parent_url,
    }


def build_seed_rule(seed_url: str) -> dict[str, str]:
    normalized = normalize_url(seed_url)
    parsed = urlparse(normalized)
    path_prefix = normalize_path_prefix(parsed.path)
    return {
        "url": normalized,
        "scheme": parsed.scheme,
        "netloc": parsed.netloc.lower(),
        "path_prefix": path_prefix,
    }


def is_allowed_url(url: str, seed_rules: list[dict[str, str]]) -> bool:
    normalized = normalize_url(url)
    if not normalized or is_file_url(normalized):
        return False
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        return False
    for rule in seed_rules:
        if parsed.netloc.lower() != rule["netloc"]:
            continue
        if is_under_path_prefix(parsed.path, rule["path_prefix"]):
            return True
    return False


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if not text or text.startswith(("mailto:", "tel:", "javascript:")):
        return None
    without_fragment = urldefrag(text)[0]
    parsed = urlparse(without_fragment)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path or "/"
    return parsed._replace(path=path, fragment="").geturl()


def normalize_path_prefix(path: str) -> str:
    normalized = "/" + str(path or "/").strip("/")
    return normalized.rstrip("/") or "/"


def is_under_path_prefix(path: str, prefix: str) -> bool:
    normalized_path = normalize_path_prefix(path)
    normalized_prefix = normalize_path_prefix(prefix)
    return normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/")


def is_file_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(extension) for extension in SKIPPED_FILE_EXTENSIONS)


def business_domain_from_url(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[0] if parts else None


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
