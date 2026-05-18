from datetime import datetime, timezone

from services.evidence.news_filter_service import filter_news_evidence


def test_filter_news_evidence_applies_basic_rules():
    patent = {
        "metadata": {"title": "강화학습 자산배분 시스템"},
        "sections": {"abstract": "액터 크리틱 알고리즘으로 금융시장 투자 전략을 수립한다."},
    }
    now = datetime(2026, 5, 5, tzinfo=timezone.utc)
    fresh_related = {
        "evidence_id": "news_1",
        "source_type": "news",
        "source": "naver_news",
        "title": "AI 강화학습, 자산배분에 적용",
        "url": "https://example.com/related",
        "published_at": "2026-03-10T00:00:00+09:00",
        "content": "강화학습 기반 자산배분 서비스가 금융시장 투자 전략에 활용되고 있다.",
        "metadata": {"content_char_count": 40},
    }
    old_item = {
        **fresh_related,
        "evidence_id": "news_2",
        "url": "https://example.com/old",
        "published_at": "2020-01-01T00:00:00+09:00",
    }
    long_item = {
        **fresh_related,
        "evidence_id": "news_3",
        "url": "https://example.com/long",
        "metadata": {"content_char_count": 5001},
    }
    unrelated = {
        **fresh_related,
        "evidence_id": "news_4",
        "url": "https://example.com/unrelated",
        "title": "축구 중계권 협상 난항",
        "content": "월드컵 중계권 협상이 난항을 겪고 있다.",
    }
    duplicate = {
        **fresh_related,
        "evidence_id": "news_5",
    }

    result = filter_news_evidence(
        [fresh_related, old_item, long_item, unrelated, duplicate],
        preprocessed_patent=patent,
        now=now,
    )

    assert result["stats"]["input_count"] == 5
    assert result["stats"]["kept_count"] == 3
    assert result["kept"][0]["evidence_id"] == "news_1"
    assert result["kept"][1]["evidence_id"] == "news_3"
    assert result["kept"][1]["metadata"]["news_filter"]["content_truncated"] is True
    assert result["kept"][2]["evidence_id"] == "news_4"
    reasons = {item["reason"] for item in result["rejected"]}
    assert {"older_than_3_years", "duplicate"} <= reasons
