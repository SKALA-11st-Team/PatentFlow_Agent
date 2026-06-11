"""KIPRIS, NAVER, GNews를 한 서버에서 제공하는 통합 FastAPI.

실행:
  python -m uvicorn open_api.api_server:app --reload
"""
from __future__ import annotations

import hmac
import logging
import os
import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any

import requests
import yaml
from dotenv import load_dotenv

from open_api.secret_scrub import scrub_secrets
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response

try:
    from .kipris_client import KiprisClient, KiprisError, OVERSEAS_PATENT_SERVICE_PATH, PAT_UTI_SERVICE_PATH
except ImportError:
    from kipris_client import KiprisClient, KiprisError, OVERSEAS_PATENT_SERVICE_PATH, PAT_UTI_SERVICE_PATH

load_dotenv()

ROOT = Path(__file__).resolve().parent
OPENAPI_FILES = (
    ROOT / "kipris_all_open_api.yaml",
    ROOT / "naver_open_api.yaml",
    ROOT / "gnews_open_api.yaml",
)

app = FastAPI(
    title="Unified API Server (KIPRIS + NAVER + GNews)",
    version="1.0.0",
    description="4개 API를 하나의 api_server.py에서 실행하고 문서도 하나로 합칩니다.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("UNIFIED_API_CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_gateway_logger = logging.getLogger("patentflow.gateway")
if not os.getenv("UNIFIED_API_KEY"):
    # SEC-01: 키 미설정 시 게이트웨이가 인증 없이 동작하는 fail-open을 운영자가 인지하도록 경고를 남긴다
    # (개발/compose 편의는 유지하되, 운영 배포 시 UNIFIED_API_KEY 설정을 강제하는 신호).
    _gateway_logger.warning(
        "UNIFIED_API_KEY 미설정 — 게이트웨이가 인증 없이 동작합니다(개발 모드). 운영 배포에선 반드시 설정하세요."
    )


@app.middleware("http")
async def require_api_key(request: Request, call_next: Any) -> Any:
    expected = os.getenv("UNIFIED_API_KEY")
    if not expected or request.method == "OPTIONS" or request.url.path in {"/", "/docs", "/redoc", "/openapi.json"}:
        return await call_next(request)
    provided = request.headers.get("X-API-Key")
    # SEC-01: 타이밍 공격 방지를 위해 상수시간 비교(hmac.compare_digest). provided가 없으면 즉시 거부.
    if not provided or not hmac.compare_digest(provided, expected):
        return JSONResponse(status_code=401, content={"detail": "유효한 X-API-Key 헤더가 필요합니다."})
    return await call_next(request)


# SEC-01: 게이트웨이는 외부 유료키(KIPRIS/DART/NAVER)를 태우므로 IP 단위 인프로세스 슬라이딩 윈도우
# 레이트리밋으로 남용을 막는다(게이트웨이는 단일 파드라 인메모리로 충분). per-minute<=0이면 비활성.
_rate_limit_windows: dict[str, deque[float]] = {}
_rate_limit_lock = Lock()
_RATE_EXEMPT_PATHS = {"/", "/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def rate_limit(request: Request, call_next: Any) -> Any:
    try:
        per_minute = int(os.getenv("UNIFIED_API_RATELIMIT_PER_MINUTE", "120"))
    except ValueError:
        per_minute = 120
    if per_minute <= 0 or request.method == "OPTIONS" or request.url.path in _RATE_EXEMPT_PATHS:
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    cutoff = now - 60.0
    with _rate_limit_lock:
        window = _rate_limit_windows.setdefault(client_ip, deque())
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= per_minute:
            return JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."},
            )
        window.append(now)
    return await call_next(request)


def _client(service_key: str | None = None) -> KiprisClient:
    try:
        return KiprisClient(service_key=service_key)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _kipris_http(fn: Any) -> Any:
    try:
        return fn()
    except KiprisError as exc:
        raise HTTPException(status_code=502, detail=_sanitize_external_error(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=_sanitize_external_error(exc)) from exc


def _strip_service_key(q: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in q.items() if k not in ("ServiceKey", "serviceKey")}


def _service_key_from_query(q: dict[str, Any]) -> str | None:
    value = q.get("ServiceKey") or q.get("serviceKey")
    return str(value) if value else None


def _access_key_from_query(q: dict[str, Any]) -> str | None:
    value = q.get("accessKey")
    return str(value) if value else None


def _kipris_request_context(request: Request) -> tuple[KiprisClient, dict[str, str]]:
    query = _query_as_single_dict(request)
    return _client(_service_key_from_query(query)), _strip_service_key(query)


def _query_as_single_dict(request: Request) -> dict[str, str]:
    return dict(request.query_params)


def _coerce_patent_search_query(q: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = dict(q)
    for key in ("patent", "utility", "descSort"):
        if key in out and isinstance(out[key], str):
            out[key] = out[key].lower() in ("true", "1", "yes", "y")
    for key in ("pageNo", "numOfRows", "docsStart", "docsCount"):
        if key in out and out[key] != "":
            try:
                out[key] = int(out[key])
            except ValueError:
                pass
    return out


def _evaluation_snapshot_openapi_shape(client: KiprisClient, application_number: str) -> dict[str, Any]:
    detail = client.bibliography_detail(application_number)
    family_patents = client.family_patents(application_number)
    pdf = client.publication_fulltext_pdf_path(application_number)
    return {
        "applicationNumber": application_number,
        "source": "KIPRISPlus",
        "kipris": {
            "bibliographyDetail": detail,
            "familyPatents": [
                {
                    "countryCode": family.country_code,
                    "registrationNumber": family.registration_number,
                }
                for family in family_patents
            ],
            "publicFullTextPdf": {"docName": pdf.doc_name, "path": pdf.path},
        },
        "externalData": {},
    }


def _merge_schema_dict(target: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if key not in target:
            target[key] = value
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_schema_dict(target[key], value)
        elif target[key] != value:
            # EXT-11: 이름 충돌 시 기존(main 우선) 유지하되, 조용히 폐기하지 않고 경고를 남겨 누락을 표면화한다.
            _gateway_logger.warning("OpenAPI 스키마 병합 충돌 — 키 '%s'의 보조 정의를 폐기하고 main 우선 유지", key)


def _infer_missing_tag(path_key: str) -> str:
    if path_key.startswith("/api/news/naver") or path_key.startswith("/api/news/search"):
        return "NAVER News"
    if path_key.startswith("/api/news/gnews") or path_key.startswith("/api/v4/"):
        return "GNews"
    if path_key.startswith("/kipris/evaluation-data"):
        return "Evaluation Data Model"
    if path_key.startswith("/kipris/patent-utility/admin-history"):
        return "Admin History"
    if path_key.startswith("/kipris/patent-utility/detail"):
        return "Patent Detail"
    if path_key.startswith("/kipris/patent-utility/documents") or path_key.startswith("/kipo-api/"):
        return "Patent Documents"
    if path_key.startswith("/kipris/overseas-patent"):
        return "Overseas Citation"
    if path_key.startswith("/kipris/patent-utility/search/applicant") or path_key.startswith(
        "/kipris/patent-utility/search/right-holder"
    ):
        return "Applicant Search"
    if path_key.startswith("/kipris/patent-utility/search/ipc") or path_key.startswith(
        "/kipris/patent-utility/search/cpc"
    ):
        return "Classification Search"
    if path_key.startswith("/kipris/patent-utility/search/"):
        return "Patent Search"
    if path_key == "/":
        return "System"
    return "Misc"


def _load_merged_openapi() -> dict[str, Any]:
    merged: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": app.title,
            "version": app.version,
            "description": app.description,
        },
        "servers": [
            {
                "url": os.getenv("UNIFIED_OPENAPI_SERVER_URL", "http://127.0.0.1:8000"),
                "description": "Local unified backend server",
            }
        ],
        "paths": {},
        "components": {},
        "tags": [],
    }
    seen_tags: set[str] = set()

    for path in OPENAPI_FILES:
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as handle:
            schema = yaml.safe_load(handle) or {}

        _merge_schema_dict(merged["paths"], schema.get("paths", {}))
        _merge_schema_dict(merged["components"], schema.get("components", {}))

        for tag in schema.get("tags", []):
            if isinstance(tag, dict):
                tag_name = str(tag.get("name", "")).strip()
                if tag_name and tag_name not in seen_tags:
                    merged["tags"].append(tag)
                    seen_tags.add(tag_name)

    for path_key, path_item in merged.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        fallback_tag = _infer_missing_tag(path_key)
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch", "options", "head"}:
                continue
            if isinstance(operation, dict) and not operation.get("tags"):
                operation["tags"] = [fallback_tag]

    return merged


@app.get("/", tags=["System"])
def root() -> dict[str, Any]:
    return {
        "status": "ok",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "services": ["kipris", "naver", "gnews"],
    }


# -------------------------
# NAVER / GNEWS
# -------------------------
@app.get("/api/news/search", tags=["NAVER News"])
def naver_news_search(
    query: str = Query(..., description="검색어"),
    display: int = Query(10, ge=1, le=100, description="검색 결과 개수"),
    start: int = Query(1, ge=1, le=1000, description="검색 시작 위치"),
    sort: str = Query("sim", pattern="^(sim|date)$", description="정렬 방식"),
) -> Any:
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="NAVER API 키가 설정되지 않았습니다.")

    try:
        res = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
            params={"query": query, "display": display, "start": start, "sort": sort},
            timeout=15,
        )
        res.raise_for_status()
        return res.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"NAVER 요청 실패: {exc}") from exc


