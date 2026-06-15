from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from html import unescape
import sqlite3
import shutil
import subprocess
from typing import Any
import io
import re
import shutil
import subprocess
import tempfile

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
        result["citing_document_records"] = citing_document_records(result["citing_documents"])
        result["citing_stats"] = build_citing_stats(result["citing_documents"])
    except Exception as exc:
        result["citing_documents"] = []
        result["citing_document_records"] = []
        result["citing_stats"] = {
            "available": False,
            "total_count": None,
            "standardized_count": None,
            "non_standardized_count": None,
            "missing_reason": "kipris_citing_info_fetch_failed",
        }
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


def fetch_kipris_bibliography_basic(application_number: str) -> dict[str, Any]:
    """서지상세(bibliography_detail) 1회만 호출하는 경량 버전.

    포트폴리오 sibling 보강처럼 제목·초록·청구항·IPC/CPC만 필요하고 패밀리·인용·
    피인용·인용근거는 쓰지 않는 경우에 사용한다. KIPRIS 호출을 특허당 5+회에서
    1회로 줄인다.
    """
    client = _kipris_client()
    kipris_application_number = normalize_kipris_application_number(application_number)
    raw = client.bibliography_detail(kipris_application_number)
    return normalize_kipris_bibliography(raw, application_number=application_number)


def fetch_kipris_abstract(application_number: str) -> str:
    """초록만 필요한 경로에서 부가 API 호출 없이 텍스트를 조회한다."""
    normalized = fetch_kipris_bibliography_basic(application_number)
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
        "citing_stats": {
            "available": False,
            "total_count": None,
            "standardized_count": None,
            "non_standardized_count": None,
            "missing_reason": "foreign_citing_api_not_connected",
        },
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
    try:
        bibliography = fetch_foreign_target_bibliography(client, candidates, patent=patent)
        if bibliography:
            result["metadata"] = merge_foreign_metadata(result["metadata"], bibliography.get("metadata") or {})
            result["sections"] = {
                **result["sections"],
                **(bibliography.get("sections") or {}),
            }
            if bibliography.get("source_type"):
                result["source_type"] = bibliography["source_type"]
            if bibliography.get("foreign_bibliography_literature_number"):
                result["foreign_bibliography_literature_number"] = bibliography["foreign_bibliography_literature_number"]
            if bibliography.get("raw") is not None:
                result["raw_bibliography"] = bibliography["raw"]
    except Exception as exc:
        result["warnings"].append(
            f"foreign_bibliography_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
        )
    result.update(fetch_foreign_target_reference_data(client, candidates, patent=patent))
    # 해외 인용(citation_documents)에서 prior_art 목록을 채운다. 국내 경로(normalize_kipris_citations)는
    # metadata["prior_art"]를 채우지만 해외 경로는 비워두던 탓에 비교문헌 빌드 게이트가 막혀 있었다.
    result["metadata"]["prior_art"] = [
        item["display_number"]
        for item in (result.get("citation_documents") or [])
        if isinstance(item, dict) and item.get("display_number")
    ]
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
        if not claims:
            pdf_claims = extract_foreign_claims_from_text(parsed_pdf.get("markdown_text") or "")
            google_claims = fetch_google_patents_target_claims(client, patent)
            resolved_claims = google_claims if len(google_claims) > len(pdf_claims) else pdf_claims
        else:
            resolved_claims = []
        if resolved_claims:
            result["claims"] = resolved_claims
            result["claim_stats"] = _build_api_claim_stats(len(resolved_claims), resolved_claims)
            result["metadata"]["claim_count"] = len(resolved_claims)
            result["metadata"]["reported_claim_count"] = len(resolved_claims)
            result["warnings"] = [
                warning for warning in result["warnings"] if warning != "kipris_foreign_claims_not_found"
            ]
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
    if value in {"GOOGLE_PATENTS_FULLTEXT", "GOOGLE_PATENTS_HTML_FULLTEXT"}:
        return "google_patents"
    if value.startswith("FOREIGN_"):
        return "kipris"
    return None


def fetch_google_patents_target_claims(client: Any, patent: dict[str, Any]) -> list[dict[str, Any]]:
    publication_id = google_patents_publication_id(patent)
    if not publication_id:
        return []
    best_claims: list[dict[str, Any]] = []
    for language in ("en", "zh", "ja"):
        try:
            response = client.session.get(
                f"https://patents.google.com/patent/{publication_id}/{language}",
                timeout=client.timeout,
            )
            response.raise_for_status()
        except Exception:
            continue
        html = decode_google_patents_html_response(response)
        claims = extract_foreign_claims_from_text("\n".join(_google_patents_claim_texts(html)))
        for claim in claims:
            claim["source"] = "google_patents_html_claims"
        if len(claims) > len(best_claims):
            best_claims = claims
    return best_claims


