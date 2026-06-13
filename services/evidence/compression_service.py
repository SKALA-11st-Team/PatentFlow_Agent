from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor
import contextvars
import json
import re

from app.config import settings
from services.evidence.store_service import now_iso, safe_filename
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt


DEFAULT_COMPRESSED_EVIDENCE_DIR = settings.output_dir / "compressed_evidence"
DEFAULT_RAG_SCORE_THRESHOLD = settings.rag_score_threshold
DEFAULT_TEXT_LIMIT = 6000
DEFAULT_COMPRESSION_WORKERS = settings.compression_workers


def compress_evidence_items(
    items: list[dict[str, Any]],
    *,
    preprocessed_patent: dict[str, Any],
    rag_score_threshold: float = DEFAULT_RAG_SCORE_THRESHOLD,
    llm: Callable[[str], str] = call_llm,
) -> dict[str, Any]:
    candidates, skipped, passthrough = select_compression_candidates(items, rag_score_threshold=rag_score_threshold)
    compressed: list[dict[str, Any]] = []
    warnings: list[str] = []
    rejected_by_llm = 0
    compression_failed = 0

    for item, compressed_item, warning in compress_candidates_concurrently(
        candidates,
        preprocessed_patent=preprocessed_patent,
        llm=llm,
    ):
        if warning:
            # EVID-10: 압축 실패(LLM 비-JSON 등) 시 근거를 조용히 누락하지 않고 원본을 미압축 패스스루로 보존한다.
            warnings.append(warning)
            compression_failed += 1
            compressed.append({**item, "compression_skipped": True})
            continue
        if compressed_item.get("is_relevant") is False:
            rejected_by_llm += 1
            continue
        compressed.append(compressed_item)

    # AG-01: 경쟁특허 근거는 LLM 압축 없이 번들에 합류시킨다. 축 프롬프트 페이로드
    # (valuation_evidence_payload)는 content를 렌더링하지 않으므로 초록을 compressed_summary로 옮긴다.
    compressed.extend(passthrough_evidence_item(item) for item in passthrough)

    return {
        "items": compressed,
        "warnings": warnings,
        "stats": {
            "input_count": len(items),
            "candidate_count": len(candidates),
            "compressed_count": len(compressed),
            "rejected_by_llm_count": rejected_by_llm,
            "compression_failed_count": compression_failed,
            "passthrough_count": len(passthrough),
            "skipped_non_target_count": skipped["non_target"],
            "skipped_low_rag_score_count": skipped["low_rag_score"],
            "rag_score_threshold": rag_score_threshold,
        },
    }


def passthrough_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if not result.get("compressed_summary"):
        result["compressed_summary"] = sanitize_external_text(str(item.get("content") or ""))[:600]
    return result


def compress_candidates_concurrently(
    candidates: list[dict[str, Any]],
    *,
    preprocessed_patent: dict[str, Any],
    llm: Callable[[str], str],
) -> list[tuple[dict[str, Any], dict[str, Any], str | None]]:
    if not candidates:
        return []

    def compress_candidate(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        try:
            compressed_item = compress_single_evidence(item, preprocessed_patent=preprocessed_patent, llm=llm)
            return item, compressed_item, None
        except Exception as exc:
            warning = f"{item.get('evidence_id')}:compression_failed:{exc.__class__.__name__}:{str(exc)[:200]}"
            return item, {}, warning

    max_workers = min(DEFAULT_COMPRESSION_WORKERS, len(candidates))
    if max_workers <= 1:
        return [compress_candidate(item) for item in candidates]
    # Propagate the current context (incl. the active LangSmith run tree, which is
    # stored in contextvars) into each worker thread so the per-item compression
    # LLM calls nest under the evidence_compression node instead of appearing as
    # separate root traces. copy_context() is evaluated here on the calling thread.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(contextvars.copy_context().run, compress_candidate, item)
            for item in candidates
        ]
        return [future.result() for future in futures]


def select_compression_candidates(
    items: list[dict[str, Any]],
    *,
    rag_score_threshold: float = DEFAULT_RAG_SCORE_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    skipped = {"non_target": 0, "low_rag_score": 0}
    passthrough: list[dict[str, Any]] = []

    for item in items:
        source_type = item.get("source_type")
        if source_type == "news":
            candidates.append(item)
            continue
        if source_type == "company_disclosure":
            # SK AX 공식 사이트/계열 매체 근거. 검색 단계에서 키워드로 거르지 않고
            # 전문을 그대로 올린 뒤, 여기서 요약 + 관련성(LLM)을 판단한다.
            candidates.append(item)
            continue
        if source_type == "industry_report":
            score = to_float(item.get("score"))
            if score is None or score >= rag_score_threshold:
                candidates.append(item)
            else:
                skipped["low_rag_score"] += 1
            continue
        if source_type == "competitor_patent":
            # AG-01(EVID-02 복원): KIPRIS 경쟁특허 근거는 이미 짧은 서지·초록이라
            # 뉴스 압축 프롬프트를 거치지 않고 무변형으로 통과시킨다.
            # 여기서 non_target으로 버리면 권리성·기술성 축이 받을 수 없다.
            passthrough.append(item)
            continue
        skipped["non_target"] += 1

    return candidates, skipped, passthrough


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
    if item.get("source_type") == "company_disclosure":
        return normalize_company_disclosure_compression(item, parsed)
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
        # SK AX(또는 SK C&C)의 사업/제품/서비스와 직접 관련된 뉴스인지 LLM이 판단한 값.
        # True이면 시장성 외에 사업연계성 축에서도 보조 근거로 쓸 수 있다.
        "sk_ax_relevant": bool(parsed.get("sk_ax_relevant", False)),
        "relation_type": normalize_relation_type(parsed.get("relation_type")),
        "compressed_summary": normalize_text(parsed.get("compressed_summary")),
        "key_facts": normalize_text_list(parsed.get("key_facts")),
    }


def normalize_company_disclosure_compression(item: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    # SK AX 공식 사이트/계열 매체 근거는 source/source_type/url/source_domain 등
    # 식별 필드를 그대로 보존하고(사업연계성 축 선택이 source 기반이므로), 그 위에
    # LLM 요약·관련성 판단 결과만 덧씌운다. 원문(content)도 유지해 근거 발췌에 쓴다.
    compressed = dict(item)
    compressed["is_relevant"] = bool(parsed.get("is_relevant", True))
    compressed["sk_ax_relevant"] = bool(parsed.get("sk_ax_relevant", True))
    compressed["compressed_summary"] = normalize_text(parsed.get("compressed_summary"))
    compressed["key_facts"] = normalize_text_list(parsed.get("key_facts"))
    compressed["collected_at"] = item.get("collected_at") or now_iso()
    return compressed


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
