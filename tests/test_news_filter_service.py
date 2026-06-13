from datetime import datetime, timezone

from services.evidence.news_filter_service import filter_news_evidence


PATENT = {
    "metadata": {"title": "강화학습 자산배분 시스템"},
    "sections": {"abstract": "액터 크리틱 알고리즘으로 금융시장 투자 전략을 수립한다."},
}
NOW = datetime(2026, 5, 5, tzinfo=timezone.utc)
RELATED = {
    "evidence_id": "news_1",
    "source_type": "news",
    "source": "naver_news",
    "title": "AI 강화학습, 자산배분에 적용",
    "url": "https://example.com/related",
    "published_at": "2026-03-10T00:00:00+09:00",
    "content": "강화학습 기반 자산배분 서비스가 금융시장 투자 전략에 활용되고 있다.",
    "metadata": {"content_char_count": 40},
}
UNRELATED = {
    "evidence_id": "news_x",
    "source_type": "news",
    "source": "naver_news",
    "title": "축구 중계권 협상 난항",
    "url": "https://example.com/x",
    "published_at": "2026-03-10T00:00:00+09:00",
    "content": "월드컵 중계권 협상이 난항을 겪고 있다.",
    "metadata": {"content_char_count": 40},
}


def test_filter_news_evidence_applies_basic_rules():
    old_item = {**RELATED, "evidence_id": "news_2", "url": "https://example.com/old",
                "published_at": "2020-01-01T00:00:00+09:00"}
    long_item = {**RELATED, "evidence_id": "news_3", "url": "https://example.com/long",
                 "metadata": {"content_char_count": 5001}}
    unrelated = {**UNRELATED, "evidence_id": "news_4", "url": "https://example.com/unrelated"}
    duplicate = {**RELATED, "evidence_id": "news_5"}

    result = filter_news_evidence(
        [RELATED, old_item, long_item, unrelated, duplicate],
        preprocessed_patent=PATENT,
        now=NOW,
    )

    assert result["stats"]["input_count"] == 5
    # EVID-07: 무관 뉴스(news_4, 키워드 0건 매칭)는 이제 폐기된다.
    assert result["stats"]["kept_count"] == 2
    assert result["kept"][0]["evidence_id"] == "news_1"
    assert result["kept"][1]["evidence_id"] == "news_3"
    assert result["kept"][1]["metadata"]["news_filter"]["content_truncated"] is True
    assert result["kept"][0]["metadata"]["news_filter"]["published_at_missing"] is False
    reasons = {item["reason"] for item in result["rejected"]}
    assert {"older_than_3_years", "duplicate", "no_patent_keyword_match"} <= reasons


def test_filter_news_evidence_rejects_zero_keyword_match():
    # EVID-07: 특허 키워드가 있는데 매칭이 0건이면 폐기한다.
    result = filter_news_evidence([UNRELATED], preprocessed_patent=PATENT, now=NOW)
    assert result["stats"]["kept_count"] == 0
    assert result["rejected"][0]["reason"] == "no_patent_keyword_match"
    assert result["rejected"][0]["matched_keywords"] == []


def test_filter_news_evidence_keeps_when_no_patent_keywords():
    # EVID-07 가드: 특허 키워드가 비어 있으면 키워드 필터를 적용하지 않아 전멸을 막는다.
    empty_patent = {"metadata": {}, "sections": {}}
    result = filter_news_evidence([UNRELATED], preprocessed_patent=empty_patent, now=NOW)
    assert result["stats"]["patent_keyword_count"] == 0
    assert result["stats"]["kept_count"] == 1


def test_filter_news_evidence_exempts_localized_foreign_news_from_keyword_match():
    # 해외특허 현지어 뉴스(source=domestic_news)는 한국어 patent_keywords와 안 겹쳐도 폐기하지 않는다.
    jp_news = {
        "evidence_id": "news_jp",
        "source_type": "news",
        "source": "domestic_news",
        "title": "半導体工程のCpkモニタリング技術",
        "url": "https://example.jp/news",
        "published_at": "2026-03-10T00:00:00+09:00",
        "content": "FDCインターロックの発生頻度を測定し工程能力を監視する。",
        "metadata": {"content_char_count": 40},
    }
    result = filter_news_evidence([jp_news], preprocessed_patent=PATENT, now=NOW)
    assert result["stats"]["kept_count"] == 1
    # 대조군: 동일 내용이 naver_news였다면 키워드 0건 매칭으로 폐기됐어야 한다.
    kr_equivalent = {**jp_news, "source": "naver_news", "evidence_id": "news_kr"}
    kr_result = filter_news_evidence([kr_equivalent], preprocessed_patent=PATENT, now=NOW)
    assert kr_result["stats"]["kept_count"] == 0
    assert kr_result["rejected"][0]["reason"] == "no_patent_keyword_match"


def test_filter_keeps_news_with_missing_published_at():
    # EVID-06: 발행일이 없어도 관련 뉴스는 통과하고 published_at_missing 플래그가 붙는다.
    no_date = {**RELATED, "evidence_id": "news_nd", "url": "https://example.com/nodate"}
    no_date.pop("published_at")
    result = filter_news_evidence([no_date], preprocessed_patent=PATENT, now=NOW)
    assert result["stats"]["kept_count"] == 1
    assert result["kept"][0]["metadata"]["news_filter"]["published_at_missing"] is True


def test_filter_keeps_news_with_unparseable_published_at():
    bad_date = {**RELATED, "evidence_id": "news_bd", "url": "https://example.com/bad",
                "published_at": "not-a-date"}
    result = filter_news_evidence([bad_date], preprocessed_patent=PATENT, now=NOW)
    assert result["stats"]["kept_count"] == 1
    assert result["kept"][0]["metadata"]["news_filter"]["published_at_missing"] is True