def foreign_patent_metadata_from_db(patent: dict[str, Any]) -> dict[str, Any]:
    country = str(patent.get("country") or "").strip().upper() or None
    title = patent.get("title_final") or patent.get("title_draft")
    return {
        "country": country,
        "patent_type": "등록특허" if patent.get("status") == "등록" else None,
        "registration_number": patent.get("registration_number"),
        "application_number": patent.get("application_number"),
        "publication_number": None,
        "title": title,
        "title_eng": title if country == "US" else None,
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


def merge_foreign_metadata(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if value in (None, "", []):
            continue
        merged[key] = value
    if not merged.get("representative_ipc") and (merged.get("ipc") or []):
        merged["representative_ipc"] = merged["ipc"][0]
    merged["assignee_count"] = len(merged.get("assignee") or [])
    merged["has_co_assignee"] = merged["assignee_count"] > 1
    return merged
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


def fetch_foreign_target_bibliography(
    client: Any,
    candidates: list[dict[str, Any]],
    *,
    patent: dict[str, Any],
) -> dict[str, Any] | None:
    attempts: list[dict[str, Any]] = []
    for candidate in candidates:
        country_code = candidate.get("country_code")
        if not country_code:
            continue
        for literature_number in _foreign_literature_number_candidates(candidate):
            try:
                raw = client.overseas_bibliographic_info(literature_number, country_code)
            except Exception as exc:
                attempts.append(
                    {
                        "literature_number": literature_number,
                        "country_code": country_code,
                        "status": "exception",
                        "error": f"{exc.__class__.__name__}:{str(exc)[:300]}",
                    }
                )
                continue
            normalized = normalize_foreign_bibliography(
                raw,
                patent=patent,
                literature_number=literature_number,
                country_code=country_code,
            )
            if normalized:
                normalized["bibliography_attempts"] = attempts + [
                    {
                        "literature_number": literature_number,
                        "country_code": country_code,
                        "status": "matched",
                        "raw_excerpt": summarize_foreign_bibliography_raw(raw),
                    }
                ]
                return normalized
            attempts.append(
                {
                    "literature_number": literature_number,
                    "country_code": country_code,
                    "status": "no_match",
                    "raw_excerpt": summarize_foreign_bibliography_raw(raw),
                }
            )
    if attempts:
        return {
            "source_type": "kipris_foreign_bibliographic_info",
            "metadata": {},
            "sections": {},
            "bibliography_attempts": attempts,
        }
    return None


def normalize_foreign_bibliography(
    raw: dict[str, Any],
    *,
    patent: dict[str, Any],
    literature_number: str,
    country_code: str,
) -> dict[str, Any] | None:
    node = (
        _get_path(raw, ["response", "body", "items", "bibliographicInfo"])
        or _get_path(raw, ["response", "body", "bibliographicInfo"])
        or _get_path(raw, ["response", "body", "items", "item"])
        or _get_path(raw, ["response", "body", "item"])
        or _get_path(raw, ["response", "body", "items"])
        or _get_path(raw, ["response", "body"])
        or {}
    )
    if not isinstance(node, dict):
        return None

    summary = (
        _get_path(node, ["bibliographicSummaryInfo"])
        or _find_first_mapping_with_keys(
            node,
            (
                "inventionTitle",
                "title",
                "applicationNumber",
                "registerNumber",
                "publicationNumber",
                "ipcNumber",
                "astrtCont",
                "abstract",
            ),
        )
        or node
    )
    abstract_mapping = _find_first_mapping_with_keys(node, ("astrtCont", "abstract", "abstractText", "abstractContent")) or {}
    applicants = _find_people_values(
        node,
        name_keys=("applicantName", "name", "applicant", "assigneeName"),
        eng_name_keys=("applicantEngName", "engName", "applicantNameEng", "assigneeEngName"),
        container_hints=("applicant", "assignee"),
    )
    inventors = _find_people_values(
        node,
        name_keys=("inventorName", "name", "inventor"),
        eng_name_keys=("inventorEngName", "engName", "inventorNameEng"),
        container_hints=("inventor",),
    )
    ipc_values = _unique_texts(
        [
            *_find_recursive_values(node, ("ipcCd",)),
            *_find_recursive_values(node, ("ipcNumber", "internationalpatentclassificationNumber", "ipc")),
        ]
    )
    cpc_values = _unique_texts(
        [
            *_find_recursive_values(node, ("cpcCd",)),
            *_find_recursive_values(node, ("cpcNumber", "cooperativePatentClassificationNumber", "cpc")),
        ]
    )
    abstract = _first_present_text(abstract_mapping, ("astrtCont", "abstract", "abstractText", "abstractContent"))
    title = _first_present_text(summary, ("inventionTitle", "title", "inventionTitleEng"))

    metadata = {
        "country": country_code,
        "patent_type": "등록특허" if patent.get("status") == "등록" else None,
        "registration_number": _strip_register_suffix(
            _first_present_text(summary, ("registerNumber", "registrationNumber", "patentNumber"))
            or patent.get("registration_number")
        ),
        "application_number": _first_present_text(summary, ("applicationNumber",)) or patent.get("application_number"),
        "publication_number": _first_present_text(summary, ("publicationNumber", "openNumber", "usNo")),
        "title": title or patent.get("title_final") or patent.get("title_draft"),
        "title_eng": _first_present_text(summary, ("inventionTitleEng", "titleEng")) or patent.get("title_draft"),
        "assignee": applicants["names"],
        "assignee_eng": applicants["eng_names"],
        "inventors": inventors["names"],
        "inventors_eng": inventors["eng_names"],
        "filing_date": _normalize_foreign_date(
            _first_present_text(summary, ("applicationDate", "filingDate"))
            or patent.get("application_date")
        ),
        "registration_date": _normalize_foreign_date(
            _first_present_text(summary, ("registerDate", "registrationDate"))
            or patent.get("registration_date")
        ),
        "publication_date": _normalize_foreign_date(_first_present_text(summary, ("publicationDate", "openDate"))),
        "open_date": _normalize_foreign_date(_first_present_text(summary, ("openDate",))),
        "ipc": ipc_values,
        "representative_ipc": ipc_values[0] if ipc_values else "",
        "cpc": cpc_values,
        "examiner": _first_present_text(summary, ("examinerName", "examiner")),
        "claim_count": _int_or_none(_first_present_text(summary, ("claimCount",))),
        "reported_claim_count": _int_or_none(_first_present_text(summary, ("claimCount",))),
        "register_status": _first_present_text(summary, ("registerStatus", "status")) or patent.get("status"),
        "final_disposal": _first_present_text(summary, ("finalDisposal",)),
        "prior_art": [],
        "expected_expiration_date": patent.get("expected_expiration_date"),
    }
    metadata["assignee_count"] = len(metadata["assignee"])
    metadata["has_co_assignee"] = metadata["assignee_count"] > 1

    if not any(
        [
            metadata.get("title"),
            metadata.get("ipc"),
            abstract,
            metadata.get("assignee"),
            metadata.get("inventors"),
        ]
    ):
        return None

    return {
        "source_type": "kipris_foreign_bibliographic_info",
        "foreign_bibliography_literature_number": literature_number,
        "metadata": metadata,
        "sections": {"abstract": abstract or ""},
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
    country: str | None = None,
) -> dict[str, Any]:
    java_path = shutil.which("java")
    if not java_path:
        raise RuntimeError(
            "java_runtime_missing: install Java or configure JAVA_HOME before PDF parsing"
        )
    java_check = subprocess.run(
        [java_path, "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if java_check.returncode != 0:
        raise RuntimeError(
            "java_runtime_unavailable: install Java or configure JAVA_HOME before PDF parsing"
        )

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
    parse_warning = None
    # 좌→우 컬럼 추출(left_right)과 일반 Tesseract(eng) OCR은 **미국 특허 전용** 경로다(US 2단 조판,
    # 이미지형 PDF 대응). 그 외(KR 국내·CN/JP/TW 해외·country 미상)는 이 경로를 타지 않고
    # opendataloader가 만든 마크다운을 그대로 사용한다:
    #  - KR 국내: KIPRIS 전문 PDF를 opendataloader로 파싱(원래 알고리즘).
    #  - CN/JP/TW: opendataloader 이미지 마크다운을 두고 하위 apply_foreign_pdf_ocr_fallback이
    #    국가별 언어팩(chi_sim/jpn/chi_tra)으로 OCR.
    # country가 "US"일 때만 left_right를 적용한다(None=KR 국내 등은 opendataloader).
    is_us_layout = str(country or "").strip().upper() == "US"
    if is_us_layout:
        column_text = extract_pdf_text_left_then_right(pdf_path)
        if has_meaningful_pdf_text(column_text):
            markdown_text = column_text
        if should_run_ocr_fallback(markdown_text):
            ocr_text = extract_pdf_text_with_ocr(pdf_path)
            if not has_meaningful_pdf_text(ocr_text):
                raise RuntimeError("foreign_pdf_text_extraction_failed_after_ocr")
            markdown_text = ocr_text
            parse_warning = "ocr_fallback_used"
    suffix = "_left_right" if is_us_layout else ""
    normalized_markdown_path = output_dir / f"{pdf_path.stem}{suffix}.md"
    normalized_markdown_path.write_text(markdown_text, encoding="utf-8")
    for path in after:
        if path.resolve() == normalized_markdown_path.resolve():
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
    return {
        "markdown_paths": [str(normalized_markdown_path)],
        "markdown_text": markdown_text,
        "parse_warning": parse_warning,
    }


_kipris_client_instance: Any = None


def has_meaningful_pdf_text(text: str | None) -> bool:
    normalized = str(text or "").strip()
    if re.search(r"\babstract\b", normalized, re.I):
        return True
    if re.search(r"\bclaims?\b", normalized, re.I):
        return True
    if re.search(r"\b\d+\.\s+\S+", normalized):
        return True
    if len(normalized) < 200:
        return False
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    non_image_lines = [line for line in lines if not re.fullmatch(r"!\[image\s+\d+\]\(.+\)", line, re.I)]
    return len(" ".join(non_image_lines)) >= 400


def should_run_ocr_fallback(markdown_text: str | None) -> bool:
    normalized = str(markdown_text or "")
    if has_meaningful_pdf_text(normalized):
        return False
    image_only_lines = [
        line.strip()
        for line in normalized.splitlines()
        if line.strip()
    ]
    if image_only_lines and all(line.startswith("![image ") for line in image_only_lines):
        return True
    return True


def extract_pdf_text_with_ocr(pdf_path: str | Path) -> str:
    tesseract_cmd = shutil.which("tesseract")
    pdftoppm_cmd = shutil.which("pdftoppm")
    if not tesseract_cmd or not pdftoppm_cmd:
        missing = []
        if not tesseract_cmd:
            missing.append("tesseract")
        if not pdftoppm_cmd:
            missing.append("pdftoppm")
        raise RuntimeError(f"ocr_tools_not_available:{','.join(missing)}")

    pdf_path = Path(pdf_path)
    with tempfile.TemporaryDirectory(prefix="patent_ocr_") as temp_dir:
        image_prefix = Path(temp_dir) / "page"
        subprocess.run(
            [pdftoppm_cmd, "-png", str(pdf_path), str(image_prefix)],
            check=True,
            capture_output=True,
            text=True,
        )
        image_paths = sorted(Path(temp_dir).glob("page-*.png"))
        if not image_paths:
            raise RuntimeError("ocr_page_render_failed")
        texts: list[str] = []
        for image_path in image_paths:
            page_text = ocr_page_left_then_right(image_path, tesseract_cmd=tesseract_cmd, temp_dir=Path(temp_dir))
            if page_text and not should_exclude_pdf_page_text(page_text):
                texts.append(page_text)
        return trim_foreign_front_matter("\n\n".join(texts))


def ocr_page_left_then_right(image_path: Path, *, tesseract_cmd: str, temp_dir: Path) -> str:
    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size
        midpoint = width // 2
        crops = [
            ("left", image.crop((0, 0, midpoint, height))),
            ("right", image.crop((midpoint, 0, width, height))),
        ]
        page_parts: list[str] = []
        for label, crop in crops:
            crop_path = temp_dir / f"{image_path.stem}_{label}.png"
            crop.save(crop_path)
            text = ocr_image_text(crop_path, tesseract_cmd=tesseract_cmd).strip()
            if text:
                page_parts.append(text)
        return "\n\n".join(page_parts)


def extract_pdf_text_left_then_right(pdf_path: str | Path) -> str:
    try:
        import pdfplumber
    except Exception:
        return ""

    pdf_path = Path(pdf_path)
    pages: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            width = float(page.width or 0)
            height = float(page.height or 0)
            if width <= 0 or height <= 0:
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text)
                continue

            midpoint = width / 2.0
            page_parts: list[str] = []
            for bbox in ((0, 0, midpoint, height), (midpoint, 0, width, height)):
                cropped = page.crop(bbox)
                text = cropped.extract_text() or ""
                text = text.strip()
                if text:
                    page_parts.append(text)
            if page_parts:
                page_text = "\n\n".join(page_parts)
                if not should_exclude_pdf_page_text(page_text):
                    pages.append(page_text)
    return trim_foreign_front_matter("\n\n".join(pages))


def should_exclude_pdf_page_text(text: str | None) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True

    if re.search(r"(?i)\b(?:sheet|heet)\s+\d+\s+of\s+\d+\b", normalized):
        return True

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    figure_lines = sum(1 for line in lines if re.search(r"(?i)\bFIG(?:S)?\b", line))
    drawing_sheet_lines = sum(
        1
        for line in lines
        if re.search(r"(?i)\b(?:sheet|heet)\s+\d+\s+of\s+\d+\b", line)
        or re.search(r"(?i)\bU\.?S\.?\s+Patent\b", line)
    )
    prose_lines = sum(1 for line in lines if re.search(r"[A-Za-z]{4,}.*[A-Za-z]{4,}", line))

    if figure_lines >= 2 and prose_lines <= 8:
        return True
    if drawing_sheet_lines >= 2 and prose_lines <= 10:
        return True

    alpha_chars = sum(1 for ch in normalized if ch.isalpha())
    digit_chars = sum(1 for ch in normalized if ch.isdigit())
    if alpha_chars < 200 and digit_chars > alpha_chars // 2 and (figure_lines or drawing_sheet_lines):
        return True

    return False


def trim_foreign_front_matter(text: str | None) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""

    body_heading = re.search(
        r"(?im)^\s*(?:TECHNICAL FIELD|BACKGROUND ART|BACKGROUND|DISCLOSURE|SUMMARY|BEST MODE|DETAILED DESCRIPTION|DESCRIPTION OF DRAWINGS|BRIEF DESCRIPTION OF (?:THE )?DRAWINGS)\b.*$",
        normalized,
    )
    if not body_heading:
        return normalized

    return normalized[body_heading.start() :].strip()


def ocr_image_text(image_path: Path, *, tesseract_cmd: str) -> str:
    result = subprocess.run(
        [tesseract_cmd, str(image_path), "stdout"],
        check=True,
        capture_output=True,
        text=True,
    )
    return str(result.stdout or "").strip()


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
    if match:
        return _int_or_none(match.group(1))
    return extract_foreign_claim_dependency(text)


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


def build_citing_stats(citing_documents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "available": True,
        "total_count": len(citing_documents),
        "standardized_count": sum(1 for item in citing_documents if item.get("is_standardized")),
        "non_standardized_count": sum(1 for item in citing_documents if not item.get("is_standardized")),
    }


def citing_document_records(citing_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "country_code",
        "document_number",
        "kind_code",
        "display_number",
        "publication_number",
        "priority_date",
        "publication_date",
        "assignee",
        "title",
        "examiner_cited",
        "citing_application_number",
        "standard_citation_application_number",
        "is_standardized",
        "source",
    )
    return [
        {key: item.get(key) for key in fields if item.get(key) is not None}
        for item in citing_documents
        if isinstance(item, dict)
    ]


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
                warnings=warnings,
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
    warnings: list[str] | None = None,
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
    except Exception as exc:
        bigquery_documents = []
        if warnings is not None:
            warnings.append(
                f"bigquery_claims_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
            )
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
        if _has_prior_art_detail(document):
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
            country=str(candidate.get("country_code") or "").strip().upper() or None,
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
    auxiliary_document: dict[str, Any] = {}
    for language in ("en", "zh", "ja"):
        try:
            response = client.session.get(
                f"https://patents.google.com/patent/{publication_id}/{language}",
                timeout=client.timeout,
            )
            response.raise_for_status()
        except Exception:
            continue
        html = decode_google_patents_html_response(response)
        title = _google_patents_meta_content(html, "DC.title")
        abstract = (
            _google_patents_meta_content(html, "DC.description")
            or _google_patents_section_text(html, "abstract")
        )
        claim_text = "\n".join(_google_patents_claim_texts(html))
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
        if _has_prior_art_detail(document) and not auxiliary_document:
            auxiliary_document = document
    return auxiliary_document


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
        "comparison_status": (
            "claim_comparison_ready"
            if claims
            else "abstract_only"
            if _clean(abstract)
            else "fulltext_claims_unparsed"
            if lookup_source == "google_patents_pdf"
            else "identifier_only"
        ),
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
    numbered_claims = []
    for attributes, body in re.findall(
        r'<div\b([^>]*)>\s*'
        r'<div\b[^>]*class=["\'][^"\']*\bclaim-text\b[^"\']*["\'][^>]*>(.*?)</div>',
        text or "",
        re.I | re.S,
    ):
        parsed_attributes = {
            key.lower(): unescape(value)
            for key, _, value in re.findall(r"""([:\w-]+)\s*=\s*(["'])(.*?)\2""", attributes, re.S)
        }
        if "claim" not in parsed_attributes.get("class", "").split():
            continue
        number = parsed_attributes.get("num")
        if not number or not number.isdigit():
            continue
        cleaned = _strip_html(body)
        if cleaned:
            numbered_claims.append(f"{number}. {cleaned}")
    if numbered_claims:
        return numbered_claims

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
        and document.get("representative_claims")
    )


def _has_prior_art_detail(document: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(document, dict)
        and document.get("comparison_status") != "identifier_only"
        and (
            document.get("representative_claims")
            or _clean(document.get("abstract"))
            or document.get("comparison_status") == "fulltext_claims_unparsed"
        )
    )


def _prior_art_comparison_status(document: dict[str, Any]) -> str:
    status = str(document.get("comparison_status") or "")
    if status == "comparison_ready" or document.get("representative_claims"):
        return "claim_comparison_ready"
    if status:
        return status
    if _clean(document.get("abstract")):
        return "abstract_only"
    return "identifier_only"


def _prior_art_collection_status(
    candidates: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    claim_ready_count = sum(
        1 for document in documents if _prior_art_comparison_status(document) == "claim_comparison_ready"
    )
    abstract_only_count = sum(
        1 for document in documents if _prior_art_comparison_status(document) == "abstract_only"
    )
    claims_unparsed_count = sum(
        1 for document in documents if _prior_art_comparison_status(document) == "fulltext_claims_unparsed"
    )
    resolved_count = claim_ready_count + abstract_only_count + claims_unparsed_count
    return {
        "candidate_count": len(candidates),
        "comparison_ready_count": claim_ready_count,
        "claim_comparison_ready_count": claim_ready_count,
        "abstract_only_count": abstract_only_count,
        "fulltext_claims_unparsed_count": claims_unparsed_count,
        "identifier_only_count": max(0, len(candidates) - resolved_count),
        "comparison_status": (
            "claim_comparison_ready"
            if claim_ready_count
            else "abstract_only"
            if abstract_only_count
            else "fulltext_claims_unparsed"
            if claims_unparsed_count
            else "unknown"
        ),
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
        # 마지막 보루로 kind 없는 12자리 패딩만 둔다(패딩 안 한 raw는 매칭 안 돼 제거).
        candidates.append(document_number.zfill(12))
    return _unique_texts(candidates)


def foreign_target_literature_candidates(patent: dict[str, Any]) -> list[dict[str, Any]]:
    country = str(patent.get("country") or "").strip().upper()
    candidates = []
    # CN 해외 문헌번호는 출원번호 기반(12자리+A0/B0)만 유효하다. 등록/공개번호 기반은 전부 빈값을
    # 돌려줘 헛호출만 되므로 CN은 출원번호를 먼저 시도한다. US/JP는 등록번호 기반이 맞아 기존 순서 유지.
    source_order = ("application_number", "registration_number") if country == "CN" else ("registration_number", "application_number")
    for source_field in source_order:
        value = _clean(patent.get(source_field))
        if not value:
            continue
        document_numbers = _foreign_target_document_numbers(country, value, source_field=source_field)
        if not document_numbers:
            continue
        kind_codes = foreign_target_kind_codes(country, value, source_field=source_field)
        for document_number in document_numbers:
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


def _foreign_target_document_numbers(country: str, value: str, *, source_field: str) -> list[str]:
    """KIPRIS 해외 문헌번호의 숫자 본체 후보를 만든다.

    CN 출원번호는 끝에 검증숫자(점 뒤 1자리)가 붙어 13자리가 되는데, KIPRIS 해외 문헌번호는
    검증숫자를 뺀 12자리 출원번호 본체를 요구한다(ex: 201780067437.9 → 201780067437A0).
    따라서 CN 출원번호는 점 앞 본체를 먼저 시도하도록 앞에 둔다.
    """
    full = re.sub(r"\D+", "", value)
    if country == "CN" and source_field == "application_number" and "." in value:
        # CN 출원번호는 검증숫자(점 뒤 1자리)를 뺀 12자리 본체만 KIPRIS에 매칭된다.
        # 검증숫자 포함 13자리(full)는 매칭 안 돼 호출만 낭비하므로 본체만 쓴다.
        base = re.sub(r"\D+", "", value.split(".")[0])
        if base:
            return [base]
    return _unique_texts([full] if full else [])


def foreign_target_kind_codes(country: str, value: str, *, source_field: str) -> list[str]:
    parsed = re.search(r"\b([A-Z][0-9]?)\b\s*$", str(value or "").strip().upper())
    parsed_kind = parsed.group(1) if parsed else ""
    # CN은 출원번호 12자리+A0(공개)/B0(등록)만 KIPRIS에 매칭된다. A/A1/B2/B 등 변형은 호출만 낭비.
    if source_field == "application_number":
        return _unique_texts([parsed_kind, "A0"] if country == "CN" else [parsed_kind, "A1", "A"])
    if country in {"US", "JP"}:
        return _unique_texts([parsed_kind, "B2", "B1", "B"])
    if country == "CN":
        return _unique_texts([parsed_kind, "A0", "B0"])
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
    patent: dict[str, Any] | None = None,
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
    cited_source = "kipris_foreign_patent_documents"

    # Google Patents HTML 1회로 인용(backward)·피인용(forward)을 함께 받는다.
    google_cited, citing_documents, citing_available = fetch_google_patents_references(
        client,
        patent or {},
        max_documents=max_documents,
    )
    # KIPRIS 해외 인용이 비는 국가(예: CN)는 Google Patents 인용으로 비교문헌 후보를 채운다.
    if not cited_documents and google_cited:
        cited_documents = dedupe_foreign_reference_documents(google_cited)[:max_documents]
        cited_source = "google_patents_html_backward_references"

    # 해외 참조 문헌엔 KR build_citation_stats가 쓰는 is_standardized 판정 근거가 없어
    # 표준화/비표준화 분해 자체가 불가능하다. 모두 표준화로 집계하면 KR과 의미가 어긋나므로
    # 표준화 분해는 미상으로 노출한다.
    stats = {
        "total_count": len(cited_documents),
        "standardized_count": None,
        "non_standardized_count": None,
        "missing_reason": "foreign_citation_standardization_basis_unavailable",
    }
    citing_stats = build_citing_stats(citing_documents)
    citing_stats["available"] = citing_available
    if not citing_available:
        citing_stats["missing_reason"] = "google_patents_forward_references_unavailable"
    api_collection = {
        "target_cited_references": {
            "available": bool(cited_documents),
            "source": cited_source,
            "used_literature_numbers": used_literature_numbers,
            "count": len(cited_documents),
        },
        "target_citing_references": {
            "available": citing_available,
            "source": "google_patents_html_forward_references" if citing_available else None,
            "count": len(citing_documents) if citing_available else None,
            "missing_reason": None if citing_available else "google_patents_forward_references_unavailable",
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
        "citing_documents": citing_documents,
        "citing_document_records": citing_document_records(citing_documents),
        "citing_stats": citing_stats,
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


def fetch_google_patents_citing_documents(
    client: Any,
    patent: dict[str, Any],
    *,
    max_documents: int = 20,
) -> tuple[list[dict[str, Any]], bool]:
    publication_id = google_patents_publication_id(patent)
    if not publication_id:
        return [], False
    for language in ("en", "zh", "ja"):
        try:
            response = client.session.get(
                f"https://patents.google.com/patent/{publication_id}/{language}",
                timeout=getattr(client, "timeout", 20.0),
            )
            response.raise_for_status()
        except Exception:
            continue
        html = decode_google_patents_html_response(response)
        return _google_patents_forward_references(html)[:max_documents], True
    return [], False


def fetch_google_patents_references(
    client: Any,
    patent: dict[str, Any],
    *,
    max_documents: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Google Patents HTML을 한 번만 받아 인용(backward)·피인용(forward)을 함께 파싱한다.

    반환: (cited_documents, citing_documents, available).
    """
    publication_id = google_patents_publication_id(patent)
    if not publication_id:
        return [], [], False
    for language in ("en", "zh", "ja"):
        try:
            response = client.session.get(
                f"https://patents.google.com/patent/{publication_id}/{language}",
                timeout=getattr(client, "timeout", 20.0),
            )
            response.raise_for_status()
        except Exception:
            continue
        html = decode_google_patents_html_response(response)
        cited = _google_patents_backward_reference_documents(html)[:max_documents]
        citing = _google_patents_forward_references(html)[:max_documents]
        return cited, citing, True
    return [], [], False


def normalize_foreign_reference_documents(raw: Any, *, source: str, direction: str) -> list[dict[str, Any]]:
    documents = []
    for item in iter_foreign_reference_items(raw):
        if not isinstance(item, dict):
            continue
        # KIPRIS 해외 인용 API(usPatentDocuments/foreignPatentDocuments)는 인용 선행문헌 번호를
        # `corgPatno`(Cited ORiGinal PATent No)로, 국가를 `cntryCodeLink`(자국)·`countryCode`(타국)로,
        # 공개일을 `patDt`로 준다. 이 필드명을 빠뜨려 번호가 전부 null로 떨어지던 것을 보완한다.
        country_code = first_mapping_value(item, ("countryCode", "CountryCode", "cntryCodeLink", "cntryCode", "citationCountryCode", "documentCountryCode"))
        document_number = first_mapping_value(
            item,
            (
                "corgPatno",
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
        publication_date = first_mapping_value(item, ("publicationDate", "PublicationDate", "openDate", "OpenDate", "patDt"))
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
    parsed_result = _download_and_parse_foreign_selection(
        client,
        selected,
        country=patent.get("country"),
        pdf_dir=pdf_dir,
        parse_output_dir=parse_output_dir,
    )
    if foreign_fulltext_parse_is_usable(parsed_result.get("markdown_text") or ""):
        return parsed_result

    downloaded_pdf_result = parsed_result
    if selected.get("selected_type") != "GOOGLE_PATENTS_FULLTEXT":
        google_selection = google_patents_fulltext_selection(client, patent)
        if google_selection:
            google_result = _download_and_parse_foreign_selection(
                client,
                google_selection,
                country=patent.get("country"),
                pdf_dir=pdf_dir,
                parse_output_dir=parse_output_dir / "google_patents",
            )
            downloaded_pdf_result = google_result
            if foreign_fulltext_parse_is_usable(google_result.get("markdown_text") or ""):
                google_result["fallback_reason"] = "kipris_pdf_parse_unusable"
                return google_result

    html_result = download_google_patents_html_fulltext(
        client,
        patent,
        output_dir=parse_output_dir / "google_patents_html",
    )
    if foreign_fulltext_parse_is_usable(html_result.get("markdown_text") or ""):
        html_result["fallback_reason"] = "foreign_pdf_parse_unusable"
        html_result["pdf_path"] = downloaded_pdf_result.get("pdf_path")
        html_result["pdf_source_path"] = downloaded_pdf_result.get("source_path")
        return html_result
    raise RuntimeError("Foreign fulltext was downloaded but no usable text or claims were extracted.")


def _download_and_parse_foreign_selection(
    client: Any,
    selected: dict[str, Any],
    *,
    country: Any = None,
    pdf_dir: Path,
    parse_output_dir: Path,
) -> dict[str, Any]:
    pdf_path = _download_pdf_url(
        selected["path"],
        output_dir=pdf_dir,
        filename=selected.get("doc_name") or f"{selected['literature_number']}.pdf",
        session=client.session,
        timeout=client.timeout,
    )
    parsed = parse_single_patent_pdf(pdf_path, output_dir=parse_output_dir, country=country)
    parsed = apply_foreign_pdf_ocr_fallback(parsed, country=country)
    return {
        "literature_number": selected["literature_number"],
        "selected_type": selected["selected_type"],
        "source_path": selected["path"],
        "doc_name": selected.get("doc_name"),
        "pdf_path": str(pdf_path),
        "parse_output_dir": str(parse_output_dir),
        "markdown_paths": parsed.get("markdown_paths") or [],
        "markdown_text": parsed.get("markdown_text") or "",
        "ocr_applied": bool(parsed.get("ocr_applied")),
        "ocr_language": parsed.get("ocr_language"),
        "ocr_warning": parsed.get("ocr_warning"),
    }


def apply_foreign_pdf_ocr_fallback(
    parsed: dict[str, Any],
    *,
    country: Any,
) -> dict[str, Any]:
    country_code = str(country or "").strip().upper()
    language = {
        "CN": "chi_sim+eng",
        "JP": "jpn+eng",
        "TW": "chi_tra+eng",
    }.get(country_code)
    markdown_text = str(parsed.get("markdown_text") or "")
    if not language or foreign_fulltext_parse_is_usable(markdown_text):
        return parsed

    tesseract_path = shutil.which("tesseract")
    if not tesseract_path:
        return {
            **parsed,
            "ocr_warning": "tesseract_not_installed",
        }

    markdown_paths = [Path(path) for path in parsed.get("markdown_paths") or []]
    image_entries = foreign_markdown_image_entries(markdown_paths)
    if not image_entries:
        return {
            **parsed,
            "ocr_warning": "image_only_markdown_has_no_local_images",
        }

    sections = [
        f"# {markdown_paths[0].stem} OCR 전문",
        "",
        (
            f"> Tesseract OCR(`{language}`, `--psm 6`) 결과입니다. "
            "인식 오류가 있을 수 있으므로 각 페이지 원본 이미지와 함께 확인해야 합니다."
        ),
        "",
    ]
    recognized_chars = 0
    for index, (image_path, image_reference) in enumerate(image_entries, 1):
        try:
            completed = subprocess.run(
                [
                    tesseract_path,
                    str(image_path),
                    "stdout",
                    "-l",
                    language,
                    "--psm",
                    "6",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            return {
                **parsed,
                "ocr_warning": f"tesseract_failed:{exc.__class__.__name__}",
            }
        ocr_text = completed.stdout.strip()
        recognized_chars += len(re.sub(r"\s+", "", ocr_text))
        sections.extend(
            [
                f"## 페이지 {index}",
                "",
                f"![페이지 {index}](<{image_reference}>)",
                "",
                "### OCR 텍스트",
                "",
                "```text",
                ocr_text,
                "```",
                "",
            ]
        )

    ocr_markdown = "\n".join(sections).strip() + "\n"
    if recognized_chars < 300:
        return {
            **parsed,
            "ocr_warning": "tesseract_text_too_short",
        }

    output_path = markdown_paths[0]
    output_path.write_text(ocr_markdown, encoding="utf-8")
    return {
        **parsed,
        "markdown_paths": [str(output_path)],
        "markdown_text": ocr_markdown,
        "ocr_applied": True,
        "ocr_language": language,
        "ocr_warning": None,
    }


def foreign_markdown_image_entries(markdown_paths: list[Path]) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for markdown_path in markdown_paths:
        if not markdown_path.exists():
            continue
        markdown_text = markdown_path.read_text(encoding="utf-8", errors="ignore")
        references = re.findall(r"!\[[^\]]*]\((?:<([^>]+)>|([^)]+))\)", markdown_text)
        for bracketed, plain in references:
            reference = (bracketed or plain).strip()
            image_path = Path(reference)
            if not image_path.is_absolute():
                image_path = markdown_path.parent / image_path
            image_path = image_path.resolve()
            if image_path in seen or not image_path.is_file():
                continue
            seen.add(image_path)
            entries.append((image_path, reference))
    return entries


def foreign_fulltext_parse_is_usable(markdown_text: str) -> bool:
    text_without_images = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", str(markdown_text or ""))
    meaningful_text = re.sub(r"[^0-9A-Za-z가-힣一-龥ぁ-んァ-ヶ]+", "", text_without_images)
    return len(meaningful_text) >= 300 or bool(extract_foreign_claims_from_text(text_without_images))


def google_patents_fulltext_selection(client: Any, patent: dict[str, Any]) -> dict[str, str | None] | None:
    pdf_url = google_patents_pdf_url(patent, session=client.session, timeout=client.timeout)
    publication_id = google_patents_publication_id(patent)
    if not pdf_url or not publication_id:
        return None
    return {
        "literature_number": publication_id,
        "selected_type": "GOOGLE_PATENTS_FULLTEXT",
        "doc_name": f"{publication_id}.pdf",
        "path": pdf_url,
    }


def download_google_patents_html_fulltext(
    client: Any,
    patent: dict[str, Any],
    *,
    output_dir: Path,
) -> dict[str, Any]:
    publication_id = google_patents_publication_id(patent)
    if not publication_id:
        return {}
    fallback_candidate: tuple[str, str, str] | None = None
    selected_candidate: tuple[str, str, str] | None = None
    for language in ("en", "zh", "ja"):
        url = f"https://patents.google.com/patent/{publication_id}/{language}"
        try:
            response = client.session.get(url, timeout=client.timeout)
            response.raise_for_status()
        except Exception:
            continue
        html = decode_google_patents_html_response(response)
        markdown_text = google_patents_html_to_markdown(html)
        if not foreign_fulltext_parse_is_usable(markdown_text):
            continue
        candidate = (url, html, markdown_text)
        if extract_foreign_claims_from_text(markdown_text):
            selected_candidate = candidate
            break
        if fallback_candidate is None:
            fallback_candidate = candidate
    selected_candidate = selected_candidate or fallback_candidate
    if selected_candidate:
        url, html, markdown_text = selected_candidate
        output_dir.mkdir(parents=True, exist_ok=True)
        figure_markdown = download_google_patents_representative_figure(
            client,
            html,
            publication_id=publication_id,
            output_dir=output_dir,
        )
        if figure_markdown:
            markdown_text = f"{markdown_text}\n\n## FIG.1\n\n{figure_markdown}"
        markdown_path = output_dir / f"{publication_id}.md"
        markdown_path.write_text(markdown_text, encoding="utf-8")
        return {
            "literature_number": publication_id,
            "selected_type": "GOOGLE_PATENTS_HTML_FULLTEXT",
            "source_path": url,
            "doc_name": f"{publication_id}.html",
            "pdf_path": None,
            "parse_output_dir": str(output_dir),
            "markdown_paths": [str(markdown_path)],
            "markdown_text": markdown_text,
        }
    return {}


def decode_google_patents_html_response(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, bytes) and content:
        return content.decode("utf-8-sig", errors="replace")
    return str(getattr(response, "text", "") or "")


def google_patents_html_to_markdown(text: str) -> str:
    title = _google_patents_meta_content(text, "DC.title")
    abstract = (
        _google_patents_meta_content(text, "DC.description")
        or _google_patents_section_text(text, "abstract")
    )
    description = _google_patents_itemprop_text(text, "description")
    claims = _google_patents_claim_texts(text)
    references = _google_patents_backward_references(text)
    sections = []
    if title:
        sections.append(f"# {title}")
    if abstract:
        sections.append(f"## ABSTRACT\n\n{abstract}")
    if description:
        sections.append(f"## DETAILED DESCRIPTION\n\n{description}")
    if claims:
        sections.append("## CLAIMS\n\n" + "\n\n".join(claims))
    if references:
        sections.append("## REFERENCES CITED\n\n" + "\n".join(f"- {value}" for value in references))
    return "\n\n".join(sections)


def download_google_patents_representative_figure(
    client: Any,
    html_text: str,
    *,
    publication_id: str,
    output_dir: Path,
) -> str | None:
    urls = _google_patents_figure_urls(html_text)
    if not urls:
        return None
    figure_url = urls[1] if len(urls) > 1 else urls[0]
    try:
        response = client.session.get(figure_url, timeout=client.timeout)
        response.raise_for_status()
    except Exception:
        return None
    content = getattr(response, "content", b"")
    if not content:
        return None
    image_dir = output_dir / f"{publication_id}_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(figure_url.split("?", 1)[0]).suffix or ".png"
    image_path = image_dir / f"imageFile1{suffix}"
    image_path.write_bytes(content)
    return f"![image 1](<{image_dir.name}/{image_path.name}>)"


def _google_patents_figure_urls(text: str) -> list[str]:
    urls = []
    for tag in re.findall(r"<img\b[^>]*>", text or "", re.I):
        attributes = {
            key.lower(): unescape(value)
            for key, _, value in re.findall(r"""([:\w-]+)\s*=\s*(["'])(.*?)\2""", tag, re.S)
        }
        if attributes.get("itemprop") != "thumbnail":
            continue
        url = attributes.get("src")
        if url and url not in urls:
            urls.append(url)
    return urls


def _google_patents_backward_references(text: str) -> list[str]:
    values = []
    for row in re.findall(
        r'<tr\b[^>]*itemprop=["\']backwardReferences(?:Orig)?["\'][^>]*>(.*?)</tr>',
        text or "",
        re.I | re.S,
    ):
        match = re.search(
            r'<span\b[^>]*itemprop=["\']publicationNumber["\'][^>]*>(.*?)</span>',
            row,
            re.I | re.S,
        )
        publication_number = _strip_html(match.group(1)) if match else None
        normalized = _google_patents_reference_display_number(publication_number)
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _google_patents_forward_references(text: str) -> list[dict[str, Any]]:
    documents = []
    seen = set()
    for row in re.findall(
        r'<tr\b[^>]*itemprop=["\']forwardReferences(?:Family)?["\'][^>]*>(.*?)</tr>',
        text or "",
        re.I | re.S,
    ):
        publication_number = _google_patents_itemprop_text(row, "publicationNumber")
        normalized = re.sub(r"[^0-9A-Z]", "", str(publication_number or "").upper())
        match = re.fullmatch(r"([A-Z]{2})(\d+)([A-Z]\d?)", normalized)
        if not match or normalized in seen:
            continue
        seen.add(normalized)
        country_code, document_number, kind_code = match.groups()
        documents.append(
            {
                "direction": "cites_target",
                "country_code": country_code,
                "document_number": document_number,
                "kind_code": kind_code,
                "display_number": f"{country_code} {document_number} {kind_code}",
                "publication_number": normalized,
                "priority_date": _google_patents_itemprop_text(row, "priorityDate"),
                "publication_date": _google_patents_itemprop_text(row, "publicationDate"),
                "assignee": _google_patents_itemprop_text(row, "assigneeOriginal"),
                "title": _google_patents_itemprop_text(row, "title"),
                "examiner_cited": bool(
                    re.search(r'itemprop=["\']examinerCited["\']', row, re.I)
                ),
                "citing_application_number": normalized,
                "is_standardized": True,
                "source": "google_patents_html_forward_references",
            }
        )
    return documents


def _google_patents_backward_reference_documents(text: str) -> list[dict[str, Any]]:
    """Google Patents 본문에서 인용 선행문헌(backward references)을 문서 dict로 파싱한다.

    KIPRIS 해외 인용 서비스가 빈값을 주는 국가(예: CN)의 비교문헌 확보용 폴백. forward(피인용)
    파서와 동일 구조이며 itemprop만 backwardReferences 계열로 바꾼다. 반환 dict는 KIPRIS 인용과
    같은 형태라 기존 비교문헌 파이프라인(collect_prior_art_candidates)에 그대로 흘러간다.
    """
    documents = []
    seen = set()
    for row in re.findall(
        r'<tr\b[^>]*itemprop=["\']backwardReferences(?:Orig|Family)?["\'][^>]*>(.*?)</tr>',
        text or "",
        re.I | re.S,
    ):
        publication_number = _google_patents_itemprop_text(row, "publicationNumber")
        normalized = re.sub(r"[^0-9A-Z]", "", str(publication_number or "").upper())
        match = re.fullmatch(r"([A-Z]{2})(\d+)([A-Z]\d?)", normalized)
        if not match or normalized in seen:
            continue
        seen.add(normalized)
        country_code, document_number, kind_code = match.groups()
        documents.append(
            {
                "direction": "cited_by_target",
                "country_code": country_code,
                "document_number": document_number,
                "standard_number": document_number,
                "kind_code": kind_code,
                "display_number": f"{country_code} {document_number} {kind_code}",
                "publication_number": normalized,
                "priority_date": _google_patents_itemprop_text(row, "priorityDate"),
                "publication_date": _google_patents_itemprop_text(row, "publicationDate"),
                "assignee": _google_patents_itemprop_text(row, "assigneeOriginal"),
                "title": _google_patents_itemprop_text(row, "title"),
                "examiner_cited": bool(
                    re.search(r'itemprop=["\']examinerCited["\']', row, re.I)
                ),
                "is_standardized": True,
                "source": "google_patents_html_backward_references",
            }
        )
    return documents


def _google_patents_reference_display_number(value: str | None) -> str | None:
    normalized = re.sub(r"[^0-9A-Z]", "", str(value or "").upper())
    match = re.fullmatch(r"([A-Z]{2})(\d+)([A-Z]\d?)", normalized)
    if not match:
        return None
    return f"{match.group(1)} {match.group(2)} {match.group(3)}"


def _google_patents_itemprop_text(text: str, itemprop: str) -> str | None:
    match = re.search(
        rf'<(?P<tag>[a-z][\w:-]*)\b[^>]*itemprop=["\']{re.escape(itemprop)}["\'][^>]*>'
        rf'(.*?)</(?P=tag)>',
        text or "",
        re.I | re.S,
    )
    return _strip_html(match.group(2)) if match else None


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
        html = decode_google_patents_html_response(response)
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


def find_cached_foreign_patent_pdf(
    patent: dict[str, Any],
    *,
    pdf_dir: str | Path | None = None,
) -> Path | None:
    pdf_dir = Path(pdf_dir or settings.patent_pdf_dir)
    candidates: list[str] = []

    publication_id = google_patents_publication_id(patent)
    if publication_id:
        candidates.append(publication_id)

    registration_number = _clean(patent.get("registration_number"))
    if registration_number:
        normalized_registration = re.sub(r"[^0-9A-Z]+", "", registration_number.upper())
        if normalized_registration:
            candidates.append(normalized_registration)

    application_number = _clean(patent.get("application_number"))
    country = str(patent.get("country") or "").strip().upper()
    if country and application_number:
        normalized_application = re.sub(r"[^0-9A-Z]+", "", application_number.upper())
        if normalized_application:
            candidates.append(f"{country}{normalized_application}")
            candidates.append(normalized_application)

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = pdf_dir / f"{candidate}.pdf"
        if path.exists():
            return path
    return None


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
        r"(?m)^\s*【\s*第\s*([0-9]{1,3})\s*項\s*】\s*(.*)$",
        r"(?m)^\s*([0-9]{1,3})\s*[、．]\s*(.*)$",
        r"(?m)^\s*-?\s*([0-9]{1,3})\s*[.)]\s*(.*)$",
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
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        else:
            next_ocr_page = re.search(r"(?m)^\s*##\s*페이지\s+\d+\s*$", normalized_text[start:])
            end = start + next_ocr_page.start() if next_ocr_page else len(normalized_text)
        body = _clean_foreign_claim_body(f"{first_line} {normalized_text[start:end]}")
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


def _clean_foreign_claim_body(text: str) -> str:
    cleaned_lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("![")
            or re.fullmatch(r"#{2,6}\s*(?:페이지\s+\d+|OCR 텍스트)", stripped, re.I)
            or re.fullmatch(r"```(?:text)?", stripped, re.I)
            or re.fullmatch(r"\d{1,3}", stripped)
            or re.match(r"^\d{6,}[\d.\s]*.*第.*(?:页|頁|丰)\s*$", stripped)
        ):
            continue
        cleaned_lines.append(stripped)
    cleaned = re.sub(r"\s+", " ", " ".join(cleaned_lines)).strip()
    return re.sub(
        r"(权利要求)\s*[|Il](?=\s|所述|记载|記載|的)",
        r"\g<1> 1",
        cleaned,
    )


def _foreign_claims_section_text(text: str) -> str:
    markers = [
        r"\bwhat\s+is\s+claimed\s+is\s*:",
        r"\bwe\s+claim\s*:",
        r"(?im)^\s*#{1,6}\s*claims?\s*$",
        r"(?im)^\s*claims?\s*$",
        r"(?im)^\s*权利要求书\s*$",
        r"(?im)^\s*申請專利範圍\s*$",
    ]
    starts = []
    for marker in markers:
        match = re.search(marker, text, re.I)
        if match:
            starts.append(match.end())
    return text[min(starts):] if starts else text


def extract_foreign_claim_dependency(text: str) -> int | None:
    compact_japanese = re.sub(r"\s+", "", text)
    if re.match(
        r"請求項[0-9０-９]+(?:(?:又は|若しくは|ないし|乃至|～|〜|-)[0-9０-９]+)?"
        r"記載の.+?(?:システム|装置|プログラム|記録媒体)であって",
        compact_japanese,
        re.S,
    ):
        return None
    patterns = [
        r"claims?\s*([0-9]+)\b",
        r"权利要求\s*([0-9]+)",
        r"申請專利範圍\s*第?\s*([0-9]+)\s*項",
        (
            r"請求項\s*([0-9０-９]+)"
            r"(?:\s*(?:又は|若しくは|ないし|乃至|～|〜|-)\s*[0-9０-９]+)?"
            r"(?:\s*のいずれか(?:１|1)項)?"
            r"\s*(?:に記載|記載)"
        ),
    ]
    for pattern in patterns:
        target = compact_japanese if "請求項" in pattern else text
        match = re.search(pattern, target, re.I)
        if match:
            return _int_or_none(normalize_fullwidth_claim_digits(match.group(1)))
    return None


def normalize_fullwidth_claim_digits(value: Any) -> str:
    return str(value or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))


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
    # KIPRIS 해외 문헌번호는 12자리 영(0) 패딩 + kind 형식만 매칭된다(ex: US000012417849B2, CN201780067437A0).
    # 패딩 안 한 형식이나 A0/A/A1 같은 여분 변형은 매칭이 안 돼 KIPRIS 호출만 낭비하므로 제거한다.
    padded = document_number.zfill(12)
    if kind_code == "A":
        return _unique_texts([f"{padded}A0", f"{padded}A"])
    if kind_code:
        return [f"{padded}{kind_code}"]
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


def _normalize_foreign_date(value: str | None) -> str | None:
    text = _clean(value)
    if not text:
        return None
    digits = re.sub(r"\D+", "", text)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


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


def _find_recursive_values(value: Any, keys: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    lower_keys = {key.lower() for key in keys}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if str(key).lower() in lower_keys:
                    if isinstance(child, list):
                        matches.extend(str(item).strip() for item in child if _clean(item))
                    else:
                        cleaned = _clean(child)
                        if cleaned:
                            matches.append(cleaned)
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return matches


def _find_first_mapping_with_keys(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    lower_keys = {key.lower() for key in keys}
    if isinstance(value, dict):
        if any(str(key).lower() in lower_keys for key in value.keys()):
            return value
        for child in value.values():
            found = _find_first_mapping_with_keys(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_mapping_with_keys(child, keys)
            if found:
                return found
    return None


def _first_present_text(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        cleaned = _clean(mapping.get(key))
        if cleaned:
            return cleaned
    return None


def _find_people_values(
    value: Any,
    *,
    name_keys: tuple[str, ...],
    eng_name_keys: tuple[str, ...],
    container_hints: tuple[str, ...],
) -> dict[str, list[str]]:
    names: list[str] = []
    eng_names: list[str] = []
    hint_tokens = tuple(token.lower() for token in container_hints)
    name_tokens = {key.lower() for key in name_keys}
    eng_name_tokens = {key.lower() for key in eng_name_keys}

    def walk(node: Any, parent_key: str = "") -> None:
        lowered_parent = str(parent_key).lower()
        if isinstance(node, dict):
            for key, child in node.items():
                lowered = str(key).lower()
                if lowered in name_tokens and any(token in lowered_parent or token in lowered for token in hint_tokens):
                    cleaned = _clean(child)
                    if cleaned:
                        names.append(cleaned)
                elif lowered in eng_name_tokens and any(token in lowered_parent or token in lowered for token in hint_tokens):
                    cleaned = _clean(child)
                    if cleaned:
                        eng_names.append(cleaned)
                walk(child, lowered)
        elif isinstance(node, list):
            for child in node:
                walk(child, parent_key)

    walk(value)
    return {
        "names": _unique_texts(names),
        "eng_names": _unique_texts(eng_names),
    }


def summarize_foreign_bibliography_raw(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"raw_type": type(raw).__name__}

    node = (
        _get_path(raw, ["response", "body", "items", "item"])
        or _get_path(raw, ["response", "body", "item"])
        or _get_path(raw, ["response", "body", "items"])
        or _get_path(raw, ["response", "body"])
        or raw
    )
    if not isinstance(node, dict):
        return {"node_type": type(node).__name__}

    keys = sorted(str(key) for key in node.keys())
    return {
        "top_level_keys": keys[:30],
        "application_number": _first_present_text(node, ("applicationNumber",)),
        "register_number": _first_present_text(node, ("registerNumber", "registrationNumber")),
        "publication_number": _first_present_text(node, ("publicationNumber", "openNumber")),
        "title": _first_present_text(node, ("inventionTitle", "title")),
        "abstract": _first_present_text(node, ("astrtCont", "abstract", "abstractText", "abstractContent")),
        "ipc_values": _find_recursive_values(node, ("ipcNumber", "internationalpatentclassificationNumber", "ipc"))[:10],
    }


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
