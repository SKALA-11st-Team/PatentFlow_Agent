import pytest

from rag.industry_vector_store import (
    HashingEmbeddingModel,
    build_search_params,
    prepare_chunks,
    render_embedding_text,
    to_pgvector_literal,
    validate_embeddings,
)
from services.rag.industry_rag_service import (
    build_patent_industry_query,
    match_industry,
    resolve_patent_industries,
)
from rag.chunkers.ai_index_chunker import is_noise_section, strip_embedded_chart_ocr_tail
from rag.industry_report_chunker import Section, infer_published_year
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

    def search(self, query, top_k=5, industry=None, industries=None):
        return [_SearchResult()]


class _QueryCaptureStore:
    queries = []

    def __init__(self, database_url, embedding_model=None):
        self.database_url = database_url
        self.embedding_model = embedding_model

    def search(self, query, top_k=5, industry=None, industries=None):
        self.__class__.queries.append(query)
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


def test_build_patent_industry_query_uses_foreign_sections_and_claim():
    query = build_patent_industry_query(
        {
            "metadata": {"title": "测量控制方法和系统"},
            "sections": {
                "abstract": "提供了一种测量控制方法。",
                "technical_field": "涉及半导体测量技术。",
                "problem": "固定周期测量效率低。",
                "solution": "根据设备可靠性指数动态决定测量。",
            },
            "claims": [
                {
                    "text": "一种基于风险分数的测量控制方法。",
                    "is_independent": True,
                }
            ],
        }
    )

    assert "测量控制方法和系统" in query
    assert "半导体测量技术" in query
    assert "设备可靠性指数" in query
    assert "基于风险分数" in query


def test_industry_rag_evidence_keeps_single_context_field(monkeypatch):
    monkeypatch.setattr(industry_rag_service, "IndustryVectorStore", _Store)

    items = industry_rag_service.search_industry_evidence("시장 전망", database_url="unused")

    assert items[0]["context"] == "산업 보고서 원문 청크"
    assert "summary" not in items[0]
    assert "raw_text" not in items[0]
    assert "content" not in items[0]


def test_patent_industry_rag_uses_rewritten_query(monkeypatch, tmp_path):
    _QueryCaptureStore.queries = []
    monkeypatch.setattr(industry_rag_service, "IndustryVectorStore", _QueryCaptureStore)

    result = industry_rag_service.search_and_save_patent_industry_evidence(
        preprocessed_patent={
            "metadata": {"title": "상품 트렌드 예측을 반영한 자산배분 시스템"},
            "sections": {"abstract": "강화학습 기반 투자 포트폴리오 기술"},
        },
        patent_id="KR10-TEST",
        rag_queries=["웰스테크 AI 에이전트 디지털 자문"],
        database_url="unused",
        output_dir=tmp_path,
    )

    assert _QueryCaptureStore.queries == ["웰스테크 AI 에이전트 디지털 자문"]
    assert result["queries"] == ["웰스테크 AI 에이전트 디지털 자문"]
    assert result["items"][0]["rag_query"] == "웰스테크 AI 에이전트 디지털 자문"
    assert result["output_path"] is not None


def test_machine_whitepaper_year_is_inferred_as_2025():
    source_name = "기계산업_디지털전환_기술백서_한국기계연구원.pdf"

    assert infer_published_year(source_name) == 2025


def test_ai_index_filters_ocr_figure_axis_noise():
    section = Section(
        heading="T u F u a",
        industry="AI",
        page=106,
        text=(
            "P p 4 p Cl s G O k O u p e o e O d Gr d u u e a a d Cl Cl u a Cl "
            "Model Figure 2.5.827 27 Data source: https://www.vals.ai/benchmarks/tax_eval_v2. 106"
        ),
    )

    assert is_noise_section(section) is True


def test_ai_index_filters_short_data_source_footnote():
    section = Section(
        heading="Page 114",
        industry="AI",
        page=114,
        text=(
            "35 Data source: https://docs.google.com/spreadsheets/d/example/edit. "
            "36 Data source: https://github.com/openai/mle-bench. 114"
        ),
    )

    assert is_noise_section(section) is True


def test_ai_index_filters_short_axis_label_noise_without_source_marker():
    section = Section(
        heading="3.9 SECURIT Y AND SAFET Y",
        industry="AI",
        page=166,
        text=(
            "2 2 0 K 0 0 T M B u e str ( T 1. 2 ( ( Gr m e nit Pr 2 ( ( 2 2 2 "
            "D str v ct P m u ni ( ct ( o1 et S D S M- I 3- et u ( 2- 5.1 et "
            "( mi ( ( 5 L o k ct ni o ct E) str 4.1 0 ni 3"
        ),
    )

    assert is_noise_section(section) is True


def test_ai_index_strips_embedded_chart_ocr_tail_from_meaningful_section():
    text = (
        "The 2025 results show continued improvement but also increasing compression "
        "at the top (Figure 3.9.2). HELM Safety: mean score Source: HELM, 2026 | "
        "Chart: 2026 AI Index report 1 0.8 e or c 0.6 s n a e 0.4 M 0.2 0 "
        "3) B) ct B) 4 B) B) d s 7) B) 01) 8) 2 2 0 K 0 0 T M B"
    )

    cleaned = strip_embedded_chart_ocr_tail(text)

    assert "The 2025 results show continued improvement" in cleaned
    assert "2 2 0 K 0 0 T M B" not in cleaned


