from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from html import unescape
import sqlite3
from typing import Any
import io
import re

import requests

from app.config import settings


def list_patents(limit: int = 20) -> list[dict[str, Any]]:
    query = """
        SELECT
            id,
            management_number,
            application_number,
            registration_number,
            title_final,
            business_area,
            technology_area,
            related_product,
            status,
            application_date,
            registration_date,
            expected_expiration_date,
            data_source_status
        FROM patents
        ORDER BY id
        LIMIT ?
    """
    with sqlite3.connect(settings.patent_db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, (limit,)).fetchall()]


def get_patent(
    patent_id: int | None = None,
    application_number: str | None = None,
    registration_number: str | None = None,
    management_number: str | None = None,
) -> dict[str, Any] | None:
    filters = {
        "id": patent_id,
        "application_number": application_number,
        "registration_number": registration_number,
        "management_number": management_number,
    }
    selected = [(key, value) for key, value in filters.items() if value is not None]
    if not selected:
        raise ValueError("One patent identifier is required.")

    column, value = selected[0]
    query = f"""
        SELECT
            id,
            source_file_id,
            management_number,
            application_number,
            registration_number,
            title_draft,
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
            expected_expiration_date,
            evaluation_status,
            data_source_status
        FROM patents
        WHERE {column} = ?
        LIMIT 1
    """
    with sqlite3.connect(settings.patent_db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, (value,)).fetchone()
        return dict(row) if row else None


def fetch_kipris_bibliography(application_number: str) -> dict[str, Any]:
    client = _kipris_client()
    kipris_application_number = normalize_kipris_application_number(application_number)
    raw = client.bibliography_detail(kipris_application_number)
    result = normalize_kipris_bibliography(raw, application_number=application_number)
    try:
        result["family_patents"] = _normalize_kipris_family_patents(
            client.family_patents(kipris_application_number)
        )
    except Exception as exc:
        result["family_patents"] = []
        result.setdefault("warnings", []).append(
            f"family_info_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
        )
    try:
        result["citation_documents"] = normalize_kipris_citations(
            client.citation_info_v3(kipris_application_number)
        )
        result["metadata"]["prior_art"] = [
            item["display_number"] for item in result["citation_documents"] if item.get("display_number")
        ]
        result["citation_stats"] = build_citation_stats(result["citation_documents"])
    except Exception as exc:
        result["citation_documents"] = []
        result["citation_stats"] = {"total_count": 0, "standardized_count": 0, "non_standardized_count": 0}
        result.setdefault("warnings", []).append(
            f"citation_info_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
        )
    try:
        result["citing_documents"] = normalize_kipris_citing_documents(
            client.citing_info(kipris_application_number)
        )
        result["citing_stats"] = build_citing_stats(result["citing_documents"])
    except Exception as exc:
        result["citing_documents"] = []
        result["citing_stats"] = {"total_count": 0, "standardized_count": 0, "non_standardized_count": 0}
        result.setdefault("warnings", []).append(
            f"citing_info_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
        )
    try:
        result["citation_evidence"] = resolve_citation_evidence(
            client,
            citation_documents=result.get("citation_documents") or [],
            citing_documents=result.get("citing_documents") or [],
        )
    except Exception as exc:
        result["citation_evidence"] = {
            "kr_citation_documents": [],
            "foreign_claim_lookup_candidates": [],
            "foreign_citation_documents": [],
            "warnings": [f"citation_evidence_resolve_failed:{exc.__class__.__name__}:{str(exc)[:300]}"],
        }
    return result


def fetch_kipris_abstract(application_number: str) -> str:
    """초록 텍스트만 가볍게 조회한다(인용/패밀리/citing 등 부가 호출 없이).

    분야 추천처럼 '초록만' 필요한 경로에서 fetch_kipris_bibliography의 무거운
    네트워크 호출을 피하려고 bibliography_detail 하나만 정규화한다.
    실패 시 빈 문자열을 반환해 호출 측이 제목만으로 폴백하게 한다.
    """
    client = _kipris_client()
    kipris_application_number = normalize_kipris_application_number(application_number)
    raw = client.bibliography_detail(kipris_application_number)
    normalized = normalize_kipris_bibliography(raw, application_number=application_number)
    return (normalized.get("sections") or {}).get("abstract") or ""


def fetch_foreign_patent_rights_data(
    patent: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    collect_pdf: bool = True,
) -> dict[str, Any]:
    """Build KIPRIS-shaped rights data for non-KR patents.

    Foreign target patents must use KIPRIS overseas literature APIs instead of
    domestic bibliography/PDF APIs, otherwise empty domestic responses are
    normalized as KR metadata.
    """
    country = str(patent.get("country") or "").strip().upper()
    metadata = foreign_patent_metadata_from_db(patent)
    result: dict[str, Any] = {
        "source_type": "kipris_foreign_patent",
        "application_number": patent.get("application_number"),
        "metadata": metadata,
        "sections": {"abstract": ""},
        "claims": [],
        "claim_stats": _build_api_claim_stats(None, []),
        "family_patents": [],
        "citation_documents": [],
        "citation_stats": {"total_count": 0, "standardized_count": 0, "non_standardized_count": 0},
        "citing_documents": [],
        "citing_stats": {"total_count": 0, "standardized_count": 0, "non_standardized_count": 0},
        "citation_evidence": {
            "kr_citation_documents": [],
            "foreign_claim_lookup_candidates": [],
            "foreign_citation_documents": [],
            "warnings": [],
        },
        "pdf_collection": {
            "status": "not_attempted",
            "source": None,
            "manual_upload_required": False,
        },
        "warnings": [],
    }
    client = _kipris_client()
    candidates = foreign_target_literature_candidates(patent)
    result["foreign_literature_candidates"] = candidates
    result.update(fetch_foreign_target_reference_data(client, candidates))
    if country in {"US", "JP", "CN"}:
        claims, used_literature_number = fetch_foreign_target_claims(client, candidates)
    else:
        claims, used_literature_number = [], None
        result["warnings"].append(f"kipris_foreign_claims_not_supported:{country or 'unknown'}")
    if claims:
        result["claims"] = claims
        result["claim_stats"] = _build_api_claim_stats(len(claims), claims)
        result["metadata"]["claim_count"] = len(claims)
        result["metadata"]["reported_claim_count"] = len(claims)
        result["foreign_claim_literature_number"] = used_literature_number
    else:
        result["warnings"].append("kipris_foreign_claims_not_found")
    if not collect_pdf:
        return result

    result["pdf_collection"]["status"] = "collecting"
    try:
        parsed_pdf = download_and_parse_foreign_patent_pdf(
            client,
            patent,
            candidates=candidates,
            output_dir=output_dir or settings.patent_markdown_dir,
        )
        result["parsed_pdf"] = parsed_pdf
        result["documents"] = {
            "foreignFullTextPdf": {
                "literatureNumber": parsed_pdf.get("literature_number"),
                "selectedType": parsed_pdf.get("selected_type"),
                "sourcePath": parsed_pdf.get("source_path"),
                "pdfPath": parsed_pdf.get("pdf_path"),
            }
        }
        result["pdf_collection"] = {
            "status": "collected",
            "source": foreign_pdf_source(parsed_pdf.get("selected_type")),
            "selected_type": parsed_pdf.get("selected_type"),
            "pdf_path": parsed_pdf.get("pdf_path"),
            "manual_upload_required": False,
        }
        if not claims and (pdf_claims := extract_foreign_claims_from_text(parsed_pdf.get("markdown_text") or "")):
            result["claims"] = pdf_claims
            result["claim_stats"] = _build_api_claim_stats(len(pdf_claims), pdf_claims)
            result["metadata"]["claim_count"] = len(pdf_claims)
            result["metadata"]["reported_claim_count"] = len(pdf_claims)
        elif not claims:
            result["warnings"].append("foreign_pdf_claims_not_extracted")
    except Exception as exc:
        result["pdf_collection"] = {
            "status": "manual_upload_required",
            "source": None,
            "manual_upload_required": True,
            "missing_reason": "kipris_and_google_patents_pdf_not_found",
        }
        result["warnings"].append(
            f"foreign_pdf_manual_upload_required:{exc.__class__.__name__}:{str(exc)[:300]}"
        )
    return result


