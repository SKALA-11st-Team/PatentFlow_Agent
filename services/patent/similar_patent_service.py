from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from open_api.kipris_client import KiprisClient
from rag.industry_vector_store import OpenAIEmbeddingModel
from services.evidence.api_normalizers import extract_kipris_items
from services.patent.kipris_patent_service import download_and_parse_patent_pdf
from services.patent.markdown_preprocess_service import preprocess_patent_markdown


def build_similar_patent_context(
    *,
    target_metadata: dict[str, Any],
    representative_cpc: str | None,
    top_k: int = 3,
    max_candidates: int = 80,
    collect_pdf: bool = False,
    output_dir: str | Path | None = None,
    pdf_text_limit: int | None = None,
    embedding_model: Any | None = None,
) -> dict[str, Any]:
    if not representative_cpc:
        return {
            "representative_cpc": None,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": ["representative_cpc_not_found"],
        }

    filing_date = parse_date(
        first_present(
            target_metadata,
            "filing_date",
            "application_date",
            "applicationDate",
        )
    )
    if not filing_date:
        return {
            "representative_cpc": representative_cpc,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": ["target_filing_date_not_found"],
        }

    target_application_number = normalize_digits(
        first_present(target_metadata, "application_number", "applicationNumber")
    )
    target_text = render_similarity_text(
        first_present(target_metadata, "title", "title_final", "inventionTitle"),
        first_present(target_metadata, "abstract", "astrtCont"),
    )
    if not target_text:
        return {
            "representative_cpc": representative_cpc,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": ["target_title_abstract_not_found"],
        }

    candidates = collect_similar_patent_candidates(
        representative_cpc=representative_cpc,
        filing_date=filing_date,
        target_application_number=target_application_number,
        max_candidates=max_candidates,
    )
    if not candidates:
        return {
            "representative_cpc": representative_cpc,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": ["similar_patent_candidates_not_found"],
        }

    model = embedding_model or OpenAIEmbeddingModel()
    texts = [target_text, *[candidate["similarity_text"] for candidate in candidates]]
    embeddings = model.embed_many(texts)
    target_embedding = embeddings[0]
    candidate_embeddings = embeddings[1:]
    ranked = []
    for candidate, embedding in zip(candidates, candidate_embeddings):
        ranked.append(
            {
                **candidate,
                "similarity": round(cosine_similarity(target_embedding, embedding), 6),
            }
        )
    ranked.sort(key=lambda item: item.get("similarity", 0), reverse=True)
    similar_patents = [
        {
            key: value
            for key, value in item.items()
            if key not in {"similarity_text", "raw"}
        }
        for item in ranked[:top_k]
    ]
    warnings: list[str] = []
    if collect_pdf:
        similar_patents, warnings = collect_similar_patent_pdfs(
            similar_patents,
            output_dir=Path(output_dir) if output_dir else None,
            text_limit=pdf_text_limit,
        )
    else:
        warnings.append("similar_patent_pdf_not_collected")
    return {
        "representative_cpc": representative_cpc,
        "candidate_count": len(candidates),
        "similar_patents": similar_patents,
        "warnings": warnings,
    }


def collect_similar_patent_pdfs(
    similar_patents: list[dict[str, Any]],
    *,
    output_dir: Path | None,
    text_limit: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    enriched = []
    warnings: list[str] = []
    for patent in similar_patents:
        application_number = patent.get("application_number")
        if not application_number:
            enriched.append(patent)
            warnings.append("similar_patent_pdf_skipped:application_number_missing")
            continue
        try:
            parsed = download_and_parse_patent_pdf(
                str(application_number),
                output_dir=(output_dir or Path("artifacts/runs/manual/technology_similar_patents")),
                prefer_announcement=patent.get("status") == "등록",
            )
            markdown_text = preprocess_patent_markdown(str(parsed.get("markdown_text") or ""))
            pdf_text = markdown_text if text_limit is None else markdown_text[:text_limit]
            enriched.append(
                {
                    **patent,
                    "pdf_path": parsed.get("pdf_path"),
                    "markdown_paths": parsed.get("markdown_paths") or [],
                    "pdf_text": pdf_text,
                    "pdf_text_excerpt": pdf_text,
                    "pdf_text_chars": len(pdf_text),
                    "pdf_text_truncated": text_limit is not None and len(markdown_text) > text_limit,
                    "pdf_drawings_removed": True,
                    "pdf_collected": True,
                }
            )
        except Exception as exc:
            enriched.append({**patent, "pdf_collected": False})
            warnings.append(
                f"similar_patent_pdf_failed:{application_number}:"
                f"{exc.__class__.__name__}:{str(exc)[:160]}"
            )
    return enriched, warnings


def collect_similar_patent_candidates(
    *,
    representative_cpc: str,
    filing_date: date,
    target_application_number: str | None,
    max_candidates: int,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    client = KiprisClient()
    raw = client.search_by_cpc(
        representative_cpc,
        patent=True,
        utility=False,
        docsCount=page_size,
        docsStart=1,
    )
    items = extract_kipris_items(raw)
    lower_bound = date(filing_date.year - 10, filing_date.month, filing_date.day)
    candidates = []
    seen = set()
    for item in items:
        application_number = normalize_digits(first_present(item, "ApplicationNumber", "applicationNumber"))
        if not application_number or application_number == target_application_number:
            continue
        if application_number in seen:
            continue
        seen.add(application_number)
        candidate_date = parse_date(first_present(item, "ApplicationDate", "applicationDate"))
        if not candidate_date or not (lower_bound <= candidate_date < filing_date):
            continue
        if is_individual_applicant(first_present(item, "Applicant", "applicantName")):
            continue
        status = str(first_present(item, "RegistrationStatus", "registerStatus", "registrationStatus") or "")
        if status not in {"공개", "등록"}:
            continue
        title = first_present(item, "InventionName", "inventionTitle")
        abstract = first_present(item, "Abstract", "astrtCont")
        similarity_text = render_similarity_text(title, abstract)
        if not similarity_text:
            continue
        candidates.append(
            {
                "application_number": application_number,
                "registration_number": normalize_digits(first_present(item, "RegistrationNumber", "registerNumber")),
                "opening_number": normalize_digits(first_present(item, "OpeningNumber", "openNumber")),
                "application_date": candidate_date.isoformat(),
                "title": str(title or ""),
                "abstract": str(abstract or ""),
                "applicant": str(first_present(item, "Applicant", "applicantName") or ""),
                "status": status,
                "ipc": str(first_present(item, "InternationalpatentclassificationNumber", "ipcNumber") or ""),
                "similarity_text": similarity_text,
                "raw": item,
            }
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def render_similarity_text(title: Any, abstract: Any) -> str:
    return "\n".join(part for part in [normalize_text(title), normalize_text(abstract)] if part)


def parse_date(value: Any) -> date | None:
    text = normalize_digits(value)
    if not text or len(text) < 8:
        text = normalize_text(value).replace("-", "")
    if len(text) < 8:
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def is_individual_applicant(value: Any) -> bool:
    text = normalize_text(value)
    if not text:
        return True
    organization_markers = [
        "주식회사",
        "(주)",
        "㈜",
        "회사",
        "법인",
        "대학",
        "학교",
        "연구원",
        "연구소",
        "재단",
        "공사",
        "청",
        "청장",
        "CORP",
        "INC",
        "LTD",
        "CO.",
        "UNIV",
    ]
    upper = text.upper()
    return not any(marker in upper for marker in organization_markers)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return None


def normalize_digits(value: Any) -> str | None:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return text or None


def normalize_text(value: Any) -> str:
    return str(value or "").strip()
