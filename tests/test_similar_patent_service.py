from services.patent import similar_patent_service


class _FakeEmbeddingModel:
    """텍스트→벡터 매핑이 고정된 결정론 임베딩 모델(테스트용)."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def embed_many(self, texts):
        return [self._vectors[text] for text in texts]


class _BrokenEmbeddingModel:
    def embed_many(self, texts):
        raise RuntimeError("embedding backend down")


def test_rank_uses_embedding_cosine_ordering():
    target_text = "대상"
    candidates = [
        {"display_number": "near", "similarity_text": "가까움"},
        {"display_number": "far", "similarity_text": "멀음"},
    ]
    model = _FakeEmbeddingModel(
        {
            "대상": [1.0, 0.0],
            "가까움": [1.0, 0.0],  # cosine 1.0
            "멀음": [0.0, 1.0],  # cosine 0.0
        }
    )

    ranked = similar_patent_service.rank_similar_patent_candidates(
        target_text=target_text, candidates=candidates, embedding_model=model
    )

    assert [item["display_number"] for item in ranked] == ["near", "far"]
    assert ranked[0]["similarity_method"] == "embedding"
    assert ranked[0]["similarity"] == 1.0
    assert ranked[1]["similarity"] == 0.0


def test_rank_falls_back_to_jaccard_without_model(monkeypatch):
    monkeypatch.setattr(
        similar_patent_service, "_default_similarity_embedding_model", lambda: None
    )
    candidates = [
        {"display_number": "overlap", "similarity_text": "공통 토큰 분석"},
        {"display_number": "none", "similarity_text": "전혀 다른 단어"},
    ]

    ranked = similar_patent_service.rank_similar_patent_candidates(
        target_text="공통 토큰 분석", candidates=candidates, embedding_model=None
    )

    assert ranked[0]["display_number"] == "overlap"
    assert all(item["similarity_method"] == "jaccard" for item in ranked)


def test_rank_falls_back_to_jaccard_when_embedding_raises():
    candidates = [{"display_number": "x", "similarity_text": "공통 토큰"}]

    ranked = similar_patent_service.rank_similar_patent_candidates(
        target_text="공통 토큰",
        candidates=candidates,
        embedding_model=_BrokenEmbeddingModel(),
    )

    assert ranked[0]["similarity_method"] == "jaccard"
    assert ranked[0]["similarity"] > 0.0


def test_collect_similar_patent_pdfs_exposes_claims_and_technical_content(monkeypatch, tmp_path):
    monkeypatch.setattr(
        similar_patent_service,
        "download_and_parse_patent_pdf",
        lambda *args, **kwargs: {
            "pdf_path": str(tmp_path / "similar.pdf"),
            "markdown_paths": [],
            "markdown_text": """
## 청구범위
청구항 1 입력을 분석하는 프로세서를 포함하는 시스템.
청구항 2 청구항 1에 있어서, 분석 결과를 저장하는 시스템.
## 해결하려는 과제
분석 정확도를 높인다.
## 과제의 해결 수단
입력 특징과 관계를 함께 분석한다.
## 발명의 효과
관계 설명력이 향상된다.
## 발명을 실시하기 위한 구체적인 내용
프로세서는 특징 벡터와 관계 행렬을 계산한다.
""",
        },
    )

    enriched, warnings = similar_patent_service.collect_similar_patent_pdfs(
        [{"application_number": "1020200000001", "title": "유사 특허", "status": "등록"}],
        output_dir=tmp_path,
        text_limit=None,
    )

    assert warnings == []
    assert [claim["claim_no"] for claim in enriched[0]["representative_claims"]] == [1, 2]
    assert enriched[0]["technical_content"] == {
        "problem": "분석 정확도를 높인다.",
        "solution": "입력 특징과 관계를 함께 분석한다.",
        "effect": "관계 설명력이 향상된다.",
        "detailed_description": "프로세서는 특징 벡터와 관계 행렬을 계산한다.",
    }
