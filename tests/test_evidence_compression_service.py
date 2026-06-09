import json
import threading
import time

from services.evidence.compression_service import compress_evidence_items, select_compression_candidates


def test_select_compression_candidates_keeps_news_and_high_score_rag():
    items = [
        {"evidence_id": "news_1", "source_type": "news", "content": "뉴스"},
        {"evidence_id": "rag_high", "source_type": "industry_report", "score": 0.5, "context": "청크"},
        {"evidence_id": "rag_low", "source_type": "industry_report", "score": 0.49, "context": "청크"},
        {"evidence_id": "disclosure_1", "source_type": "company_disclosure", "content": "공시"},
    ]

    candidates, skipped = select_compression_candidates(items, rag_score_threshold=0.5)

    # company_disclosure(SK AX 공식 근거)도 이제 압축 후보에 포함된다.
    assert [item["evidence_id"] for item in candidates] == ["news_1", "rag_high", "disclosure_1"]
    assert skipped["low_rag_score"] == 1
    assert skipped["non_target"] == 0


def test_compress_evidence_items_normalizes_news_and_rag_shapes():
    items = [
        {
            "evidence_id": "news_1",
            "source_type": "news",
            "source": "naver_news",
            "title": "AI 로보어드바이저 경쟁",
            "url": "https://example.com/news",
            "published_at": "2026-05-05T00:00:00+09:00",
            "collected_at": "2026-05-06T00:00:00+09:00",
            "content": "퇴직연금 시장에서 AI 로보어드바이저 경쟁이 확대된다.",
        },
        {
            "evidence_id": "rag_1",
            "source_type": "industry_report",
            "source": "report.pdf",
            "title": "자동차 내수",
            "published_at": "2026",
            "collected_at": "2026-05-06T00:00:00+09:00",
            "metadata": {"industry": "자동차", "page": 26, "heading": "자동차 내수"},
            "context": "경기침체와 차량 가격 상승으로 자동차 내수가 제한된다.",
            "score": 0.63,
        },
    ]

    def fake_llm(prompt):
        if "AI 로보어드바이저 경쟁" in prompt:
            response = {
                "is_relevant": True,
                "relation_type": "indirect",
                "compressed_summary": "AI 로보어드바이저 경쟁이 확대되고 있다.",
                "key_facts": ["퇴직연금 시장에서 AI 로보어드바이저 경쟁이 확대된다."],
            }
        else:
            response = {
                "related_axes": ["market"],
                "compressed_summary": "자동차 내수 반등 요인이 제한적이다.",
                "key_facts": ["경기침체와 가격 상승이 내수에 부정적이다."],
            }
        return json.dumps(response, ensure_ascii=False)

    result = compress_evidence_items(
        items,
        preprocessed_patent={"metadata": {"title": "테스트 특허"}, "sections": {"abstract": "초록"}},
        rag_score_threshold=0.5,
        llm=fake_llm,
    )

    assert result["stats"]["compressed_count"] == 2
    items_by_id = {item["evidence_id"]: item for item in result["items"]}
    news = items_by_id["news_1"]
    assert news["is_relevant"] is True
    assert news["compressed_summary"] == "AI 로보어드바이저 경쟁이 확대되고 있다."
    assert "content" not in news

    rag = items_by_id["rag_1"]
    assert rag["retrieval_score"] == 0.63
    assert rag["compressed_summary"] == "자동차 내수 반등 요인이 제한적이다."
    assert "context" not in rag
    assert "content" not in rag


def test_compress_evidence_items_runs_llm_calls_concurrently():
    items = [
        {
            "evidence_id": "news_1",
            "source_type": "news",
            "source": "naver_news",
            "title": "뉴스 1",
            "content": "로보어드바이저 시장 확대",
        },
        {
            "evidence_id": "news_2",
            "source_type": "news",
            "source": "naver_news",
            "title": "뉴스 2",
            "content": "AI 자산관리 시장 확대",
        },
    ]
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_llm(prompt):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return json.dumps(
            {
                "is_relevant": True,
                "related_axes": ["market"],
                "compressed_summary": "시장 확대 근거",
                "key_facts": ["시장 확대"],
            },
            ensure_ascii=False,
        )

    result = compress_evidence_items(
        items,
        preprocessed_patent={"metadata": {"title": "테스트 특허"}, "sections": {"abstract": "초록"}},
        llm=fake_llm,
    )

    assert max_active > 1
    assert [item["evidence_id"] for item in result["items"]] == ["news_1", "news_2"]


def test_compression_prompt_uses_minimal_patent_context():
    item = {
        "evidence_id": "news_1",
        "source_type": "news",
        "source": "naver_news",
        "title": "뉴스",
        "content": "뉴스 본문",
    }
    captured_prompts = []

    def fake_llm(prompt):
        captured_prompts.append(prompt)
        return json.dumps(
            {
                "is_relevant": True,
                "related_axes": ["market"],
                "compressed_summary": "요약",
                "key_facts": ["사실"],
            },
            ensure_ascii=False,
        )

    compress_evidence_items(
        [item],
        preprocessed_patent={
            "metadata": {"title": "테스트 특허", "title_eng": "Test Patent", "ipc": ["G06Q"]},
            "sections": {
                "abstract": "초록",
                "technical_field": "기술분야",
                "problem": "문제",
                "solution": "해결수단",
            },
        },
        llm=fake_llm,
    )

    payload = json.loads(captured_prompts[0].split("Input JSON:", 1)[1])
    assert payload["patent"] == {
        "title": "테스트 특허",
        "abstract": "초록",
        "technical_field": "기술분야",
    }
