from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import json
import re
import sqlite3

from app.config import settings
from services.evidence.compression_service import parse_json_object
from services.evidence.store_service import now_iso, safe_filename
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.patent.kipris_patent_service import (
    fetch_kipris_bibliography,
    fetch_kipris_bibliography_basic,
)


DEFAULT_PORTFOLIO_OUTPUT_DIR = settings.output_dir / "portfolio_evidence"
EXCLUDED_RELATED_PRODUCTS = {"", "기타", "etc", "ETC", "기타/미정"}
DEFAULT_SIBLING_LIMIT = 8


def analyze_portfolio_siblings(
    *,
    target_patent: dict[str, Any],
    target_api_data: dict[str, Any] | None = None,
    database_path: Path | str = settings.patent_db_path,
    sibling_limit: int = DEFAULT_SIBLING_LIMIT,
    llm: Callable[[str], str] = call_llm,
    # 포트폴리오 보강은 제목·초록·청구항·IPC/CPC만 쓰므로 sibling마다 전체 서지(패밀리·
    # 인용·피인용·인용근거)를 당기지 않고 bibliography_detail 1회만 호출한다.
    kipris_fetcher: Callable[[str], dict[str, Any]] = fetch_kipris_bibliography_basic,
) -> dict[str, Any]:
    siblings = find_sibling_patents(
        target_patent,
        database_path=database_path,
        limit=sibling_limit,
    )
    if not siblings:
        return empty_result(target_patent, reason="no_sibling_patents")

    target_payload, target_warning = enrich_target_patent(
        target_patent,
        api_data=target_api_data,
        kipris_fetcher=kipris_fetcher,
    )
    enriched, warnings = enrich_sibling_patents(siblings, kipris_fetcher=kipris_fetcher)
    warnings.extend([target_warning] if target_warning else [])
    if not enriched:
        return {
            **empty_result(target_patent, reason="sibling_enrichment_empty"),
            "warnings": warnings,
        }

    evidence, summary_warning = build_portfolio_evidence(
        target_patent=target_payload,
        enriched_siblings=enriched,
        llm=llm,
    )
    warnings.extend([summary_warning] if summary_warning else [])
    return {
        "items": [evidence],
        "warnings": warnings,
        "stats": {
            "candidate_count": len(siblings),
            "enriched_count": len(enriched),
            "portfolio_evidence_count": 1,
            "group_size": len(siblings) + 1,
        },
    }


def find_sibling_patents(
    target_patent: dict[str, Any],
    *,
    database_path: Path | str = settings.patent_db_path,
    limit: int = DEFAULT_SIBLING_LIMIT,
) -> list[dict[str, Any]]:
    related_product = normalize_text(target_patent.get("related_product"))
    management_family = management_family_key(target_patent.get("management_number"))
    if related_product in EXCLUDED_RELATED_PRODUCTS and not management_family:
        return []

    product_family = ""
    conditions: list[str] = []
    params: list[Any] = [target_patent.get("id")]

    if related_product not in EXCLUDED_RELATED_PRODUCTS:
        product_family = normalize_product_family(related_product)
        if product_family:
            conditions.append(
                """
                (
                    COALESCE(related_product, '') NOT IN ('', '기타', 'etc', 'ETC', '기타/미정')
                    AND (related_product = ? OR related_product LIKE ?)
                )
                """
            )
            params.extend([related_product, f"{product_family}%"])
        else:
            conditions.append(
                """
                (
                    COALESCE(related_product, '') NOT IN ('', '기타', 'etc', 'ETC', '기타/미정')
                    AND related_product = ?
                )
                """
            )
            params.append(related_product)

    if management_family:
        conditions.append("(management_number = ? OR management_number LIKE ?)")
        params.extend([management_family, f"{management_family}-%"])

    if not conditions:
        return []

    query = f"""
        SELECT
            id,
            management_number,
            application_number,
            registration_number,
            title_final,
            business_area,
            technology_area,
            related_product,
            country,
            joint_application,
            joint_applicant_name,
            status,
            application_date,
            registration_date,
            expected_expiration_date
        FROM patents
        WHERE id != ?
          AND ({" OR ".join(conditions)})
    """
    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    candidates = []
    for row in rows:
        sibling = dict(row)
        reasons = sibling_match_reasons(
            target_patent,
            sibling,
            target_product_family=product_family,
            target_management_family=management_family,
        )
        if not reasons:
            continue
        sibling["sibling_match_reasons"] = reasons
        candidates.append(sibling)
    candidates.sort(
        key=lambda sibling: (
            sibling_match_priority(sibling["sibling_match_reasons"]),
            sibling.get("application_date") or "",
            sibling.get("id") or 0,
        )
    )
    return candidates[: max(1, int(limit))]