@app.get("/api/news/naver/search", tags=["NAVER News"])
def naver_news_search_alias(
    query: str = Query(...),
    display: int = Query(10, ge=1, le=100),
    start: int = Query(1, ge=1, le=1000),
    sort: str = Query("sim", pattern="^(sim|date)$"),
) -> Any:
    return naver_news_search(query=query, display=display, start=start, sort=sort)


@app.get("/api/v4/search", tags=["GNews"])
def gnews_search(
    q: str = Query(..., description="검색어"),
    lang: str | None = Query(None, description="언어 코드"),
    country: str | None = Query(None, description="국가 코드"),
    max: int = Query(10, ge=1, le=100, description="반환 기사 수"),
    from_: str | None = Query(None, alias="from", description="시작 발행일 ISO 8601"),
    to: str | None = Query(None, description="종료 발행일 ISO 8601"),
    sortby: str | None = Query(None, pattern="^(publishedAt|relevance)$"),
    page: int = Query(1, ge=1, description="페이지 번호"),
) -> Any:
    api_key = os.getenv("GNEWS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GNEWS_API_KEY가 설정되지 않았습니다.")

    params: dict[str, Any] = {"q": q, "apikey": api_key, "max": max, "page": page}
    if lang:
        params["lang"] = lang
    if country:
        params["country"] = country
    if from_:
        params["from"] = from_
    if to:
        params["to"] = to
    if sortby:
        params["sortby"] = sortby

    try:
        res = requests.get("https://gnews.io/api/v4/search", params=params, timeout=15)
        res.raise_for_status()
        return res.json()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(
            status_code=status_code,
            detail=f"GNews 요청 실패: {_sanitize_external_error(exc)}",
        ) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"GNews 요청 실패: {_sanitize_external_error(exc)}") from exc