def test_ai_index_filters_short_report_heading_only_section():
    section = Section(
        heading="6.2 CLINICAL APPLICATIONS",
        industry="AI",
        page=274,
        text=(
            "6.2 CLINICAL APPLICATIONS | MEDICINE | AI INDEX REPORT 2026 "
            "FDA 510(k)-cleared AI/ML-enabled imaging-related medical devices, 2011-25"
        ),
    )

    assert is_noise_section(section) is True


@pytest.mark.parametrize(
    "text, expected",
    [
        ("인공지능 기반 자연어 처리 시스템", "AI"),
        ("리튬 이차전지 양극재 조성물", "이차전지"),
        ("반도체 웨이퍼 식각 공정 장치", "반도체"),
        ("LNG 운반선 조선 의장 자동화", "조선"),
        ("자산배분 포트폴리오 로보어드바이저 핀테크", "핀테크"),
        ("전기차 자율주행 ADAS 제어", "자동차"),
        ("OLED 디스플레이 패널 화소 구동", "디스플레이"),
        # 매칭 없음 → None(필터 미적용 폴백).
        ("그냥 일반적인 장치와 방법", None),
        # 'retail' 안의 'ai' 부분문자열을 AI로 오인하지 않는다(단어 경계).
        ("retail point of sale terminal", None),
        ("", None),
    ],
)
def test_match_industry_maps_technology_area_to_corpus_label(text, expected):
    assert match_industry(text) == expected


def test_resolve_patent_industries_includes_common_label():
    industries = resolve_patent_industries(
        {"technology_area": "인공지능", "business_area": "AI 플랫폼"},
        {"metadata": {"title": "자연어 처리 장치"}, "sections": {}},
    )
    assert industries == ["AI", "공통"]


def test_resolve_patent_industries_reads_preprocessed_sections():
    industries = resolve_patent_industries(
        {},
        {"metadata": {"title": "측정 장치"}, "sections": {"technical_field": "반도체 웨이퍼 검사"}},
    )
    assert industries == ["반도체", "공통"]


def test_resolve_patent_industries_returns_none_when_unmatched():
    assert resolve_patent_industries({"technology_area": "일반 장치"}, {}) is None
    assert resolve_patent_industries(None, None) is None


def test_build_search_params_multi_industry_passes_list_then_limit():
    embedding = [0.1, 0.2, 0.3]
    params = build_search_params(embedding, limit=3, industries=["AI", "공통"])
    assert params[-2:] == [["AI", "공통"], 3]


def test_build_search_params_prefers_industries_over_single_industry():
    embedding = [0.1, 0.2, 0.3]
    params = build_search_params(embedding, limit=5, industry="조선", industries=["AI", "공통"])
    # 다중 산업 필터가 단일 industry보다 우선한다.
    assert params[-2:] == [["AI", "공통"], 5]


def test_build_search_sql_uses_any_for_multi_industry():
    pytest.importorskip("psycopg")
    from rag.industry_vector_store import build_search_sql

    multi = str(build_search_sql("patent_chunks", industries=True))
    single = str(build_search_sql("patent_chunks", industry=True))
    assert "ANY(%s)" in multi
    assert "= %s" in single and "ANY(%s)" not in single


from rag.industry_vector_store import OpenAIEmbeddingModel


class _FakeEmbItem:
    def __init__(self, index, embedding):
        self.index = index
        self.embedding = embedding


class _FakeEmbResponse:
    def __init__(self, data):
        self.data = data


class _CountingEmbeddingClient:
    """embeddings.create 호출 횟수를 세는 가짜 OpenAI 클라이언트(EVID-08)."""

    def __init__(self):
        self.calls = 0
        self.embeddings = self

    def create(self, model, input):
        self.calls += 1
        return _FakeEmbResponse([_FakeEmbItem(i, [float(len(t) + 1), 1.0, 0.0]) for i, t in enumerate(input)])


def test_openai_embedding_query_cache_reuses_embeddings():
    client = _CountingEmbeddingClient()
    model = OpenAIEmbeddingModel(client=client, query_cache_size=8)

    first = model.embed("동일 검색 쿼리")
    second = model.embed("동일 검색 쿼리")

    assert first == second
    assert client.calls == 1  # 두 번째 호출은 캐시에서 재사용

    model.embed("다른 검색 쿼리")
    assert client.calls == 2


def test_openai_embedding_query_cache_evicts_least_recently_used():
    client = _CountingEmbeddingClient()
    model = OpenAIEmbeddingModel(client=client, query_cache_size=1)

    model.embed("a")
    model.embed("b")  # 'a'를 밀어냄
    assert client.calls == 2

    model.embed("a")  # 재계산 필요
    assert client.calls == 3


def test_openai_embedding_cache_disabled_recomputes():
    client = _CountingEmbeddingClient()
    model = OpenAIEmbeddingModel(client=client, query_cache_size=0)

    model.embed("x")
    model.embed("x")

    assert client.calls == 2


def test_openai_embedding_uses_timeout_and_retry_settings():
    from app.config import settings

    model = OpenAIEmbeddingModel(client=_CountingEmbeddingClient())

    assert model.timeout == settings.openai_embedding_timeout
    assert model.max_retries == settings.openai_embedding_max_retries
