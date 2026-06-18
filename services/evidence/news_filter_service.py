from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import json
import re

from app.config import settings
from services.evidence.store_service import now_iso, safe_filename


DEFAULT_FILTERED_NEWS_DIR = settings.output_dir / "filtered_evidence" / "news"
DEFAULT_PREVIEW_CHARS = 100
DEFAULT_MAX_CONTENT_CHARS = 5000
DEFAULT_MAX_AGE_DAYS = settings.news_max_age_days

KOREAN_STOPWORDS = {
    "그리고",
    "그러나",
    "대한",
    "관련",
    "기반",
    "방법",
    "시스템",
    "제공",
    "포함",
    "단계",
    "통해",
    "있는",
    "있다",
    "한다",
}


# @author 배세은
# @date 2026-05-06
# @relatedFR FR-007
# @relatedUI UI-005
# @description 수집한 뉴스를 특허 키워드 매칭·노후(5년 컷오프)·중복 기준으로 걸러 시장성
# 평가 근거로 쓸 뉴스만 남긴다. kept/rejected와 사유를 함께 돌려 근거 선별 근거를 추적 가능하게 한다.
def filter_news_evidence(
    items: list[dict[str, Any]],
    *,
    preprocessed_patent: dict[str, Any],
    now: datetime | None = None,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> dict[str, Any]:
    reference_text = build_patent_reference_text(preprocessed_patent)
    patent_keywords = extract_keywords(reference_text)
    current_time = now or datetime.now(timezone.utc).astimezone()
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for item in items:
        decision = evaluate_news_item(
            item,
            patent_keywords=patent_keywords,
            seen=seen,
            now=current_time,
            preview_chars=preview_chars,
            max_content_chars=max_content_chars,
            max_age_days=max_age_days,
        )
        if decision["keep"]:
            filtered_item = dict(item)
            metadata = dict(filtered_item.get("metadata") or {})
            metadata["news_filter"] = {
                "preview": decision["preview"],
                "matched_keywords": decision["matched_keywords"],
                "content_truncated": decision["content_truncated"],
                "original_content_char_count": decision["content_char_count"],
                "published_at_missing": decision["published_at_missing"],
            }
            filtered_item["metadata"] = metadata
            if decision["content_truncated"]:
                filtered_item["content"] = str(filtered_item.get("content") or "")[:max_content_chars]
            kept.append(filtered_item)
        else:
            rejected.append(
                {
                    "evidence_id": item.get("evidence_id"),
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "published_at": item.get("published_at"),
                    "reason": decision["reason"],
                    "content_char_count": decision["content_char_count"],
                    "preview": decision["preview"],
                    "matched_keywords": decision["matched_keywords"],
                }
            )

    return {
        "kept": kept,
        "rejected": rejected,
        "stats": {
            "input_count": len(items),
            "kept_count": len(kept),
            "rejected_count": len(rejected),
            "patent_keyword_count": len(patent_keywords),
        },
    }


def evaluate_news_item(
    item: dict[str, Any],
    *,
    patent_keywords: set[str],
    seen: set[str],
    now: datetime,
    preview_chars: int,
    max_content_chars: int,
    max_age_days: int,
) -> dict[str, Any]:
    title = str(item.get("title") or "")
    content = str(item.get("content") or "")
    preview = content[:preview_chars]
    content_char_count = get_content_char_count(item, content)
    dedupe_key = build_news_dedupe_key(item)

    if dedupe_key in seen:
        return reject("duplicate", preview, content_char_count)
    seen.add(dedupe_key)

    published_at = parse_datetime(item.get("published_at"))
    # EVID-06: 발행일 미상(누락·파싱 실패)이라도 관련 뉴스는 폐기하지 않고 통과시키되, 노후 컷오프만 건너뛴다.
    if published_at is not None and now - published_at > timedelta(days=max_age_days):
        return reject("older_than_3_years", preview, content_char_count)

    matched_keywords = sorted(extract_keywords(f"{title}\n{preview}") & patent_keywords)
    # EVID-07: 특허 키워드와 한 건도 매칭되지 않는 무관 뉴스는 거른다.
    # 단 특허 키워드 자체가 비어 있으면(빈 메타데이터) 전체 전멸을 막기 위해 필터를 적용하지 않는다.
    # 해외특허 현지어 뉴스(domestic_news)는 Tavily country+현지어 쿼리로 이미 관련성이 확보됐고,
    # 한국어 patent_keywords와는 언어가 달라 교집합이 비어 대량 오거부되므로 키워드 매칭 거부를 면제한다.
    is_localized_foreign_news = str(item.get("source") or "") == "domestic_news"
    if patent_keywords and not matched_keywords and not is_localized_foreign_news:
        return reject("no_patent_keyword_match", preview, content_char_count)

    content_truncated = content_char_count > max_content_chars

    return {
        "keep": True,
        "reason": "passed",
        "preview": preview,
        "content_char_count": content_char_count,
        "content_truncated": content_truncated,
        "matched_keywords": matched_keywords,
        "published_at_missing": published_at is None,
    }


def reject(reason: str, preview: str, content_char_count: int) -> dict[str, Any]:
    return {
        "keep": False,
        "reason": reason,
        "preview": preview,
        "content_char_count": content_char_count,
        "content_truncated": False,
        "matched_keywords": [],
        "published_at_missing": False,
    }


def save_filtered_news_result(
    *,
    patent_id: str | int | None,
    result: dict[str, Any],
    output_dir: Path | str = DEFAULT_FILTERED_NEWS_DIR,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(str(patent_id) if patent_id is not None else 'unknown')}_filtered_news.json"
    path = directory / filename
    payload = {
        "source_type": "news",
        "source": "news_filter",
        "patent_id": str(patent_id) if patent_id is not None else None,
        "collected_at": now_iso(),
        **result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_patent_reference_text(preprocessed_patent: dict[str, Any]) -> str:
    metadata = preprocessed_patent.get("metadata") or {}
    sections = preprocessed_patent.get("sections") or {}
    parts = [
        metadata.get("title"),
        metadata.get("title_eng"),
        sections.get("abstract"),
    ]
    return "\n".join(str(part) for part in parts if part)


def extract_keywords(text: str) -> set[str]:
    normalized = re.sub(r"\s+", " ", text.lower())
    tokens = set(re.findall(r"[0-9a-zA-Z가-힣]{2,}", normalized))
    return {
        token
        for token in tokens
        if len(token) >= 2 and token not in KOREAN_STOPWORDS and not token.isdigit()
    }


def get_content_char_count(item: dict[str, Any], content: str) -> int:
    metadata = item.get("metadata") or {}
    value = metadata.get("content_char_count")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return len(content)


def build_news_dedupe_key(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "").strip().lower()
    if url:
        return f"url:{url}"
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip().lower()
    published_at = str(item.get("published_at") or "")
    return f"title:{title}:{published_at}"


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()