def foreign_pdf_source(selected_type: Any) -> str | None:
    value = str(selected_type or "")
    if value == "GOOGLE_PATENTS_FULLTEXT":
        return "google_patents"
    if value.startswith("FOREIGN_"):
        return "kipris"
    return None


def foreign_patent_metadata_from_db(patent: dict[str, Any]) -> dict[str, Any]:
    return {
        "country": str(patent.get("country") or "").strip().upper() or None,
        "patent_type": "등록특허" if patent.get("status") == "등록" else None,
        "registration_number": patent.get("registration_number"),
        "application_number": patent.get("application_number"),
        "publication_number": None,
        "title": patent.get("title_final") or patent.get("title_draft"),
        "title_eng": patent.get("title_draft") if patent.get("country") in {"US"} else None,
        "assignee": [],
        "assignee_eng": [],
        "inventors": [],
        "inventors_eng": [],
        "filing_date": patent.get("application_date"),
        "registration_date": patent.get("registration_date"),
        "publication_date": None,
        "open_date": None,
        "ipc": [],
        "cpc": [],
        "examiner": None,
        "claim_count": None,
        "reported_claim_count": None,
        "register_status": patent.get("status"),
        "final_disposal": None,
        "prior_art": [],
        "expected_expiration_date": patent.get("expected_expiration_date"),
        "assignee_count": 0,
        "has_co_assignee": False,
    }


def normalize_kipris_bibliography(raw: dict[str, Any], *, application_number: str) -> dict[str, Any]:
    item = _get_path(raw, ["response", "body", "item"]) or {}
    summary = _first_item(_get_path(item, ["biblioSummaryInfoArray", "biblioSummaryInfo"])) or {}
    abstract = _first_item(_get_path(item, ["abstractInfoArray", "abstractInfo"])) or {}
    applicants = _ensure_list(_get_path(item, ["applicantInfoArray", "applicantInfo"]))
    inventors = _ensure_list(_get_path(item, ["inventorInfoArray", "inventorInfo"]))
    ipcs = _ensure_list(_get_path(item, ["ipcInfoArray", "ipcInfo"]))
    claims = _normalize_kipris_claims(_ensure_list(_get_path(item, ["claimInfoArray", "claimInfo"])))

    metadata = {
        "country": "KR",
        "patent_type": "등록특허" if summary.get("registerStatus") == "등록" else None,
        "registration_number": _strip_register_suffix(summary.get("registerNumber")),
        "application_number": summary.get("applicationNumber") or application_number,
        "publication_number": summary.get("openNumber"),
        "title": summary.get("inventionTitle"),
        "title_eng": summary.get("inventionTitleEng"),
        "assignee": [applicant.get("name") for applicant in applicants if applicant.get("name")],
        "assignee_eng": [applicant.get("engName") for applicant in applicants if applicant.get("engName")],
        "inventors": [inventor.get("name") for inventor in inventors if inventor.get("name")],
        "inventors_eng": [inventor.get("engName") for inventor in inventors if inventor.get("engName")],
        "filing_date": _normalize_dot_date(summary.get("applicationDate")),
        "registration_date": _normalize_dot_date(summary.get("registerDate")),
        "publication_date": _normalize_dot_date(summary.get("publicationDate")),
        "open_date": _normalize_dot_date(summary.get("openDate")),
        "ipc": [ipc.get("ipcNumber") for ipc in ipcs if ipc.get("ipcNumber")],
        "cpc": [],
        "examiner": summary.get("examinerName"),
        "claim_count": len([claim for claim in claims if not claim["is_deleted"]])
        or _int_or_none(summary.get("claimCount")),
        "reported_claim_count": _int_or_none(summary.get("claimCount")),
        "register_status": summary.get("registerStatus"),
        "final_disposal": summary.get("finalDisposal"),
        "prior_art": [],
    }
    metadata["assignee_count"] = len(metadata["assignee"])
    metadata["has_co_assignee"] = metadata["assignee_count"] > 1

    active_claims = [claim for claim in claims if not claim["is_deleted"]]
    return {
        "source_type": "kipris_bibliography_detail",
        "application_number": application_number,
        "kipris_application_number": normalize_kipris_application_number(application_number),
        "metadata": metadata,
        "sections": {
            "abstract": abstract.get("astrtCont") or "",
        },
        "claims": active_claims,
        "claim_stats": _build_api_claim_stats(metadata["reported_claim_count"], claims),
        "raw": raw,
    }


def download_and_parse_patent_pdf(
    application_number: str,
    *,
    pdf_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    prefer_announcement: bool = True,
) -> dict[str, Any]:
    client = _kipris_client()
    kipris_application_number = normalize_kipris_application_number(application_number)
    pdf_dir = Path(pdf_dir or settings.patent_pdf_dir)
    output_dir = Path(output_dir or settings.patent_markdown_dir) / _safe_filename(application_number)

    selected = _select_fulltext_pdf(
        client,
        fulltext_application_number_candidates(application_number),
        prefer_announcement=prefer_announcement,
    )
    pdf_path = _download_pdf_url(
        selected["path"],
        output_dir=pdf_dir,
        filename=selected.get("doc_name") or f"{application_number}.pdf",
        session=client.session,
        timeout=client.timeout,
    )

    parsed = parse_single_patent_pdf(pdf_path, output_dir=output_dir)
    return {
        "application_number": application_number,
        "kipris_application_number": kipris_application_number,
        "selected_application_number": selected["application_number"],
        "selected_type": selected["selected_type"],
        "doc_name": selected.get("doc_name"),
        "source_path": selected["path"],
        "pdf_path": str(pdf_path),
        "parse_output_dir": str(output_dir),
        "markdown_paths": parsed["markdown_paths"],
        "markdown_text": parsed["markdown_text"],
    }


def parse_single_patent_pdf(
    pdf_path: str | Path,
    *,
    output_dir: str | Path,
    output_format: str = "markdown-with-images",
) -> dict[str, Any]:
    import opendataloader_pdf

    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    before = {path.resolve() for path in output_dir.rglob("*.md")}
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        opendataloader_pdf.convert(
            input_path=[str(pdf_path)],
            output_dir=str(output_dir),
            format=output_format,
        )
    after = sorted(path for path in output_dir.rglob("*.md") if path.resolve() not in before)
    if not after:
        after = sorted(output_dir.rglob("*.md"))

    markdown_text = "\n\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in after)
    return {
        "markdown_paths": [str(path) for path in after],
        "markdown_text": markdown_text,
    }


_kipris_client_instance: Any = None


def _kipris_client() -> Any:
    # EXT-08: 호출마다 KiprisClient(+requests 세션)를 새로 만들던 것을 모듈 단위로 1회 생성·재사용한다
    # (세션·연결 풀 공유로 반복 핸드셰이크 제거). requests.Session은 동시 전송에 안전.
    global _kipris_client_instance
    if _kipris_client_instance is None:
        from open_api.kipris_client import KiprisClient
        _kipris_client_instance = KiprisClient()
    return _kipris_client_instance


