from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import html
import re

from services.evidence.store_service import now_iso


def normalize_naver_news_response(
    raw: dict[str, Any] | list[dict[str, Any]],
    *,
    query: str,
    collected_at: str | None = None,
) -> list[dict[str, Any]]:
    collected_at = collected_at or now_iso()
    items = raw if isinstance(raw, list) else raw.get("items", [])
    evidence = []
    for rank, item in enumerate(items, start=1):
        title = clean_html(item.get("title"))
        description = clean_html(item.get("description"))
        published_at = parse_rfc2822_datetime(item.get("pubDate"))
        evidence.append(
            {
                "evidence_id": None,
                "source_type": "news",
                "source": "naver_news",
                "title": title,
                "url": item.get("originallink") or item.get("link"),
                "published_at": published_at,
                "collected_at": collected_at,
                "content": description or title or "",
                "confidence": None,
                "metadata": {
                    "query": query,
                    "rank": rank,
                    "raw_pubDate": item.get("pubDate"),
                },
            }
        )
    return evidence


def normalize_gnews_response(
    raw: dict[str, Any] | list[dict[str, Any]],
    *,
    query: str,
    collected_at: str | None = None,
) -> list[dict[str, Any]]:
    collected_at = collected_at or now_iso()
    articles = raw if isinstance(raw, list) else raw.get("articles", [])
    evidence = []
    for rank, article in enumerate(articles, start=1):
        source = article.get("source") or {}
        published_at = normalize_iso_datetime(article.get("publishedAt"))
        evidence.append(
            {
                "evidence_id": None,
                "source_type": "news",
                "source": "gnews",
                "title": article.get("title"),
                "url": article.get("url"),
                "published_at": published_at,
                "collected_at": collected_at,
                "content": article.get("description") or article.get("content") or article.get("title") or "",
                "confidence": None,
                "metadata": {
                    "query": query,
                    "rank": rank,
                    "image": article.get("image"),
                    "publisher": source.get("name") if isinstance(source, dict) else source,
                },
            }
        )
    return evidence


def normalize_tavily_news_response(
    raw: dict[str, Any] | list[dict[str, Any]],
    *,
    query: str,
    collected_at: str | None = None,
    source: str = "global_news",
    country: str | None = None,
) -> list[dict[str, Any]]:
    """Tavily(topic=news) 검색 결과를 공통 뉴스 evidence shape로 변환한다.

    기본은 글로벌/해외 뉴스 근거(source="global_news", 시장성 글로벌 사업성)다.
    해외특허 domestic 채널은 source="domestic_news" + country(대상국)로 호출해
    대상 특허 본국 현지 뉴스로 태깅한다(시장성 산업 시장성·경쟁성).
    """
    collected_at = collected_at or now_iso()
    results = raw if isinstance(raw, list) else raw.get("results", [])
    evidence = []
    for rank, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        # Tavily news는 published_date를 RFC 2822(예: 'Mon, 11 Nov 2024 ...')로 준다.
        published_at = parse_rfc2822_datetime(item.get("published_date")) or normalize_iso_datetime(
            item.get("published_date")
        )
        content = item.get("raw_content") or item.get("content") or item.get("title") or ""
        evidence.append(
            {
                "evidence_id": None,
                "source_type": "news",
                "source": source,
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": published_at,
                "collected_at": collected_at,
                "content": content,
                "confidence": None,
                "metadata": {
                    "query": query,
                    "rank": rank,
                    "score": item.get("score"),
                    "provider": "tavily_news",
                    "country": country,
                },
            }
        )
    return evidence


def normalize_kipris_patent_results(
    raw: dict[str, Any] | list[dict[str, Any]],
    *,
    query: str,
    collected_at: str | None = None,
    source: str = "kipris",
) -> list[dict[str, Any]]:
    collected_at = collected_at or now_iso()
    items = raw if isinstance(raw, list) else extract_kipris_items(raw)
    evidence = []
    for rank, item in enumerate(items, start=1):
        title = first_present(item, "inventionTitle", "title", "inventionName")
        applicant = first_present(item, "applicantName", "applicant", "applicantNameList")
        application_number = first_present(item, "applicationNumber", "applicationNo")
        published_at = (
            parse_yyyymmdd(first_present(item, "openDate", "publicationDate"))
            or parse_yyyymmdd(first_present(item, "applicationDate", "applicationDt"))
            or parse_dot_date(first_present(item, "openDate", "publicationDate", "applicationDate"))
        )
        evidence.append(
            {
                "evidence_id": None,
                "source_type": "competitor_patent",
                "source": source,
                "title": title,
                "url": None,
                "published_at": published_at,
                "collected_at": collected_at,
                "content": first_present(item, "abstract", "astrtCont", "summary") or " - ".join(part for part in (title, applicant) if part),
                "confidence": None,
                "metadata": {
                    "query": query,
                    "rank": rank,
                    "application_number": application_number,
                    "registration_number": first_present(item, "registerNumber", "registrationNumber"),
                    "applicant": applicant,
                    "ipc": first_present(item, "ipcNumber", "ipc"),
                    "raw": item,
                },
            }
        )
    return evidence


def clean_html(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_rfc2822_datetime(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


def normalize_iso_datetime(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat(timespec="seconds")


def parse_yyyymmdd(value: Any) -> str | None:
    if not value:
        return None
    text = re.sub(r"\D", "", str(value))
    if len(text) != 8:
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def parse_dot_date(value: Any) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{4})[.년/-]\s*(\d{1,2})[.월/-]\s*(\d{1,2})", str(value))
    if not match:
        return None
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def extract_kipris_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        raw.get("items"),
        raw.get("item"),
        ((raw.get("response") or {}).get("body") or {}).get("items", {}).get("PatentUtilityInfo")
        if isinstance(((raw.get("response") or {}).get("body") or {}).get("items"), dict)
        else None,
        (((raw.get("response") or {}).get("body") or {}).get("items") or {}).get("item")
        if isinstance((((raw.get("response") or {}).get("body") or {}).get("items") or {}), dict)
        else None,
        ((raw.get("response") or {}).get("body") or {}).get("item"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            return [candidate]
    return []


def first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None
