from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import json
import re

from app.config import settings
from services.evidence.store_service import now_iso, safe_filename
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt


DEFAULT_COMPRESSED_EVIDENCE_DIR = settings.output_dir / "compressed_evidence"
DEFAULT_RAG_SCORE_THRESHOLD = 0.5
ALLOWED_AXES = {"legal", "technology", "market", "business_fit"}
DEFAULT_TEXT_LIMIT = 6000


def compress_evidence_items(
    items: list[dict[str, Any]],
    *,
    preprocessed_patent: dict[str, Any],
    rag_score_threshold: float = DEFAULT_RAG_SCORE_THRESHOLD,
    llm: Callable[[str], str] = call_llm,
) -> dict[str, Any]:
    candidates, skipped = select_compression_candidates(items, rag_score_threshold=rag_score_threshold)
    compressed: list[dict[str, Any]] = []
    warnings: list[str] = []
    rejected_by_llm = 0
    compression_failed = 0

    for item in candidates:
        try:
            compressed_item = compress_single_evidence(item, preprocessed_patent=preprocessed_patent, llm=llm)
        except Exception as exc:
            # EVID-10: 압축 실패(LLM 비-JSON 등) 시 근거를 조용히 누락하지 않고 원본을 미압축 패스스루로 보존한다.
            warnings.append(f"{item.get('evidence_id')}:compression_failed:{exc.__class__.__name__}:{str(exc)[:200]}")
            compression_failed += 1
            compressed.append({**item, "compression_skipped": True})
            continue

        if compressed_item.get("is_relevant") is False:
            rejected_by_llm += 1
            continue
        compressed.append(compressed_item)

    return {
        "items": compressed,
        "warnings": warnings,
        "stats": {
            "input_count": len(items),
            "candidate_count": len(candidates),
            "compressed_count": len(compressed),
            "rejected_by_llm_count": rejected_by_llm,
            "compression_failed_count": compression_failed,
            "skipped_non_target_count": skipped["non_target"],
            "skipped_low_rag_score_count": skipped["low_rag_score"],
            "rag_score_threshold": rag_score_threshold,
        },
    }


def select_compression_candidates(
    items: list[dict[str, Any]],
    *,
    rag_score_threshold: float = DEFAULT_RAG_SCORE_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    skipped = {"non_target": 0, "low_rag_score": 0}

    for item in items:
        source_type = item.get("source_type")
        if source_type == "news":
            candidates.append(item)
            continue
        if source_type == "industry_report":
            score = to_float(item.get("score"))
            if score is None or score >= rag_score_threshold:
                candidates.append(item)
            else:
                skipped["low_rag_score"] += 1
            continue
        skipped["non_target"] += 1

    return candidates, skipped


def compress_single_evidence(
    item: dict[str, Any],
    *,
    preprocessed_patent: dict[str, Any],
    llm: Callable[[str], str] = call_llm,
) -> dict[str, Any]:
    raw = llm(build_compression_prompt(item, preprocessed_patent=preprocessed_patent))
    parsed = parse_json_object(raw)
    if not parsed:
        raise ValueError("LLM response was not valid JSON object")

    if item.get("source_type") == "industry_report":
        return normalize_industry_compression(item, parsed)
    return normalize_news_compression(item, parsed)


def build_compression_prompt(item: dict[str, Any], *, preprocessed_patent: dict[str, Any]) -> str:
    prompt_template = load_prompt("evidence/evidence_compression.md").strip()
    payload = {
        "patent": patent_prompt_payload(preprocessed_patent),
        "evidence": evidence_prompt_payload(item),
    }
    return f"{prompt_template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def patent_prompt_payload(preprocessed_patent: dict[str, Any]) -> dict[str, Any]:
    metadata = preprocessed_patent.get("metadata") or {}
    sections = preprocessed_patent.get("sections") or {}
    return {
        "title": metadata.get("title"),
        "abstract": sections.get("abstract"),
        "technical_field": sections.get("technical_field"),
    }


# SEC-03: 외부 근거 텍스트의 비가시 제어문자를 제거한다(숨은 지시·출력 조작에 악용되는 C0/DEL 문자 차단).
# 줄바꿈(\n)·탭(\t)은 보존하고 그 외 제어문자만 제거한다.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_external_text(text: str) -> str:
    return _CONTROL_CHARS_RE.sub("", text)


def evidence_prompt_payload(item: dict[str, Any]) -> dict[str, Any]:
    text = sanitize_external_text(str(item.get("content") or item.get("context") or ""))[:DEFAULT_TEXT_LIMIT]
    return {
        "evidence_id": item.get("evidence_id"),
        "source_type": item.get("source_type"),
        "source": item.get("source"),
        "title": item.get("title"),
        "published_at": item.get("published_at"),
        "metadata": item.get("metadata"),
        "retrieval_score": item.get("score"),
        "text": text,
    }


def normalize_news_compression(item: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "source_type": "news",
        "source": item.get("source"),
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "collected_at": item.get("collected_at") or now_iso(),
        "is_relevant": bool(parsed.get("is_relevant", True)),
        "related_axes": normalize_axes(parsed.get("related_axes")),
        "relation_type": normalize_relation_type(parsed.get("relation_type")),
        "compressed_summary": normalize_text(parsed.get("compressed_summary")),
        "key_facts": normalize_text_list(parsed.get("key_facts")),
        "axis_context": normalize_axis_context(parsed.get("axis_context")),
    }


def normalize_industry_compression(item: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "source_type": "industry_report",
        "source": item.get("source"),
        "title": item.get("title") or item.get("heading"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "collected_at": item.get("collected_at") or now_iso(),
        "metadata": dict(item.get("metadata") or {}),
        "related_axes": normalize_axes(parsed.get("related_axes")) or ["market"],
        "compressed_summary": normalize_text(parsed.get("compressed_summary")),
        "key_facts": normalize_text_list(parsed.get("key_facts")),
        "retrieval_score": to_float(item.get("score")),
    }


def save_compressed_evidence_result(
    *,
    patent_id: str | int | None,
    result: dict[str, Any],
    output_dir: Path | str = DEFAULT_COMPRESSED_EVIDENCE_DIR,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(str(patent_id) if patent_id is not None else 'unknown')}_compressed_evidence.json"
    path = directory / filename
    payload = {
        "source_type": "compressed_evidence",
        "source": "llm_evidence_compression",
        "patent_id": str(patent_id) if patent_id is not None else None,
        "collected_at": now_iso(),
        **result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def normalize_axes(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    else:
        values = []
    axes = []
    for axis in values:
        normalized = axis.strip()
        if normalized in ALLOWED_AXES and normalized not in axes:
            axes.append(normalized)
    return axes


def normalize_axis_context(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        axis: normalize_text(text)
        for axis, text in value.items()
        if axis in ALLOWED_AXES and normalize_text(text)
    }


def normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (normalize_text(item) for item in value) if text]


def normalize_relation_type(value: Any) -> str:
    text = normalize_text(value)
    return text if text in {"direct", "indirect", "background", "unrelated"} else "indirect"


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
