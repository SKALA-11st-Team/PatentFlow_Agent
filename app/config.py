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
    patent_db_path: Path = data_dir / "patents.sqlite3"
    patent_pdf_dir: Path = data_dir / "patent_pdf"
    output_dir: Path = run_outputs_dir / "manual"
    patent_markdown_dir: Path = output_dir / "patent_markdown"
    preprocessed_output_dir: Path = output_dir / "preprocessed_patents"
    langsmith_tracing: bool = getenv("LANGSMITH_TRACING", "false").lower() == "true"
    langsmith_api_key: str | None = getenv("LANGSMITH_API_KEY")
    langsmith_project: str = getenv("LANGSMITH_PROJECT", "patent-agent-valuation")
    openai_api_key: str | None = getenv("OPENAI_API_KEY")
    openai_chat_model: str = getenv("OPENAI_CHAT_MODEL") or getenv("OPENAI_MODEL", "gpt-5-mini")
    openai_embedding_model: str = getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    unified_api_base_url: str = getenv("UNIFIED_API_BASE_URL", "http://127.0.0.1:8000")
    search_query_count: int = int(getenv("SEARCH_QUERY_COUNT", "3"))
    fetch_news_full_text: bool = getenv("FETCH_NEWS_FULL_TEXT", "true").lower() == "true"
    pgvector_database_url: str | None = getenv("PGVECTOR_DATABASE_URL") or getenv("DATABASE_URL")
    pgvector_table_name: str = getenv("PGVECTOR_TABLE_NAME", "industry_report_chunks")


settings = Settings()
