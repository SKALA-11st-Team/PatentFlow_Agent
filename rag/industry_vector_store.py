from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
import argparse
import hashlib
import json
import math
import re

from app.config import settings


DEFAULT_HASH_DIMENSIONS = 512
DEFAULT_CHUNKS_PATH = settings.data_dir / "vector_db" / "industry_report_chunks.jsonl"
DEFAULT_TABLE_NAME = settings.pgvector_table_name

# Backward-compatible name used by services/industry_rag_service.py.
DEFAULT_DB_PATH = settings.pgvector_database_url


class EmbeddingModel(Protocol):
    model_name: str

    def embed(self, text: str) -> list[float]:
        ...

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass
class SearchResult:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    score: float


class HashingEmbeddingModel:
    """Deterministic local embedding model used only for tests/offline checks."""

    model_name = "local-hashing"

    def __init__(self, dimensions: int = DEFAULT_HASH_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize_for_embedding(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, byteorder="big", signed=False)
            index = value % self.dimensions
            sign = 1.0 if value & 1 else -1.0
            vector[index] += sign
        return normalize_vector(vector)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class OpenAIEmbeddingModel:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        batch_size: int = 100,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The openai package is required for OpenAI embeddings. "
                "Install dependencies with `pip install -r requirements.txt`."
            ) from exc

        self.model_name = model or settings.openai_embedding_model
        self.batch_size = batch_size
        self.client = OpenAI(api_key=api_key or settings.openai_api_key)

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self.client.embeddings.create(model=self.model_name, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            embeddings.extend(normalize_vector(item.embedding) for item in ordered)
        return embeddings


class IndustryVectorStore:
    def __init__(
        self,
        database_url: str | None = DEFAULT_DB_PATH,
        embedding_model: EmbeddingModel | None = None,
        *,
        table_name: str = DEFAULT_TABLE_NAME,
    ) -> None:
        self.database_url = database_url or settings.pgvector_database_url
        self.table_name = table_name
        self.embedding_model = embedding_model or OpenAIEmbeddingModel()

    def upsert_chunks(self, chunks: list[dict[str, Any]], *, reset: bool = False) -> int:
        prepared_chunks = prepare_chunks(chunks, self.embedding_model.model_name)
        if not prepared_chunks:
            return 0

        embedding_texts = [
            render_embedding_text(chunk["text"], chunk["metadata"])
            for chunk in prepared_chunks
        ]
        embeddings = self.embedding_model.embed_many(embedding_texts)
        dimension = validate_embeddings(embeddings)

        with connect_pgvector(self.database_url) as connection:
            ensure_pgvector_schema(
                connection,
                table_name=self.table_name,
                dimensions=dimension,
                reset=reset,
            )
            upsert_pgvector_rows(
                connection,
                table_name=self.table_name,
                chunks=prepared_chunks,
                embeddings=embeddings,
            )
        return len(prepared_chunks)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        industry: str | None = None,
        source_name: str | None = None,
    ) -> list[SearchResult]:
        query_embedding = self.embedding_model.embed(query)
        limit = max(1, int(top_k))

        with connect_pgvector(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    build_search_sql(self.table_name, industry=industry is not None, source_name=source_name is not None),
                    build_search_params(query_embedding, limit=limit, industry=industry, source_name=source_name),
                )
                rows = cursor.fetchall()

        results: list[SearchResult] = []
        for chunk_id, text, metadata, score in rows:
            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    text=text,
                    metadata=dict(metadata or {}),
                    score=float(score),
                )
            )
        return results

    def count(self) -> int:
        with connect_pgvector(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(count_sql(self.table_name))
                return int(cursor.fetchone()[0])


def load_chunks_jsonl(path: str | Any = DEFAULT_CHUNKS_PATH) -> list[dict[str, Any]]:
    chunks = []
    with open(path, encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                chunks.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
    return chunks


def build_industry_vector_db(
    *,
    chunks_path: str | Any = DEFAULT_CHUNKS_PATH,
    database_url: str | None = DEFAULT_DB_PATH,
    reset: bool = True,
    embedding_model: EmbeddingModel | None = None,
    table_name: str = DEFAULT_TABLE_NAME,
    db_path: str | None = None,
) -> int:
    chunks = load_chunks_jsonl(chunks_path)
    store = IndustryVectorStore(
        database_url=db_path or database_url,
        embedding_model=embedding_model,
        table_name=table_name,
    )
    return store.upsert_chunks(chunks, reset=reset)


def prepare_chunks(chunks: list[dict[str, Any]], embedding_model_name: str) -> list[dict[str, Any]]:
    prepared = []
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        metadata = dict(chunk.get("metadata") or {})
        metadata["chunk_id"] = chunk_id
        metadata["embedding_model"] = embedding_model_name
        prepared.append(
            {
                "chunk_id": chunk_id,
                "text": chunk["text"],
                "metadata": metadata,
            }
        )
    return prepared


def render_embedding_text(text: str, metadata: dict[str, Any]) -> str:
    parts = [
        str(metadata.get("industry") or ""),
        str(metadata.get("heading") or ""),
        text,
    ]
    return "\n".join(part for part in parts if part)


def validate_embeddings(embeddings: list[list[float]]) -> int:
    if not embeddings:
        raise ValueError("No embeddings to index.")
    dimensions = len(embeddings[0])
    if dimensions == 0:
        raise ValueError("Embedding vector cannot be empty.")
    for embedding in embeddings:
        if len(embedding) != dimensions:
            raise ValueError("All embeddings must have the same dimension.")
    return dimensions


def build_search_params(
    embedding: list[float],
    *,
    limit: int,
    industry: str | None = None,
    source_name: str | None = None,
) -> list[Any]:
    params: list[Any] = [to_pgvector_literal(embedding)]
    if industry is not None:
        params.append(industry)
    if source_name is not None:
        params.append(source_name)
    params.append(limit)
    return params


def to_pgvector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in normalize_vector(vector)) + "]"


def build_search_sql(table_name: str, *, industry: bool = False, source_name: bool = False) -> Any:
    sql, _ = import_psycopg()
    filters = []
    if industry:
        filters.append(sql.SQL("chunks.metadata->>'industry' = %s"))
    if source_name:
        filters.append(sql.SQL("chunks.metadata->>'source_name' = %s"))

    where_clause = sql.SQL("")
    if filters:
        where_clause = sql.SQL("WHERE ") + sql.SQL(" AND ").join(filters)

    return sql.SQL(
        """
        WITH query_embedding AS (
            SELECT %s::vector AS embedding
        )
        SELECT
            chunks.chunk_id,
            chunks.text,
            chunks.metadata,
            1 - (chunks.embedding <=> query_embedding.embedding) AS score
        FROM {table} AS chunks, query_embedding
        {where_clause}
        ORDER BY chunks.embedding <=> query_embedding.embedding
        LIMIT %s
        """
    ).format(table=sql.Identifier(table_name), where_clause=where_clause)


def connect_pgvector(database_url: str | None) -> Any:
    if not database_url:
        raise RuntimeError(
            "PGVECTOR_DATABASE_URL is required for the industry pgvector store. "
            "Example: postgresql://user:password@localhost:5432/patent_rag"
        )
    _, psycopg = import_psycopg()
    return psycopg.connect(database_url)


def ensure_pgvector_schema(
    connection: Any,
    *,
    table_name: str,
    dimensions: int,
    reset: bool = False,
) -> None:
    sql, _ = import_psycopg()
    with connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        if reset:
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {table}").format(table=sql.Identifier(table_name)))
        cursor.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {table} (
                    chunk_id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding_model TEXT NOT NULL,
                    embedding vector({dimensions}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            ).format(table=sql.Identifier(table_name), dimensions=sql.Literal(dimensions))
        )
        cursor.execute(
            sql.SQL(
                """
                CREATE INDEX IF NOT EXISTS {index}
                ON {table} USING hnsw (embedding vector_cosine_ops)
                """
            ).format(
                index=sql.Identifier(f"{table_name}_embedding_hnsw_idx"),
                table=sql.Identifier(table_name),
            )
        )
        cursor.execute(
            sql.SQL(
                """
                CREATE INDEX IF NOT EXISTS {index}
                ON {table} ((metadata->>'industry'))
                """
            ).format(
                index=sql.Identifier(f"{table_name}_industry_idx"),
                table=sql.Identifier(table_name),
            )
        )
    connection.commit()


def upsert_pgvector_rows(
    connection: Any,
    *,
    table_name: str,
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
) -> None:
    sql, _ = import_psycopg()
    Jsonb = import_jsonb()
    rows = [
        (
            chunk["chunk_id"],
            chunk["text"],
            Jsonb(chunk["metadata"]),
            chunk["metadata"].get("embedding_model") or "",
            to_pgvector_literal(embedding),
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            sql.SQL(
                """
                INSERT INTO {table} (chunk_id, text, metadata, embedding_model, embedding)
                VALUES (%s, %s, %s, %s, %s::vector)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    text = EXCLUDED.text,
                    metadata = EXCLUDED.metadata,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding = EXCLUDED.embedding,
                    updated_at = now()
                """
            ).format(table=sql.Identifier(table_name)),
            rows,
        )
    connection.commit()


def count_sql(table_name: str) -> Any:
    sql, _ = import_psycopg()
    return sql.SQL("SELECT count(*) FROM {table}").format(table=sql.Identifier(table_name))


def import_psycopg() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for the pgvector store. "
            "Install dependencies with `pip install -r requirements.txt`."
        ) from exc
    return sql, psycopg


def import_jsonb() -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for JSONB adaptation. "
            "Install dependencies with `pip install -r requirements.txt`."
        ) from exc
    return Jsonb