def sibling_match_reasons(
    target_patent: dict[str, Any],
    sibling: dict[str, Any],
    *,
    target_product_family: str,
    target_management_family: str | None,
) -> list[str]:
    reasons: list[str] = []
    target_related_product = normalize_text(target_patent.get("related_product"))
    sibling_related_product = normalize_text(sibling.get("related_product"))
    sibling_product_family = normalize_product_family(sibling.get("related_product"))

    if target_related_product not in EXCLUDED_RELATED_PRODUCTS:
        if sibling_related_product == target_related_product:
            reasons.append("same_related_product")
        elif target_product_family and sibling_product_family == target_product_family:
            reasons.append("same_product_family")

    sibling_family = management_family_key(sibling.get("management_number"))
    if target_management_family and sibling_family == target_management_family:
        reasons.append("same_management_family")

    return reasons


def sibling_match_priority(reasons: list[str]) -> int:
    if "same_management_family" in reasons:
        return 0
    if "same_related_product" in reasons:
        return 1
    if "same_product_family" in reasons:
        return 2
    return 3


def enrich_target_patent(
    target_patent: dict[str, Any],
    *,
    api_data: dict[str, Any] | None = None,
    kipris_fetcher: Callable[[str], dict[str, Any]] = fetch_kipris_bibliography,
) -> tuple[dict[str, Any], str | None]:
    application_number = target_patent.get("application_number")
    warning = None
    if not api_data and application_number:
        try:
            api_data = kipris_fetcher(str(application_number))
        except Exception as exc:
            warning = f"{application_number}:target_kipris_fetch_failed:{exc.__class__.__name__}:{str(exc)[:200]}"
    return build_patent_api_payload(target_patent, api_data=api_data), warning


def enrich_sibling_patents(
    siblings: list[dict[str, Any]],
    *,
    kipris_fetcher: Callable[[str], dict[str, Any]] = fetch_kipris_bibliography,
) -> tuple[list[dict[str, Any]], list[str]]:
    enriched: list[dict[str, Any]] = []
    warnings: list[str] = []
    for sibling in siblings:
        application_number = sibling.get("application_number")
        api_data: dict[str, Any] | None = None
        if application_number and is_kr_patent(sibling):
            try:
                api_data = kipris_fetcher(str(application_number))
            except Exception as exc:
                warnings.append(f"{application_number}:kipris_fetch_failed:{exc.__class__.__name__}:{str(exc)[:200]}")
        enriched.append(build_patent_api_payload(sibling, api_data=api_data))
    return enriched, warnings


def is_kr_patent(patent: dict[str, Any]) -> bool:
    country = normalize_text(patent.get("country")).upper()
    if country:
        return country == "KR"
    application_number = normalize_text(patent.get("application_number"))
    return application_number.startswith("10-")


