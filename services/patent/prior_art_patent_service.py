from __future__ import annotations

from pathlib import Path
from typing import Any

from open_api.kipris_client import KiprisClient
from services.evidence.api_normalizers import extract_kipris_items
from services.patent.kipris_patent_service import download_and_parse_patent_pdf, fetch_kipris_bibliography
from services.patent.markdown_preprocess_service import extract_sections, preprocess_patent_markdown


def build_prior_art_patent_context(
    *,
    target_metadata: dict[str, Any],
    kipris_api_data: dict[str, Any] | None = None,
    top_k: int | None = None,
    collect_pdf: bool = False,
    output_dir: str | Path | None = None,
    pdf_text_limit: int | None = None,
) -> dict[str, Any]:
    citation_documents = list((kipris_api_data or {}).get("citation_documents") or [])
    prior_art_candidates = collect_prior_art_candidates(target_metadata=target_metadata, citation_documents=citation_documents)
    warnings: list[str] = []

    if not prior_art_candidates:
        return {
            "comparison_mode": "prior-art",
            "candidate_count": 0,
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": ["prior_art_candidates_not_found"],
        }

    selected_candidates = prior_art_candidates if top_k is None else prior_art_candidates[:top_k]
    resolved = [
        resolve_prior_art_candidate(candidate, output_dir=Path(output_dir) if output_dir else None, collect_pdf=collect_pdf, text_limit=pdf_text_limit)
        for candidate in selected_candidates
    ]
    warnings.extend(
        warning
        for item in resolved
        for warning in item.pop("_warnings", [])
    )
    return {
        "comparison_mode": "prior-art",
        "candidate_count": len(prior_art_candidates),
        "similar_patents": resolved,
        "prior_art_patents": resolved,
        "warnings": warnings,
    }


