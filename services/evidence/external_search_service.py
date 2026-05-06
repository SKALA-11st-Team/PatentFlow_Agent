from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import json
import re
import time

import requests

from app.config import settings
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.evidence.api_normalizers import (
    normalize_dart_disclosures,
    normalize_gnews_response,
    normalize_kipris_patent_results,
    normalize_naver_news_response,
)
from services.evidence.news_article_extraction_service import enrich_news_items_with_full_text
from services.evidence.store_service import (
    merge_evidence_sources,
    save_evidence_collection,
)


DEFAULT_UNIFIED_API_BASE_URL = "http://127.0.0.1:8000"
MAX_SEARCH_QUERIES = settings.search_query_count
API_REQUEST_MAX_ATTEMPTS = 3
API_REQUEST_RETRY_STATUS_CODES = {502, 503, 504}


def rewrite_search_queries(
    preprocessed_patent: dict[str, Any],
    *,
    missing_evidence: list[str] | None = None,
    previous_queries: list[str] | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    previous = set(previous_queries or [])
    rewritten_ko: list[str] = []
    rewritten_en: list[str] = []

    rewrite_source = "rule_based"
    llm_error: str | None = None

    if use_llm:
        llm_result, llm_error = llm_rewrite_search_queries(
            preprocessed_patent=preprocessed_patent,
            missing_evidence=missing_evidence or [],
            previous_queries=previous_queries or [],
        )
        if llm_result:
            rewritten_ko = [query for query in compact_queries(llm_result.get("ko", []))[:MAX_SEARCH_QUERIES] if query not in previous]
            rewritten_en = [query for query in compact_queries(llm_result.get("en", []))[:MAX_SEARCH_QUERIES] if query not in previous]
            rewrite_source = "llm"
        else:
            rewrite_source = "fallback_empty"

    rewritten_en = enforce_english_queries(
        rewritten_en,
        preprocessed_patent,
        fill_to=MAX_SEARCH_QUERIES if use_llm else None,
    )

    return {
        "ko": rewritten_ko,
        "en": rewritten_en,
        "meta": {
            "rewrite_source": rewrite_source,
            "llm_error": llm_error,
        },
    }


def collect_external_evidence(
    *,
    preprocessed_patent: dict[str, Any],
    patent_id: str | int | None = None,
    application_number: str | None = None,
    api_base_url: str = DEFAULT_UNIFIED_API_BASE_URL,
    query_limit_per_axis: int = 1,
    include_naver: bool = True,
    include_gnews: bool = True,
    include_kipris: bool = True,
    dart_corp_code: str | None = None,
    dart_bgn_de: str | None = None,
    dart_end_de: str | None = None,
    missing_evidence: list[str] | None = None,
    previous_queries: list[str] | None = None,
    use_llm_rewrite: bool = True,
    ko_queries_override: list[str] | None = None,
    en_queries_override: list[str] | None = None,
    fetch_news_full_text: bool = settings.fetch_news_full_text,
    output_dir: Path | str | None = None,
    save: bool = True,
) -> dict[str, Any]:
    rewrite_meta: dict[str, Any]
    if ko_queries_override is not None or en_queries_override is not None:
        ko_queries = compact_queries(ko_queries_override or [])[:MAX_SEARCH_QUERIES]
        en_queries = enforce_english_queries(
            en_queries_override or [],
            preprocessed_patent,
            fill_to=MAX_SEARCH_QUERIES,
        )
        rewrite_meta = {"rewrite_source": "precomputed", "llm_error": None}
    else:
        rewritten = rewrite_search_queries(
            preprocessed_patent,
            missing_evidence=missing_evidence,
            previous_queries=previous_queries,
            use_llm=use_llm_rewrite,
        )
        ko_queries = rewritten["ko"]
        en_queries = rewritten["en"]
        rewrite_meta = rewritten.get("meta") or {}
    safe_limit = max(1, int(query_limit_per_axis))
    selected_queries = ko_queries[:safe_limit]
    selected_gnews_queries = en_queries[:safe_limit]

    api_base_url = api_base_url.rstrip("/")
    sources: list[list[dict[str, Any]]] = []
    saved_paths: list[str] = []
    warnings: list[str] = []

    if include_naver:
        for query in selected_queries:
            try:
                raw = request_json(
                    api_base_url,
                    "/api/news/search",
                    {"query": query, "display": 5, "start": 1, "sort": "sim"},
                )
                items = enrich_news_items_with_full_text(
                    normalize_naver_news_response(raw, query=query),
                    enabled=fetch_news_full_text,
                )
                sources.append(items)
                if save:
                    path = save_evidence_collection(
                        source_type="news",
                        source="naver_news",
                        items=items,
                        query=query,
                        patent_id=patent_id,
                        output_dir=output_dir or settings.output_dir / "api_evidence",
                    )
                    saved_paths.append(str(path))
            except requests.RequestException as exc:
                warnings.append(f"naver_news call failed for query '{query}': {exc}")

    if include_gnews:
        for gnews_query in selected_gnews_queries:
            try:
                raw = request_json(
                    api_base_url,
                    "/api/v4/search",
                    {"q": gnews_query, "lang": "en", "max": 5, "page": 1},
                )
                items = enrich_news_items_with_full_text(
                    normalize_gnews_response(raw, query=gnews_query),
                    enabled=fetch_news_full_text,
                )
                sources.append(items)
                if save:
                    path = save_evidence_collection(
                        source_type="news",
                        source="gnews",
                        items=items,
                        query=gnews_query,
                        patent_id=patent_id,
                        output_dir=output_dir or settings.output_dir / "api_evidence",
                    )
                    saved_paths.append(str(path))
            except requests.RequestException as exc:
                warnings.append(f"gnews call failed for query '{gnews_query}': {exc}")

    if include_kipris and application_number:
        try:
            raw = request_json(
                api_base_url,
                "/kipris/patent-utility/search/application-number",
                {"applicationNumber": application_number},
            )
            query = f"application_number:{application_number}"
            items = normalize_kipris_patent_results(raw, query=query, source="kipris")
            sources.append(items)
            if save:
                path = save_evidence_collection(
                    source_type="competitor_patent",
                    source="kipris",
                    items=items,
                    query=query,
                    patent_id=patent_id,
                    output_dir=output_dir or settings.output_dir / "api_evidence",
                )
                saved_paths.append(str(path))
        except requests.RequestException as exc:
            warnings.append(f"kipris call failed for application_number '{application_number}': {exc}")

    if dart_corp_code:
        try:
            bgn_de, end_de = resolve_dart_date_range(dart_bgn_de, dart_end_de)
            raw = request_json(
                api_base_url,
                "/dart/disclosure",
                {"corp_code": dart_corp_code, "bgn_de": bgn_de, "end_de": end_de},
            )
            query = f"corp_code:{dart_corp_code}"
            items = normalize_dart_disclosures(raw, query=query)
            sources.append(items)
            if save:
                path = save_evidence_collection(
                    source_type="company_disclosure",
                    source="dart",
                    items=items,
                    query=query,
                    patent_id=patent_id,
                    output_dir=output_dir or settings.output_dir / "api_evidence",
                )
                saved_paths.append(str(path))
        except requests.RequestException as exc:
            warnings.append(f"dart call failed for corp_code '{dart_corp_code}': {exc}")

    merged = merge_evidence_sources(sources, prefix="api")

    return {
        "ko_queries": ko_queries,
        "en_queries": en_queries,
        "queries": selected_queries,
        "gnews_queries": selected_gnews_queries,
        "rewrite_meta": rewrite_meta,
        "items": merged,
        "saved_collections": saved_paths,
        "warnings": warnings + ([f"query rewriting fallback: {rewrite_meta.get('llm_error')}"] if rewrite_meta.get("llm_error") else []),
    }


def compact_queries(queries: list[str]) -> list[str]:
    result = []
    seen = set()
    for query in queries:
        compacted = " ".join(str(query).split())
        if not compacted or compacted in seen:
            continue
        seen.add(compacted)
        result.append(compacted)
    return result


def request_json(
    base_url: str,
    path: str,
    params: dict[str, Any],
    *,
    timeout: int = 20,
) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    last_error: requests.RequestException | None = None
    for attempt in range(1, API_REQUEST_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            last_error = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code not in API_REQUEST_RETRY_STATUS_CODES or attempt == API_REQUEST_MAX_ATTEMPTS:
                raise with_response_detail(exc)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            if attempt == API_REQUEST_MAX_ATTEMPTS:
                raise
        time.sleep(0.5 * attempt)
    if last_error:
        raise last_error
    raise requests.RequestException(f"API request failed: {url}")


def with_response_detail(exc: requests.HTTPError) -> requests.HTTPError:
    if exc.response is None:
        return exc
    detail = exc.response.text[:300]
    return requests.HTTPError(f"{exc}; response={detail}", response=exc.response)


def resolve_dart_date_range(
    bgn_de: str | None,
    end_de: str | None,
) -> tuple[str, str]:
    if bgn_de and end_de:
        return bgn_de, end_de
    today = datetime.now()
    end_value = end_de or today.strftime("%Y%m%d")
    bgn_value = bgn_de or (today - timedelta(days=365)).strftime("%Y%m%d")
    return bgn_value, end_value


def contains_hangul(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value or ""))


def llm_rewrite_search_queries(
    *,
    preprocessed_patent: dict[str, Any],
    missing_evidence: list[str],
    previous_queries: list[str],
) -> tuple[dict[str, list[str]] | None, str | None]:
    prompt_template = load_prompt("query_rewriting.md").replace(
        "{{search_query_count}}",
        str(MAX_SEARCH_QUERIES),
    )
    payload = {
        "metadata": preprocessed_patent.get("metadata") or {},
        "sections": {
            "abstract": (preprocessed_patent.get("sections") or {}).get("abstract") or "",
            "technical_field": (preprocessed_patent.get("sections") or {}).get("technical_field") or "",
            "problem": (preprocessed_patent.get("sections") or {}).get("problem") or "",
            "solution": (preprocessed_patent.get("sections") or {}).get("solution") or "",
        },
        "missing_evidence": missing_evidence,
        "previous_queries": previous_queries,
    }
    prompt = f"{prompt_template.strip()}\n\n입력 데이터(JSON):\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

    try:
        raw = call_llm(prompt)
    except Exception as exc:
        return None, f"llm_call_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
    parsed = parse_query_rewrite_response(raw)
    if not parsed:
        return None, "llm_parse_failed"
    return parsed, None


def parse_query_rewrite_response(raw: str) -> dict[str, list[str]] | None:
    text = (raw or "").strip()
    if not text:
        return None

    parsed = None
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

    if not isinstance(parsed, dict):
        return None

    result: dict[str, list[str]] = {"ko": [], "en": []}
    for lang in ("ko", "en"):
        section = parsed.get(lang)
        if isinstance(section, list):
            result[lang] = compact_queries([str(query) for query in section if str(query).strip()])[:MAX_SEARCH_QUERIES]
        elif isinstance(section, dict):
            flattened: list[str] = []
            for value in section.values():
                if isinstance(value, list):
                    flattened.extend(str(query) for query in value if str(query).strip())
            result[lang] = compact_queries(flattened)[:MAX_SEARCH_QUERIES]
    return result


def enforce_english_queries(
    queries: list[str],
    preprocessed_patent: dict[str, Any],
    *,
    fill_to: int | None = None,
) -> list[str]:
    del preprocessed_patent, fill_to
    return compact_queries(
        [normalize_gnews_query(query) for query in queries if query and not contains_hangul(query)]
    )[:MAX_SEARCH_QUERIES]


def normalize_gnews_query(query: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\s]", " ", query)
    return " ".join(normalized.split())