def build_patent_api_payload(
    patent: dict[str, Any],
    *,
    api_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = (api_data or {}).get("metadata") or {}
    claims = (api_data or {}).get("claims") or []
    representative_claim = next(
        (claim.get("text") for claim in claims if claim.get("is_independent") and claim.get("text")),
        claims[0].get("text") if claims else None,
    )
    return {
        "patent_id": patent.get("id"),
        "management_number": patent.get("management_number"),
        "application_number": patent.get("application_number"),
        "registration_number": patent.get("registration_number"),
        "country": patent.get("country"),
        "title": metadata.get("title") or patent.get("title_final"),
        "related_product": patent.get("related_product"),
        "business_area": patent.get("business_area"),
        "technology_area": patent.get("technology_area"),
        "application_date": patent.get("application_date"),
        "registration_date": patent.get("registration_date"),
        "assignee": metadata.get("assignee") or [],
        "ipc": metadata.get("ipc") or [],
        "cpc": metadata.get("cpc") or [],
        "claim_stats": (api_data or {}).get("claim_stats") or {},
        "abstract": ((api_data or {}).get("sections") or {}).get("abstract") or "",
        "representative_claim": representative_claim or "",
        "enrichment_source": "kipris_api" if api_data else "patent_db",
    }


def build_portfolio_evidence(
    *,
    target_patent: dict[str, Any],
    enriched_siblings: list[dict[str, Any]],
    llm: Callable[[str], str] = call_llm,
) -> tuple[dict[str, Any], str | None]:
    raw = llm(build_portfolio_prompt(target_patent=target_patent, enriched_siblings=enriched_siblings))
    parsed = parse_json_object(raw)

    if not parsed:
        raise RuntimeError("Portfolio sibling summary response was not valid JSON.")

    siblings = normalize_sibling_summaries(parsed.get("sibling_patents"), enriched_siblings)
    evidence = {
        "evidence_id": build_portfolio_evidence_id(target_patent),
        "source_type": "portfolio_context",
        "source": "kipris_api",
        "related_product": target_patent.get("related_product"),
        "application_date": target_patent.get("application_date"),
        "business_area": target_patent.get("business_area"),
        "technology_area": target_patent.get("technology_area"),
        "group_size": len(enriched_siblings) + 1,
        "sibling_patents": siblings,
        "compressed_summary": normalize_text(parsed.get("compressed_summary")),
        "key_facts": normalize_text_list(parsed.get("key_facts")),
        "collected_at": now_iso(),
    }
    return evidence, None


def build_portfolio_prompt(
    *,
    target_patent: dict[str, Any],
    enriched_siblings: list[dict[str, Any]],
) -> str:
    template = load_prompt("evidence/portfolio_sibling_summary.md").strip()
    payload = {
        "target_patent": portfolio_prompt_patent_payload(target_patent),
        "sibling_patents": [
            portfolio_prompt_patent_payload(sibling)
            for sibling in enriched_siblings
        ],
    }
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def portfolio_prompt_patent_payload(patent: dict[str, Any]) -> dict[str, Any]:
    return {
        "patent_id": patent.get("patent_id") or patent.get("id"),
        "management_number": patent.get("management_number"),
        "application_number": patent.get("application_number"),
        "registration_number": patent.get("registration_number"),
        "country": patent.get("country"),
        "title": patent.get("title") or patent.get("title_final"),
        "related_product": patent.get("related_product"),
        "business_area": patent.get("business_area"),
        "technology_area": patent.get("technology_area"),
        "application_date": patent.get("application_date"),
        "registration_date": patent.get("registration_date"),
        "assignee": patent.get("assignee") or [],
        "ipc": patent.get("ipc") or [],
        "cpc": patent.get("cpc") or [],
        "claim_stats": patent.get("claim_stats") or {},
        "abstract": str(patent.get("abstract") or "")[:1500],
        "representative_claim": str(patent.get("representative_claim") or "")[:2000],
    }


def save_portfolio_evidence_result(
    *,
    patent_id: str | int | None,
    result: dict[str, Any],
    output_dir: Path | str = DEFAULT_PORTFOLIO_OUTPUT_DIR,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(str(patent_id) if patent_id is not None else 'unknown')}_portfolio_evidence.json"
    path = directory / filename
    payload = {
        "source_type": "portfolio_evidence",
        "source": "portfolio_sibling_analysis",
        "patent_id": str(patent_id) if patent_id is not None else None,
        "collected_at": now_iso(),
        **result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def empty_result(target_patent: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "items": [],
        "warnings": [reason],
        "stats": {
            "candidate_count": 0,
            "enriched_count": 0,
            "portfolio_evidence_count": 0,
            "group_size": 1 if target_patent else 0,
        },
    }


def normalize_sibling_summaries(value: Any, enriched_siblings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_application = {str(sibling.get("application_number")): sibling for sibling in enriched_siblings}
    by_id = {str(sibling.get("patent_id")): sibling for sibling in enriched_siblings}
    items = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = by_application.get(str(item.get("application_number"))) or by_id.get(str(item.get("patent_id"))) or {}
        normalized.append(
            {
                "patent_id": item.get("patent_id") or source.get("patent_id"),
                "application_number": item.get("application_number") or source.get("application_number"),
                "title": normalize_text(item.get("title") or source.get("title")),
                "portfolio_role": normalize_text(item.get("portfolio_role")),
                "covered_capability": normalize_text(item.get("covered_capability")),
                "relation_to_target": normalize_text(item.get("relation_to_target")),
            }
        )
    if not normalized:
        raise RuntimeError("Portfolio sibling summary response must include sibling_patents.")
    return normalized


def build_portfolio_evidence_id(target_patent: dict[str, Any]) -> str:
    parts = [
        "portfolio",
        str(target_patent.get("id") or "unknown"),
        safe_filename(str(target_patent.get("related_product") or "product")),
        re.sub(r"[^0-9]", "", str(target_patent.get("application_date") or "")) or "date",
    ]
    return "_".join(parts)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (normalize_text(item) for item in value) if text]


def normalize_product_family(value: Any) -> str:
    text = normalize_text(value)
    text = re.split(r"[\(（]", text, maxsplit=1)[0]
    return re.sub(r"\s+", "", text).upper()


def management_family_key(value: Any) -> str | None:
    match = re.match(r"([A-Za-z]\d{9})(?:-[A-Za-z]{2}\d+)?$", normalize_text(value))
    return match.group(1).upper() if match else None
