from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
import re
from typing import Any, Mapping

from open_api.kipris_client import KiprisClient
from services.evidence.api_normalizers import extract_kipris_items
from services.patent.kipris_patent_service import (
    _download_pdf_url,
    _foreign_literature_number_candidates,
    decode_google_patents_html_response,
    download_and_parse_patent_pdf,
    fetch_kipris_bibliography_basic,
    google_patents_pdf_url,
    google_patents_publication_id,
    parse_single_patent_pdf,
)
from services.patent.markdown_preprocess_service import (
    build_preprocessed_patent,
    extract_sections,
    preprocess_patent_markdown,
)


DEFAULT_PRIOR_ART_TOP_K = 5

# 전문 성공 목표를 채우기 위해 자국 우선 순서로 인용을 순회할 때, 다운로드를 시도하는 최대 후보 수.
# (전문 다운로드는 네트워크/OCR 비용이 크므로 무한 순회를 막는 안전장치)
PRIOR_ART_MAX_RESOLUTION_ATTEMPTS = 15


def has_prior_art_fulltext(item: dict[str, Any]) -> bool:
    """비교문헌으로 쓸 수 있는 전문(본문)이 확보됐는지 판정한다."""
    if not isinstance(item, dict):
        return False
    return bool(str(item.get("pdf_text") or item.get("pdf_text_excerpt") or "").strip())


# @author 김한규
# @date 2026-05-20
# @relatedFR FR-007
# @relatedUI UI-005
# @description 대상 특허의 인용/선행기술 후보를 모아 전문(본문)을 확보한 비교문헌 컨텍스트를 만든다 —
#              권리성 평가 근거(선행기술 대비)의 입력. 자국 우선 순회로 전문 성공 건수 목표를 채운다.
def build_prior_art_patent_context(
    *,
    target_metadata: dict[str, Any],
    kipris_api_data: dict[str, Any] | None = None,
    top_k: int | None = DEFAULT_PRIOR_ART_TOP_K,
    collect_pdf: bool = False,
    output_dir: str | Path | None = None,
    pdf_text_limit: int | None = None,
    home_country: str | None = None,
    target_fulltext_count: int | None = None,
    max_resolution_attempts: int = PRIOR_ART_MAX_RESOLUTION_ATTEMPTS,
) -> dict[str, Any]:
    citation_documents = list((kipris_api_data or {}).get("citation_documents") or [])
    prior_art_candidates = collect_prior_art_candidates(
        target_metadata=target_metadata,
        citation_documents=citation_documents,
        home_country=home_country,
    )
    warnings: list[str] = []

    if not prior_art_candidates:
        return {
            "comparison_mode": "prior-art",
            "candidate_count": 0,
            "fulltext_count": 0,
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": ["prior_art_candidates_not_found"],
        }

    output_path = Path(output_dir) if output_dir else None

    if collect_pdf and target_fulltext_count:
        # 자국 우선 순서대로 순회하며 전문 성공 건수가 목표(target_fulltext_count)에 도달하면 멈춘다.
        # 앞쪽 후보가 전문 확보에 실패해도 뒤 후보로 계속 시도하므로 [:top_k] 고정 컷의 누락을 없앤다.
        attempt_cap = min(len(prior_art_candidates), max(max_resolution_attempts, target_fulltext_count))
        attempt_candidates = prior_art_candidates[:attempt_cap]
        resolved: list[dict[str, Any]] = []
        fulltext_count = 0
        for candidate in attempt_candidates:
            item = resolve_prior_art_candidate(
                candidate,
                output_dir=output_path,
                collect_pdf=collect_pdf,
                text_limit=pdf_text_limit,
                fulltext_source="remote",
            )
            resolved.append(item)
            if has_prior_art_fulltext(item):
                fulltext_count += 1
                if fulltext_count >= target_fulltext_count:
                    break
        if fulltext_count < target_fulltext_count:
            for index, item in enumerate(resolved):
                if has_prior_art_fulltext(item):
                    continue
                google_item = resolve_prior_art_candidate(
                    attempt_candidates[index],
                    output_dir=output_path,
                    collect_pdf=collect_pdf,
                    text_limit=pdf_text_limit,
                    fulltext_source="google",
                )
                if has_prior_art_fulltext(google_item):
                    resolved[index] = google_item
                    fulltext_count += 1
                    if fulltext_count >= target_fulltext_count:
                        break
                    continue
                google_item["_warnings"] = [
                    *(item.get("_warnings") or []),
                    *(google_item.get("_warnings") or []),
                ]
                resolved[index] = google_item
    else:
        selected_candidates = prior_art_candidates if top_k is None else prior_art_candidates[:top_k]
        resolved = [
            resolve_prior_art_candidate(
                candidate,
                output_dir=output_path,
                collect_pdf=collect_pdf,
                text_limit=pdf_text_limit,
                fulltext_source="remote",
            )
            for candidate in selected_candidates
        ]
        fulltext_count = sum(1 for item in resolved if has_prior_art_fulltext(item))

    warnings.extend(
        warning
        for item in resolved
        for warning in item.pop("_warnings", [])
    )
    return {
        "comparison_mode": "prior-art",
        "candidate_count": len(prior_art_candidates),
        "fulltext_count": fulltext_count,
        "similar_patents": resolved,
        "prior_art_patents": resolved,
        "warnings": warnings,
    }