@app.get("/api/news/gnews/search", tags=["GNews"])
def gnews_search_alias(
    q: str = Query(...),
    lang: str | None = Query(None),
    country: str | None = Query(None),
    max: int = Query(10, ge=1, le=100),
    page: int = Query(1, ge=1),
    sortby: str | None = Query(None, pattern="^(publishedAt|relevance)$"),
) -> Any:
    return gnews_search(q=q, lang=lang, country=country, max=max, page=page, sortby=sortby)


def _sanitize_external_error(exc: Exception) -> str:
    # EXT-10: 시크릿 마스킹은 공용 모듈(secret_scrub)로 일원화한다(에이전트 KiprisError 경로와 동일 로직).
    return scrub_secrets(str(exc))


# -------------------------
# KIPRIS
# -------------------------
@app.get("/kipris/patent-utility/search/advanced", tags=["KIPRIS Patent Search"])
def get_advanced_search(request: Request) -> Any:
    client, raw_query = _kipris_request_context(request)
    query = _coerce_patent_search_query(raw_query)
    return _kipris_http(lambda: client.advanced_search(**query))


@app.get("/kipris/patent-utility/search/application-number", tags=["KIPRIS Patent Search"])
def application_number_search_info(request: Request) -> Any:
    client, raw_query = _kipris_request_context(request)
    query = _coerce_patent_search_query(raw_query)
    app_no = query.pop("applicationNumber", None)
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    return _kipris_http(lambda: client.search_by_application_number(str(app_no), **query))


