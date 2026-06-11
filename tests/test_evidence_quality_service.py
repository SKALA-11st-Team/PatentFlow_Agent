from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from services.evidence.quality_service import (
    MIN_RECENCY_WEIGHT,
    MISSING_DATE_RECENCY_WEIGHT,
    drop_near_duplicate_titles,
    jaccard_similarity,
    normalized_title_tokens,
    recency_weight,
    score_and_rank_evidence_items,
    source_tier_weight,
)


NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_source_tier_weight_trusted_domain_is_full_weight():
    assert source_tier_weight({"url": "https://www.yna.co.kr/view/AKR123"}) == 1.0
    # 서브도메인도 1티어로 인정한다.
    assert source_tier_weight({"url": "https://news.reuters.com/article/1"}) == 1.0


def test_source_tier_weight_other_domain_uses_default_weight():
    assert source_tier_weight({"url": "https://unknown-blog.example.com/post"}) == pytest.approx(
        settings.evidence_default_source_weight
    )
    # URL이 없으면 source_domain 메타데이터로 판단한다.
    assert source_tier_weight({"metadata": {"source_domain": "etnews.co.kr"}}) == pytest.approx(
        settings.evidence_default_source_weight
    )
    assert source_tier_weight({"source_domain": "zdnet.co.kr"}) == 1.0


def test_recency_weight_linear_decay():
    # 오늘 발행 → 1.0
    assert recency_weight(NOW.isoformat(), now=NOW) == pytest.approx(1.0)
    # max_age 시점 발행 → 0.5
    oldest = NOW - timedelta(days=settings.news_max_age_days)
    assert recency_weight(oldest.isoformat(), now=NOW) == pytest.approx(MIN_RECENCY_WEIGHT)
    # 절반 경과 → 0.75
    half = NOW - timedelta(days=settings.news_max_age_days / 2)
    assert recency_weight(half.isoformat(), now=NOW) == pytest.approx(0.75, abs=0.01)
    # max_age 초과는 하한 0.5로 고정한다.
    too_old = NOW - timedelta(days=settings.news_max_age_days * 3)
    assert recency_weight(too_old.isoformat(), now=NOW) == pytest.approx(MIN_RECENCY_WEIGHT)


def test_recency_weight_missing_or_invalid_date():
    assert recency_weight(None, now=NOW) == pytest.approx(MISSING_DATE_RECENCY_WEIGHT)
    assert recency_weight("not-a-date", now=NOW) == pytest.approx(MISSING_DATE_RECENCY_WEIGHT)


def test_score_and_rank_sorts_by_combined_weight():
    items = [
        {"evidence_id": "low", "title": "무명 블로그 오래된 글", "url": "https://blog.example.com/1",
         "published_at": (NOW - timedelta(days=settings.news_max_age_days)).isoformat()},
        {"evidence_id": "high", "title": "연합뉴스 오늘 기사", "url": "https://www.yna.co.kr/1",
         "published_at": NOW.isoformat()},
        {"evidence_id": "mid", "title": "한국경제 발행일 미상", "url": "https://www.hankyung.com/1"},
    ]

    result = score_and_rank_evidence_items(items, now=NOW)
    ranked = result["items"]

    assert [item["evidence_id"] for item in ranked] == ["high", "mid", "low"]
    # combined = source_weight * recency_weight
    assert ranked[0]["quality_weight"] == pytest.approx(1.0)
    assert ranked[1]["quality_weight"] == pytest.approx(1.0 * MISSING_DATE_RECENCY_WEIGHT)
    assert ranked[2]["quality_weight"] == pytest.approx(
        settings.evidence_default_source_weight * MIN_RECENCY_WEIGHT
    )
    assert result["stats"] == {"input_count": 3, "kept_count": 3, "near_duplicate_dropped_count": 0}


def test_score_and_rank_drops_near_duplicate_keeping_higher_weight():
    items = [
        {"evidence_id": "copy", "title": "삼성전자, AI 반도체 신제품 공개!", "url": "https://blog.example.com/1",
         "published_at": NOW.isoformat()},
        {"evidence_id": "original", "title": "삼성전자 AI 반도체 신제품 공개", "url": "https://www.yna.co.kr/1",
         "published_at": NOW.isoformat()},
        {"evidence_id": "other", "title": "전기차 배터리 시장 동향 분석", "url": "https://www.mk.co.kr/1",
         "published_at": NOW.isoformat()},
    ]

    result = score_and_rank_evidence_items(items, now=NOW)
    kept_ids = [item["evidence_id"] for item in result["items"]]

    # 가중치가 높은(1티어) 근거가 살아남고 저가중치 사본이 제거된다.
    assert "original" in kept_ids
    assert "copy" not in kept_ids
    assert "other" in kept_ids
    assert result["stats"]["near_duplicate_dropped_count"] == 1


def test_drop_near_duplicate_titles_threshold():
    base = {"evidence_id": "a", "title": "특허 기술 시장 전망 보고"}
    similar = {"evidence_id": "b", "title": "특허 기술 시장 전망 보고!"}  # Jaccard 1.0
    different = {"evidence_id": "c", "title": "완전히 다른 주제의 기사 제목"}

    kept, dropped = drop_near_duplicate_titles([base, similar, different])

    assert [item["evidence_id"] for item in kept] == ["a", "c"]
    assert [item["evidence_id"] for item in dropped] == ["b"]


def test_drop_near_duplicate_keeps_items_without_title():
    items = [{"evidence_id": "a"}, {"evidence_id": "b", "title": ""}]

    kept, dropped = drop_near_duplicate_titles(items)

    assert len(kept) == 2
    assert dropped == []


def test_normalized_title_tokens_and_jaccard():
    left = normalized_title_tokens("LG에너지솔루션, 배터리 특허 '확대'!")
    right = normalized_title_tokens("lg에너지솔루션 배터리 특허 확대")

    assert jaccard_similarity(left, right) == pytest.approx(1.0)
    assert jaccard_similarity(left, set()) == 0.0
