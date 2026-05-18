from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from rag.industry_vector_store import (
    DEFAULT_CHUNKS_PATH,
    DEFAULT_DB_PATH,
    EmbeddingModel,
    IndustryVectorStore,
    build_industry_vector_db,
)
from services.evidence.store_service import now_iso, safe_filename


DEFAULT_INDUSTRY_RAG_OUTPUT_DIR = settings.output_dir / "industry_rag"


def index_industry_evidence(
    *,
    chunks_path: Path | str = DEFAULT_CHUNKS_PATH,
    database_url: str | None = DEFAULT_DB_PATH,
    reset: bool = True,
    embedding_model: EmbeddingModel | None = None,
) -> int:
    return build_industry_vector_db(
        chunks_path=chunks_path,
        database_url=database_url,
        reset=reset,
        embedding_model=embedding_model,
    )


def search_industry_evidence(
    query: str,
    *,
    industry: str | None = None,
    top_k: int = 5,
    database_url: str | None = DEFAULT_DB_PATH,
    embedding_model: EmbeddingModel | None = None,
) -> list[dict[str, Any]]:
    store = IndustryVectorStore(database_url, embedding_model=embedding_model)
    collected_at = now_iso()
    return [
        {
            "evidence_id": result.chunk_id,
            "source": result.metadata.get("source_name"),
            "source_type": result.metadata.get("source_type", "industry_report"),
            "title": result.metadata.get("heading"),
            "url": None,
            "published_at": str(result.metadata.get("published_year")) if result.metadata.get("published_year") else None,
            "published_at_precision": "year" if result.metadata.get("published_year") else None,
            "collected_at": collected_at,
            "industry": result.metadata.get("industry"),
            "page": result.metadata.get("page"),
            "heading": result.metadata.get("heading"),
            "context": result.text,
            "related_axis": ["market"],
            "confidence": None,
            "score": result.score,
            "metadata": result.metadata,
        }
        for result in store.search(query, top_k=top_k, industry=industry)
    ]


def build_patent_industry_query(preprocessed_patent: dict[str, Any]) -> str:
    metadata = preprocessed_patent.get("metadata") or {}
    sections = preprocessed_patent.get("sections") or {}
    title = str(metadata.get("title") or "").strip()
    abstract = str(sections.get("abstract") or "").strip()
    return "\n\n".join(part for part in (title, abstract) if part)


def compact_rag_queries(queries: list[str]) -> list[str]:
    compacted: list[str] = []
    seen: set[str] = set()
    for query in queries:
        value = " ".join(str(query or "").split())
        if not value or value in seen:
            continue
        seen.add(value)
        compacted.append(value)
    return compacted


def dedupe_industry_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id and evidence_id in seen:
            continue
        if evidence_id:
            seen.add(evidence_id)
        deduped.append(item)
    return deduped


def search_and_save_patent_industry_evidence(
    *,
    preprocessed_patent: dict[str, Any],
    patent_id: str | int | None = None,
    rag_queries: list[str] | None = None,
    top_k: int = 3,
    industry: str | None = None,
    database_url: str | None = DEFAULT_DB_PATH,
    embedding_model: EmbeddingModel | None = None,
    output_dir: Path | str = DEFAULT_INDUSTRY_RAG_OUTPUT_DIR,
    save: bool = True,
) -> dict[str, Any]:
    queries = compact_rag_queries(rag_queries or [])
    if not queries:
        fallback_query = build_patent_industry_query(preprocessed_patent)
        if fallback_query:
            queries = [fallback_query]
    if not queries:
        return {
            "query": "",
            "queries": [],
            "items": [],
            "output_path": None,
            "warning": "title and abstract are both empty",
        }

    collected_items: list[dict[str, Any]] = []
    for query in queries:
        query_items = search_industry_evidence(
            query,
            industry=industry,
            top_k=top_k,
            database_url=database_url,
            embedding_model=embedding_model,
        )
        collected_items.extend({**item, "rag_query": query} for item in query_items)
    items = dedupe_industry_items(collected_items)
    query = queries[0] if len(queries) == 1 else "\n".join(queries)
    output_path = None
    if save:
        output_path = save_industry_rag_result(
            patent_id=patent_id,
            query=query,
            queries=queries,
            items=items,
            output_dir=output_dir,
        )
    return {
        "query": query,
        "queries": queries,
        "items": items,
        "output_path": str(output_path) if output_path else None,
        "warning": None,
    }


def save_industry_rag_result(
    *,
    patent_id: str | int | None,
    query: str,
    queries: list[str] | None = None,
    items: list[dict[str, Any]],
    output_dir: Path | str = DEFAULT_INDUSTRY_RAG_OUTPUT_DIR,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(str(patent_id) if patent_id is not None else 'unknown')}_industry_rag_top3.json"
    path = directory / filename
    payload = {
        "source_type": "industry_report",
        "source": "pgvector",
        "query": query,
        "queries": queries or ([query] if query else []),
        "patent_id": str(patent_id) if patent_id is not None else None,
        "top_k": len(items),
        "collected_at": now_iso(),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
