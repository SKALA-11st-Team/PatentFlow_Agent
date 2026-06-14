from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import ipaddress
import json
import os
import re
import socket
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
from services.evidence.news_filter_service import extract_keywords
from services.evidence.news_localization import DEFAULT_DOMESTIC_LANGUAGE
from services.evidence.store_service import (
    merge_evidence_sources,
    save_evidence_collection,
)


DEFAULT_UNIFIED_API_BASE_URL = settings.unified_api_base_url
MAX_SEARCH_QUERIES = settings.search_query_count
MAX_INDUSTRY_RAG_QUERIES = settings.industry_rag_query_count
# query rewriting(LLM)이 skax_site에 기여하는 변형 검색어 개수. 제품명 검색어는
# build_query_generation_plan의 rule-based 후보가 담당하므로, 여기서는 LLM 변형만 추린다.
SKAX_REWRITE_QUERY_COUNT = 2
API_REQUEST_MAX_ATTEMPTS = 3
API_REQUEST_RETRY_STATUS_CODES = {502, 503, 504}
BLOCKED_HOSTNAMES = {"localhost.localdomain", "metadata.google.internal"}
BLOCKED_LINK_LOCAL_IP = ipaddress.ip_address("169.254.169.254")
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def search_news_via_tavily(query: str, *, max_results: int, country: str | None = None) -> dict[str, Any]:
    """Tavily로 뉴스를 검색한다.

    country가 None이면 topic=news로 국가 제한 없이 전세계 뉴스를 가져온다(글로벌 사업성).
    country가 주어지면(해외특허 domestic 채널) 해당 국가 현지 결과로 한정한다. Tavily의
    `country` 파라미터는 topic=general에서만 동작하므로(문서상 "Available only if topic is
    general"), country 지정 시 topic=general로 호출한다. 최근성은 다운스트림 뉴스 필터(5년)가
    published_at 기준으로 거르고, 본문은 raw_content로 함께 수집한다.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise requests.RequestException("TAVILY_API_KEY is not set")
    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": max(1, int(max_results)),
        "include_raw_content": True,
    }
    if country:
        # country는 topic=general에서만 적용된다. days(news 전용)는 보내지 않는다.
        payload["topic"] = "general"
        payload["country"] = country
    else:
        payload["topic"] = "news"
        payload["days"] = settings.tavily_news_max_age_days
    response = requests.post(
        TAVILY_SEARCH_URL,
        json=payload,
        timeout=settings.skax_search_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def search_global_news_via_tavily(query: str, *, max_results: int) -> dict[str, Any]:
    """GNews 대체: 국가 제한 없는 글로벌 영어 뉴스 검색(하위호환 래퍼)."""
    return search_news_via_tavily(query, max_results=max_results, country=None)


def rewrite_search_queries(
    preprocessed_patent: dict[str, Any],
    *,
    missing_evidence: list[str] | None = None,
    previous_queries: list[str] | None = None,
    retry_count: int = 0,
    use_llm: bool = True,
    domestic_language: str = DEFAULT_DOMESTIC_LANGUAGE,
    is_foreign: bool = False,
) -> dict[str, Any]:
    previous = set(previous_queries or [])
    if not use_llm:
        raise RuntimeError("LLM query rewriting is required, but use_llm is disabled.")

    # AG-02: LLM 재작성 실패(비-JSON 응답·네트워크 오류 등)가 평가 전체를 500으로 만들지 않도록
    # 빈 결과로 폴백한다 — 아래 ensure_* 인젝터가 특허 메타데이터 기반 결정적 쿼리를 채워
    # degraded 상태로도 수집을 계속한다. 실패 사유는 meta.llm_error로 남겨 추적 가능하게 한다.
    llm_error: str | None = None
    try:
        llm_result = llm_rewrite_search_queries(
            preprocessed_patent=preprocessed_patent,
            missing_evidence=missing_evidence or [],
            previous_queries=previous_queries or [],
            retry_count=retry_count,
            domestic_language=domestic_language,
        )
    except Exception as exc:
        llm_result = {}
        llm_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
    if not isinstance(llm_result, dict):
        llm_result = {}
        llm_error = llm_error or "InvalidRewriteResult: LLM 재작성 결과가 객체가 아님"

    rewritten_domestic = [query for query in compact_queries(llm_result.get("domestic") or [])[:MAX_SEARCH_QUERIES] if query not in previous]
    rewritten_en = [query for query in compact_queries(llm_result.get("en") or [])[:MAX_SEARCH_QUERIES] if query not in previous]

    product_query_enforced = False
    company_query_meta = {"owner_query_enforced": False, "joint_applicant_query_enforced": False}
    if not is_foreign:
        # 한국어 전용 후처리(제품명 `… 시장 동향`, 회사명 쿼리)는 국내(KR) domestic 채널에만
        # 적용한다. 해외특허 domestic 채널은 현지어 쿼리이므로 한국어 접미사 주입을 스킵한다.
        rewritten_domestic, product_query_enforced = ensure_related_product_query(
            rewritten_domestic,
            preprocessed_patent,
            previous,
        )
        rewritten_domestic, company_query_meta = ensure_applicant_context_queries(
            rewritten_domestic,
            preprocessed_patent,
            previous,
        )

    rewritten_en = enforce_english_queries(rewritten_en)
    rewritten_industry_rag = [
        query
        for query in compact_queries(llm_result.get("industry_rag") or [])[:MAX_INDUSTRY_RAG_QUERIES]
        if query not in previous
    ]
    rewritten_skax_site = select_skax_rewrite_queries(
        llm_result.get("skax_site") or [],
        preprocessed_patent,
        previous,
    )

    return {
        "domestic": rewritten_domestic,
        "en": rewritten_en,
        "industry_rag": rewritten_industry_rag,
        "skax_site": rewritten_skax_site,
        "meta": {
            "rewrite_source": "fallback" if llm_error else "llm",
            "llm_error": llm_error,
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
    is_foreign: bool = False,
    domestic_country: str | None = None,
    missing_evidence: list[str] | None = None,
    previous_queries: list[str] | None = None,
    use_llm_rewrite: bool = True,
    domestic_queries_override: list[str] | None = None,
    en_queries_override: list[str] | None = None,
    fetch_news_full_text: bool = settings.fetch_news_full_text,
    output_dir: Path | str | None = None,
    save: bool = True,
) -> dict[str, Any]:
    rewrite_meta: dict[str, Any]
    if domestic_queries_override is not None or en_queries_override is not None:
        domestic_queries = compact_queries(domestic_queries_override or [])[:MAX_SEARCH_QUERIES]
        en_queries = enforce_english_queries(en_queries_override or [])
        rewrite_meta = {"rewrite_source": "precomputed", "llm_error": None}
    else:
        rewritten = rewrite_search_queries(
            preprocessed_patent,
            missing_evidence=missing_evidence,
            previous_queries=previous_queries,
            use_llm=use_llm_rewrite,
        )
        domestic_queries = rewritten["domestic"]
        en_queries = rewritten["en"]
        rewrite_meta = rewritten.get("meta") or {}
    safe_limit = max(1, int(query_limit_per_axis))
    selected_queries = domestic_queries[:safe_limit]
    selected_gnews_queries = en_queries[:safe_limit]
    news_results_per_query = max(1, int(settings.news_results_per_query))

    api_base_url = (api_base_url or settings.unified_api_base_url).rstrip("/")
    sources: list[list[dict[str, Any]]] = []
    saved_paths: list[str] = []
    warnings: list[str] = []
    rewrite_meta = {
        **rewrite_meta,
        "debug_include_naver": include_naver,
        "debug_include_gnews": include_gnews,
        "debug_include_kipris": include_kipris,
        "debug_domestic_queries_generated": domestic_queries,
        "debug_en_queries_generated": en_queries,
        "debug_selected_domestic_queries": selected_queries,
        "debug_selected_en_queries": selected_gnews_queries,
    }
    # EXT-03: 게이트웨이(:8080) 호출 시도/실패 횟수를 추적한다. 모든 시도가 실패하면 게이트웨이
    # 미기동·미도달로 간주해 '증거 0건'을 조용히 통과시키지 않고 hard-surface(missing_reason)한다.
    attempted_calls = 0
    failed_calls = 0

    # EVID-04: naver/global_news/kipris 소스 호출을 직렬 → 병렬화. 각 호출을 독립 태스크로 만들고
    # ThreadPoolExecutor로 동시 실행한 뒤 결과를 집계한다. attempted/failed 카운트(EXT-03 hard-surface)는
    # 태스크 수·실패 수로 동일하게 보존한다. sources/warnings/saved_paths는 순서 무관이라 병렬 안전.
    fetch_tasks: list[dict[str, Any]] = []
    if include_naver:
        # domestic 뉴스 채널: 국내(KR)는 게이트웨이 Naver News(한국어), 해외특허는
        # Tavily(country=대상국, 현지어)로 본국 현지 뉴스를 대체 수집한다.
        if not selected_queries:
            warnings.append("naver_news_skipped:no_domestic_queries_selected")
        for query in selected_queries:
            if is_foreign:
                fetch_tasks.append({
                    "fetch": lambda q=query: search_news_via_tavily(
                        q, max_results=news_results_per_query, country=domestic_country
                    ),
                    "normalize": lambda raw, q=query: normalize_tavily_news_response(
                        raw, query=q, source="domestic_news", country=domestic_country
                    ),
                    "enrich": True,
                    "source_type": "news",
                    "source": "domestic_news",
                    "query": query,
                    "warn_prefix": f"domestic_news call failed for query '{query}'",
                })
            else:
                fetch_tasks.append({
                    "fetch": lambda q=query: request_json(
                        api_base_url,
                        "/api/news/search",
                        {"query": q, "display": news_results_per_query, "start": 1, "sort": "sim"},
                    ),
                    "normalize": lambda raw, q=query: normalize_naver_news_response(raw, query=q),
                    "enrich": True,
                    "source_type": "news",
                    "source": "naver_news",
                    "query": query,
                    "warn_prefix": f"naver_news call failed for query '{query}'",
                })
    if include_gnews:
        if not selected_gnews_queries:
            warnings.append("global_news_skipped:no_en_queries_selected")
        # GNews 대체: 글로벌 뉴스는 게이트웨이 대신 Tavily(topic=news)를 직접 호출한다.
        for gnews_query in selected_gnews_queries:
            fetch_tasks.append({
                "fetch": lambda q=gnews_query: search_global_news_via_tavily(q, max_results=news_results_per_query),
                "normalize": lambda raw, q=gnews_query: normalize_tavily_news_response(raw, query=q),
                "enrich": True,
                "source_type": "news",
                "source": "global_news",
                "query": gnews_query,
                "warn_prefix": f"global_news call failed for query '{gnews_query}'",
            })
    if include_kipris and application_number and not is_foreign:
        # 경쟁특허 검색(`patent-utility/search/application-number`)은 국내 KR 특허 DB를 출원번호로
        # 조회하는 기능이라 해외특허(US/CN/JP)의 출원번호는 형식이 맞지 않아 거부된다(rc=10).
        # 해외특허의 인용/선행은 overseas 인용 엔드포인트를 타는 fetch_foreign_target_reference_data가
        # 담당하므로, 여기서는 국내 경쟁검색을 해외특허에 대해 스킵한다.
        kipris_query = f"application_number:{application_number}"
        fetch_tasks.append({
            "fetch": lambda: request_json(
                api_base_url,
                "/kipris/patent-utility/search/application-number",
                {"applicationNumber": application_number},
            ),
            "normalize": lambda raw, q=kipris_query: normalize_kipris_patent_results(raw, query=q, source="kipris"),
            "enrich": False,
            "source_type": "competitor_patent",
            "source": "kipris",
            "query": kipris_query,
            "warn_prefix": f"kipris call failed for application_number '{application_number}'",
        })

    attempted_calls = len(fetch_tasks)
    rewrite_meta["debug_fetch_task_sources"] = [task["source"] for task in fetch_tasks]
    rewrite_meta["debug_fetch_task_queries"] = [
        {"source": task["source"], "query": task["query"]}
        for task in fetch_tasks
    ]

    def _run_fetch_task(task: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = task["fetch"]()
            items = task["normalize"](raw)
            if task["enrich"]:
                items = enrich_news_items_with_full_text(items, enabled=fetch_news_full_text)
            saved = None
            if save:
                saved = str(save_evidence_collection(
                    source_type=task["source_type"],
                    source=task["source"],
                    items=items,
                    query=task["query"],
                    patent_id=patent_id,
                    output_dir=output_dir or settings.output_dir / "api_evidence",
                ))
            empty_warning = None
            if task["source"] in ("naver_news", "domestic_news") and not items:
                empty_warning = f"{task['source']}_empty_results:query='{task['query']}'"
            if task["source"] == "global_news" and not items:
                empty_warning = f"global_news_empty_results:query='{task['query']}'"
            return {"items": items, "saved": saved, "warning": None, "empty_warning": empty_warning}
        except Exception as exc:
            # AG-03: 네트워크 오류(RequestException)만이 아니라 외부 응답 형태 드리프트로
            # normalize/enrich가 던지는 TypeError·KeyError 등도 수집 실패로 집계한다 —
            # 한 소스의 페이로드 변형이 평가 전체를 500으로 만들지 않게 한다(EXT-03 경고 의미 보존).
            return {
                "items": None,
                "saved": None,
                "warning": f"{task['warn_prefix']}: {exc.__class__.__name__}: {exc}",
                "empty_warning": None,
            }

    if fetch_tasks:
        max_workers = max(1, min(len(fetch_tasks), settings.evidence_fetch_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            task_results = list(executor.map(_run_fetch_task, fetch_tasks))
        for outcome in task_results:
            if outcome["warning"] is not None:
                failed_calls += 1
                warnings.append(outcome["warning"])
            else:
                sources.append(outcome["items"])
                if outcome["saved"]:
                    saved_paths.append(outcome["saved"])
                if outcome["empty_warning"]:
                    warnings.append(outcome["empty_warning"])

    merged = merge_evidence_sources(sources, prefix="api")
    quality = annotate_evidence_quality(merged, preprocessed_patent=preprocessed_patent)
    warnings.extend(quality["warnings"])

    # EXT-03: 시도한 모든 게이트웨이 호출이 실패했고 수집 증거가 0건이면, 정상적인 '근거 없음'이
    # 아니라 게이트웨이 미도달로 본다. missing_reason으로 표면화해 하위 단계가 무근거 평가를
    # 정상 점수로 오인하지 않게 한다.
    gateway_unreachable = attempted_calls > 0 and failed_calls == attempted_calls
    missing_reason = None
    if gateway_unreachable and not merged:
        missing_reason = f"external_gateway_failed:all_{attempted_calls}_calls_failed"
        warnings.append(
            f"external_evidence_unavailable: {failed_calls}/{attempted_calls} gateway calls failed; "
            "외부 근거 0건이 게이트웨이 미도달일 수 있음"
        )

    return {
        "domestic_queries": domestic_queries,
        "en_queries": en_queries,
        "queries": selected_queries,
        "gnews_queries": selected_gnews_queries,
        "rewrite_meta": rewrite_meta,
        "items": merged,
        "saved_collections": saved_paths,
        "warnings": warnings,
        "attempted_calls": attempted_calls,
        "failed_calls": failed_calls,
        "gateway_unreachable": gateway_unreachable,
        "missing_reason": missing_reason,
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


def select_skax_rewrite_queries(
    llm_queries: list[str],
    preprocessed_patent: dict[str, Any],
    previous_queries: set[str],
) -> list[str]:
    """query rewriting이 skax_site에 넘길 LLM 변형 검색어를 최대 SKAX_REWRITE_QUERY_COUNT개 추린다.

    제품명 그대로의 검색어는 build_query_generation_plan의 rule-based 후보가 이미 1순위로
    포함하므로(중복 방지), 여기서는 제품명을 따로 주입하지 않고 LLM이 만든 기술/서비스 변형만
    남긴다. LLM 변형이 build_query_generation_plan에서 rule-based보다 우선 배치되어 실제 검색에
    반영된다. 제품명과 동일한 변형·이전 라운드 검색어는 제외한다.
    """
    product_norm = normalize_related_product(extract_related_product(preprocessed_patent))
    variant_source = [
        query
        for query in llm_queries
        if not product_norm or normalize_related_product(query) != product_norm
    ]
    variants = [
        query
        for query in normalize_skax_site_queries(variant_source)
        if query not in previous_queries
    ]
    return compact_queries(variants)[:SKAX_REWRITE_QUERY_COUNT]


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
    if not product_query and not queries:
        # Naver News가 완전히 비는 상황은 시장성 축을 과도하게 약화시키므로,
        # 재검색 라운드에서 이전 쿼리와 중복되더라도 최소 1개의 제품명 기반 한국어 검색어는 유지한다.
        product_query = candidates[0]
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
    validate_unified_api_base_url(base_url)
    url = f"{base_url.rstrip('/')}{path}"
    last_error: requests.RequestException | None = None
    for attempt in range(1, API_REQUEST_MAX_ATTEMPTS + 1):
        # EVID-04: 첫 시도 고정 0.5s 지연 제거 — 성공 호출이 매번 0.5s를 물던 문제. 백오프는 재시도 직전(아래)에만.
        try:
            response = requests.get(url, params=params, headers=unified_api_headers(), timeout=timeout)
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


def _is_blocked_gateway_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # 게이트웨이는 사설망/로컬(unified-api:8080, localhost:8080, 10.x 등)에 정상 배치되므로
    # loopback·사설은 허용한다(기사 URL 가드와 달리 차단하지 않는다). 단 순서가 중요하다:
    # 1) loopback(127.x, ::1) 허용.
    if ip.is_loopback:
        return False
    # 2) 클라우드 메타데이터(link-local 169.254.0.0/16)는 Python에서 is_private이기도 하므로 사설 허용보다 먼저 차단.
    if ip.is_link_local or ip.is_multicast or ip == BLOCKED_LINK_LOCAL_IP:
        return True
    # 3) 사설망(10/172.16/192.168, fd00::)은 게이트웨이 정상 배치라 허용.
    if ip.is_private:
        return False
    # 4) 그 외 예약 대역만 차단(공인 IP 게이트웨이는 SSRF 대상이 아니므로 허용).
    return bool(ip.is_reserved)


def validate_unified_api_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise requests.RequestException(f"blocked_unified_api_base_url: invalid URL '{base_url}'")
    if parsed.username or parsed.password:
        raise requests.RequestException("blocked_unified_api_base_url: userinfo is not allowed")
    host = parsed.hostname.strip().lower()
    if host in BLOCKED_HOSTNAMES:
        raise requests.RequestException(f"blocked_unified_api_base_url: blocked host '{host}'")
    # 호스트가 리터럴 IP면 그대로, 도메인명이면 DNS로 해석한 모든 IP를 검사한다.
    # (A레코드가 메타데이터/link-local로 해석되는 도메인을 통한 DNS rebinding SSRF 차단.)
    try:
        candidate_ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, None)
        except socket.gaierror:
            # 해석 실패는 호출 시점 ConnectionError로 자연 실패한다(게이트웨이는 운영자 설정값이라 fail-open 허용).
            return
        candidate_ips = []
        for entry in resolved:
            try:
                candidate_ips.append(ipaddress.ip_address(entry[4][0]))
            except ValueError:
                continue
    for ip in candidate_ips:
        if _is_blocked_gateway_ip(ip):
            raise requests.RequestException(
                f"blocked_unified_api_base_url: metadata/link-local IP is not allowed ({ip})"
            )


def unified_api_headers() -> dict[str, str]:
    api_key = os.getenv("UNIFIED_API_KEY")
    return {"X-API-Key": api_key} if api_key else {}


def annotate_evidence_quality(
    items: list[dict[str, Any]],
    *,
    preprocessed_patent: dict[str, Any],
) -> dict[str, list[str]]:
    reference_text = evidence_reference_text(preprocessed_patent)
    patent_keywords = extract_keywords(reference_text)
    warnings: list[str] = []
    if not patent_keywords:
        warnings.append("evidence_quality_keywords_unavailable")
        return {"warnings": warnings}

    for item in items:
        text = " ".join(str(item.get(key) or "") for key in ("title", "content"))
        matched_keywords = sorted(extract_keywords(text) & patent_keywords)
        metadata = item.setdefault("metadata", {})
        metadata["matched_keywords"] = matched_keywords
        metadata["matched_keyword_count"] = len(matched_keywords)
        if not matched_keywords:
            metadata["quality_warning"] = "no_patent_keyword_match"
            warnings.append(f"{item.get('evidence_id') or 'unknown'}:evidence_quality_low:no_patent_keyword_match")
        elif len(matched_keywords) == 1:
            metadata["quality_warning"] = "weak_patent_keyword_match"
            warnings.append(f"{item.get('evidence_id') or 'unknown'}:evidence_quality_weak:single_keyword_match")
    return {"warnings": compact_queries(warnings)}


def evidence_reference_text(preprocessed_patent: dict[str, Any]) -> str:
    metadata = preprocessed_patent.get("metadata") or {}
    sections = preprocessed_patent.get("sections") or {}
    parts: list[str] = []
    for key in (
        "title",
        "title_final",
        "title_eng",
        "related_product",
        "technology_area",
        "business_area",
        "ipc",
        "cpc",
    ):
        value = metadata.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))
    for key in ("abstract", "technical_field", "problem", "solution", "effect"):
        if sections.get(key):
            parts.append(str(sections[key]))
    return "\n".join(parts)


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
    domestic_language: str = DEFAULT_DOMESTIC_LANGUAGE,
) -> dict[str, list[str]]:
    prompt_template = (
        load_prompt("evidence/query_rewriting.md")
        .replace("{{search_query_count}}", str(MAX_SEARCH_QUERIES))
        .replace("{{industry_rag_query_count}}", str(MAX_INDUSTRY_RAG_QUERIES))
        .replace("{{domestic_language}}", domestic_language)
    )
    payload = {
        "metadata": preprocessed_patent.get("metadata") or {},
        "sections": {
            "abstract": (preprocessed_patent.get("sections") or {}).get("abstract") or "",
            "technical_field": (preprocessed_patent.get("sections") or {}).get("technical_field") or "",
            "problem": (preprocessed_patent.get("sections") or {}).get("problem") or "",
            "solution": (preprocessed_patent.get("sections") or {}).get("solution") or "",
        },
        "domestic_news_language": domestic_language,
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

    result: dict[str, list[str]] = {"domestic": [], "en": [], "industry_rag": [], "skax_site": []}
    limits = {
        "domestic": MAX_SEARCH_QUERIES,
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


def enforce_english_queries(queries: list[str]) -> list[str]:
    # EVID-14: preprocessed_patent·fill_to는 호출부에서 전달됐으나 내부에서 즉시 del되던 데드 파라미터였다 — 제거.
    return compact_queries(
        [normalize_gnews_query(query) for query in queries if query and not contains_hangul(query)]
    )[:MAX_SEARCH_QUERIES]


def normalize_gnews_query(query: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\s]", " ", query)
    return " ".join(normalized.split())
