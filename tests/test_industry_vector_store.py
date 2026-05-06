from rag.industry_vector_store import (
    HashingEmbeddingModel,
    build_search_params,
    prepare_chunks,
    render_embedding_text,
    to_pgvector_literal,
    validate_embeddings,
)
from services.rag import industry_rag_service


class _SearchResult:
    chunk_id = "chunk_001"
    text = "산업 보고서 원문 청크"
    score = 0.25
    metadata = {
        "source_type": "industry_report",
        "source_name": "sample.pdf",
        "industry": "자동차",
        "page": 12,
        "heading": "시장 전망",
        "published_year": 2026,
    }


class _Store:
    def __init__(self, database_url, embedding_model=None):
        self.database_url = database_url
        self.embedding_model = embedding_model

    def search(self, query, top_k=5, industry=None):
        return [_SearchResult()]


def test_prepare_chunks_and_pgvector_embedding_payload():
    chunks = [
        {
            "chunk_id": "ship_001",
            "text": "LNG 운반선 수요와 조선산업 수출 전망",
            "metadata": {
                "source_type": "industry_report",
                "source_name": "sample.pdf",
                "industry": "조선",
                "page": 30,
                "heading": "세계 발주",
            },
        }
    ]

    embedding_model = HashingEmbeddingModel(dimensions=16)
    prepared = prepare_chunks(chunks, embedding_model.model_name)
    embedding_text = render_embedding_text(prepared[0]["text"], prepared[0]["metadata"])
    embedding = embedding_model.embed(embedding_text)

    assert prepared[0]["metadata"]["chunk_id"] == "ship_001"
    assert prepared[0]["metadata"]["embedding_model"] == "local-hashing"
    assert embedding_text.startswith("조선\n세계 발주\n")
    assert validate_embeddings([embedding]) == 16
    assert to_pgvector_literal(embedding).startswith("[")
    assert build_search_params(embedding, limit=3, industry="조선")[-2:] == ["조선", 3]


def test_industry_rag_evidence_keeps_single_context_field(monkeypatch):
    monkeypatch.setattr(industry_rag_service, "IndustryVectorStore", _Store)

    items = industry_rag_service.search_industry_evidence("시장 전망", database_url="unused")

    assert items[0]["context"] == "산업 보고서 원문 청크"
    assert "summary" not in items[0]
    assert "raw_text" not in items[0]
    assert "content" not in items[0]
