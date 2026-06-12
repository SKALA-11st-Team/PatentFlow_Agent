from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re

from app.config import settings
from schemas.evidence import Evidence


DEFAULT_API_EVIDENCE_DIR = settings.output_dir / "api_evidence"
DEFAULT_FILTERED_EVIDENCE_DIR = settings.output_dir / "filtered_evidence"
DEFAULT_SKAX_SEARCH_DIR = settings.output_dir / "skax_site_search"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_evidence_ids(
    items: Iterable[dict[str, Any] | Evidence],
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    normalized = [to_evidence_dict(item) for item in items]
    for index, item in enumerate(normalized, start=1):
        if item.get("evidence_id"):
            continue
        item["evidence_id"] = build_evidence_id(prefix, item, index)
    return normalized


def merge_evidence_sources(
    sources: Iterable[Iterable[dict[str, Any] | Evidence]],
    *,
    prefix: str = "ev",
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source in sources:
        for item in source:
            evidence = to_evidence_dict(item)
            key = dedupe_key(evidence)
            if key in seen:
                continue
            seen.add(key)
            merged.append(evidence)

    return ensure_evidence_ids(merged, prefix=prefix)


def save_evidence_collection(
    *,
    source_type: str,
    source: str,
    items: Iterable[dict[str, Any] | Evidence],
    query: str | None = None,
    patent_id: str | int | None = None,
    output_dir: Path | str = DEFAULT_API_EVIDENCE_DIR,
    collected_at: str | None = None,
) -> Path:
    collected_at = collected_at or now_iso()
    evidence_items = ensure_evidence_ids(items, prefix=source_type)
    payload = {
        "source_type": source_type,
        "source": source,
        "query": query,
        "patent_id": str(patent_id) if patent_id is not None else None,
        "collected_at": collected_at,
        "items": evidence_items,
    }

    directory = Path(output_dir) / safe_filename(source_type)
    directory.mkdir(parents=True, exist_ok=True)
    filename_parts = [str(patent_id) if patent_id is not None else None, source, query]
    filename_stem = safe_filename("_".join(part for part in filename_parts if part) or source_type)
    path = directory / f"{filename_stem}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_filtered_evidence_bundle(
    *,
    patent_id: str | int | None,
    news_items: Iterable[dict[str, Any] | Evidence],
    industry_items: Iterable[dict[str, Any] | Evidence],
    other_items: Iterable[dict[str, Any] | Evidence] = (),
    output_dir: Path | str = DEFAULT_FILTERED_EVIDENCE_DIR,
) -> Path:
    news = [to_evidence_dict(item) for item in news_items]
    industry = [to_evidence_dict(item) for item in industry_items]
    other = [to_evidence_dict(item) for item in other_items]
    items = [*other, *news, *industry]
    payload = {
        "source_type": "filtered_evidence",
        "source": "workflow",
        "patent_id": str(patent_id) if patent_id is not None else None,
        "collected_at": now_iso(),
        "stats": {
            "total_count": len(items),
            "news_count": len(news),
            "industry_report_count": len(industry),
            "other_count": len(other),
        },
        "news": news,
        "industry_report": industry,
        "other": other,
        "items": items,
    }
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(str(patent_id) if patent_id is not None else 'unknown')}_filtered_evidence.json"
    path = directory / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_skax_site_search_result(
    *,
    patent_id: str | int | None,
    skax_result: dict[str, Any],
    output_dir: Path | str = DEFAULT_SKAX_SEARCH_DIR,
) -> Path:
    """SK AX 공식 사이트 검색의 raw 결과·진단을 통째로 artifact로 남긴다.

    뉴스는 쿼리별 raw 파일을 저장하지만 SK AX 검색은 최종 채택 근거만 filtered_evidence에
    실렸을 뿐, "어떤 쿼리가 무엇을 가져왔는지"(실제 전송 쿼리·후보 URL·필터 통계)는
    어디에도 저장되지 않아 디버깅 시 LangSmith trace에 의존해야 했다. 이를 JSON으로 고정한다.
    """
    payload = {
        "source_type": "skax_site_search",
        "source": "skax_co_kr",
        "patent_id": str(patent_id) if patent_id is not None else None,
        "collected_at": now_iso(),
        "queries": skax_result.get("queries", []),
        "stats": skax_result.get("stats", {}),
        "query_generation_diagnostics": skax_result.get("query_generation_diagnostics", {}),
        "search_diagnostics": skax_result.get("search_diagnostics", []),
        "failed_urls": skax_result.get("failed_urls", []),
        "warning": skax_result.get("warning"),
        "items": [to_evidence_dict(item) for item in skax_result.get("items", [])],
    }
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(str(patent_id) if patent_id is not None else 'unknown')}_skax_site_search.json"
    path = directory / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def to_evidence_dict(item: dict[str, Any] | Evidence) -> dict[str, Any]:
    if isinstance(item, Evidence):
        evidence = item.model_dump()
    else:
        evidence = dict(item)
    if evidence.get("content") is None and evidence.get("summary") is not None:
        evidence["content"] = evidence["summary"]
    if evidence.get("content") is None and evidence.get("raw_text") is not None:
        evidence["content"] = evidence["raw_text"]
    for field in ("summary", "raw_text", "published_at_precision"):
        evidence.pop(field, None)
    return evidence


def build_evidence_id(prefix: str, item: dict[str, Any], index: int) -> str:
    seed = "|".join(
        str(item.get(key) or "")
        for key in ("source_type", "source", "url", "title", "published_at", "content")
    )
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=4).hexdigest()
    return f"{safe_filename(prefix)}_{index:03d}_{digest}"


def dedupe_key(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "").strip().lower()
    if url:
        return f"url:{url}"
    source = str(item.get("source") or "").strip().lower()
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip().lower()
    published_at = str(item.get("published_at") or "")
    return f"title:{source}:{title}:{published_at}"


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^0-9a-zA-Z가-힣._-]+", "_", value).strip("_")
    return safe or "unknown"