@app.get("/kipris/patent-utility/detail/bibliography", tags=["KIPRIS Patent Detail"])
def get_bibliography_detail_info_search(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    return _kipris_http(lambda: client.bibliography_detail(str(app_no)))


@app.get("/kipris/patent-utility/detail/family", tags=["KIPRIS Patent Detail"])
def family_info_v2(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    application_number = str(app_no)
    family_patents = _kipris_http(lambda: client.family_patents(application_number))
    return {
        "applicationNumber": application_number,
        "familyPatents": [
            {
                "countryCode": family.country_code,
                "registrationNumber": family.registration_number,
            }
            for family in family_patents
        ],
    }


@app.get("/kipris/patent-utility/search/ipc", tags=["KIPRIS Classification"])
def ipc_search_info(request: Request) -> Any:
    client, raw_query = _kipris_request_context(request)
    query = _coerce_patent_search_query(raw_query)
    ipc = query.pop("ipcNumber", None)
    if not ipc:
        raise HTTPException(status_code=400, detail="ipcNumber는 필수입니다.")
    return _kipris_http(lambda: client.search_by_ipc(str(ipc), **query))


@app.get("/kipris/patent-utility/search/cpc", tags=["KIPRIS Classification"])
def cpc_search_info(request: Request) -> Any:
    client, raw_query = _kipris_request_context(request)
    query = _coerce_patent_search_query(raw_query)
    cpc = query.pop("cpcNumber", None)
    if not cpc:
        raise HTTPException(status_code=400, detail="cpcNumber는 필수입니다.")
    return _kipris_http(lambda: client.search_by_cpc(str(cpc), **query))


@app.get("/kipris/patent-utility/search/applicant", tags=["KIPRIS Applicant Search"])
def applicant_name_search_info(request: Request) -> Any:
    client, raw_query = _kipris_request_context(request)
    query = _coerce_patent_search_query(raw_query)
    applicant = query.pop("applicant", None)
    if not applicant:
        raise HTTPException(status_code=400, detail="applicant는 필수입니다.")
    return _kipris_http(lambda: client.search_by_applicant(str(applicant), **query))


@app.get("/kipris/patent-utility/search/right-holder", tags=["KIPRIS Applicant Search"])
def right_holer_search_info(request: Request) -> Any:
    client, raw_query = _kipris_request_context(request)
    query = _coerce_patent_search_query(raw_query)
    right_holer = query.pop("rightHoler", None)
    if not right_holer:
        raise HTTPException(status_code=400, detail="rightHoler는 필수입니다.")
    return _kipris_http(lambda: client.search_by_right_holder(str(right_holer), **query))


@app.get("/kipris/patent-utility/admin-history/transfers", tags=["KIPRIS Admin History"])
def transfer_list_info(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    for key in ("kind", "searchRight", "transferDate"):
        if key not in query or not query[key]:
            raise HTTPException(status_code=400, detail=f"{key}는 필수입니다.")
    return _kipris_http(
        lambda: client.transfer_list_info(
            kind=query["kind"],
            search_right=query["searchRight"],
            transfer_date=query["transferDate"],
        )
    )


@app.get("/kipris/overseas-patent/bibliography/claims", tags=["KIPRIS Overseas Citation"])
def demand_paragraph_info(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    literature_number = query.get("literatureNumber")
    country_code = query.get("countryCode")
    if not literature_number or not country_code:
        raise HTTPException(status_code=400, detail="literatureNumber와 countryCode는 필수입니다.")
    return _kipris_http(lambda: client.overseas_demand_paragraph(str(literature_number), str(country_code)))


@app.get("/kipris/overseas-patent/citations/domestic-documents", tags=["KIPRIS Overseas Citation"])
def us_patent_documents_info(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    literature_number = query.get("literatureNumber")
    country_code = query.get("countryCode")
    if not literature_number or not country_code:
        raise HTTPException(status_code=400, detail="literatureNumber와 countryCode는 필수입니다.")
    return _kipris_http(
        lambda: client.overseas_us_patent_documents(str(literature_number), str(country_code))
    )


@app.get("/kipris/overseas-patent/citations/foreign-documents", tags=["KIPRIS Overseas Citation"])
def foreign_patent_documents_info(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    literature_number = query.get("literatureNumber")
    country_code = query.get("countryCode")
    if not literature_number or not country_code:
        raise HTTPException(status_code=400, detail="literatureNumber와 countryCode는 필수입니다.")
    return _kipris_http(
        lambda: client.overseas_foreign_patent_documents(str(literature_number), str(country_code))
    )


@app.get("/kipris/overseas-patent/documents/open-fulltext", tags=["KIPRIS Overseas Documents"])
def overseas_open_fulltext_info(request: Request) -> Any:
    query = _query_as_single_dict(request)
    literature_number = query.get("literatureNumber")
    country_code = query.get("countryCode")
    if not literature_number or not country_code:
        raise HTTPException(status_code=400, detail="literatureNumber와 countryCode는 필수입니다.")
    client = _client(_access_key_from_query(query))
    return _kipris_http(lambda: client.overseas_open_fulltext(str(literature_number), str(country_code)))


@app.get("/kipris/overseas-patent/documents/registration-fulltext", tags=["KIPRIS Overseas Documents"])
def overseas_registration_fulltext_info(request: Request) -> Any:
    query = _query_as_single_dict(request)
    literature_number = query.get("literatureNumber")
    country_code = query.get("countryCode")
    if not literature_number or not country_code:
        raise HTTPException(status_code=400, detail="literatureNumber와 countryCode는 필수입니다.")
    client = _client(_access_key_from_query(query))
    return _kipris_http(lambda: client.overseas_registration_fulltext(str(literature_number), str(country_code)))


@app.get("/openapi/rest/CitationService/citationInfoV3", tags=["KIPRIS Patent Citation"])
def citation_info_v3(request: Request) -> Any:
    query = _query_as_single_dict(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    client = _client(_access_key_from_query(query))
    return _kipris_http(lambda: client.citation_info_v3(str(app_no)))


@app.get("/openapi/rest/CitingService/citingInfo", tags=["KIPRIS Patent Citation"])
def citing_info(request: Request) -> Any:
    query = _query_as_single_dict(request)
    app_no = query.get("standardCitationApplicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="standardCitationApplicationNumber는 필수입니다.")
    client = _client(_access_key_from_query(query))
    return _kipris_http(lambda: client.citing_info(str(app_no)))


@app.get("/kipris/patent-utility/documents/publication-fulltext-pdf", tags=["KIPRIS Documents"])
def get_patent_publication_full_text_pdf_path(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    return _kipris_http(lambda: client.publication_fulltext_pdf_path(str(app_no)).raw)


@app.get("/kipris/patent-utility/documents/announcement-fulltext-pdf", tags=["KIPRIS Documents"])
def get_patent_announcement_full_text_pdf_path(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    return _kipris_http(lambda: client.announcement_fulltext_pdf_path(str(app_no)).raw)


@app.get("/kipris/patent-utility/documents/preferred-fulltext-pdf", tags=["KIPRIS Documents"])
def get_preferred_patent_full_text_pdf_path(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")

    application_number = str(app_no)
    announcement = _kipris_http(lambda: client.announcement_fulltext_pdf_path(application_number))
    if announcement.path:
        return {
            "applicationNumber": application_number,
            "selectedType": "ANNOUNCEMENT_FULLTEXT_PDF",
            "reason": "등록/공고 정보가 존재하여 공고전문PDF를 선택했습니다.",
            "docName": announcement.doc_name,
            "path": announcement.path,
        }

    publication = _kipris_http(lambda: client.publication_fulltext_pdf_path(application_number))
    if publication.path:
        return {
            "applicationNumber": application_number,
            "selectedType": "PUBLICATION_FULLTEXT_PDF",
            "reason": "공고전문PDF가 없어 공개전문PDF를 선택했습니다.",
            "docName": publication.doc_name,
            "path": publication.path,
        }

    raise HTTPException(status_code=404, detail="공고전문/공개전문 PDF 경로를 찾지 못했습니다.")


@app.get("/kipo-api/kipi/patUtiModInfoSearchSevice/getPubFullTextInfoSearch", tags=["KIPRIS Documents"])
def get_kipris_pub_full_text_info_search(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    return _kipris_http(
        lambda: client.request(
            "getPubFullTextInfoSearch",
            {"applicationNumber": str(app_no)},
            service_path=PAT_UTI_SERVICE_PATH,
        )
    )


@app.get("/kipo-api/kipi/patUtiModInfoSearchSevice/getAnnFullTextInfoSearch", tags=["KIPRIS Documents"])
def get_kipris_ann_full_text_info_search(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    return _kipris_http(
        lambda: client.request(
            "getAnnFullTextInfoSearch",
            {"applicationNumber": str(app_no)},
            service_path=PAT_UTI_SERVICE_PATH,
        )
    )


@app.get("/kipo-api/kipi/patFamInfoSearchService/getAppNoPatFamInfoSearch", tags=["KIPRIS Patent Detail"])
def get_kipris_app_no_pat_fam_info_search(request: Request) -> Any:
    client, query = _kipris_request_context(request)
    app_no = query.get("applicationNumber")
    if not app_no:
        raise HTTPException(status_code=400, detail="applicationNumber는 필수입니다.")
    return _kipris_http(lambda: client.family_info(str(app_no)))


@app.get("/kipris/evaluation-data/{applicationNumber}/snapshot", tags=["KIPRIS Evaluation Data"])
def get_kipris_evaluation_data_snapshot(applicationNumber: str, request: Request) -> Any:
    client, _query = _kipris_request_context(request)
    return _kipris_http(lambda: _evaluation_snapshot_openapi_shape(client, applicationNumber))


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    merged = _load_merged_openapi()
    if merged.get("paths"):
        app.openapi_schema = merged
        return app.openapi_schema

    app.openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    return app.openapi_schema


app.openapi = custom_openapi
