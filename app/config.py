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
    openai_embedding_model: str = getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    
    # Spring Boot(BE) 통신용 기본 주소.
    # 로컬 직접 실행은 localhost를 기본으로 두고, Docker Compose에서는 UNIFIED_API_BASE_URL로 서비스명을 주입합니다.
    unified_api_base_url: str = getenv("UNIFIED_API_BASE_URL", "http://localhost:8080")
    
    search_query_count: int = int(getenv("SEARCH_QUERY_COUNT", "3"))
    news_results_per_query: int = int(getenv("NEWS_RESULTS_PER_QUERY", "3"))
    industry_rag_query_count: int = int(getenv("INDUSTRY_RAG_QUERY_COUNT", "1"))
    industry_rag_top_k: int = int(getenv("INDUSTRY_RAG_TOP_K", "3"))
    max_evidence_search_rounds: int = int(getenv("MAX_EVIDENCE_SEARCH_ROUNDS", "4"))
    workflow_recursion_limit: int = int(getenv("WORKFLOW_RECURSION_LIMIT", "80"))
    evaluate_timeout_seconds: int = int(getenv("EVALUATE_TIMEOUT_SECONDS", "180"))
    evaluate_max_concurrency: int = int(getenv("EVALUATE_MAX_CONCURRENCY", "2"))
    valuation_schema_strict: bool = getenv("VALUATION_SCHEMA_STRICT", "true").lower() == "true"
    valuation_ensemble_runs: int = int(getenv("VALUATION_ENSEMBLE_RUNS", "1"))
    # 점수 재현성(VAL-01): 평가 축 LLM 호출에 고정 seed를 전달해 동일 입력→동일 점수를 보장한다.
    # 기본 모델은 메인 챗 모델(gpt-5-mini)을 그대로 사용하고, 필요 시 VALUATION_MODEL로 재정의한다.
    # (사용 모델/엔드포인트가 seed를 수용하지 않으면 VALUATION_SEED_SUPPORTED=false로 끈다.)
    valuation_model: str = getenv("VALUATION_MODEL") or openai_chat_model
    valuation_seed: int | None = int(getenv("VALUATION_SEED", "20260608"))
    valuation_seed_supported: bool = getenv("VALUATION_SEED_SUPPORTED", "true").lower() == "true"
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