def _rank_prior_art_citations(citation_documents: list[dict[str, Any]], home_country: str | None) -> list[dict[str, Any]]:
    """자국(대상국) 인용을 앞으로 정렬한다.

    자국 문헌은 KIPRIS 본문/전문 다운로드 성공률이 높아 비교문헌 확보에 유리하므로
    표준화·등록 여부와 함께 자국 우선으로 정렬한다. home_country 미지정 시 KR을 자국으로 본다.
    """
    home = (normalize_text(home_country) or "KR").upper()

    def _key(item: dict[str, Any]) -> tuple[int, int, int, str]:
        kind = str(item.get("kind_code") or "").upper()
        country = str(item.get("country_code") or "").upper()
        return (
            0 if item.get("is_standardized") else 1,
            0 if kind.startswith("B") else 1,
            0 if country == home else 1,
            str(item.get("display_number") or ""),
        )

    return sorted(citation_documents, key=_key)


# @relatedFR FR-007
# @relatedUI UI-005
# @description KIPRIS 인용문헌과 전처리된 선행기술 번호를 합쳐 중복 제거한 선행기술 후보 목록을 만든다.
def collect_prior_art_candidates(
    *,
    target_metadata: dict[str, Any],
    citation_documents: list[dict[str, Any]],
    home_country: str | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen = set()

    for citation in _rank_prior_art_citations(citation_documents, home_country):
        display_number = normalize_text(citation.get("display_number"))
        if not display_number or display_number in seen:
            continue
        seen.add(display_number)
        # 해외 인용(normalize_foreign_reference_documents)은 번호를 standard_number가 아닌 document_number로 준다.
        # 해외 전문 다운로드(_foreign_literature_number_candidates)가 document_number를 읽으므로 함께 보존한다.
        document_number = normalize_text(citation.get("document_number"))
        items.append(
            {
                "display_number": display_number,
                "source": "kipris_citation",
                "country_code": normalize_text(citation.get("country_code")),
                "kind_code": normalize_text(citation.get("kind_code")),
                "standard_number": normalize_digits(citation.get("standard_number") or document_number),
                "document_number": document_number,
                "original_number": normalize_text(citation.get("original_number")) or document_number,
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
        parsed = parse_prior_art_display_number(display_number)
        items.append(
            {
                "display_number": display_number,
                "source": "preprocessed_prior_art",
                "country_code": parsed.get("country_code") or (display_number[:2] if len(display_number) >= 2 else None),
                "kind_code": parsed.get("kind_code") or extract_kind_code(display_number),
                "standard_number": parsed.get("standard_number") or normalize_digits(display_number),
                "original_number": display_number,
                "citation_type_names": [],
                "publication_date": None,
                "is_standardized": False,
                "search_matches": [],
            }
        )
    return items


# @relatedFR FR-007
# @relatedUI UI-005
# @description 선행기술 후보 한 건을 KIPRIS 검색·서지·전문 PDF(국내/해외)로 해소해 비교 가능한 문헌으로 만든다.
def resolve_prior_art_candidate(
    candidate: dict[str, Any],
    *,
    output_dir: Path | None,
    collect_pdf: bool,
    text_limit: int | None,
    fulltext_source: str = "remote",
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
        "country_code": candidate.get("country_code"),
        "document_number": candidate.get("standard_number"),
        "kind_code": candidate.get("kind_code"),
        "pdf_collected": False,
        "resolved_application_numbers": [],
        "_warnings": [],
    }

    if is_foreign_prior_art_candidate(candidate):
        item.update(
            {
                "source_type": "foreign_prior_art",
                "source_label": "해외 선행기술문헌",
                "title": candidate.get("display_number"),
                "publication_date": candidate.get("publication_date"),
            }
        )
        if collect_pdf:
            attach_foreign_prior_art_fulltext(
                item,
                candidate,
                output_dir=output_dir or Path("artifacts/runs/manual/technology_prior_art"),
                text_limit=text_limit,
                fulltext_source=fulltext_source,
            )
        return item

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
            bibliography = fetch_kipris_bibliography_basic(application_number)
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
            item.update(prior_art_legal_content_from_markdown(markdown_text, country_code=item.get("country_code")))
        else:
            joined = " | ".join(error_messages)[:500]
            item["_warnings"].append(
                f"prior_art_pdf_failed:{'/'.join(application_numbers)}:{joined or 'no_pdf_candidate_succeeded'}"
            )

    return item


def is_foreign_prior_art_candidate(candidate: dict[str, Any]) -> bool:
    country_code = normalize_text(candidate.get("country_code"))
    return bool(country_code and country_code != "KR")


def attach_foreign_prior_art_fulltext(
    item: dict[str, Any],
    candidate: dict[str, Any],
    *,
    output_dir: Path,
    text_limit: int | None,
    fulltext_source: str,
) -> None:
    parsed, literature_number, fulltext_type, pdf_path, errors = download_foreign_prior_art_fulltext(
        candidate,
        output_dir=output_dir,
        fulltext_source=fulltext_source,
    )
    if parsed and literature_number and pdf_path:
        markdown_text = preprocess_patent_markdown(str(parsed.get("markdown_text") or ""))
        pdf_text = markdown_text if text_limit is None else markdown_text[:text_limit]
        item.update(
            {
                "literature_number": literature_number,
                "foreign_fulltext_type": fulltext_type,
                "pdf_path": pdf_path,
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
        item.update(prior_art_legal_content_from_markdown(markdown_text, country_code=item.get("country_code")))
        return

    joined = " | ".join(errors)[:500]
    item["_warnings"].append(
        f"foreign_prior_art_fulltext_failed:{item.get('display_number')}:{joined or 'no_fulltext_candidate_succeeded'}"
    )


def download_foreign_prior_art_fulltext(
    candidate: dict[str, Any],
    *,
    output_dir: Path,
    fulltext_source: str = "remote",
) -> tuple[dict[str, Any] | None, str | None, str | None, str | None, list[str]]:
    client = KiprisClient()
    errors: list[str] = []
    country_code = normalize_text(candidate.get("country_code"))
    if not country_code:
        return None, None, None, None, ["country_code_missing"]
    country = country_code.upper()

    if fulltext_source == "remote":
        # 선행문헌 전문 1차 패스는 KIPRIS 해외 전문 공개 다운로드(remoteFile.do)만 시도한다.
        # ServiceKey/쿼터를 쓰지 않는 공개 파일 URL이고 publ_key가 결정론적이라, 쿼터 쓰는 전문 API 호출을
        # 생략한다. (US/JP는 공개번호 기반, CN은 Google 페이지에서 출원번호를 받아 publ_key를 만든다.)
        for publ_key in foreign_fulltext_remote_publ_keys(client, candidate, country):
            try:
                remote = download_kipris_remote_fulltext(client, publ_key, country, output_dir=output_dir)
                if remote is None:
                    errors.append(f"{publ_key}:not_pdf")
                    continue
                if remote["kind"] == "ocr":
                    # 구형 스캔본은 OCR로 이미 전문 텍스트를 확보했다(opendataloader 파싱 불필요).
                    parsed = {
                        "markdown_paths": [str(remote["markdown_path"])],
                        "markdown_text": remote["markdown_text"],
                        "parse_warning": "remote_image_ocr",
                    }
                    return parsed, publ_key, "kipris_remote_ocr", str(remote["markdown_path"]), errors
                pdf_path = remote["pdf_path"]
                parsed = parse_single_patent_pdf(
                    pdf_path,
                    output_dir=output_dir / safe_filename(Path(pdf_path).stem),
                    country=country,
                )
                return parsed, publ_key, "kipris_remote_fulltext", str(pdf_path), errors
            except Exception as exc:
                errors.append(f"{publ_key}:{exc.__class__.__name__}:{str(exc)[:180]}")
        return None, None, None, None, errors

    if fulltext_source != "google":
        return None, None, None, None, [f"unsupported_fulltext_source:{fulltext_source}"]

    patent = {
        "country": country_code,
        "registration_number": candidate.get("display_number") or candidate.get("original_number"),
    }
    publication_id = google_patents_publication_id(patent)
    try:
        pdf_url = google_patents_pdf_url(patent, session=client.session, timeout=client.timeout)
        if pdf_url and publication_id:
            pdf_path = _download_pdf_url(
                pdf_url,
                output_dir=output_dir,
                filename=f"{publication_id}.pdf",
                session=client.session,
                timeout=client.timeout,
            )
            parsed = parse_single_patent_pdf(
                pdf_path,
                output_dir=output_dir / safe_filename(publication_id),
                country=country,
            )
            return parsed, publication_id, "google_patents", str(pdf_path), errors
        errors.append("google_patents:pdf_not_found")
    except Exception as exc:
        errors.append(f"google_patents:{exc.__class__.__name__}:{str(exc)[:180]}")
    return None, None, None, None, errors


def foreign_fulltext_remote_publ_keys(client: Any, candidate: dict[str, Any], country: str) -> list[str]:
    """remoteFile.do용 publ_key 후보를 만든다(국가 + 12자리 번호 + A0/B0).

    US/JP는 공개번호 기반 문헌번호를 그대로 쓴다. CN은 remoteFile.do가 출원번호로만 조회되므로
    인용문헌의 Google 페이지에서 출원번호를 받아 CN{출원12자리}A0/B0를 만든다.
    """
    if country == "CN":
        application_number = resolve_cn_application_number(client, candidate)
        if not application_number:
            return []
        return [f"CN{application_number}A0", f"CN{application_number}B0"]
    return [f"{country}{literature_number}" for literature_number in _foreign_literature_number_candidates(candidate)]


def resolve_cn_application_number(client: Any, candidate: dict[str, Any]) -> str | None:
    """CN 인용문헌의 Google Patents 페이지에서 출원번호(12자리)를 추출한다(KIPRIS 쿼터 미사용)."""
    publication_id = google_patents_publication_id(
        {
            "country": "CN",
            "registration_number": candidate.get("display_number")
            or candidate.get("publication_number")
            or candidate.get("original_number"),
        }
    )
    if not publication_id:
        return None
    try:
        response = client.session.get(
            f"https://patents.google.com/patent/{publication_id}/en",
            timeout=getattr(client, "timeout", 20.0),
        )
        response.raise_for_status()
        html = decode_google_patents_html_response(response)
    except Exception:
        return None
    match = re.search(r'itemprop=["\']applicationNumber["\'][^>]*>([^<]+)<', html)
    if not match:
        return None
    digits = re.sub(r"\D+", "", match.group(1))
    # CN 출원번호는 12자리(+검증숫자 1자리). 앞 12자리만 KIPRIS publ_key에 쓴다.
    return digits[:12] if len(digits) >= 12 else None


# 구형 특허 스캔본 OCR 언어팩(apply_foreign_pdf_ocr_fallback과 동일 매핑). 그 외 국가는 영어.
REMOTE_FULLTEXT_OCR_LANGUAGE = {"CN": "chi_sim+eng", "JP": "jpn+eng", "TW": "chi_tra+eng"}
_REMOTE_FULLTEXT_IMAGE_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg")


def download_kipris_remote_fulltext(client: Any, publ_key: str, country: str, *, output_dir: Path) -> dict[str, Any] | None:
    """KIPRIS 해외 전문 공개 다운로드(remoteFile.do)에서 전문을 받는다(ServiceKey/쿼터 미사용).

    반환:
      - PDF: {"kind": "pdf", "pdf_path": Path}
      - 구형 스캔본(ZIP of TIFF·단일 TIFF): tesseract OCR → {"kind": "ocr", "markdown_path": Path, "markdown_text": str}
      - 전문 없음(빈 응답·HTML 에러·OCR 실패): None → 호출부가 다음 후보/Google 폴백으로.
    """
    url = f"http://www.kipris.or.kr/abpat/remoteFile.do?method=fullText&publ_key={publ_key}&cntry={country}"
    from services.evidence.news_article_extraction_service import validate_article_url

    block_reason = validate_article_url(url)
    if block_reason:
        raise RuntimeError(f"document_url_blocked:{block_reason}")
    response = client.session.get(url, timeout=getattr(client, "timeout", None))
    response.raise_for_status()
    content = response.content
    if content[:5].startswith(b"%PDF"):
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / safe_filename(f"{publ_key}.pdf")
        path.write_bytes(content)
        return {"kind": "pdf", "pdf_path": path}
    # 구형 특허는 PDF 대신 스캔 이미지(ZIP of TIFF 또는 단일 TIFF)로 온다 → OCR로 전문 텍스트 확보.
    if content[:2] == b"PK" or content[:2] in (b"II", b"MM"):
        ocr = ocr_remote_fulltext_images(content, country, output_dir=output_dir, publ_key=publ_key)
        if ocr:
            markdown_path, markdown_text = ocr
            return {"kind": "ocr", "markdown_path": markdown_path, "markdown_text": markdown_text}
    return None


def ocr_remote_fulltext_images(content: bytes, country: str, *, output_dir: Path, publ_key: str) -> tuple[Path, str] | None:
    """remoteFile.do가 PDF 대신 준 스캔 이미지(ZIP of TIFF·단일 TIFF)를 tesseract로 OCR한다.

    국가별 언어팩으로 페이지별 OCR 후 합쳐 마크다운으로 저장한다. 인식 텍스트가 너무 짧으면 None.
    """
    tesseract_path = shutil.which("tesseract")
    if not tesseract_path:
        return None
    language = REMOTE_FULLTEXT_OCR_LANGUAGE.get(str(country or "").strip().upper(), "eng")
    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="remote_fulltext_ocr_") as temp_dir:
        temp_path = Path(temp_dir)
        image_paths: list[Path] = []
        if content[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    for name in sorted(archive.namelist()):
                        if name.lower().endswith(_REMOTE_FULLTEXT_IMAGE_EXTS):
                            image_path = temp_path / safe_filename(Path(name).name)
                            image_path.write_bytes(archive.read(name))
                            image_paths.append(image_path)
            except zipfile.BadZipFile:
                return None
        else:
            image_path = temp_path / f"{safe_filename(publ_key)}.tif"
            image_path.write_bytes(content)
            image_paths.append(image_path)
        if not image_paths:
            return None
        for image_path in image_paths:
            try:
                completed = subprocess.run(
                    [tesseract_path, str(image_path), "stdout", "-l", language, "--psm", "6"],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
            except (OSError, subprocess.CalledProcessError):
                continue
            page_text = completed.stdout.strip()
            if page_text:
                texts.append(page_text)
    markdown_text = "\n\n".join(texts).strip()
    # OCR 노이즈만 잡힌 경우는 버린다(비교문헌으로 쓸 본문이 안 됨).
    if len(re.sub(r"\s+", "", markdown_text)) < 300:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / safe_filename(f"{publ_key}_ocr.md")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    return markdown_path, markdown_text


def foreign_fulltext_operation_order(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    kind_code = normalize_text(candidate.get("kind_code")) or ""
    if kind_code.startswith("A"):
        return [("open", "overseas_open_fulltext"), ("registration", "overseas_registration_fulltext")]
    return [("registration", "overseas_registration_fulltext"), ("open", "overseas_open_fulltext")]


def extract_foreign_fulltext_document(raw: Any) -> dict[str, str | None]:
    mapping = find_document_path_mapping(raw) or {}
    return {
        "doc_name": first_mapping_value(mapping, ("docName", "documentName", "fileName", "doc_name")),
        "path": first_mapping_value(mapping, ("path", "fullTextPath", "downloadPath", "filePath", "pdfPath", "url")),
    }


def find_document_path_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
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


def first_mapping_value(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    lower_keys = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = lower_keys.get(key.lower())
        text = normalize_text(value)
        if text:
            return text
    return None


def download_foreign_fulltext_pdf(
    client: Any,
    url: str,
    *,
    output_dir: Path,
    filename: str,
) -> Path:
    # EXT-07: 외부 문서 URL 다운로드 전 SSRF 가드(스킴/사설·링크로컬 IP 차단).
    from services.evidence.news_article_extraction_service import validate_article_url
    block_reason = validate_article_url(url)
    if block_reason:
        raise RuntimeError(f"document_url_blocked:{block_reason}")
    response = client.session.get(url, timeout=getattr(client, "timeout", None))
    response.raise_for_status()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / safe_filename(filename)
    path.write_bytes(response.content)
    return path


def safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", str(value or "")).strip("._") or "document"


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
        # 한 검색 방식에서 해당 선행문헌을 찾았으면 나머지 번호 방식은 시도하지 않는다
        # (같은 특허를 4가지 번호로 중복 조회하는 KIPRIS 낭비 방지).
        if results:
            break
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


def parse_prior_art_display_number(value: Any) -> dict[str, str | None]:
    text = str(value or "").strip().replace("*", "")
    match = re.match(r"^\s*([A-Za-z]{2})\s*[- ]?\s*([0-9][0-9A-Za-z./-]*?)(?:\s+([A-Za-z][0-9]?))?\s*$", text)
    if not match:
        return {"country_code": None, "standard_number": None, "kind_code": None}
    country_code = match.group(1).upper()
    standard_number = normalize_digits(match.group(2))
    kind_code = match.group(3).upper() if match.group(3) else None
    return {
        "country_code": country_code,
        "standard_number": standard_number,
        "kind_code": kind_code,
    }


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


# @relatedFR FR-007
# @relatedUI UI-005
# @description 선행문헌 전문 마크다운에서 대표 청구항·초록·기술내용을 뽑아 청구항 비교용 권리성 근거로 정리한다.
def prior_art_legal_content_from_markdown(
    markdown_text: str,
    *,
    country_code: str | None,
    max_claims: int = 5,
) -> dict[str, Any]:
    preprocessed = build_preprocessed_patent(
        markdown_text,
        db_metadata={"country": country_code} if country_code else None,
    )
    claims = list(preprocessed.get("claims") or [])
    ordered_claims = [
        *[claim for claim in claims if claim.get("is_independent")],
        *[claim for claim in claims if not claim.get("is_independent")],
    ]
    representative_claims = [
        {
            "claim_no": claim.get("claim_no"),
            "is_independent": claim.get("is_independent"),
            "dependency": claim.get("dependency"),
            "text": normalize_text(claim.get("text")),
        }
        for claim in ordered_claims[:max_claims]
        if normalize_text(claim.get("text"))
    ]
    abstract = normalize_text((preprocessed.get("sections") or {}).get("abstract"))
    sections = preprocessed.get("sections") or {}
    return {
        "abstract": abstract,
        "claim_stats": preprocessed.get("claim_stats") or {},
        "representative_claims": representative_claims,
        "technical_content": {
            "problem": normalize_text(sections.get("problem")),
            "solution": normalize_text(sections.get("solution")),
            "effect": normalize_text(sections.get("effect")),
            "detailed_description": normalize_text(sections.get("detailed_description")),
        },
        "lookup_status": "resolved",
        "lookup_source": "prior_art_pdf_fulltext",
        "comparison_status": (
            "claim_comparison_ready" if representative_claims else "fulltext_claims_unparsed"
        ),
    }


def prior_art_context_citation_documents(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    documents = []
    for item in (context or {}).get("prior_art_patents") or (context or {}).get("similar_patents") or []:
        if not isinstance(item, dict):
            continue
        if item.get("comparison_status") == "identifier_only":
            continue
        comparison_status = item.get("comparison_status")
        if comparison_status == "comparison_ready":
            comparison_status = "claim_comparison_ready"
        documents.append(
            {
                "direction": "cited_by_target",
                "country_code": item.get("country_code"),
                "application_number": item.get("application_number"),
                "registration_number": item.get("registration_number"),
                "publication_number": item.get("opening_number"),
                "document_number": item.get("document_number"),
                "kind_code": item.get("kind_code"),
                "display_number": item.get("display_number"),
                "title": item.get("title"),
                "abstract": item.get("abstract"),
                "claim_stats": item.get("claim_stats") or {},
                "representative_claims": item.get("representative_claims") or [],
                "lookup_status": item.get("lookup_status"),
                "lookup_source": item.get("lookup_source"),
                "comparison_status": comparison_status,
            }
        )
    return documents


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