def collect_prior_art_candidates(*, target_metadata: dict[str, Any], citation_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen = set()

    for citation in citation_documents:
        display_number = normalize_text(citation.get("display_number"))
        if not display_number or display_number in seen:
            continue
        seen.add(display_number)
        items.append(
            {
                "display_number": display_number,
                "source": "kipris_citation",
                "country_code": normalize_text(citation.get("country_code")),
                "kind_code": normalize_text(citation.get("kind_code")),
                "standard_number": normalize_digits(citation.get("standard_number")),
                "original_number": normalize_text(citation.get("original_number")),
                "citation_type_names": list(citation.get("citation_type_names") or []),
                "publication_date": normalize_text(citation.get("publication_date")),
                "is_standardized": bool(citation.get("is_standardized")),
                "search_matches": [],
            }
        )

    for raw in ensure_list(target_metadata.get("prior_art")):
        display_number = normalize_text(raw)
        if not display_number or display_number in seen:
            continue
        seen.add(display_number)
        items.append(
            {
                "display_number": display_number,
                "source": "preprocessed_prior_art",
                "country_code": display_number[:2] if len(display_number) >= 2 else None,
                "kind_code": extract_kind_code(display_number),
                "standard_number": normalize_digits(display_number),
                "original_number": display_number,
                "citation_type_names": [],
                "publication_date": None,
                "is_standardized": False,
                "search_matches": [],
            }
        )
    return items


def resolve_prior_art_candidate(
    candidate: dict[str, Any],
    *,
    output_dir: Path | None,
    collect_pdf: bool,
    text_limit: int | None,
) -> dict[str, Any]:
    item = {
        "source_type": "prior_art",
        "comparison_source": "prior-art",
        "display_number": candidate.get("display_number"),
        "source_label": "선행기술조사문헌",
        "title": candidate.get("display_number"),
        "status": None,
        "application_number": None,
        "registration_number": None,
        "opening_number": None,
        "application_date": None,
        "publication_date": candidate.get("publication_date"),
        "applicant": None,
        "abstract": None,
        "similarity": None,
        "citation_type_names": candidate.get("citation_type_names") or [],
        "pdf_collected": False,
        "resolved_application_numbers": [],
        "_warnings": [],
    }

    search_matches = candidate_search_matches(candidate)
    application_numbers = unique_texts(
        [match.get("application_number") for match in search_matches if match.get("application_number")]
    )
    item["resolved_application_numbers"] = application_numbers
    item["resolved_search_matches"] = search_matches
    application_number = application_numbers[0] if application_numbers else None
    primary_match = search_matches[0] if search_matches else {}
    if primary_match:
        item.update(
            {
                "title": primary_match.get("title") or item["title"],
                "status": primary_match.get("status") or item.get("status"),
                "application_number": primary_match.get("application_number") or item.get("application_number"),
                "registration_number": primary_match.get("registration_number") or item.get("registration_number"),
                "opening_number": primary_match.get("opening_number") or item.get("opening_number"),
                "application_date": primary_match.get("application_date") or item.get("application_date"),
                "publication_date": primary_match.get("publication_date") or item.get("publication_date"),
                "applicant": primary_match.get("applicant") or item.get("applicant"),
                "abstract": primary_match.get("abstract") or item.get("abstract"),
            }
        )
    if application_number:
        try:
            bibliography = fetch_kipris_bibliography(application_number)
            metadata = bibliography.get("metadata") or {}
            sections = bibliography.get("sections") or {}
            abstract = sections.get("abstract") if isinstance(sections, dict) else None
            item.update(
                {
                    "title": metadata.get("title") or item["title"],
                    "status": "등록" if metadata.get("registration_number") else "공개",
                    "application_number": normalize_digits(metadata.get("application_number")) or application_number,
                    "registration_number": normalize_digits(metadata.get("registration_number")),
                    "opening_number": normalize_digits(metadata.get("publication_number")),
                    "application_date": metadata.get("filing_date"),
                    "publication_date": metadata.get("publication_date") or item.get("publication_date"),
                    "applicant": ", ".join(metadata.get("assignee") or []) or item.get("applicant"),
                    "abstract": abstract if isinstance(abstract, str) else item.get("abstract"),
                }
            )
        except Exception as exc:
            item["_warnings"].append(
                f"prior_art_metadata_failed:{application_number}:{exc.__class__.__name__}:{str(exc)[:160]}"
            )
    else:
        item["_warnings"].append(f"prior_art_application_number_unresolved:{item['display_number']}")

    if collect_pdf and application_numbers:
        parsed, used_application_number, error_messages = download_prior_art_pdf(
            application_numbers,
            output_dir=(output_dir or Path("artifacts/runs/manual/technology_prior_art")),
            prefer_announcement=prefer_announcement_for_candidate(candidate, item),
        )
        if parsed and used_application_number:
            markdown_text = preprocess_patent_markdown(str(parsed.get("markdown_text") or ""))
            pdf_text = markdown_text if text_limit is None else markdown_text[:text_limit]
            item.update(
                {
                    "application_number": used_application_number,
                    "pdf_path": parsed.get("pdf_path"),
                    "markdown_paths": parsed.get("markdown_paths") or [],
                    "pdf_text": pdf_text,
                    "pdf_text_excerpt": pdf_text,
                    "pdf_text_chars": len(pdf_text),
                    "pdf_text_truncated": text_limit is not None and len(markdown_text) > text_limit,
                    "pdf_drawings_removed": True,
                    "pdf_collected": True,
                    "similarity_text": prior_art_similarity_text_from_markdown(markdown_text),
                }
            )
        else:
            joined = " | ".join(error_messages)[:500]
            item["_warnings"].append(
                f"prior_art_pdf_failed:{'/'.join(application_numbers)}:{joined or 'no_pdf_candidate_succeeded'}"
            )

    return item


def candidate_search_matches(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    number = normalize_digits(candidate.get("standard_number"))
    if not number:
        return []
    if normalize_text(candidate.get("country_code")) != "KR":
        return []

    kind_code = normalize_text(candidate.get("kind_code")) or ""
    client = KiprisClient()
    results: list[dict[str, Any]] = []
    seen = set()
    for search_kind, params in candidate_search_params(number, kind_code=kind_code):
        try:
            if search_kind == "application":
                raw = client.search_by_application_number(number)
            else:
                raw = client.advanced_search(**params)
            for resolved in extract_search_matches(raw):
                app_no = resolved.get("application_number")
                if not app_no or app_no in seen:
                    continue
                seen.add(app_no)
                results.append(resolved)
        except Exception:
            continue
    if len(number) == 13 and number not in seen:
        results.append(
            {
                "application_number": number,
                "registration_number": None,
                "opening_number": None,
                "application_date": None,
                "publication_date": None,
                "title": None,
                "applicant": None,
                "status": None,
                "abstract": None,
            }
        )
    return results


def candidate_search_params(number: str, *, kind_code: str) -> list[tuple[str, dict[str, Any]]]:
    common = {"patent": True, "utility": False, "docsStart": 1, "docsCount": 5}
    if kind_code.startswith("A"):
        ordered = [
            ("advanced", {**common, "openNumber": number}),
            ("advanced", {**common, "publicationNumber": number}),
            ("application", {"applicationNumber": number}),
            ("advanced", {**common, "registerNumber": number}),
        ]
    elif kind_code.startswith("B"):
        ordered = [
            ("advanced", {**common, "registerNumber": number}),
            ("advanced", {**common, "publicationNumber": number}),
            ("application", {"applicationNumber": number}),
            ("advanced", {**common, "openNumber": number}),
        ]
    else:
        ordered = [
            ("application", {"applicationNumber": number}),
            ("advanced", {**common, "openNumber": number}),
            ("advanced", {**common, "publicationNumber": number}),
            ("advanced", {**common, "registerNumber": number}),
        ]
    return ordered


def extract_search_matches(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = extract_kipris_items(raw)
    if not items:
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        application_number = normalize_digits(item.get("applicationNumber") or item.get("ApplicationNumber"))
        if not application_number:
            continue
        result.append(
            {
                "application_number": application_number,
                "registration_number": normalize_digits(item.get("registerNumber") or item.get("RegistrationNumber")),
                "opening_number": normalize_digits(item.get("openNumber") or item.get("OpeningNumber") or item.get("publicationNumber")),
                "application_date": normalize_text(item.get("applicationDate") or item.get("ApplicationDate")),
                "publication_date": normalize_text(item.get("publicationDate") or item.get("PublicationDate") or item.get("openDate")),
                "title": normalize_text(item.get("inventionTitle") or item.get("InventionTitle") or item.get("inventionName")),
                "applicant": normalize_text(item.get("applicantName") or item.get("Applicant")),
                "status": normalize_text(item.get("registerStatus") or item.get("RegistrationStatus")),
                "abstract": normalize_text(item.get("astrtCont") or item.get("Abstract")),
            }
        )
    return result


def download_prior_art_pdf(
    application_numbers: list[str],
    *,
    output_dir: Path,
    prefer_announcement: bool,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    errors: list[str] = []
    for application_number in application_numbers:
        try:
            parsed = download_and_parse_patent_pdf(
                str(application_number),
                output_dir=output_dir,
                prefer_announcement=prefer_announcement,
            )
            return parsed, application_number, errors
        except Exception as exc:
            errors.append(f"{application_number}:{exc.__class__.__name__}:{str(exc)[:180]}")
    return None, None, errors


def prefer_announcement_for_candidate(candidate: dict[str, Any], item: dict[str, Any]) -> bool:
    kind_code = normalize_text(candidate.get("kind_code")) or ""
    if kind_code.startswith("A"):
        return False
    if kind_code.startswith("B"):
        return True
    return item.get("status") == "등록"


def ensure_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def normalize_digits(value: Any) -> str | None:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return text or None


def normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def extract_kind_code(value: str) -> str | None:
    parts = str(value or "").strip().replace("*", "").split()
    return parts[-1] if len(parts) >= 2 else None


def unique_texts(values: list[Any]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def render_similarity_text(title: Any, abstract: Any) -> str:
    return "\n".join(part for part in [normalize_text(title), normalize_text(abstract)] if part)


def prior_art_similarity_text_from_markdown(markdown_text: str) -> str:
    sections = extract_sections(markdown_text)
    return build_claims_and_description_text(
        claims_text=sections.get("claims_text"),
        representative_claim_text=None,
        solution=sections.get("solution"),
        detailed_description=sections.get("detailed_description"),
        abstract=sections.get("abstract"),
    )


def build_claims_and_description_text(
    *,
    claims_text: Any,
    representative_claim_text: Any,
    solution: Any,
    detailed_description: Any,
    abstract: Any,
) -> str:
    parts = [
        normalize_text(claims_text),
        normalize_text(representative_claim_text),
    ]
    text = "\n".join(part for part in parts if part)
    return text[:MAX_SIMILARITY_TEXT_CHARS]


MAX_SIMILARITY_TEXT_CHARS = 12000
