from __future__ import annotations

from datetime import date, datetime
from math import sqrt
from pathlib import Path
import re
from typing import Any, Protocol

from app.config import settings
from open_api.kipris_client import KiprisClient
from services.evidence.api_normalizers import extract_kipris_items
from services.patent.kipris_patent_service import download_and_parse_patent_pdf
from services.patent.markdown_preprocess_service import (
    build_preprocessed_patent,
    extract_sections,
    preprocess_patent_markdown,
)


def build_similar_patent_context(
    *,
    target_metadata: dict[str, Any],
    representative_cpc: str | None,
    representative_ipc: str | None = None,
    country_code: str | None = None,
    top_k: int = 3,
    max_candidates: int = 50,
    collect_pdf: bool = False,
    output_dir: str | Path | None = None,
    pdf_text_limit: int | None = None,
    embedding_model: "EmbeddingModel | None" = None,
) -> dict[str, Any]:
    classification_code = representative_ipc if country_code else representative_cpc
    classification_label = "ipc" if country_code else "cpc"
    if not classification_code:
        return {
            "representative_cpc": None,
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": [f"representative_{classification_label}_not_found"],
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
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": ["target_filing_date_not_found"],
        }

    target_application_number = normalize_digits(
        first_present(target_metadata, "application_number", "applicationNumber")
    )
    target_text = target_similarity_text(target_metadata)
    if not target_text:
        return {
            "representative_cpc": representative_cpc,
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": ["target_claims_not_found"],
        }

    candidates = collect_similar_patent_candidates(
        representative_cpc=representative_cpc,
        representative_ipc=representative_ipc,
        country_code=country_code,
        filing_date=filing_date,
        target_application_number=target_application_number,
        max_candidates=max_candidates,
    )
    if not candidates:
        return {
            "representative_cpc": representative_cpc,
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": 0,
            "similar_patents": [],
            "warnings": ["similar_patent_candidates_not_found"],
        }

    ranked = rank_similar_patent_candidates(
        target_text=target_text, candidates=candidates, embedding_model=embedding_model
    )
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
        similar_patents, pdf_warnings = collect_similar_patent_pdfs(
            similar_patents,
            output_dir=Path(output_dir) if output_dir else None,
            text_limit=pdf_text_limit,
        )
        warnings.extend(pdf_warnings)
        similar_patents = rank_similar_patent_candidates(
            target_text=target_text, candidates=similar_patents, embedding_model=embedding_model
        )
    else:
        warnings.append("similarity_scoring_disabled")
    return {
        "representative_cpc": representative_cpc,
        "representative_ipc": representative_ipc,
        "country_code": country_code,
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
            preprocessed = build_preprocessed_patent(markdown_text)
            sections = preprocessed.get("sections") or {}
            claims = list(preprocessed.get("claims") or [])
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
                    "representative_claims": [
                        {
                            "claim_no": claim.get("claim_no"),
                            "is_independent": claim.get("is_independent"),
                            "dependency": claim.get("dependency"),
                            "text": normalize_text(claim.get("text")),
                        }
                        for claim in [
                            *[item for item in claims if item.get("is_independent")],
                            *[item for item in claims if not item.get("is_independent")],
                        ][:5]
                        if normalize_text(claim.get("text"))
                    ],
                    "technical_content": {
                        "problem": normalize_text(sections.get("problem")),
                        "solution": normalize_text(sections.get("solution")),
                        "effect": normalize_text(sections.get("effect")),
                        "detailed_description": normalize_text(
                            sections.get("detailed_description")
                        ),
                    },
                    "similarity_text": patent_similarity_text_from_markdown(
                        markdown_text,
                        title=patent.get("title"),
                        abstract=patent.get("abstract"),
                    ),
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
    representative_ipc: str | None,
    country_code: str | None,
    filing_date: date,
    target_application_number: str | None,
    max_candidates: int,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    client = KiprisClient()
    search_fn = client.search_by_ipc if country_code else client.search_by_cpc
    classification_code = representative_ipc if country_code else representative_cpc
    raw = search_fn(
        classification_code,
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
        item_country_code = normalize_text(
            first_present(item, "publicationCountryCode", "countryCode", "applicationCountryCode", "country")
        ).upper()
        if country_code and item_country_code != country_code:
            continue
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
                "country_code": item_country_code or None,
                "similarity_text": similarity_text,
                "raw": item,
            }
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def render_similarity_text(title: Any, abstract: Any) -> str:
    parts = [
        normalize_text(title),
        normalize_text(abstract),
    ]
    text = "\n".join(part for part in parts if part)
    return text[:MAX_SIMILARITY_TEXT_CHARS]


def target_similarity_text(target_metadata: dict[str, Any]) -> str:
    return render_similarity_text(
        target_metadata.get("title"),
        target_metadata.get("abstract") or target_metadata.get("astrtCont"),
    )


class EmbeddingModel(Protocol):
    def embed_many(self, texts: list[str]) -> list[list[float]]:
        ...


def rank_similar_patent_candidates(
    *,
    target_text: str,
    candidates: list[dict[str, Any]],
    embedding_model: EmbeddingModel | None = None,
) -> list[dict[str, Any]]:
    """후보를 대상 특허와의 유사도로 정렬한다.

    임베딩 코사인을 우선 사용하고(의미·동의어·교차언어에 강함), 모델이 없거나 임베딩
    호출이 실패하면 토큰 Jaccard(text_overlap_similarity)로 폴백한다. similarity 값은
    정렬 순서로만 소비되므로 두 방식의 점수 분포가 달라도 하위 로직에 영향이 없다.
    """
    if not candidates:
        return []

    model = embedding_model if embedding_model is not None else _default_similarity_embedding_model()
    if model is not None:
        embedded = _rank_by_embedding(target_text=target_text, candidates=candidates, model=model)
        if embedded is not None:
            return embedded

    ranked = [
        {
            **candidate,
            "similarity": round(text_overlap_similarity(target_text, candidate.get("similarity_text")), 4),
            "similarity_method": "jaccard",
        }
        for candidate in candidates
    ]
    ranked.sort(key=lambda item: item.get("similarity") or 0.0, reverse=True)
    return ranked


def _rank_by_embedding(
    *,
    target_text: str,
    candidates: list[dict[str, Any]],
    model: EmbeddingModel,
) -> list[dict[str, Any]] | None:
    """임베딩 코사인으로 정렬한다. 실패하면 None을 돌려 호출부가 Jaccard로 폴백하게 한다."""
    texts = [target_text, *[normalize_text(candidate.get("similarity_text")) for candidate in candidates]]
    try:
        vectors = model.embed_many(texts)
    except Exception:
        return None
    if not vectors or len(vectors) != len(texts):
        return None
    target_vector = vectors[0]
    ranked = [
        {
            **candidate,
            "similarity": round(cosine_similarity(target_vector, vectors[index + 1]), 4),
            "similarity_method": "embedding",
        }
        for index, candidate in enumerate(candidates)
    ]
    ranked.sort(key=lambda item: item.get("similarity") or 0.0, reverse=True)
    return ranked


def _default_similarity_embedding_model() -> EmbeddingModel | None:
    """운영 기본 임베딩 모델. API 키가 없으면 None을 반환해 Jaccard로 폴백한다."""
    if not settings.openai_api_key:
        return None
    try:
        from rag.industry_vector_store import OpenAIEmbeddingModel

        return OpenAIEmbeddingModel()
    except Exception:
        return None


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def patent_similarity_text_from_markdown(markdown_text: str, *, title: Any = None, abstract: Any = None) -> str:
    sections = extract_sections(markdown_text)
    return render_similarity_text(
        title,
        abstract or sections.get("abstract"),
    )


def text_overlap_similarity(left: Any, right: Any) -> float:
    left_tokens = tokenize_similarity_text(left)
    right_tokens = tokenize_similarity_text(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    if union == 0:
        return 0.0
    return intersection / union


def tokenize_similarity_text(value: Any) -> set[str]:
    text = normalize_text(value).lower()
    if not text:
        return set()
    tokens = [token for token in re.split(r"[^0-9a-zA-Z가-힣]+", text) if len(token) >= 2]
    return set(tokens)


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
MAX_SIMILARITY_TEXT_CHARS = 12000