def _normalize_kipris_claims(raw_claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for raw_claim in raw_claims:
        text = str(raw_claim.get("claim") or "").strip()
        match = re.match(r"^\s*(\d+)\.\s*(.*)$", text, re.S)
        if not match:
            continue
        claim_no = int(match.group(1))
        body = re.sub(r"\s+", " ", match.group(2)).strip()
        is_deleted = body == "삭제"
        dependency = _extract_claim_dependency(body)
        result.append(
            {
                "claim_no": claim_no,
                "text": body,
                "is_independent": dependency is None and not is_deleted,
                "dependency": dependency,
                "is_deleted": is_deleted,
                "source": "kipris_api",
            }
        )
    return result


# EXT-06: 종속 청구항 인용은 인용 종결어미(에 있어서/에 따른/에 기재된/의 등)를 반드시 동반한다.
# 단순 구성요소 나열(예: "제1 또는 제2 위치")을 종속 인용으로 오판(false-positive)하지 않으면서,
# "제1항에 따른/기재된/의" 같은 인용 표현 누락(false-negative)도 방지한다.
_CLAIM_DEPENDENCY_PATTERN = (
    r"(?:청구항|제)\s*(\d+)\s*항?"
    r"(?:\s*(?:내지|또는|및)\s*(?:청구항|제)?\s*\d+\s*항?)*"
    r"\s*(?:중\s*)?(?:어느\s*(?:한|하나의?)\s*항)?"
    r"\s*(?:에\s*있어서|에\s*기재된|에\s*따른|에\s*의한|에\s*있어|에서|의\s|에\s)"
)


def _extract_claim_dependency(text: str) -> int | None:
    match = re.search(_CLAIM_DEPENDENCY_PATTERN, text)
    return _int_or_none(match.group(1) if match else None)


def _build_api_claim_stats(reported_claim_count: int | None, claims: list[dict[str, Any]]) -> dict[str, Any]:
    active = [claim for claim in claims if not claim["is_deleted"]]
    active_numbers = [claim["claim_no"] for claim in active]
    independent_numbers = [claim["claim_no"] for claim in active if claim.get("is_independent")]
    dependent_numbers = [claim["claim_no"] for claim in active if not claim.get("is_independent")]
    deleted_numbers = [claim["claim_no"] for claim in claims if claim["is_deleted"]]
    expected_numbers = set(range(1, max([claim["claim_no"] for claim in claims], default=0) + 1))
    return {
        "reported_claim_count": reported_claim_count,
        "active_claim_count": len(active),
        "active_claim_numbers": active_numbers,
        "independent_claim_numbers": independent_numbers,
        "dependent_claim_numbers": dependent_numbers,
        "deleted_claim_numbers": deleted_numbers,
        "has_deleted_claims_gap": bool(expected_numbers - set(active_numbers)) if expected_numbers else False,
    }


def _normalize_kipris_family_patents(raw_family_patents: list[Any]) -> list[dict[str, Any]]:
    result = []
    for family in raw_family_patents:
        country_code = getattr(family, "country_code", None)
        registration_number = getattr(family, "registration_number", None)
        if not country_code and not registration_number:
            continue
        result.append(
            {
                "country_code": country_code,
                "registration_number": _strip_register_suffix(registration_number),
                "source": "kipris_family_info_v2",
            }
        )
    return result


def normalize_kipris_citations(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = _ensure_list(
        _get_path(raw, ["response", "body", "items", "citationInfoV3"])
        or _get_path(raw, ["response", "body", "citationInfoV3"])
    )
    normalized = [_normalize_kipris_citation(item) for item in items if isinstance(item, dict)]
    return _dedupe_kipris_citations(normalized)


def build_citation_stats(citations: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_count": len(citations),
        "standardized_count": sum(1 for item in citations if item.get("is_standardized")),
        "non_standardized_count": sum(1 for item in citations if not item.get("is_standardized")),
    }


def normalize_kipris_citing_documents(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = _ensure_list(
        _get_path(raw, ["response", "body", "items", "citingInfo"])
        or _get_path(raw, ["response", "body", "citingInfo"])
    )
    normalized = [_normalize_kipris_citing_document(item) for item in items if isinstance(item, dict)]
    return _dedupe_kipris_citing_documents(normalized)


def build_citing_stats(citing_documents: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_count": len(citing_documents),
        "standardized_count": sum(1 for item in citing_documents if item.get("is_standardized")),
        "non_standardized_count": sum(1 for item in citing_documents if not item.get("is_standardized")),
    }


def resolve_citation_evidence(
    client: Any,
    *,
    citation_documents: list[dict[str, Any]],
    citing_documents: list[dict[str, Any]],
    foreign_claims_fetcher: Any | None = None,
    max_kr_citations: int = 3,
    max_kr_citing: int = 3,
    max_foreign_citations: int = 3,
) -> dict[str, Any]:
    """권리성 평가용 선행 인용문헌을 조회 가능한 형태로 보강합니다."""
    warnings: list[str] = []
    kr_citation_documents = []
    foreign_claim_lookup_candidates = []
    foreign_citation_documents = []

    for citation in _rank_citation_documents(citation_documents):
        country_code = citation.get("country_code")
        if country_code != "KR":
            candidate = _foreign_claim_lookup_candidate(citation)
            if candidate:
                foreign_claim_lookup_candidates.append(candidate)
            continue
        if len(kr_citation_documents) >= max_kr_citations:
            continue

        application_number = _resolve_kr_citation_application_number(client, citation)
        if not application_number:
            warnings.append(f"kr_citation_application_number_not_found:{citation.get('display_number')}")
            continue
        enriched = _enrich_kr_reference_document(
            client,
            application_number,
            direction="cited_by_target",
            source_document=citation,
        )
        if enriched:
            kr_citation_documents.append(enriched)

    deduped_foreign_candidates = _dedupe_foreign_claim_lookup_candidates(foreign_claim_lookup_candidates)
    if deduped_foreign_candidates:
        try:
            fetcher = foreign_claims_fetcher or (
                lambda candidates, **kwargs: _fetch_foreign_claims(client, candidates, **kwargs)
            )
            foreign_citation_documents = fetcher(
                deduped_foreign_candidates,
                max_candidates=max_foreign_citations,
            )
        except Exception as exc:
            warnings.append(f"foreign_claims_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}")

    return {
        "kr_citation_documents": kr_citation_documents,
        "foreign_claim_lookup_candidates": deduped_foreign_candidates,
        "foreign_citation_documents": foreign_citation_documents,
        "warnings": warnings,
    }


def _normalize_kipris_citation(item: dict[str, Any]) -> dict[str, Any]:
    country_code = _clean(item.get("StandardCitationLiteratureCountryCode"))
    standard_number = _clean(item.get("StandardCitationLiteraturenumber"))
    kind_code = _clean(item.get("StandardCitationIdentificationCode"))
    standard_status_name = _clean(item.get("StandardStatusCodeName"))
    citation_type_name = _clean(item.get("CitationLiteratureTypeCodeName"))
    original_number = _clean(item.get("OriginalcitationLiteraturenumber"))
    is_standardized = standard_status_name == "표준화" and bool(standard_number)
    display_number = _citation_display_number(
        country_code=country_code,
        standard_number=standard_number,
        kind_code=kind_code,
        original_number=original_number,
    )
    return {
        "application_number": _clean(item.get("ApplicationNumber")),
        "original_number": original_number,
        "original_date": _normalize_yyyymmdd(_clean(item.get("OriginalcitationLiteraturenumberDate"))),
        "standard_number": standard_number,
        "country_code": country_code,
        "country_name": _clean(item.get("StandardCitationLiteratureCountryCodeName")),
        "kind_code": kind_code,
        "publication_date": _normalize_yyyymmdd(_clean(item.get("StandardCitationLiteraturePublicationDate"))),
        "standard_status_code": _clean(item.get("StandardStatusCode")),
        "standard_status_name": standard_status_name,
        "citation_type_code": _clean(item.get("CitationLiteratureTypeCode")),
        "citation_type_name": citation_type_name,
        "citation_type_codes": [_clean(item.get("CitationLiteratureTypeCode"))]
        if _clean(item.get("CitationLiteratureTypeCode"))
        else [],
        "citation_type_names": [citation_type_name] if citation_type_name else [],
        "display_number": display_number,
        "is_standardized": is_standardized,
        "raw": item,
    }


def _normalize_kipris_citing_document(item: dict[str, Any]) -> dict[str, Any]:
    standard_status_name = _clean(item.get("StandardStatusCodeName"))
    citation_type_name = _clean(item.get("CitationLiteratureTypeCodeName"))
    return {
        "standard_citation_application_number": _clean(item.get("StandardCitationApplicationNumber")),
        "citing_application_number": _clean(item.get("ApplicationNumber")),
        "standard_status_code": _clean(item.get("StandardStatusCode")),
        "standard_status_name": standard_status_name,
        "citation_type_code": _clean(item.get("CitationLiteratureTypeCode")),
        "citation_type_name": citation_type_name,
        "citation_type_codes": [_clean(item.get("CitationLiteratureTypeCode"))]
        if _clean(item.get("CitationLiteratureTypeCode"))
        else [],
        "citation_type_names": [citation_type_name] if citation_type_name else [],
        "is_standardized": standard_status_name == "표준화",
        "raw": item,
    }


def _dedupe_kipris_citing_documents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    index_by_application_number: dict[str, int] = {}
    for item in items:
        application_number = item.get("citing_application_number")
        if not application_number:
            continue
        if application_number in index_by_application_number:
            existing = selected[index_by_application_number[application_number]]
            existing["citation_type_codes"] = _unique_texts(
                [*existing.get("citation_type_codes", []), *item.get("citation_type_codes", [])]
            )
            existing["citation_type_names"] = _unique_texts(
                [*existing.get("citation_type_names", []), *item.get("citation_type_names", [])]
            )
            existing["is_standardized"] = bool(existing.get("is_standardized") or item.get("is_standardized"))
            continue
        index_by_application_number[application_number] = len(selected)
        selected.append(item)
    return selected


def _dedupe_kipris_citations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    index_by_original_key: dict[str, int] = {}
    standardized_original_keys = {
        _citation_original_key(item)
        for item in items
        if item.get("is_standardized") and _citation_original_key(item)
    }

    for item in sorted(items, key=lambda value: 0 if value.get("is_standardized") else 1):
        if not item.get("display_number"):
            continue
        if not item.get("is_standardized") and _citation_original_key(item) in standardized_original_keys:
            original_key = _citation_original_key(item)
            if original_key in index_by_original_key:
                existing = selected[index_by_original_key[original_key]]
                existing["citation_type_codes"] = _unique_texts(
                    [*existing.get("citation_type_codes", []), *item.get("citation_type_codes", [])]
                )
                existing["citation_type_names"] = _unique_texts(
                    [*existing.get("citation_type_names", []), *item.get("citation_type_names", [])]
                )
            continue
        key = _citation_dedupe_key(item)
        if key in index_by_key:
            existing = selected[index_by_key[key]]
            existing["citation_type_codes"] = _unique_texts(
                [*existing.get("citation_type_codes", []), *item.get("citation_type_codes", [])]
            )
            existing["citation_type_names"] = _unique_texts(
                [*existing.get("citation_type_names", []), *item.get("citation_type_names", [])]
            )
            continue
        index_by_key[key] = len(selected)
        original_key = _citation_original_key(item)
        if original_key:
            index_by_original_key[original_key] = len(selected)
        selected.append(item)
    return selected


def _rank_citation_documents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(item: dict[str, Any]) -> tuple[int, int, int, str]:
        kind = str(item.get("kind_code") or "").upper()
        return (
            0 if item.get("is_standardized") else 1,
            0 if kind.startswith("B") else 1,
            _citation_type_priority(item),
            0 if item.get("country_code") == "KR" else 1,
            str(item.get("display_number") or ""),
        )

    return sorted(items, key=_key)


def _rank_citing_documents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            0 if item.get("is_standardized") else 1,
            _citation_type_priority(item),
            str(item.get("citing_application_number") or ""),
        ),
    )


def _citation_type_priority(item: dict[str, Any]) -> int:
    codes = set(item.get("citation_type_codes") or [])
    names = set(item.get("citation_type_names") or [])
    if codes & {"E0802", "E0805"} or names & {"선행기술조사보고서", "선행기술조사문헌"}:
        return 0
    if "E0801" in codes or "발송문서" in names:
        return 1
    if "E0806" in codes or "출원서인용문헌이력정보" in names:
        return 2
    return 3


def _resolve_kr_citation_application_number(client: Any, citation: dict[str, Any]) -> str | None:
    standard_number = _clean(citation.get("standard_number"))
    kind_code = str(citation.get("kind_code") or "").upper()
    if not standard_number:
        return None

    search_params: dict[str, Any]
    if kind_code.startswith("B"):
        search_params = {"registerNumber": standard_number}
    else:
        search_params = {"openNumber": standard_number}

    raw = client.advanced_search(
        **search_params,
        patent=True,
        utility=False,
        pageNo=1,
        numOfRows=1,
    )
    item = _first_item(
        _get_path(raw, ["response", "body", "items", "item"])
        or _get_path(raw, ["response", "body", "items", "PatentUtilityInfo"])
        or _get_path(raw, ["response", "body", "item"])
    )
    if not isinstance(item, dict):
        return None
    return _clean(
        item.get("applicationNumber")
        or item.get("ApplicationNumber")
        or item.get("application_number")
    )


def _enrich_kr_reference_document(
    client: Any,
    application_number: str,
    *,
    direction: str,
    source_document: dict[str, Any],
    max_independent_claims: int = 6,
) -> dict[str, Any] | None:
    try:
        normalized = normalize_kipris_bibliography(
            client.bibliography_detail(application_number),
            application_number=application_number,
        )
    except Exception as exc:
        return {
            "direction": direction,
            "country_code": "KR",
            "application_number": application_number,
            "lookup_status": "failed",
            "failure_reason": f"{exc.__class__.__name__}:{str(exc)[:300]}",
            "source_document": source_document,
        }

    metadata = normalized.get("metadata") or {}
    claims = normalized.get("claims") or []
    representative_claims = [claim for claim in claims if claim.get("is_independent") and claim.get("text")]
    if not representative_claims and claims:
        representative_claims = [claims[0]]
    return {
        "direction": direction,
        "country_code": "KR",
        "application_number": metadata.get("application_number") or application_number,
        "registration_number": metadata.get("registration_number"),
        "publication_number": metadata.get("publication_number"),
        "title": metadata.get("title"),
        "abstract": (normalized.get("sections") or {}).get("abstract"),
        "register_status": metadata.get("register_status"),
        "claim_stats": normalized.get("claim_stats") or {},
        "representative_claims": [
            {
                "claim_no": claim.get("claim_no"),
                "text": claim.get("text"),
                "is_independent": claim.get("is_independent"),
                "dependency": claim.get("dependency"),
            }
            for claim in representative_claims[:max_independent_claims]
        ],
        "lookup_status": "resolved",
        "lookup_source": "kipris_bibliography_detail",
        "source_document": source_document,
    }


def _foreign_claim_lookup_candidate(citation: dict[str, Any]) -> dict[str, Any] | None:
    country_code = citation.get("country_code")
    document_number = citation.get("standard_number")
    if not country_code or not document_number:
        return None
    return {
        "direction": "cited_by_target",
        "country_code": country_code,
        "document_number": document_number,
        "kind_code": citation.get("kind_code"),
        "original_number": citation.get("original_number"),
        "display_number": citation.get("display_number"),
        "lookup_source": "bigquery_claims",
    }


def _fetch_foreign_claims(
    client: Any,
    candidates: list[dict[str, Any]],
    *,
    max_candidates: int = 3,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    kipris_documents = _fetch_foreign_claims_from_kipris(
        client,
        candidates,
        max_candidates=max_candidates,
    )
    resolved_keys = {
        (
            document.get("country_code"),
            document.get("document_number"),
            document.get("kind_code"),
        )
        for document in kipris_documents
    }
    remaining_candidates = [
        candidate
        for candidate in candidates[:max_candidates]
        if (candidate.get("country_code"), candidate.get("document_number"), candidate.get("kind_code"))
        not in resolved_keys
    ]
    if not remaining_candidates:
        return kipris_documents
    try:
        bigquery_documents = _fetch_foreign_claims_from_bigquery(
            remaining_candidates,
            max_candidates=max_candidates,
            **kwargs,
        )
    except Exception:
        bigquery_documents = []
    documents = [*kipris_documents, *bigquery_documents]
    resolved_keys = {_foreign_document_key(document) for document in documents}
    remaining_candidates = [
        candidate
        for candidate in remaining_candidates
        if _foreign_document_key(candidate) not in resolved_keys
    ]
    if not remaining_candidates:
        return documents
    return [
        *documents,
        *_fetch_foreign_claims_from_google_patents(
            client,
            remaining_candidates,
            max_candidates=max_candidates,
        ),
    ]


def _fetch_foreign_claims_from_kipris(
    client: Any,
    candidates: list[dict[str, Any]],
    *,
    max_candidates: int = 3,
    max_claims_per_document: int = 5,
) -> list[dict[str, Any]]:
    documents = []
    for candidate in candidates[:max_candidates]:
        country_code = candidate.get("country_code")
        if not country_code:
            continue
        for literature_number in _foreign_literature_number_candidates(candidate):
            try:
                raw = client.overseas_demand_paragraph(literature_number, country_code)
            except Exception:
                continue
            claims = _normalize_foreign_kipris_claims(raw)
            if not claims:
                continue
            documents.append(
                {
                    "direction": candidate.get("direction"),
                    "country_code": country_code,
                    "literature_number": literature_number,
                    "document_number": candidate.get("document_number"),
                    "kind_code": candidate.get("kind_code"),
                    "display_number": candidate.get("display_number"),
                    "representative_claims": claims[:max_claims_per_document],
                    "lookup_status": "resolved",
                    "lookup_source": "kipris_foreign_bibliographic_claims",
                    "source_document": candidate,
                }
            )
            break
    return documents


def resolve_foreign_prior_art_evidence(
    prior_art_numbers: list[str],
    *,
    max_candidates: int = 5,
) -> dict[str, Any]:
    candidates = [
        candidate
        for value in prior_art_numbers
        if (candidate := foreign_reference_candidate_from_text(value))
    ][:max_candidates]
    if not candidates:
        return {
            "foreign_claim_lookup_candidates": [],
            "foreign_citation_documents": [],
            "foreign_identifier_only_documents": [],
            "prior_art_collection": _prior_art_collection_status([], []),
            "warnings": [],
        }

    try:
        documents = _fetch_foreign_claims(
            _kipris_client(),
            candidates,
            max_candidates=max_candidates,
        )
    except Exception as exc:
        return {
            "foreign_claim_lookup_candidates": candidates,
            "foreign_citation_documents": [],
            "foreign_identifier_only_documents": candidates,
            "prior_art_collection": _prior_art_collection_status(candidates, []),
            "warnings": [f"foreign_prior_art_enrichment_failed:{exc.__class__.__name__}"],
        }
    resolved_numbers = {
        document.get("display_number")
        for document in documents
        if document.get("display_number")
    }
    unresolved = [
        candidate
        for candidate in candidates
        if candidate.get("display_number") not in resolved_numbers
    ]
    warnings = (
        [f"foreign_prior_art_details_not_found:{len(unresolved)}"]
        if unresolved
        else []
    )
    return {
        "foreign_claim_lookup_candidates": candidates,
        "foreign_citation_documents": documents,
        "foreign_identifier_only_documents": unresolved,
        "prior_art_collection": _prior_art_collection_status(candidates, documents),
        "warnings": warnings,
    }


def _fetch_foreign_claims_from_google_patents(
    client: Any,
    candidates: list[dict[str, Any]],
    *,
    max_candidates: int = 3,
    max_claims_per_document: int = 5,
) -> list[dict[str, Any]]:
    documents = []
    for candidate in candidates[:max_candidates]:
        publication_id = google_patents_publication_id(_candidate_patent(candidate))
        if not publication_id:
            continue
        document = _google_patents_pdf_document(
            client,
            candidate,
            publication_id=publication_id,
            max_claims=max_claims_per_document,
        )
        if not _is_comparison_ready(document):
            document = _google_patents_html_document(
                client,
                candidate,
                publication_id=publication_id,
                max_claims=max_claims_per_document,
            )
        if _is_comparison_ready(document):
            documents.append(document)
    return documents


def _google_patents_pdf_document(
    client: Any,
    candidate: dict[str, Any],
    *,
    publication_id: str,
    max_claims: int,
) -> dict[str, Any]:
    try:
        pdf_url = google_patents_pdf_url(
            _candidate_patent(candidate),
            session=client.session,
            timeout=client.timeout,
        )
        if not pdf_url:
            return {}
        pdf_path = _download_pdf_url(
            pdf_url,
            output_dir=Path(settings.patent_pdf_dir) / "prior_art",
            filename=f"{publication_id}.pdf",
            session=client.session,
            timeout=client.timeout,
        )
        parsed = parse_single_patent_pdf(
            pdf_path,
            output_dir=Path(settings.output_dir) / "prior_art_markdown" / publication_id,
        )
        claims = extract_foreign_claims_from_text(parsed.get("markdown_text") or "")
        return _foreign_prior_art_document(
            candidate,
            representative_claims=claims[:max_claims],
            lookup_source="google_patents_pdf",
        )
    except Exception:
        return {}


def _google_patents_html_document(
    client: Any,
    candidate: dict[str, Any],
    *,
    publication_id: str,
    max_claims: int,
) -> dict[str, Any]:
    for language in ("en", "zh", "ja"):
        try:
            response = client.session.get(
                f"https://patents.google.com/patent/{publication_id}/{language}",
                timeout=client.timeout,
            )
            response.raise_for_status()
        except Exception:
            continue
        title = _google_patents_meta_content(response.text, "DC.title")
        abstract = (
            _google_patents_meta_content(response.text, "DC.description")
            or _google_patents_section_text(response.text, "abstract")
        )
        claim_text = "\n".join(_google_patents_claim_texts(response.text))
        claims = extract_foreign_claims_from_text(claim_text)
        document = _foreign_prior_art_document(
            candidate,
            title=title,
            abstract=abstract,
            representative_claims=claims[:max_claims],
            lookup_source="google_patents_html",
        )
        if _is_comparison_ready(document):
            return document
    return {}


def _candidate_patent(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "country": candidate.get("country_code"),
        "registration_number": candidate.get("display_number") or candidate.get("original_number"),
    }


def _foreign_prior_art_document(
    candidate: dict[str, Any],
    *,
    title: str | None = None,
    abstract: str | None = None,
    representative_claims: list[dict[str, Any]] | None = None,
    lookup_source: str,
) -> dict[str, Any]:
    claims = representative_claims or []
    for claim in claims:
        claim["source"] = lookup_source
    return {
        **candidate,
        "title": _clean(title),
        "abstract": _clean(abstract),
        "representative_claims": claims,
        "lookup_status": "resolved",
        "lookup_source": lookup_source,
        "comparison_status": "comparison_ready" if claims or _clean(abstract) else "identifier_only",
        "source_document": candidate,
    }


def _google_patents_meta_content(text: str, name: str) -> str | None:
    for tag in re.findall(r"<meta\b[^>]*>", text or "", re.I):
        attributes = {
            key.lower(): unescape(value)
            for key, _, value in re.findall(r"""([:\w-]+)\s*=\s*(["'])(.*?)\2""", tag, re.S)
        }
        if attributes.get("name", "").lower() == name.lower():
            return _strip_html(attributes.get("content"))
    return None


def _google_patents_section_text(text: str, class_name: str) -> str | None:
    match = re.search(
        rf'<(?:section|div)\b[^>]*class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\'][^>]*>(.*?)</(?:section|div)>',
        text or "",
        re.I | re.S,
    )
    return _strip_html(match.group(1)) if match else None


def _google_patents_claim_texts(text: str) -> list[str]:
    return [
        cleaned
        for body in re.findall(
            r'<div\b[^>]*class=["\'][^"\']*\bclaim-text\b[^"\']*["\'][^>]*>(.*?)</div>',
            text or "",
            re.I | re.S,
        )
        if (cleaned := _strip_html(body))
    ]


def _strip_html(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(text)).strip() or None


def _foreign_document_key(document: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (
        document.get("country_code"),
        document.get("document_number"),
        document.get("kind_code"),
    )


def _is_comparison_ready(document: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(document, dict)
        and (document.get("representative_claims") or _clean(document.get("abstract")))
    )


def _prior_art_collection_status(
    candidates: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    comparison_ready_count = sum(1 for document in documents if _is_comparison_ready(document))
    return {
        "candidate_count": len(candidates),
        "comparison_ready_count": comparison_ready_count,
        "identifier_only_count": max(0, len(candidates) - comparison_ready_count),
        "comparison_status": "comparison_ready" if comparison_ready_count else "unknown",
    }


def foreign_reference_candidate_from_text(value: str) -> dict[str, Any] | None:
    match = re.search(
        r"\b([A-Z]{2})\s*-?\s*([0-9][0-9A-Z./-]*)\s+([A-Z][0-9]?)\b",
        str(value or "").upper(),
    )
    if not match:
        return None
    country_code = match.group(1)
    document_number = re.sub(r"\D+", "", match.group(2))
    kind_code = match.group(3)
    if not document_number:
        return None
    return {
        "direction": "cited_by_target",
        "country_code": country_code,
        "document_number": document_number,
        "kind_code": kind_code,
        "original_number": str(value).strip(),
        "display_number": f"{country_code} {document_number} {kind_code}",
        "lookup_source": "foreign_target_pdf_prior_art",
    }


def _normalize_foreign_kipris_claims(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = _ensure_list(
        _get_path(raw, ["response", "body", "items", "demandParagraphInfo"])
        or _get_path(raw, ["response", "body", "demandParagraphInfo"])
    )
    claims = []
    for index, item in enumerate(items, 1):
        if isinstance(item, dict):
            text = _clean(item.get("claimText"))
        else:
            text = _clean(item)
        if not text:
            continue
        claims.append(
            {
                "claim_no": index,
                "text": text,
                "is_independent": index == 1,
                "dependency": None,
                "source": "kipris_foreign_bibliographic_claims",
            }
        )
    return claims


def _foreign_literature_number_candidates(candidate: dict[str, Any]) -> list[str]:
    document_number = re.sub(r"\D+", "", str(candidate.get("document_number") or ""))
    kind_code = str(candidate.get("kind_code") or "").strip().upper()
    original_number = _clean(candidate.get("original_number"))
    display_number = _clean(candidate.get("display_number"))
    candidates = []
    base_numbers = _foreign_literature_base_numbers(candidate, document_number)
    for number in base_numbers:
        candidates.extend(_foreign_literature_candidates_for_number(number, kind_code))
    for value in (original_number, display_number):
        parsed = _foreign_literature_number_from_text(value)
        if parsed:
            candidates.append(parsed)
    if document_number:
        candidates.append(document_number.zfill(12))
        candidates.append(document_number)
    return _unique_texts(candidates)


def foreign_target_literature_candidates(patent: dict[str, Any]) -> list[dict[str, Any]]:
    country = str(patent.get("country") or "").strip().upper()
    candidates = []
    for source_field in ("registration_number", "application_number"):
        value = _clean(patent.get(source_field))
        if not value:
            continue
        document_number = re.sub(r"\D+", "", value)
        if not document_number:
            continue
        kind_codes = foreign_target_kind_codes(country, value, source_field=source_field)
        for kind_code in kind_codes:
            candidates.append(
                {
                    "direction": "target_foreign_patent",
                    "country_code": country,
                    "document_number": document_number,
                    "kind_code": kind_code,
                    "original_number": value,
                    "display_number": " ".join(part for part in [country + document_number, kind_code] if part),
                    "lookup_source": "target_foreign_patent",
                    "publication_date": patent.get("registration_date") or patent.get("application_date"),
                    "source_field": source_field,
                }
            )
    return _dedupe_foreign_claim_lookup_candidates(candidates)


def foreign_target_kind_codes(country: str, value: str, *, source_field: str) -> list[str]:
    parsed = re.search(r"\b([A-Z][0-9]?)\b\s*$", str(value or "").strip().upper())
    parsed_kind = parsed.group(1) if parsed else ""
    if source_field == "application_number":
        return _unique_texts([parsed_kind, "A0", "A", "A1"] if country == "CN" else [parsed_kind, "A1", "A"])
    if country in {"US", "JP"}:
        return _unique_texts([parsed_kind, "B2", "B1", "B"])
    if country == "CN":
        return _unique_texts([parsed_kind, "B2", "B", "A0", "A", "A1"])
    return _unique_texts([parsed_kind, "B2", "B"])


def fetch_foreign_target_claims(client: Any, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    for candidate in candidates:
        country_code = candidate.get("country_code")
        if not country_code:
            continue
        for literature_number in _foreign_literature_number_candidates(candidate):
            try:
                raw = client.overseas_demand_paragraph(literature_number, country_code)
            except Exception:
                continue
            claims = _normalize_foreign_kipris_claims(raw)
            if claims:
                return normalize_foreign_claims_for_target(claims), literature_number
    return [], None


def fetch_foreign_target_reference_data(
    client: Any,
    candidates: list[dict[str, Any]],
    *,
    max_documents: int = 20,
) -> dict[str, Any]:
    cited_documents: list[dict[str, Any]] = []
    warnings: list[str] = []
    used_literature_numbers: list[str] = []
    for candidate in candidates:
        country_code = candidate.get("country_code")
        if not country_code:
            continue
        for literature_number in _foreign_literature_number_candidates(candidate):
            domestic_raw = None
            foreign_raw = None
            try:
                domestic_raw = client.overseas_us_patent_documents(literature_number, country_code)
            except Exception as exc:
                warnings.append(f"foreign_domestic_citations_failed:{literature_number}:{exc.__class__.__name__}")
            try:
                foreign_raw = client.overseas_foreign_patent_documents(literature_number, country_code)
            except Exception as exc:
                warnings.append(f"foreign_foreign_citations_failed:{literature_number}:{exc.__class__.__name__}")
            documents = [
                *normalize_foreign_reference_documents(
                    domestic_raw,
                    source="kipris_foreign_domestic_citation_documents",
                    direction="cited_by_target",
                ),
                *normalize_foreign_reference_documents(
                    foreign_raw,
                    source="kipris_foreign_foreign_citation_documents",
                    direction="cited_by_target",
                ),
            ]
            if documents:
                cited_documents.extend(documents)
                used_literature_numbers.append(literature_number)
                break
        if cited_documents:
            break

    cited_documents = dedupe_foreign_reference_documents(cited_documents)[:max_documents]
    stats = {
        "total_count": len(cited_documents),
        "standardized_count": len(cited_documents),
        "non_standardized_count": 0,
    }
    api_collection = {
        "target_cited_references": {
            "available": bool(cited_documents),
            "source": "kipris_foreign_patent_documents",
            "used_literature_numbers": used_literature_numbers,
            "count": len(cited_documents),
        },
        "target_citing_references": {
            "available": False,
            "source": None,
            "missing_reason": "foreign_citing_api_not_connected",
        },
        "target_family": {
            "available": False,
            "source": None,
            "missing_reason": "foreign_family_api_not_connected",
        },
        "target_legal_status": {
            "available": False,
            "source": None,
            "missing_reason": "foreign_legal_status_api_not_connected",
        },
    }
    return {
        "citation_documents": cited_documents,
        "citation_stats": stats,
        "citation_evidence": {
            "kr_citation_documents": [],
            "foreign_claim_lookup_candidates": [],
            "foreign_citation_documents": [
                foreign_reference_to_citation_evidence(item) for item in cited_documents[:5]
            ],
            "warnings": warnings,
        },
        "foreign_api_collection": api_collection,
    }


def normalize_foreign_reference_documents(raw: Any, *, source: str, direction: str) -> list[dict[str, Any]]:
    documents = []
    for item in iter_foreign_reference_items(raw):
        if not isinstance(item, dict):
            continue
        country_code = first_mapping_value(item, ("countryCode", "CountryCode", "citationCountryCode", "documentCountryCode"))
        document_number = first_mapping_value(
            item,
            (
                "literatureNumber",
                "LiteratureNumber",
                "documentNumber",
                "DocumentNumber",
                "publicationNumber",
                "PublicationNumber",
                "patentNumber",
                "PatentNumber",
            ),
        )
        kind_code = first_mapping_value(item, ("kindCode", "KindCode", "publicationKindCode", "PublicationKindCode"))
        title = first_mapping_value(item, ("inventionTitle", "title", "Title", "documentTitle"))
        publication_date = first_mapping_value(item, ("publicationDate", "PublicationDate", "openDate", "OpenDate"))
        if not (country_code or document_number or title):
            continue
        documents.append(
            {
                "direction": direction,
                "country_code": country_code,
                "document_number": document_number,
                "kind_code": kind_code,
                "display_number": _citation_display_number(
                    country_code=country_code,
                    standard_number=document_number,
                    kind_code=kind_code,
                    original_number=None,
                ),
                "title": title,
                "publication_date": _normalize_yyyymmdd(publication_date) or publication_date,
                "lookup_status": "resolved",
                "lookup_source": source,
                "raw": item,
            }
        )
    return documents


def iter_foreign_reference_items(raw: Any) -> list[Any]:
    if not isinstance(raw, dict):
        return []
    matches: list[Any] = []

    def walk(value: Any, key_hint: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if isinstance(child, list) and any(token in lowered for token in ("documentsinfo", "patentdocuments", "citation")):
                    matches.extend(child)
                elif isinstance(child, dict) and any(token in lowered for token in ("documentsinfo", "patentdocuments", "citation")):
                    matches.append(child)
                walk(child, lowered)
        elif isinstance(value, list):
            for child in value:
                walk(child, key_hint)

    walk(raw)
    return matches


def dedupe_foreign_reference_documents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = (
            item.get("country_code"),
            item.get("document_number"),
            item.get("kind_code"),
            item.get("title"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def foreign_reference_to_citation_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "direction": item.get("direction"),
        "country_code": item.get("country_code"),
        "publication_number": item.get("display_number") or item.get("document_number"),
        "title": item.get("title"),
        "abstract": "",
        "register_status": None,
        "claim_stats": {},
        "representative_claims": [],
        "lookup_status": item.get("lookup_status"),
        "lookup_source": item.get("lookup_source"),
    }


def normalize_foreign_claims_for_target(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, claim in enumerate(claims, 1):
        text = _clean(claim.get("text"))
        if not text:
            continue
        dependency = extract_foreign_claim_dependency(text)
        normalized.append(
            {
                "claim_no": int(claim.get("claim_no") or index),
                "text": text,
                "is_independent": dependency is None,
                "dependency": dependency,
                "is_deleted": False,
                "source": claim.get("source") or "kipris_foreign_bibliographic_claims",
            }
        )
    return normalized


def download_and_parse_foreign_patent_pdf(
    client: Any,
    patent: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    output_dir: str | Path,
) -> dict[str, Any]:
    selected = select_foreign_fulltext_pdf_with_fallback(client, patent, candidates)
    pdf_dir = Path(settings.patent_pdf_dir)
    parse_output_dir = Path(output_dir) / _safe_filename(str(patent.get("management_number") or patent.get("registration_number") or "foreign"))
    pdf_path = _download_pdf_url(
        selected["path"],
        output_dir=pdf_dir,
        filename=selected.get("doc_name") or f"{selected['literature_number']}.pdf",
        session=client.session,
        timeout=client.timeout,
    )
    parsed = parse_single_patent_pdf(pdf_path, output_dir=parse_output_dir)
    return {
        "literature_number": selected["literature_number"],
        "selected_type": selected["selected_type"],
        "source_path": selected["path"],
        "doc_name": selected.get("doc_name"),
        "pdf_path": str(pdf_path),
        "parse_output_dir": str(parse_output_dir),
        "markdown_paths": parsed.get("markdown_paths") or [],
        "markdown_text": parsed.get("markdown_text") or "",
    }


def select_foreign_fulltext_pdf_with_fallback(
    client: Any,
    patent: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, str | None]:
    try:
        selected = select_foreign_fulltext_pdf(client, candidates)
        try:
            validate_pdf_url(selected["path"], session=client.session, timeout=client.timeout)
            return selected
        except Exception:
            pass
    except Exception:
        pass

    google_pdf_url = google_patents_pdf_url(patent, session=client.session, timeout=client.timeout)
    if not google_pdf_url:
        raise RuntimeError("Could not find foreign fulltext PDF from KIPRIS or Google Patents.")
    return {
        "literature_number": google_patents_publication_id(patent),
        "selected_type": "GOOGLE_PATENTS_FULLTEXT",
        "doc_name": f"{google_patents_publication_id(patent)}.pdf",
        "path": google_pdf_url,
    }


def select_foreign_fulltext_pdf(client: Any, candidates: list[dict[str, Any]]) -> dict[str, str | None]:
    errors: list[str] = []
    for candidate in candidates:
        country_code = candidate.get("country_code")
        if not country_code:
            continue
        kind_code = str(candidate.get("kind_code") or "").upper()
        operations = (
            [("registration", client.overseas_registration_fulltext), ("open", client.overseas_open_fulltext)]
            if kind_code.startswith("B")
            else [("open", client.overseas_open_fulltext), ("registration", client.overseas_registration_fulltext)]
        )
        for literature_number in _foreign_literature_number_candidates(candidate):
            for selected_type, operation in operations:
                try:
                    raw = operation(literature_number, country_code)
                    document = extract_foreign_fulltext_document(raw)
                    if document.get("path"):
                        return {
                            "literature_number": literature_number,
                            "selected_type": f"FOREIGN_{selected_type.upper()}_FULLTEXT",
                            "doc_name": document.get("doc_name"),
                            "path": document.get("path"),
                        }
                except Exception as exc:
                    errors.append(f"{literature_number}:{selected_type}:{exc.__class__.__name__}")
    raise RuntimeError(f"Could not find KIPRIS foreign fulltext PDF path. errors={errors[:8]}")


def validate_pdf_url(url: str | None, *, session: requests.Session, timeout: float) -> None:
    if not url:
        raise RuntimeError("pdf_url_missing")
    response = session.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").lower()
    content_length = response.headers.get("content-length")
    first_chunk = b""
    for chunk in response.iter_content(chunk_size=8):
        first_chunk = chunk or b""
        break
    response.close()
    if content_length == "0" or not first_chunk:
        raise RuntimeError("empty_pdf_response")
    if "pdf" not in content_type and not first_chunk.startswith(b"%PDF"):
        raise RuntimeError(f"non_pdf_response:{content_type or 'unknown'}")


def google_patents_pdf_url(
    patent: dict[str, Any],
    *,
    session: requests.Session | None = None,
    timeout: float = 20.0,
) -> str | None:
    publication_id = google_patents_publication_id(patent)
    if not publication_id:
        return None
    http = session or requests.Session()
    for language in ("en", "zh"):
        url = f"https://patents.google.com/patent/{publication_id}/{language}"
        try:
            response = http.get(url, timeout=timeout)
            response.raise_for_status()
        except Exception:
            continue
        html = response.text
        match = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if match:
            return match.group(1)
        match = re.search(r'https://patentimages\.storage\.googleapis\.com/[^"\']+\.pdf', html, re.I)
        if match:
            return match.group(0)
    return None


def google_patents_publication_id(patent: dict[str, Any]) -> str | None:
    country = str(patent.get("country") or "").strip().upper()
    registration_number = _clean(patent.get("registration_number"))
    application_number = _clean(patent.get("application_number"))
    base = registration_number or application_number
    if not country or not base:
        return None
    normalized = re.sub(r"[^0-9A-Z]+", "", base.upper())
    if normalized.startswith(country):
        if country == "US":
            normalized = _normalize_us_publication_id(normalized)
        return normalized
    if country == "TW" and normalized.startswith("I"):
        return f"TW{normalized}"
    kind = ""
    parsed = re.search(r"\b([A-Z][0-9]?)\b\s*$", base.upper())
    if parsed:
        kind = parsed.group(1)
    elif registration_number and country in {"US", "JP"}:
        kind = "B2"
    digits = re.sub(r"\D+", "", base)
    if country == "US" and kind.startswith("A"):
        digits = _normalize_us_publication_digits(digits)
    return f"{country}{digits}{kind}"


def _normalize_us_publication_id(publication_id: str) -> str:
    match = re.fullmatch(r"US(\d+)(A\d?)", publication_id)
    if not match:
        return publication_id
    return f"US{_normalize_us_publication_digits(match.group(1))}{match.group(2)}"


def _normalize_us_publication_digits(document_number: str) -> str:
    if re.fullmatch(r"(?:19|20)\d{2}\d{1,6}", document_number):
        return f"{document_number[:4]}{document_number[4:].zfill(7)}"
    return document_number


def extract_foreign_fulltext_document(raw: Any) -> dict[str, str | None]:
    mapping = find_document_path_mapping(raw) or {}
    return {
        "doc_name": first_mapping_value(mapping, ("docName", "documentName", "fileName", "doc_name")),
        "path": first_mapping_value(mapping, ("path", "fullTextPath", "downloadPath", "filePath", "pdfPath", "url")),
    }


def find_document_path_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if first_mapping_value(value, ("path", "fullTextPath", "downloadPath", "filePath", "pdfPath", "url")):
            return value
        for child in value.values():
            found = find_document_path_mapping(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_document_path_mapping(child)
            if found:
                return found
    return None


def first_mapping_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    lower_keys = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        text = _clean(lower_keys.get(key.lower()))
        if text:
            return text
    return None


def extract_foreign_claims_from_text(text: str) -> list[dict[str, Any]]:
    normalized_text = _foreign_claims_section_text(str(text or ""))
    patterns = [
        r"(?im)^\s*(?:claim|claims?)\s*([0-9]+)\s*[:.)-]?\s*(.*)$",
        r"(?m)^\s*-?\s*([0-9]+)\s*[.)]\s*(.*)$",
        r"(?m)^\s*权利要求\s*([0-9]+)\s*(.*)$",
    ]
    matches = []
    for pattern in patterns:
        matches = list(re.finditer(pattern, normalized_text))
        if matches:
            break
    claims = []
    for index, match in enumerate(matches):
        claim_no = _int_or_none(match.group(1)) or (index + 1)
        first_line = match.group(2).strip() if len(match.groups()) >= 2 else ""
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized_text)
        body = re.sub(r"\s+", " ", f"{first_line} {normalized_text[start:end]}").strip()
        if len(body) < 20:
            continue
        dependency = extract_foreign_claim_dependency(body)
        claims.append(
            {
                "claim_no": claim_no,
                "text": body[:5000],
                "is_independent": dependency is None,
                "dependency": dependency,
                "is_deleted": False,
                "source": "kipris_foreign_fulltext_pdf",
            }
        )
    claims_by_number = {}
    for claim in claims:
        claims_by_number.setdefault(claim["claim_no"], claim)
    return [claims_by_number[claim_no] for claim_no in sorted(claims_by_number)]


def _foreign_claims_section_text(text: str) -> str:
    markers = [
        r"\bwhat\s+is\s+claimed\s+is\s*:",
        r"\bwe\s+claim\s*:",
        r"(?im)^\s*#{1,6}\s*claims?\s*$",
        r"(?im)^\s*claims?\s*$",
        r"权利要求书",
    ]
    starts = []
    for marker in markers:
        match = re.search(marker, text, re.I)
        if match:
            starts.append(match.end())
    return text[min(starts):] if starts else text


def extract_foreign_claim_dependency(text: str) -> int | None:
    patterns = [
        r"claims?\s*([0-9]+)\b",
        r"权利要求\s*([0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _int_or_none(match.group(1))
    return None


def _foreign_literature_base_numbers(candidate: dict[str, Any], document_number: str) -> list[str]:
    numbers = []
    jp_open_number = _jp_era_open_number(candidate, document_number)
    if jp_open_number:
        numbers.append(jp_open_number)
    if document_number:
        numbers.append(document_number)
    return _unique_texts(numbers)


def _foreign_literature_candidates_for_number(document_number: str, kind_code: str) -> list[str]:
    if not document_number:
        return []
    if kind_code == "A":
        return [
            f"{document_number.zfill(12)}A0",
            f"{document_number}A0",
            f"{document_number.zfill(12)}A",
            f"{document_number}A",
            f"{document_number.zfill(12)}A1",
            f"{document_number}A1",
        ]
    if kind_code:
        return [
            f"{document_number.zfill(12)}{kind_code}",
            f"{document_number}{kind_code}",
        ]
    return []


def _jp_era_open_number(candidate: dict[str, Any], document_number: str) -> str | None:
    if str(candidate.get("country_code") or "").upper() != "JP":
        return None
    if not re.fullmatch(r"\d{8}", document_number or ""):
        return None
    publication_year = _year_from_date(candidate.get("publication_date"))
    if not publication_year:
        return None
    era_year = int(document_number[:2])
    serial = document_number[2:]
    if publication_year >= 2019:
        expected_era_year = publication_year - 2018
    elif publication_year >= 1989:
        expected_era_year = publication_year - 1988
    else:
        return None
    if era_year != expected_era_year:
        return None
    return f"{publication_year}{serial}"


def _year_from_date(value: Any) -> int | None:
    match = re.search(r"(19|20)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def _foreign_literature_number_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b[A-Z]{2}\s*-?\s*([0-9][0-9A-Z./-]*)\s*-?\s*([A-Z][0-9]?)?\b", value.upper())
    if not match:
        return None
    document_number = re.sub(r"\D+", "", match.group(1))
    kind_code = match.group(2) or ""
    if not document_number:
        return None
    return f"{document_number.zfill(12)}{kind_code}"


def _fetch_foreign_claims_from_bigquery(candidates: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    from services.patent.bigquery_patent_service import fetch_foreign_claims_from_bigquery

    return fetch_foreign_claims_from_bigquery(candidates, **kwargs)


def _dedupe_foreign_claim_lookup_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = (
            item.get("country_code"),
            item.get("document_number"),
            item.get("kind_code"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _citation_display_number(
    *,
    country_code: str | None,
    standard_number: str | None,
    kind_code: str | None,
    original_number: str | None,
) -> str:
    if country_code and standard_number:
        return " ".join(part for part in [f"{country_code}{standard_number}", kind_code] if part)
    return original_number or ""


def _citation_dedupe_key(item: dict[str, Any]) -> str:
    if item.get("is_standardized"):
        return "|".join(
            [
                str(item.get("country_code") or ""),
                str(item.get("standard_number") or ""),
                str(item.get("kind_code") or ""),
            ]
        )
    return _citation_original_key(item) or str(item.get("display_number") or "")


def _citation_original_key(item: dict[str, Any]) -> str:
    return re.sub(r"\s+", "", str(item.get("original_number") or item.get("display_number") or "")).upper()


def _normalize_yyyymmdd(value: str | None) -> str | None:
    text = _clean(value)
    if not text or not re.match(r"^\d{8}$", text):
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _unique_texts(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _ensure_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _first_item(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _get_path(data: dict[str, Any], keys: list[str]) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _strip_register_suffix(value: str | None) -> str | None:
    # EXT-09: 등록번호 항차(권리 구분 4자리) 제거. 하이픈 표기('10-0309314-0001')와
    # 13자리 압축 표기('1003093140000')를 모두 처리하되, 13자리가 아닌 해외 번호는 보존한다.
    if not value:
        return None
    text = str(value).strip()
    text = re.sub(r"-\d{4}$", "", text)
    if re.fullmatch(r"\d{13}", text):
        text = text[:-4]
    return text or None


def _normalize_dot_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})$", value)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else value


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def _select_fulltext_pdf(
    client: Any,
    application_numbers: list[str],
    *,
    prefer_announcement: bool,
) -> dict[str, str | None]:
    errors: list[str] = []
    empty_responses: list[str] = []
    for application_number in application_numbers:
        if prefer_announcement:
            try:
                announcement = client.announcement_fulltext_pdf_path(application_number)
                if announcement.path:
                    return {
                        "application_number": application_number,
                        "selected_type": "ANNOUNCEMENT_FULLTEXT_PDF",
                        "doc_name": announcement.doc_name,
                        "path": announcement.path,
                    }
                empty_responses.append(_summarize_document_response(f"announcement:{application_number}", announcement.raw))
            except Exception as exc:
                errors.append(f"announcement:{application_number}: {exc}")

        try:
            publication = client.publication_fulltext_pdf_path(application_number)
            if publication.path:
                return {
                    "application_number": application_number,
                    "selected_type": "PUBLICATION_FULLTEXT_PDF",
                    "doc_name": publication.doc_name,
                    "path": publication.path,
                }
            empty_responses.append(_summarize_document_response(f"publication:{application_number}", publication.raw))
        except Exception as exc:
            errors.append(f"publication:{application_number}: {exc}")

    raise RuntimeError(
        "Could not find KIPRIS fulltext PDF path. "
        f"application_numbers={application_numbers}, errors={errors}, responses={empty_responses}"
    )


def _download_pdf_url(
    url: str,
    *,
    output_dir: Path,
    filename: str,
    session: requests.Session,
    timeout: float,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(filename)
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"

    # EXT-07: 외부 PDF URL을 검증 없이 받지 않는다 — 스킴/사설·링크로컬 IP(메타데이터 169.254.169.254 등) SSRF 차단.
    from services.evidence.news_article_extraction_service import validate_article_url
    block_reason = validate_article_url(url)
    if block_reason:
        raise RuntimeError(f"pdf_url_blocked:{block_reason}")

    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("empty_pdf_response")

    file_path = output_dir / safe_name
    file_path.write_bytes(response.content)
    return file_path


def _safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", value).strip("_")


def normalize_kipris_application_number(application_number: str) -> str:
    return re.sub(r"\D+", "", application_number)


def fulltext_application_number_candidates(application_number: str) -> list[str]:
    candidates = [
        normalize_kipris_application_number(application_number),
        " ".join(str(application_number or "").split()),
    ]
    result = []
    for candidate in candidates:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _summarize_document_response(label: str, raw: dict[str, Any]) -> str:
    response = raw.get("response", {}) if isinstance(raw, dict) else {}
    header = response.get("header", {}) if isinstance(response, dict) else {}
    body = response.get("body", {}) if isinstance(response, dict) else {}
    item = body.get("item") if isinstance(body, dict) else None
    return (
        f"{label}: resultCode={header.get('resultCode')}, "
        f"resultMsg={header.get('resultMsg')}, item={item}"
    )
