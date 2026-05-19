from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
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
    return result


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


def _kipris_client() -> Any:
    from open_api.kipris_client import KiprisClient

    return KiprisClient()


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
        dependency = _int_or_none((re.search(r"청구항\s+(\d+)\s*에 있어서", body) or ["", None])[1])
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
        "citation_type_names": [citation_type_name] if citation_type_name else [],
        "display_number": display_number,
        "is_standardized": is_standardized,
        "raw": item,
    }


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
                existing["citation_type_names"] = _unique_texts(
                    [*existing.get("citation_type_names", []), *item.get("citation_type_names", [])]
                )
            continue
        key = _citation_dedupe_key(item)
        if key in index_by_key:
            existing = selected[index_by_key[key]]
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
    if not value:
        return None
    return re.sub(r"-0000$", "", value)


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

    response = session.get(url, timeout=timeout)
    response.raise_for_status()

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
