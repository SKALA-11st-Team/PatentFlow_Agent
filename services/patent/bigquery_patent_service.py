from __future__ import annotations

from typing import Any, Callable, Iterable
import os
import re


DEFAULT_PATENTS_TABLE = "patents-public-data.patents.publications"
DEFAULT_LANGUAGES = ["en", "ja", "zh", "ko"]


class BigQueryPatentClaimsError(RuntimeError):
    """BigQuery patent claims 조회 실패."""


def fetch_foreign_claims_from_bigquery(
    candidates: list[dict[str, Any]],
    *,
    client: Any | None = None,
    patents_table: str | None = None,
    preferred_languages: list[str] | None = None,
    max_candidates: int = 3,
    max_claims_per_document: int = 3,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    query_client = client or _default_bigquery_client()
    table = patents_table or os.getenv("BIGQUERY_PATENTS_TABLE") or DEFAULT_PATENTS_TABLE
    languages = preferred_languages or DEFAULT_LANGUAGES
    selected_candidates = candidates[:max_candidates]
    publication_candidates_by_key = {
        _candidate_key(candidate): publication_number_candidates(candidate)
        for candidate in selected_candidates
    }
    publication_numbers = _unique_texts(
        number for numbers in publication_candidates_by_key.values() for number in numbers
    )
    if not publication_numbers:
        return []

    rows = list(
        query_client.query(
            _publication_query(table),
            job_config=_bigquery_job_config(publication_numbers, languages),
        ).result()
    )
    rows_by_publication_number: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row_dict = _row_to_dict(row)
        rows_by_publication_number.setdefault(str(row_dict.get("publication_number") or ""), []).append(row_dict)

    documents = []
    for candidate in selected_candidates:
        rows_for_candidate = _rows_for_candidate(
            candidate,
            rows_by_publication_number,
            publication_candidates_by_key.get(_candidate_key(candidate), []),
        )
        if not rows_for_candidate:
            continue
        documents.append(
            _foreign_claim_document_payload(
                candidate,
                rows_for_candidate,
                max_claims_per_document=max_claims_per_document,
            )
        )
    return documents


def publication_number_candidates(candidate: dict[str, Any]) -> list[str]:
    country_code = _clean(candidate.get("country_code"))
    document_number = _clean(candidate.get("document_number"))
    kind_code = _clean(candidate.get("kind_code"))
    original_number = _clean(candidate.get("original_number"))
    display_number = _clean(candidate.get("display_number"))

    candidates = []
    for value in (original_number, display_number):
        parsed = _publication_number_from_text(value)
        if parsed:
            candidates.append(parsed)

    if country_code and document_number and kind_code:
        candidates.append(f"{country_code}-{document_number}-{kind_code}")
    if country_code and document_number:
        candidates.append(f"{country_code}-{document_number}")
    return _unique_texts(candidates)


def _default_bigquery_client() -> Any:
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise BigQueryPatentClaimsError(
            "google-cloud-bigquery가 설치되어 있지 않습니다. requirements.txt 설치 후 다시 실행하세요."
        ) from exc
    return bigquery.Client(project=os.getenv("BIGQUERY_PROJECT") or None)


def _bigquery_job_config(publication_numbers: list[str], languages: list[str]) -> Any:
    try:
        from google.cloud import bigquery
    except ImportError:
        return None
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("publication_numbers", "STRING", publication_numbers),
            bigquery.ArrayQueryParameter("languages", "STRING", languages),
        ]
    )


def _claims_query(table: str) -> str:
    return _publication_query(table)


def _publication_query(table: str) -> str:
    safe_table = table.replace("`", "")
    return f"""
SELECT
  p.publication_number,
  p.country_code,
  p.kind_code,
  (
    SELECT title.text
    FROM UNNEST(p.title_localized) AS title
    WHERE title.text IS NOT NULL
    ORDER BY IF(title.language IN UNNEST(@languages), 0, 1), title.language
    LIMIT 1
  ) AS title,
  (
    SELECT abstract.text
    FROM UNNEST(p.abstract_localized) AS abstract
    WHERE abstract.text IS NOT NULL
    ORDER BY IF(abstract.language IN UNNEST(@languages), 0, 1), abstract.language
    LIMIT 1
  ) AS abstract,
  claim.language AS claim_language,
  claim.text AS claim_text
FROM `{safe_table}` AS p
LEFT JOIN UNNEST(p.claims_localized) AS claim
WHERE p.publication_number IN UNNEST(@publication_numbers)
  AND (claim.text IS NULL OR claim.language IN UNNEST(@languages) OR claim.language IS NULL)
ORDER BY p.publication_number, IF(claim.language IN UNNEST(@languages), 0, 1), claim.language
"""


def _foreign_claim_document_payload(
    candidate: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    max_claims_per_document: int,
) -> dict[str, Any]:
    first = rows[0]
    claims = []
    for index, row in enumerate(rows[:max_claims_per_document], 1):
        claims.append(
            {
                "claim_no": index,
                "text": _clean(row.get("claim_text")),
                "language": _clean(row.get("claim_language")),
                "is_independent": index == 1,
                "dependency": None,
            }
        )
    representative_claims = [claim for claim in claims if claim.get("text")]
    return {
        "direction": candidate.get("direction"),
        "country_code": first.get("country_code") or candidate.get("country_code"),
        "publication_number": first.get("publication_number"),
        "document_number": candidate.get("document_number"),
        "kind_code": first.get("kind_code") or candidate.get("kind_code"),
        "display_number": candidate.get("display_number"),
        "title": first.get("title"),
        "abstract": first.get("abstract"),
        "representative_claims": representative_claims,
        "lookup_status": "resolved" if representative_claims else "metadata_only",
        "lookup_source": "bigquery_patents_publications",
        "source_document": candidate,
    }


def _rows_for_candidate(
    candidate: dict[str, Any],
    rows_by_publication_number: dict[str, list[dict[str, Any]]],
    publication_candidates: list[str],
) -> list[dict[str, Any]]:
    for publication_number in publication_candidates:
        rows = rows_by_publication_number.get(publication_number)
        if rows:
            return rows
    country_code = candidate.get("country_code")
    document_number = re.sub(r"\D+", "", str(candidate.get("document_number") or ""))
    kind_code = str(candidate.get("kind_code") or "").upper()
    for publication_number, rows in rows_by_publication_number.items():
        normalized_publication = publication_number.replace("-", "").upper()
        if country_code and not normalized_publication.startswith(str(country_code).upper()):
            continue
        if document_number and document_number not in normalized_publication:
            continue
        if kind_code and kind_code not in normalized_publication:
            continue
        return rows
    return []


def _publication_number_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b([A-Z]{2})\s*-?\s*([0-9][0-9A-Z./-]*)\s*-?\s*([A-Z][0-9]?)?\b", value.upper())
    if not match:
        return None
    country_code = match.group(1)
    document_number = re.sub(r"[^0-9A-Z]+", "", match.group(2))
    kind_code = match.group(3)
    parts = [country_code, document_number]
    if kind_code:
        parts.append(kind_code)
    return "-".join(parts)


def _candidate_key(candidate: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        candidate.get("country_code"),
        candidate.get("document_number"),
        candidate.get("kind_code"),
        candidate.get("display_number"),
    )


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "items"):
        return dict(row.items())
    return {
        key: getattr(row, key)
        for key in (
            "publication_number",
            "country_code",
            "kind_code",
            "title",
            "abstract",
            "claim_language",
            "claim_text",
        )
        if hasattr(row, key)
    }


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _unique_texts(values: Iterable[str | None]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
