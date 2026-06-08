from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
import json
import os
import re
import time

import requests

from app.config import settings
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.evidence.api_normalizers import (
    normalize_kipris_patent_results,
    normalize_naver_news_response,
    normalize_tavily_news_response,
)
from services.evidence.news_article_extraction_service import enrich_news_items_with_full_text
from services.evidence.store_service import (
    merge_evidence_sources,
    save_evidence_collection,
)


DEFAULT_UNIFIED_API_BASE_URL = settings.unified_api_base_url
MAX_SEARCH_QUERIES = settings.search_query_count
MAX_INDUSTRY_RAG_QUERIES = settings.industry_rag_query_count
API_REQUEST_MAX_ATTEMPTS = 3
API_REQUEST_RETRY_STATUS_CODES = {502, 503, 504}
DEFAULT_NEWS_SEARCH_WORKERS = 4
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def search_global_news_via_tavily(query: str, *, max_results: int) -> dict[str, Any]:
    """GNews 대체: Tavily(topic=news, 도메인 제한 없음)로 글로벌 영어 뉴스를 검색한다.

    국가 제한 없이 영어 검색어로 전세계 뉴스를 가져오고, 최근 기간은 뉴스 필터(5년)와
    정렬된 days 범위로 제한한다. 본문은 raw_content로 함께 수집한다.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise requests.RequestException("TAVILY_API_KEY is not set")
    response = requests.post(
        TAVILY_SEARCH_URL,
        json={
            "api_key": api_key,
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": max(1, int(max_results)),
            "include_raw_content": True,
            "days": settings.tavily_news_max_age_days,
        },
        timeout=settings.skax_search_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def rewrite_search_queries(
    preprocessed_patent: dict[str, Any],
    *,
    missing_evidence: list[str] | None = None,
    previous_queries: list[str] | None = None,
    retry_count: int = 0,
    use_llm: bool = True,
) -> dict[str, Any]:
    previous = set(previous_queries or [])
    if not use_llm:
        raise RuntimeError("LLM query rewriting is required, but use_llm is disabled.")

    llm_result = llm_rewrite_search_queries(
        preprocessed_patent=preprocessed_patent,
        missing_evidence=missing_evidence or [],
        previous_queries=previous_queries or [],
        retry_count=retry_count,
    )
    rewritten_ko = [query for query in compact_queries(llm_result.get("ko", []))[:MAX_SEARCH_QUERIES] if query not in previous]
    rewritten_en = [query for query in compact_queries(llm_result.get("en", []))[:MAX_SEARCH_QUERIES] if query not in previous]

    rewritten_ko, product_query_enforced = ensure_related_product_query(
        rewritten_ko,
        preprocessed_patent,
        previous,
    )
    rewritten_ko, company_query_meta = ensure_applicant_context_queries(
        rewritten_ko,
        preprocessed_patent,
        previous,
    )

    rewritten_en = enforce_english_queries(
        rewritten_en,
        preprocessed_patent,
        fill_to=MAX_SEARCH_QUERIES,
    )
    rewritten_industry_rag = [
        query
        for query in compact_queries(llm_result.get("industry_rag", []))[:MAX_INDUSTRY_RAG_QUERIES]
        if query not in previous
    ]
    rewritten_skax_site = [
        query
        for query in normalize_skax_site_queries(llm_result.get("skax_site", []))[:MAX_SEARCH_QUERIES]
        if query not in previous
    ]

    return {
        "ko": rewritten_ko,
        "en": rewritten_en,
        "industry_rag": rewritten_industry_rag,
        "skax_site": rewritten_skax_site,
        "meta": {
            "rewrite_source": "llm",
            "llm_error": None,
            "product_query_enforced": product_query_enforced,
            **company_query_meta,
        },
    }


def collect_external_evidence(
    *,
    preprocessed_patent: dict[str, Any],
    patent_id: str | int | None = None,
    application_number: str | None = None,
    api_base_url: str | None = None,
    query_limit_per_axis: int = 1,
    include_naver: bool = True,
    include_gnews: bool = True,
    include_kipris: bool = False,
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
    news_results_per_query = max(1, int(settings.news_results_per_query))

    api_base_url = (api_base_url or settings.unified_api_base_url).rstrip("/")
    sources: list[list[dict[str, Any]]] = []
    saved_paths: list[str] = []
    warnings: list[str] = []

    if include_naver:
        for search_result in collect_news_queries_concurrently(
            api_base_url=api_base_url,
            queries=selected_queries,
            provider="naver",
            results_per_query=news_results_per_query,
            fetch_news_full_text=fetch_news_full_text,
        ):
            if search_result["warning"]:
                warnings.append(search_result["warning"])
                continue
            items = search_result["items"]
            sources.append(items)
            if save:
                path = save_evidence_collection(
                    source_type="news",
                    source="naver_news",
                    items=items,
                    query=search_result["query"],
                    patent_id=patent_id,
                    output_dir=output_dir or settings.output_dir / "api_evidence",
                )
                saved_paths.append(str(path))

    if include_gnews:
        for search_result in collect_news_queries_concurrently(
            api_base_url=api_base_url,
            queries=selected_gnews_queries,
            provider="gnews",
            results_per_query=news_results_per_query,
            fetch_news_full_text=fetch_news_full_text,
        ):
            if search_result["warning"]:
                warnings.append(search_result["warning"])
                continue
            items = search_result["items"]
            sources.append(items)
            if save:
                path = save_evidence_collection(
                    source_type="news",
                    source="gnews",
                    items=items,
                    query=search_result["query"],
                    patent_id=patent_id,
                    output_dir=output_dir or settings.output_dir / "api_evidence",
                )
                saved_paths.append(str(path))

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

    merged = merge_evidence_sources(sources, prefix="api")

    return {
        "ko_queries": ko_queries,
        "en_queries": en_queries,
        "queries": selected_queries,
        "gnews_queries": selected_gnews_queries,
        "rewrite_meta": rewrite_meta,
        "items": merged,
        "saved_collections": saved_paths,
        "warnings": warnings,
    }


def collect_news_queries_concurrently(
    *,
    api_base_url: str,
    queries: list[str],
    provider: str,
    results_per_query: int,
    fetch_news_full_text: bool,
) -> list[dict[str, Any]]:
    if not queries:
        return []

    def collect_query(query: str) -> dict[str, Any]:
        try:
            if provider == "naver":
                raw = request_json(
                    api_base_url,
                    "/api/news/search",
                    {"query": query, "display": results_per_query, "start": 1, "sort": "sim"},
                )
                items = normalize_naver_news_response(raw, query=query)
                source = "naver_news"
            elif provider == "gnews":
                raw = search_global_news_via_tavily(query, max_results=results_per_query)
                items = normalize_tavily_news_response(raw, query=query)
                source = "gnews"
            else:
                raise ValueError(f"Unknown news provider: {provider}")
            return {
                "query": query,
                "source": source,
                "items": enrich_news_items_with_full_text(items, enabled=fetch_news_full_text),
                "warning": None,
            }
        except requests.RequestException as exc:
            source = "naver_news" if provider == "naver" else "gnews"
            return {
                "query": query,
                "source": source,
                "items": [],
                "warning": f"{source} call failed for query '{query}': {exc}",
            }

    max_workers = min(DEFAULT_NEWS_SEARCH_WORKERS, len(queries))
    if max_workers <= 1:
        return [collect_query(query) for query in queries]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(collect_query, queries))


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


def ensure_related_product_query(
    queries: list[str],
    preprocessed_patent: dict[str, Any],
    previous_queries: set[str],
) -> tuple[list[str], bool]:
    product = extract_related_product(preprocessed_patent)
    if not product or any(product in query for query in queries):
        return queries, False

    candidates = [
        f"{product} 시장 동향",
        f"{product} 기술 적용",
        f"{product} 기업 동향",
        product,
    ]
    product_query = next(
        (
            query
            for query in candidates
            if query not in previous_queries and query not in queries
        ),
        None,
    )
    if not product_query:
        return queries, False

    if len(queries) < MAX_SEARCH_QUERIES:
        return compact_queries([*queries, product_query])[:MAX_SEARCH_QUERIES], True
    return compact_queries([*queries[: max(MAX_SEARCH_QUERIES - 1, 0)], product_query])[:MAX_SEARCH_QUERIES], True


def ensure_applicant_context_queries(
    queries: list[str],
    preprocessed_patent: dict[str, Any],
    previous_queries: set[str],
) -> tuple[list[str], dict[str, bool]]:
    product = extract_related_product(preprocessed_patent)
    owner, joint_applicant = extract_applicant_context(preprocessed_patent)
    required: list[tuple[str, str]] = []
    if owner:
        required.append(("owner_query_enforced", build_company_query(owner, product, preprocessed_patent)))
    if joint_applicant:
        required.append(("joint_applicant_query_enforced", build_company_query(joint_applicant, product, preprocessed_patent)))

    meta = {"owner_query_enforced": False, "joint_applicant_query_enforced": False}
    result = compact_queries(queries)
    additions: list[tuple[str, str]] = []
    for key, query in required:
        if not query or query in previous_queries:
            continue
        company = owner if key == "owner_query_enforced" else joint_applicant
        if company and any(company in existing for existing in result):
            continue
        additions.append((key, query))

    if not additions or MAX_SEARCH_QUERIES <= 0:
        return result[:MAX_SEARCH_QUERIES], meta

    while len(result) > max(MAX_SEARCH_QUERIES - len(additions), 0):
        result.pop()
    result = compact_queries([*result, *(query for _, query in additions)])[:MAX_SEARCH_QUERIES]
    for key, query in additions:
        meta[key] = any(query == existing for existing in result)
    return result, meta


def extract_applicant_context(preprocessed_patent: dict[str, Any]) -> tuple[str, str]:
    metadata = preprocessed_patent.get("metadata") or {}
    owner = normalize_company_name(first_non_empty(metadata.get("assignee")))
    if not owner:
        for key in ("applicant_name", "applicant", "right_holder", "owner", "company_name"):
            owner = normalize_company_name(metadata.get(key))
            if owner:
                break
    joint_applicant = ""
    if is_truthy_joint_application(metadata.get("joint_application")):
        joint_applicant = normalize_company_name(metadata.get("joint_applicant_name"))
    return owner, joint_applicant


def build_company_query(company: str, product: str, preprocessed_patent: dict[str, Any]) -> str:
    if product:
        return f"{company} {product}"
    metadata = preprocessed_patent.get("metadata") or {}
    for key in ("technology_area", "business_area", "title_final", "title"):
        value = normalize_related_product(metadata.get(key))
        if value:
            return f"{company} {value}"
    return company


def first_non_empty(value: Any) -> Any:
    if isinstance(value, list):
        return next((item for item in value if str(item or "").strip()), "")
    return value


def normalize_company_name(value: Any) -> str:
    company = " ".join(str(first_non_empty(value) or "").split())
    if not company:
        return ""
    if company.casefold() in {"n/a", "na", "none", "null", "-", "기타", "해당사항없음", "해당 사항 없음"}:
        return ""
    return company


def is_truthy_joint_application(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    return normalized in {"1", "true", "y", "yes", "공동", "공동출원"}


def extract_related_product(preprocessed_patent: dict[str, Any]) -> str:
    metadata = preprocessed_patent.get("metadata") or {}
    for source in (metadata, preprocessed_patent):
        if not isinstance(source, dict):
            continue
        for key in ("related_product", "related_products", "product", "product_name", "service_name"):
            value = source.get(key)
            if isinstance(value, list):
                value = next((item for item in value if str(item).strip()), "")
            product = normalize_related_product(value)
            if product:
                return product
    return ""


def normalize_related_product(value: Any) -> str:
    product = " ".join(str(value or "").split())
    if not product:
        return ""
    if product.casefold() in {"n/a", "na", "none", "null", "-", "기타", "해당사항없음", "해당 사항 없음"}:
        return ""
    return product


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
        time.sleep(0.5)
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


def contains_hangul(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value or ""))


def llm_rewrite_search_queries(
    *,
    preprocessed_patent: dict[str, Any],
    missing_evidence: list[str],
    previous_queries: list[str],
    retry_count: int = 0,
) -> dict[str, list[str]]:
    prompt_template = (
        load_prompt("evidence/query_rewriting.md")
        .replace("{{search_query_count}}", str(MAX_SEARCH_QUERIES))
        .replace("{{industry_rag_query_count}}", str(MAX_INDUSTRY_RAG_QUERIES))
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
        "retry_count": retry_count,
    }
    prompt = f"{prompt_template.strip()}\n\n입력 데이터(JSON):\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

    raw = call_llm(prompt)
    parsed = parse_query_rewrite_response(raw)
    if not parsed:
        raise RuntimeError("LLM query rewriting response was not valid JSON.")
    return parsed


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

    result: dict[str, list[str]] = {"ko": [], "en": [], "industry_rag": [], "skax_site": []}
    limits = {
        "ko": MAX_SEARCH_QUERIES,
        "en": MAX_SEARCH_QUERIES,
        "industry_rag": MAX_INDUSTRY_RAG_QUERIES,
        "skax_site": MAX_SEARCH_QUERIES,
    }
    for lang, limit in limits.items():
        section = parsed.get(lang)
        if isinstance(section, list):
            result[lang] = compact_queries([str(query) for query in section if str(query).strip()])[:limit]
        elif isinstance(section, dict):
            flattened: list[str] = []
            for value in section.values():
                if isinstance(value, list):
                    flattened.extend(str(query) for query in value if str(query).strip())
            result[lang] = compact_queries(flattened)[:limit]
    result["skax_site"] = normalize_skax_site_queries(result["skax_site"])[:MAX_SEARCH_QUERIES]
    return result


def normalize_skax_site_queries(queries: list[str]) -> list[str]:
    normalized_queries = []
    for query in compact_queries(queries):
        value = re.sub(r"\s+", " ", str(query or "")).strip()
        if not value:
            continue
        value = re.sub(r"^site\s*:\s*skax\.co\.kr\s*", "", value, flags=re.IGNORECASE).strip()
        normalized_queries.append(f"site:skax.co.kr {value}".strip())
    return compact_queries(normalized_queries)[:MAX_SEARCH_QUERIES]


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
