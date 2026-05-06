from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
import re

import requests

try:
    import trafilatura
except ImportError:  # pragma: no cover - optional dependency fallback
    trafilatura = None


ARTICLE_MIN_CHARS = 400
ARTICLE_MAX_CHARS = 12000
ARTICLE_TIMEOUT_SECONDS = 8
MAX_NOISE_LINE_RATIO = 0.25
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class ParagraphHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.current: list[str] = []
        self.capture_depth = 0
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "footer", "header", "nav"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "h1", "h2", "h3", "li"}:
            self._flush()
            self.capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag in {"script", "style", "noscript", "svg", "footer", "header", "nav"}:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in {"p", "h1", "h2", "h3", "li"} and self.capture_depth:
            self.capture_depth -= 1
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.capture_depth:
            return
        text = normalize_article_text(data)
        if text:
            self.current.append(text)

    def _flush(self) -> None:
        if not self.current:
            return
        text = normalize_article_text(" ".join(self.current))
        self.current = []
        if len(text) >= 20:
            self.paragraphs.append(text)


def enrich_news_items_with_full_text(
    items: list[dict[str, Any]],
    *,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    if not enabled:
        for item in items:
            mark_content_source(item, "snippet")
        return items

    for item in items:
        if item.get("source_type") != "news":
            continue
        snippet = str(item.get("content") or "")
        url = item.get("url")
        if not url:
            mark_content_source(item, "snippet")
            continue
        result = fetch_article_text(str(url))
        if result["text"]:
            item["content"] = result["text"]
            mark_content_source(item, result.get("source") or "full_text")
            item.setdefault("metadata", {})["content_char_count"] = len(result["text"])
        else:
            item["content"] = snippet
            mark_content_source(item, "snippet")
            if result["error"]:
                item.setdefault("metadata", {})["article_fetch_error"] = result["error"]
    return items


def fetch_article_text(url: str) -> dict[str, str | None]:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=ARTICLE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return {"text": None, "error": f"{exc.__class__.__name__}: {exc}", "source": None}

    trafilatura_text = extract_with_trafilatura(response.text, url=url)
    if trafilatura_text:
        return {"text": trafilatura_text[:ARTICLE_MAX_CHARS], "error": None, "source": "full_text_trafilatura"}

    parser = ParagraphHTMLParser()
    try:
        parser.feed(response.text)
        parser.close()
    except Exception as exc:
        return {"text": None, "error": f"html_parse_failed:{exc.__class__.__name__}", "source": None}

    text = join_article_paragraphs(parser.paragraphs)
    if not is_valid_article_text(text):
        return {"text": None, "error": classify_article_failure(text), "source": None}
    return {"text": text[:ARTICLE_MAX_CHARS], "error": None, "source": "full_text_fallback_parser"}


def extract_with_trafilatura(html_text: str, *, url: str) -> str | None:
    if trafilatura is None:
        return None
    extracted = trafilatura.extract(
        html_text,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
        deduplicate=True,
        output_format="txt",
    )
    text = clean_article_text(extracted or "")
    if not is_valid_article_text(text):
        return None
    return text


def join_article_paragraphs(paragraphs: list[str]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        normalized = normalize_article_text(paragraph)
        if not normalized or normalized in seen:
            continue
        if is_boilerplate(normalized):
            continue
        seen.add(normalized)
        result.append(normalized)
    return clean_article_text("\n".join(result))


def clean_article_text(value: str) -> str:
    lines = []
    for line in (value or "").splitlines():
        cleaned = normalize_article_text(line)
        if not cleaned:
            continue
        if is_boilerplate(cleaned):
            continue
        lines.append(cleaned)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_article_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "")
    return text.strip()


def is_boilerplate(text: str) -> bool:
    lowered = text.lower()
    if len(text) < 20:
        return True
    boilerplate_keywords = [
        "advertisement",
        "copyright",
        "all rights reserved",
        "all contents copyright",
        "recommended articles",
        "related articles",
        "newsletter",
        "subscribe",
        "sign in",
        "sign up",
        "click here",
        "photo by",
        "getty images",
        "연합뉴스",
        "무단전재",
        "무단 전재",
        "재배포 금지",
        "무단복제",
        "저작권자",
        "저작권",
        "광고",
        "추천기사",
        "관련기사",
        "관련 기사",
        "많이 본 뉴스",
        "인기뉴스",
        "주요뉴스",
        "함께 보면 좋은",
        "구독",
        "로그인",
        "회원가입",
        "제보",
        "카카오톡",
        "네이버에서",
    ]
    if any(keyword in lowered for keyword in boilerplate_keywords):
        return True
    reporter_patterns = [
        r"^[가-힣]{2,4}\s*기자\s*[a-z0-9_.+-]+@[a-z0-9.-]+$",
        r"^[가-힣]{2,4}\s*기자$",
        r"^[a-z .'-]+\s+reports\s+",
    ]
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in reporter_patterns)


def is_valid_article_text(text: str) -> bool:
    if len(text) < ARTICLE_MIN_CHARS:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    noisy = [line for line in lines if is_noise_like_line(line)]
    return (len(noisy) / len(lines)) <= MAX_NOISE_LINE_RATIO


def is_noise_like_line(text: str) -> bool:
    lowered = text.lower()
    noise_tokens = [
        "광고",
        "추천",
        "관련기사",
        "copyright",
        "subscribe",
        "newsletter",
        "login",
        "구독",
        "로그인",
        "회원가입",
    ]
    if any(token in lowered for token in noise_tokens):
        return True
    if len(re.sub(r"[^가-힣a-zA-Z0-9]", "", text)) < 20:
        return True
    return False


def classify_article_failure(text: str) -> str:
    if len(text) < ARTICLE_MIN_CHARS:
        return "article_text_too_short"
    return "article_text_too_noisy"


def mark_content_source(item: dict[str, Any], source: str) -> None:
    metadata = item.setdefault("metadata", {})
    metadata["content_source"] = source
    metadata["content_char_count"] = len(str(item.get("content") or ""))
