from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
import re

from app.config import settings
from services.evidence.news_filter_service import parse_datetime

# @relatedFR FR-007
# @description EVID-12: 근거 품질 가중치(출처 신뢰도 x 최신성)를 부여하고
# 가중치 내림차순 정렬 + 제목 근사중복 제거를 수행한다. 압축/선택 단계에서
# 절단이 일어나도 고품질 근거가 먼저 살아남게 하는 것이 목적이다.

# 발행일 미상 근거의 최신성 가중치(과도한 우대/배제를 피하는 중간값).
MISSING_DATE_RECENCY_WEIGHT = 0.7
# 최신성 선형 감쇠 하한: news_max_age_days 이상 오래된 근거의 가중치.
MIN_RECENCY_WEIGHT = 0.5
# 정규화 제목 토큰 Jaccard 유사도가 이 값 이상이면 근사중복으로 본다.
NEAR_DUPLICATE_JACCARD_THRESHOLD = 0.8

_TITLE_NORMALIZE_RE = re.compile(r"[^0-9a-z가-힣\s]+")


def score_and_rank_evidence_items(
    items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """근거 목록에 quality_weight를 부여하고 정렬·근사중복 제거한 결과를 반환한다."""
    current_time = now or datetime.now(timezone.utc).astimezone()
    scored: list[dict[str, Any]] = []
    for item in items:
        weighted = dict(item)
        weighted["quality_weight"] = round(
            source_tier_weight(item) * recency_weight(item.get("published_at"), now=current_time),
            4,
        )
        scored.append(weighted)
    # 안정 정렬이므로 가중치가 같은 근거는 수집 순서를 유지한다.
    scored.sort(key=lambda entry: entry["quality_weight"], reverse=True)
    kept, dropped = drop_near_duplicate_titles(scored)
    return {
        "items": kept,
        "stats": {
            "input_count": len(items),
            "kept_count": len(kept),
            "near_duplicate_dropped_count": len(dropped),
        },
    }


def source_tier_weight(item: dict[str, Any]) -> float:
    domain = evidence_source_domain(item)
    if domain and is_trusted_domain(domain):
        return 1.0
    return settings.evidence_default_source_weight


def evidence_source_domain(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "").strip()
    if url:
        netloc = urlparse(url).netloc.lower()
        return netloc.removeprefix("www.")
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    domain = str(item.get("source_domain") or metadata.get("source_domain") or "").strip().lower()
    return domain.removeprefix("www.")


def is_trusted_domain(domain: str) -> bool:
    return any(
        domain == trusted or domain.endswith(f".{trusted}")
        for trusted in settings.evidence_trusted_domains
    )


def recency_weight(published_at: Any, *, now: datetime) -> float:
    published = parse_datetime(published_at)
    if published is None:
        return MISSING_DATE_RECENCY_WEIGHT
    max_age_days = max(1, settings.news_max_age_days)
    age_days = (now - published).total_seconds() / 86400
    # 미래 발행일(시간대 오차 등)은 오늘로 간주하고, max_age 초과는 하한으로 고정한다.
    ratio = min(max(age_days, 0.0) / max_age_days, 1.0)
    return 1.0 - (1.0 - MIN_RECENCY_WEIGHT) * ratio


def drop_near_duplicate_titles(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """가중치 내림차순 목록에서 제목 근사중복을 제거한다(먼저 온 고가중치 근거 유지)."""
    kept: list[dict[str, Any]] = []
    kept_tokens: list[set[str]] = []
    dropped: list[dict[str, Any]] = []
    for item in items:
        tokens = normalized_title_tokens(item.get("title"))
        if tokens and any(
            jaccard_similarity(tokens, existing) >= NEAR_DUPLICATE_JACCARD_THRESHOLD
            for existing in kept_tokens
        ):
            dropped.append(item)
            continue
        kept.append(item)
        if tokens:
            kept_tokens.append(tokens)
    return kept, dropped


def normalized_title_tokens(title: Any) -> set[str]:
    normalized = _TITLE_NORMALIZE_RE.sub(" ", str(title or "").lower())
    return {token for token in normalized.split() if token}


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
