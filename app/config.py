from pathlib import Path
from os import getenv
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseModel):
    project_root: Path = Path(__file__).resolve().parents[1]
    data_dir: Path = project_root / "data"
    artifacts_dir: Path = project_root / "artifacts"
    run_outputs_dir: Path = artifacts_dir / "runs"
    
    # 기존 SQLite 경로는 로컬 단일 테스트용으로 유지하되, 실제 운영/도커 환경에서는 pgvector를 메인으로 사용합니다.
    patent_db_path: Path = data_dir / "patents.sqlite3"
    
    patent_pdf_dir: Path = data_dir / "patent_pdf"
    output_dir: Path = run_outputs_dir / "manual"
    patent_markdown_dir: Path = output_dir / "patent_markdown"
    preprocessed_output_dir: Path = output_dir / "preprocessed_patents"
    
    # LangSmith observability 설정
    langsmith_tracing: bool = getenv("LANGSMITH_TRACING", "false").lower() == "true"
    langsmith_api_key: str | None = getenv("LANGSMITH_API_KEY")
    langsmith_project: str = getenv("LANGSMITH_PROJECT", "patent-agent-valuation")
    
    # OpenAI 모델 및 인증 설정
    openai_api_key: str | None = getenv("OPENAI_API_KEY")
    openai_chat_model: str = getenv("OPENAI_CHAT_MODEL") or getenv("OPENAI_MODEL", "gpt-5-mini")
    openai_supervisor_model: str | None = getenv("OPENAI_SUPERVISOR_MODEL", "gpt-5-nano")
    # 채점(가치평가 축) 전용 모델. 미설정 시 VALUATION_MODEL → openai_chat_model 순으로 폴백.
    # 특허 가치평가 품질을 우선하면 gpt-5 계열을 지정한다.
    openai_valuation_model: str | None = getenv("OPENAI_VALUATION_MODEL")
    # 특허 구조화 전용 모델. 구조화는 판단이 아니라 추출·청구항 분해라 가치평가 축보다
    # 가벼운 모델이 적합하다. 미설정 시 openai_chat_model(gpt-5-mini)로 폴백한다.
    openai_structuring_model: str | None = getenv("OPENAI_STRUCTURING_MODEL")
    # 최종 보고서·요약 작성 전용 모델. 미설정 시 openai_chat_model로 폴백.
    # 서술 품질을 위해 gpt-5 같은 상위 모델을 쓰고 싶을 때 지정한다(작성은 KIPRIS
    # 호출이 없어 느려도 워크플로우를 막지 않는다).
    openai_writing_model: str | None = getenv("OPENAI_WRITING_MODEL")
    # GPT-5 추론량(reasoning effort)·출력 상세도(verbosity). gpt-5 계열에만 적용되며
    # 미설정 시 OpenAI 기본값을 따른다. 작업별 값이 있으면 전역값보다 우선한다.
    # effort: none|minimal|low|medium|high|xhigh, verbosity: low|medium|high
    openai_reasoning_effort: str | None = getenv("OPENAI_REASONING_EFFORT")
    openai_verbosity: str | None = getenv("OPENAI_VERBOSITY")
    openai_valuation_reasoning_effort: str | None = getenv("OPENAI_VALUATION_REASONING_EFFORT")
    # 구조화는 추출 작업이라 깊은 추론이 불필요하므로 기본 low로 비용을 낮춘다.
    openai_structuring_reasoning_effort: str | None = getenv("OPENAI_STRUCTURING_REASONING_EFFORT", "low")
    openai_writing_reasoning_effort: str | None = getenv("OPENAI_WRITING_REASONING_EFFORT")
    openai_writing_verbosity: str | None = getenv("OPENAI_WRITING_VERBOSITY")
    openai_supervisor_reasoning_effort: str | None = getenv("OPENAI_SUPERVISOR_REASONING_EFFORT")
    openai_request_timeout_seconds: float = float(getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "90"))
    openai_valuation_timeout_seconds: float = float(
        getenv("OPENAI_VALUATION_TIMEOUT_SECONDS", "180")
    )
    # 보고서/요약 작성(writing)은 출력이 길어 일반 호출보다 오래 걸리므로 별도 timeout.
    openai_writing_timeout_seconds: float = float(getenv("OPENAI_WRITING_TIMEOUT_SECONDS", "240"))
    # 가치평가 축·특허 구조화는 특허 전문을 통째로 읽고 reasoning을 돌려 일반 호출보다
    # 오래 걸리므로 별도 timeout.
    openai_valuation_timeout_seconds: float = float(getenv("OPENAI_VALUATION_TIMEOUT_SECONDS", "300"))
    # 수집 후 뉴스 recency 필터의 최대 기간(일). naver·글로벌 뉴스 공통. 기본 5년.
    news_max_age_days: int = int(getenv("NEWS_MAX_AGE_DAYS", str(365 * 5)))
    openai_embedding_model: str = getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    # EVID-08: 임베딩 호출 타임아웃·재시도·쿼리 캐시. 일시 장애로 인덱싱/검색이 통째로 중단되지 않게 한다.
    openai_embedding_timeout: float = float(getenv("OPENAI_EMBEDDING_TIMEOUT", "30"))
    openai_embedding_max_retries: int = int(getenv("OPENAI_EMBEDDING_MAX_RETRIES", "3"))
    embedding_query_cache_size: int = int(getenv("EMBEDDING_QUERY_CACHE_SIZE", "256"))

    # Spring Boot(BE) 통신용 기본 주소.
    # 로컬 직접 실행은 localhost를 기본으로 두고, Docker Compose에서는 UNIFIED_API_BASE_URL로 서비스명을 주입합니다.
    unified_api_base_url: str = getenv("UNIFIED_API_BASE_URL", "http://localhost:8080")
    
    search_query_count: int = int(getenv("SEARCH_QUERY_COUNT", "3"))
    news_results_per_query: int = int(getenv("NEWS_RESULTS_PER_QUERY", "3"))
    # EVID-04: 근거 수집(소스 호출·기사 본문 fetch) 병렬 동시성 상한.
    evidence_fetch_concurrency: int = int(getenv("EVIDENCE_FETCH_CONCURRENCY", "6"))
    industry_rag_query_count: int = int(getenv("INDUSTRY_RAG_QUERY_COUNT", "1"))
    industry_rag_top_k: int = int(getenv("INDUSTRY_RAG_TOP_K", "3"))
    # 근거 압축(LLM) 동시 처리 워커 수. 근거가 많을수록 높이면 빨라진다.
    compression_workers: int = int(getenv("COMPRESSION_WORKERS", "8"))
    # GNews 대체: 글로벌 뉴스를 Tavily(topic=news)로 수집할 때 최근 N일 범위.
    # 뉴스 필터(5년)와 정렬해 기본 5년.
    tavily_news_max_age_days: int = int(getenv("TAVILY_NEWS_MAX_AGE_DAYS", str(365 * 5)))
    # 산업 리포트 RAG 근거 채택 최소 유사도. 이 값 미만이면 압축 후보에서 제외한다.
    rag_score_threshold: float = float(getenv("RAG_SCORE_THRESHOLD", "0.5"))
    # SK AX(Tavily/Google) 사이트 검색 HTTP 타임아웃(초). raw_content 수집 때문에
    # 5초로는 자주 ReadTimeout이 나므로 기본을 넉넉히 둔다.
    skax_search_timeout_seconds: int = int(getenv("SKAX_SEARCH_TIMEOUT_SECONDS", "20"))
    max_evidence_search_rounds: int = int(getenv("MAX_EVIDENCE_SEARCH_ROUNDS", "3"))
    workflow_recursion_limit: int = int(getenv("WORKFLOW_RECURSION_LIMIT", "80"))
    evaluate_timeout_seconds: int = int(getenv("EVALUATE_TIMEOUT_SECONDS", "180"))
    evaluate_max_concurrency: int = int(getenv("EVALUATE_MAX_CONCURRENCY", "2"))
    valuation_schema_strict: bool = getenv("VALUATION_SCHEMA_STRICT", "true").lower() == "true"
    valuation_ensemble_runs: int = int(getenv("VALUATION_ENSEMBLE_RUNS", "1"))
    # 점수 재현성(VAL-01): seed는 Chat Completions 전용이며, gpt-5-mini는 seed를 받아도
    # system_fingerprint=None으로 결과를 재현하지 않음(실측). 기본 평가 모델은 gpt-5-mini이고,
    # 재현성이 필요하면 VALUATION_MODEL=gpt-4o(또는 seed를 존중하는 다른 모델) + VALUATION_SEED_SUPPORTED=true로 켠다.
    valuation_seed: int | None = int(getenv("VALUATION_SEED") or "20260624")
    valuation_seed_supported: bool = getenv("VALUATION_SEED_SUPPORTED", "false").lower() == "true"
    valuation_model: str = getenv("VALUATION_MODEL") or openai_chat_model
    fetch_news_full_text: bool = getenv("FETCH_NEWS_FULL_TEXT", "true").lower() == "true"
    enable_shared_db_fallback: bool = getenv("ENABLE_SHARED_DB_FALLBACK", "false").lower() == "true"
    
    # Vector DB (pgvector) 접속 정보.
    # 로컬 직접 실행은 localhost를 기본으로 두고, Docker Compose에서는 PGVECTOR_DATABASE_URL로 서비스명을 주입합니다.
    pgvector_database_url: str | None = getenv(
        "PGVECTOR_DATABASE_URL",
        getenv("DATABASE_URL", "postgresql://patentflow:patentflow@localhost:5432/patentflow"),
    )
    pgvector_table_name: str = getenv("PGVECTOR_TABLE_NAME", "industry_report_chunks")

settings = Settings()
