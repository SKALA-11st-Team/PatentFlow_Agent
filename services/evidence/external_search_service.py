from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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
    normalize_dart_disclosures,
    normalize_gnews_response,
    normalize_kipris_patent_results,
    normalize_naver_news_response,
)
from services.evidence.news_article_extraction_service import enrich_news_items_with_full_text
from services.evidence.news_filter_service import extract_keywords
from services.evidence.store_service import (
    merge_evidence_sources,
    save_evidence_collection,
)


DEFAULT_UNIFIED_API_BASE_URL = settings.unified_api_base_url
MAX_SEARCH_QUERIES = settings.search_query_count
MAX_INDUSTRY_RAG_QUERIES = settings.industry_rag_query_count
API_REQUEST_MAX_ATTEMPTS = 3
API_REQUEST_RETRY_STATUS_CODES = {502, 503, 504}
BLOCKED_HOSTNAMES = {"localhost.localdomain", "metadata.google.internal"}
BLOCKED_LINK_LOCAL_IP = ipaddress.ip_address("169.254.169.254")


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

    rewritten_en = enforce_english_queries(rewritten_en)
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
        en_queries = enforce_english_queries(en_queries_override or [])
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
    # EXT-03: 게이트웨이(:8080) 호출 시도/실패 횟수를 추적한다. 모든 시도가 실패하면 게이트웨이
    # 미기동·미도달로 간주해 '증거 0건'을 조용히 통과시키지 않고 hard-surface(missing_reason)한다.
    attempted_calls = 0
    failed_calls = 0

    # EVID-04: naver/gnews/kipris/dart 소스 호출을 직렬 → 병렬화. 각 호출을 독립 태스크로 만들고
    # ThreadPoolExecutor로 동시 실행한 뒤 결과를 집계한다. attempted/failed 카운트(EXT-03 hard-surface)는
    # 태스크 수·실패 수로 동일하게 보존한다. sources/warnings/saved_paths는 순서 무관이라 병렬 안전.
    fetch_tasks: list[dict[str, Any]] = []
    if include_naver:
        for query in selected_queries:
            fetch_tasks.append({
                "path": "/api/news/search",
                "params": {"query": query, "display": news_results_per_query, "start": 1, "sort": "sim"},
                "normalize": lambda raw, q=query: normalize_naver_news_response(raw, query=q),
                "enrich": True,
                "source_type": "news",
                "source": "naver_news",
                "query": query,
                "warn_prefix": f"naver_news call failed for query '{query}'",
            })
    if include_gnews:
        for gnews_query in selected_gnews_queries:
            fetch_tasks.append({
                "path": "/api/v4/search",
                "params": {"q": gnews_query, "lang": "en", "max": news_results_per_query, "page": 1},
                "normalize": lambda raw, q=gnews_query: normalize_gnews_response(raw, query=q),
                "enrich": True,
                "source_type": "news",
                "source": "gnews",
                "query": gnews_query,
                "warn_prefix": f"gnews call failed for query '{gnews_query}'",
            })
    if include_kipris and application_number:
        kipris_query = f"application_number:{application_number}"
        fetch_tasks.append({
            "path": "/kipris/patent-utility/search/application-number",
            "params": {"applicationNumber": application_number},
            "normalize": lambda raw, q=kipris_query: normalize_kipris_patent_results(raw, query=q, source="kipris"),
            "enrich": False,
            "source_type": "competitor_patent",
            "source": "kipris",
            "query": kipris_query,
            "warn_prefix": f"kipris call failed for application_number '{application_number}'",
        })
    if dart_corp_code:
        bgn_de, end_de = resolve_dart_date_range(dart_bgn_de, dart_end_de)
        dart_query = f"corp_code:{dart_corp_code}"
        fetch_tasks.append({
            "path": "/dart/disclosure",
            "params": {"corp_code": dart_corp_code, "bgn_de": bgn_de, "end_de": end_de},
            "normalize": lambda raw, q=dart_query: normalize_dart_disclosures(raw, query=q),
            "enrich": False,
            "source_type": "company_disclosure",
            "source": "dart",
            "query": dart_query,
            "warn_prefix": f"dart call failed for corp_code '{dart_corp_code}'",
        })

    attempted_calls = len(fetch_tasks)

    def _run_fetch_task(task: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = request_json(api_base_url, task["path"], task["params"])
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
            return {"items": items, "saved": saved, "warning": None}
        except requests.RequestException as exc:
            return {"items": None, "saved": None, "warning": f"{task['warn_prefix']}: {exc}"}

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
        "ko_queries": ko_queries,
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


def enforce_english_queries(queries: list[str]) -> list[str]:
    # EVID-14: preprocessed_patent·fill_to는 호출부에서 전달됐으나 내부에서 즉시 del되던 데드 파라미터였다 — 제거.
    return compact_queries(
        [normalize_gnews_query(query) for query in queries if query and not contains_hangul(query)]
    )[:MAX_SEARCH_QUERIES]


def normalize_gnews_query(query: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\s]", " ", query)
    return " ".join(normalized.split())