def tokenize_for_embedding(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    word_tokens = re.findall(r"[0-9a-zA-Z가-힣]{2,}", normalized)
    compact_korean = "".join(re.findall(r"[가-힣]", normalized))
    char_ngrams = [
        compact_korean[index : index + 3]
        for index in range(max(0, len(compact_korean) - 2))
    ]
    return word_tokens + char_ngrams


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/search the pgvector industry report vector DB.")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    parser.add_argument("--database-url", default=DEFAULT_DB_PATH)
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME)
    parser.add_argument("--no-reset", action="store_true", help="Upsert chunks without dropping the table first.")
    parser.add_argument("--query", help="Optional query to test search after indexing.")
    parser.add_argument("--industry", help="Optional industry filter for query.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--embedding-model", default=settings.openai_embedding_model)
    args = parser.parse_args()

    embedding_model = OpenAIEmbeddingModel(model=args.embedding_model)
    inserted = build_industry_vector_db(
        chunks_path=args.chunks,
        database_url=args.database_url,
        reset=not args.no_reset,
        embedding_model=embedding_model,
        table_name=args.table_name,
    )
    print(f"Indexed chunks: {inserted}")
    print(f"Embedding model: {embedding_model.model_name}")
    print(f"Postgres table: {args.table_name}")

    if args.query:
        store = IndustryVectorStore(
            args.database_url,
            embedding_model=embedding_model,
            table_name=args.table_name,
        )
        for result in store.search(args.query, top_k=args.top_k, industry=args.industry):
            industry = result.metadata.get("industry")
            page = result.metadata.get("page")
            heading = result.metadata.get("heading")
            print(f"{result.score:.4f} | {industry} | p{page} | {heading} | {result.chunk_id}")
            print(result.text[:240].replace("\n", " "))


if __name__ == "__main__":
    main()
