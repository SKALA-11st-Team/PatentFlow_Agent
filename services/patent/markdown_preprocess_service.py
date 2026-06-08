from pathlib import Path
from typing import Any
import re


STRUCTURAL_HEADERS = {
    "명세서",
    "청구범위",
    "발명의 설명",
    "기술분야",
    "배경기술",
    "발명의 내용",
    "해결하려는 과제",
    "과제의 해결 수단",
    "발명의 효과",
    "도면의 간단한 설명",
    "발명을 실시하기 위한 구체적인 내용",
    "부호의 설명",
    "도면",
}

SECTION_HEADINGS = {
    "명세서",
    "청구범위",
    "발명의 설명",
    "기술분야",
    "배경기술",
    "발명의 내용",
    "해결하려는 과제",
    "과제의 해결 수단",
    "발명의 효과",
    "도면의 간단한 설명",
    "발명을 실시하기 위한 구체적인 내용",
    "부호의 설명",
}

SECTION_ALIASES = {
    "요약": "abstract",
    "청구범위": "claims_text",
    "기술분야": "technical_field",
    "배경기술": "background_art",
    "해결하려는 과제": "problem",
    "과제의 해결 수단": "solution",
    "발명의 효과": "effect",
    "도면의 간단한 설명": "figure_description",
    "발명을 실시하기 위한 구체적인 내용": "detailed_description",
    "부호의 설명": "reference_signs",
}

HEADER_NORMALIZE_MAP = {
    "명 세 서": "명세서",
    "기 술 분 야": "기술분야",
    "배 경 기 술": "배경기술",
}

IMAGE_MARKDOWN_RE = re.compile(r"!\[image\s+(\d+)\]\(<([^>]+)>\)")
US_CLASSIFICATION_RE = re.compile(r"[A-HY]\d{2}[A-Z]\s*\d+\s*/?\s*\d+")
US_CLASSIFICATION_CANDIDATE_RE = re.compile(r"\b[A-HY][0-9OIGQS]{2}[A-Z]\s*\d+\s*/?\s*\d+\b", re.I)
USPTO_LABEL_STOP_PATTERN = (
    r"^(?:\(\d{2}\)\s*)?(?:"
    r"Patent No\.?|Date of Patent|U\.S\. Cl\.|CPC|Int\. Cl\.|Applicant|Assignee|Inventors?|Appl\.?\s*No\.?|Filed|PCT Filed|"
    r"Prior Publication Data|Foreign Application Priority Data|References Cited|Field of Classification Search|Notice|ABSTRACT|What is claimed is|"
    r"The invention claimed is|TECHNICAL FIELD|BACKGROUND ART|BACKGROUND|DISCLOSURE|BRIEF DESCRIPTION OF DRAWINGS|"
    r"DESCRIPTION|DETAILED DESCRIPTION"
    r")(?=\s|:|$)"
)
USPTO_LABEL_STOP_RE = re.compile(USPTO_LABEL_STOP_PATTERN, re.I | re.M)
REPRESENTATIVE_FIGURE_RE = re.compile(
    r"(?:대\s*표\s*도|대표도)\s*[-:]\s*도\s*(\d+)",
)
REPRESENTATIVE_LABEL_RE = re.compile(r"(?:대\s*표\s*도|대표도)")
DRAWING_SECTION_HEADING_RE = re.compile(r"(?:^|\n)#?\s*도면\s*(?:\n|$)")
DRAWING_ITEM_RE = re.compile(r"^\s*-?\s*도면\s*(\d+)\s*$", re.MULTILINE)


def remove_image_markdown(text: str) -> str:
    return IMAGE_MARKDOWN_RE.sub("", text)


def extract_representative_drawing(
    raw_text: str,
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    representative_match = REPRESENTATIVE_FIGURE_RE.search(raw_text or "")
    if not representative_match:
        representative_label_match = REPRESENTATIVE_LABEL_RE.search(raw_text or "")
        if not representative_label_match:
            return None
        image_match = IMAGE_MARKDOWN_RE.search(raw_text, representative_label_match.end())
        if not image_match:
            return None
        markdown_paths = (source or {}).get("markdown_paths") or []
        drawing = {
            "figure_number": "대표도",
            "image_path": image_match.group(2),
            "image_source": "cover_representative",
        }
        if markdown_paths:
            drawing["markdown_path"] = str(markdown_paths[0])
        return drawing

    figure_number = representative_match.group(1)
    image_match = find_drawing_section_image(raw_text, figure_number)
    image_source = "drawing_section"
    if not image_match:
        image_match = find_ordered_drawing_section_image(raw_text, figure_number)
        image_source = "drawing_section_order"
    if not image_match:
        image_match = IMAGE_MARKDOWN_RE.search(raw_text, representative_match.end())
        image_source = "cover_representative"
    if not image_match:
        return None

    markdown_paths = (source or {}).get("markdown_paths") or []
    drawing = {
        "figure_number": f"도{figure_number}",
        "image_path": image_match.group(2),
        "image_source": image_source,
    }
    if markdown_paths:
        drawing["markdown_path"] = str(markdown_paths[0])
    return drawing


def find_drawing_section_image(raw_text: str, figure_number: str) -> re.Match[str] | None:
    section_match = DRAWING_SECTION_HEADING_RE.search(raw_text or "")
    if not section_match:
        return None

    section_text = raw_text[section_match.end() :]
    target_item: re.Match[str] | None = None
    next_item: re.Match[str] | None = None
    for item_match in DRAWING_ITEM_RE.finditer(section_text):
        if target_item:
            next_item = item_match
            break
        if item_match.group(1) == figure_number:
            target_item = item_match

    if not target_item:
        return None

    search_end = next_item.start() if next_item else len(section_text)
    return IMAGE_MARKDOWN_RE.search(section_text, target_item.end(), search_end)


def find_ordered_drawing_section_image(raw_text: str, figure_number: str) -> re.Match[str] | None:
    section_match = DRAWING_SECTION_HEADING_RE.search(raw_text or "")
    if not section_match:
        return None

    try:
        target_index = int(figure_number) - 1
    except ValueError:
        return None

    if target_index < 0:
        return None

    section_text = raw_text[section_match.end() :]
    images = list(IMAGE_MARKDOWN_RE.finditer(section_text))
    if target_index >= len(images):
        return None
    return images[target_index]


def extract_representative_figure_detail(
    sections: dict[str, str],
    figure_number: str | None,
) -> str | None:
    if not figure_number:
        return None

    digit_match = re.search(r"\d+", figure_number)
    if not digit_match:
        return None

    target_number = digit_match.group(0)
    detailed_description = sections.get("detailed_description") or ""
    if not detailed_description:
        return None

    start_match = re.search(rf"도\s*{re.escape(target_number)}(?!\d)\s*(?:은|는|을|를|에)?", detailed_description)
    if not start_match:
        return None

    next_figure_match = re.search(
        rf"도\s*(?!{re.escape(target_number)}(?!\d))\d+\s*(?:은|는|을|를|에)?",
        detailed_description[start_match.end() :],
    )
    end_index = (
        start_match.end() + next_figure_match.start()
        if next_figure_match
        else len(detailed_description)
    )
    return postprocess_agent_text(detailed_description[start_match.start() : end_index])


def build_drawing_context(
    raw_text: str,
    sections: dict[str, str],
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    representative = extract_representative_drawing(raw_text, source=source)
    figure_description = postprocess_agent_text(sections.get("figure_description") or "")
    if not representative and not figure_description:
        return None

    context: dict[str, Any] = {}
    if representative:
        context["representative_drawing"] = representative
        representative_detail = extract_representative_figure_detail(
            sections,
            representative.get("figure_number"),
        )
        if representative_detail:
            context["representative_figure_detail"] = representative_detail
    if figure_description:
        context["figure_description"] = figure_description
    return context


def remove_duplicate_registration_title(text: str, max_scan_lines: int = 40) -> str:
    lines = text.splitlines()
    result = []
    seen = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if index < max_scan_lines and re.fullmatch(r"등록특허\s+\d{2}-\d+", stripped):
            if seen:
                continue
            seen = True
        result.append(line)

    return "\n".join(result)


def normalize_headers(text: str) -> str:
    for src, dst in HEADER_NORMALIZE_MAP.items():
        text = text.replace(src, dst)
    return text


def remove_page_artifacts(text: str) -> str:
    text = text.replace("(뒷면에 계속)", "")
    text = re.sub(r"대\s*표\s*도\s*[-:]\s*도\s*\d+", "", text)
    text = re.sub(r"대표도\s*[-:]\s*도\s*\d+", "", text)
    return text


def remove_deleted_claims(text: str) -> str:
    return re.sub(
        r"^-?\s*청구항\s+\d+\s+삭제\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )


def remove_figure_section(text: str) -> str:
    pattern = r"\n#?\s*도면\s*\n"
    match = re.search(pattern, text)
    if match:
        return text[: match.start()].strip()
    return text


def is_structural_line(line: str) -> bool:
    stripped = line.strip()
    header = stripped.lstrip("#").strip()

    if not stripped:
        return True
    if header in STRUCTURAL_HEADERS:
        return True
    if re.match(r"^-?\s*청구항\s+\d+", stripped):
        return True
    if re.match(r"^\[\d{4}\]", stripped):
        return True
    if re.match(r"^-?\s*\[\d{4}\]", stripped):
        return True
    if re.match(r"^#\s*", stripped):
        return True
    if re.match(r"^-+\s*$", stripped):
        return True
    return False


def merge_broken_lines(text: str) -> str:
    lines = text.splitlines()
    result: list[str] = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            result.append("")
            continue

        if is_structural_line(stripped):
            result.append(stripped)
            continue

        if result and result[-1] and not is_structural_line(result[-1]):
            result[-1] = result[-1].rstrip() + " " + stripped
        else:
            result.append(stripped)

    merged = "\n".join(result)
    return re.sub(r"([;,.])\s+([은는이가을를])", r"\1\2", merged)


def normalize_blank_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def preprocess_patent_markdown(raw_text: str) -> str:
    text = raw_text
    text = remove_image_markdown(text)
    text = remove_duplicate_registration_title(text)
    text = normalize_headers(text)
    text = remove_page_artifacts(text)
    text = remove_deleted_claims(text)
    text = remove_figure_section(text)
    text = merge_broken_lines(text)
    text = normalize_blank_lines(text)
    return text


def build_preprocessed_patent(
    raw_text: str,
    *,
    source: dict[str, Any] | None = None,
    db_metadata: dict[str, Any] | None = None,
    api_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cleaned_text = preprocess_patent_markdown(raw_text)
    sections = extract_sections(cleaned_text)
    country = str((db_metadata or {}).get("country") or ((api_data or {}).get("metadata") or {}).get("country") or "").upper()
    if country and country != "KR":
        sections = merge_foreign_sections(
            sections,
            extract_us_patent_sections(cleaned_text, cleaned_text=cleaned_text),
        )
    drawing_context = build_drawing_context(raw_text, sections, source=source)

    if api_data:
        metadata = merge_api_metadata(
            extract_pdf_support_metadata(raw_text, db_metadata=db_metadata),
            api_data.get("metadata") or {},
        )
        api_sections = api_data.get("sections") or {}
        if api_sections.get("abstract"):
            api_abstract = postprocess_agent_text(api_sections["abstract"])
            if not sections.get("abstract") or len(api_abstract) > len(sections.get("abstract") or ""):
                sections["abstract"] = api_abstract
        api_claims = api_data.get("claims") or []
        claims = [
            {
                "claim_no": claim["claim_no"],
                "text": postprocess_agent_text(claim["text"]),
                "is_independent": claim.get("is_independent", False),
                "dependency": claim.get("dependency"),
            }
            for claim in api_claims
        ]
        if not claims and country and country != "KR":
            claims = extract_english_claims(sections.get("claims_text", ""))
        claim_stats = {
            key: value
            for key, value in (api_data.get("claim_stats") or {}).items()
            if key != "deleted_claim_numbers"
        }
        if not claim_stats:
            claim_stats = build_claim_stats(metadata.get("reported_claim_count"), claims)
    else:
        metadata = extract_pdf_fallback_metadata(cleaned_text, db_metadata=db_metadata)
        claims = extract_claims(sections.get("claims_text", ""))
        claim_stats = build_claim_stats(metadata.get("claim_count"), claims)

    metadata = merge_db_context_metadata(metadata, db_metadata=db_metadata)
    metadata["claim_count"] = claim_stats["active_claim_count"] or metadata.get("claim_count")
    metadata["assignee_count"] = len(metadata.get("assignee") or [])
    metadata["has_co_assignee"] = metadata["assignee_count"] > 1

    validation = validate_preprocessed_patent(metadata, sections, claims, source_text=cleaned_text)
    patent_id = _first_non_empty(
        metadata.get("registration_number"),
        metadata.get("application_number"),
        source.get("application_number") if source else None,
    )

    country_prefix = str(metadata.get("country") or db_metadata.get("country") or "KR").upper()
    result = {
        "patent_id": f"{country_prefix}{patent_id}" if patent_id else None,
        "source": {
            "source_type": "kipris_api_plus_pdf_markdown" if api_data else "kipris_pdf_markdown",
            **(source or {}),
            "has_kipris_api": bool(api_data),
            "has_pdf_markdown": bool(raw_text.strip()),
        },
        "metadata": metadata,
        "sections": sections,
        "claims": claims,
        "claim_stats": claim_stats,
        "agent_inputs": build_agent_inputs(metadata, sections, claims),
        "validation": validation,
        "debug": {
            "paragraph_numbers": extract_paragraph_numbers(cleaned_text),
        },
        "cleaned_markdown": cleaned_text,
    }
    if drawing_context:
        result["drawing_context"] = drawing_context
    return result


def preprocess_markdown_file(
    markdown_path: str | Path,
    *,
    source: dict[str, Any] | None = None,
    db_metadata: dict[str, Any] | None = None,
    api_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(markdown_path)
    raw_text = path.read_text(encoding="utf-8", errors="ignore")
    source_info = {"file_name": path.name, **(source or {})}
    return build_preprocessed_patent(
        raw_text,
        source=source_info,
        db_metadata=db_metadata,
        api_data=api_data,
    )


def merge_api_metadata(pdf_metadata: dict[str, Any], api_metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(pdf_metadata)
    merged_source = dict(pdf_metadata.get("metadata_source") or {})
    prefer_api_fields = [
        "country",
        "patent_type",
        "registration_number",
        "application_number",
        "publication_number",
        "title",
        "title_eng",
        "assignee",
        "assignee_eng",
        "inventors",
        "inventors_eng",
        "filing_date",
        "registration_date",
        "publication_date",
        "open_date",
        "ipc",
        "examiner",
        "claim_count",
        "reported_claim_count",
        "register_status",
        "final_disposal",
    ]
    for field in prefer_api_fields:
        if api_metadata.get(field) not in (None, "", []):
            merged[field] = api_metadata[field]
            merged_source[field] = "api"

    for field in ["prior_art"]:
        api_values = api_metadata.get(field) or []
        pdf_values = pdf_metadata.get(field) or []
        merged[field] = _dedupe([*api_values, *pdf_values])
        if api_values:
            merged_source[field] = "api"
        elif pdf_values:
            merged_source[field] = (pdf_metadata.get("metadata_source") or {}).get(field, "ocr_front_page")
    if pdf_metadata.get("representative_ipc") not in (None, ""):
        merged["representative_ipc"] = pdf_metadata["representative_ipc"]
        if (pdf_metadata.get("metadata_source") or {}).get("representative_ipc"):
            merged_source["representative_ipc"] = (pdf_metadata.get("metadata_source") or {}).get("representative_ipc")
    merged["cpc"] = []
    if "cpc" in merged_source:
        merged_source["cpc"] = ""
    if merged_source:
        merged["metadata_source"] = merged_source
    return merged


def merge_db_context_metadata(
    metadata: dict[str, Any],
    *,
    db_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db_metadata = db_metadata or {}
    merged = dict(metadata)
    db_context_fields = [
        "id",
        "management_number",
        "title_draft",
        "title_final",
        "business_area",
        "technology_area",
        "related_product",
        "joint_application",
        "joint_applicant_name",
        "status",
        "application_date",
        "expected_expiration_date",
        "data_source_status",
    ]
    for field in db_context_fields:
        if db_metadata.get(field) not in (None, "", []):
            merged[field] = db_metadata[field]
    if db_metadata.get("registration_date") not in (None, "", []) and not merged.get("registration_date"):
        merged["registration_date"] = db_metadata["registration_date"]
    if db_metadata.get("country") not in (None, "", []) and not merged.get("country"):
        merged["country"] = db_metadata["country"]
    if not merged.get("title") and db_metadata.get("title_final"):
        merged["title"] = db_metadata["title_final"]
    return merged


def extract_pdf_support_metadata(text: str, *, db_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    db_metadata = db_metadata or {}
    foreign_frontpage = extract_foreign_frontpage_metadata(text)
    metadata_source = foreign_frontpage.get("metadata_source") or {}
    return {
        "country": db_metadata.get("country"),
        "application_number": db_metadata.get("application_number"),
        "registration_number": db_metadata.get("registration_number"),
        "title": foreign_frontpage.get("title") or db_metadata.get("title_final"),
        "ipc": foreign_frontpage.get("ipc") or _extract_classifications(text, "국제특허분류"),
        "representative_ipc": foreign_frontpage.get("representative_ipc") or "",
        "cpc": [],
        "assignee": foreign_frontpage.get("assignee") or foreign_frontpage.get("applicant") or [],
        "inventors": foreign_frontpage.get("inventors") or [],
        "claim_count": foreign_frontpage.get("claim_count"),
        "reported_claim_count": foreign_frontpage.get("claim_count"),
        "patent_number": foreign_frontpage.get("patent_number") or db_metadata.get("registration_number"),
        "registration_date": foreign_frontpage.get("registration_date") or db_metadata.get("registration_date"),
        "filing_date": foreign_frontpage.get("filing_date") or db_metadata.get("application_date"),
        "prior_art": _extract_prior_art(text),
        "metadata_source": metadata_source,
    }


def extract_pdf_fallback_metadata(text: str, *, db_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    db_metadata = db_metadata or {}
    metadata: dict[str, Any] = {
        "country": db_metadata.get("country") or _search_group(r"\(19\).*?\((KR)\)", text),
        "patent_type": _search_group(r"\(12\)\s*([^\n]+)", text) or "등록특허",
        "registration_number": _search_group(r"\(11\)\s*등록번호\s*([0-9-]+)", text)
        or db_metadata.get("registration_number"),
        "application_number": _search_group(r"\(21\)\s*출원번호\s*([0-9-]+)", text)
        or db_metadata.get("application_number"),
        "publication_number": _search_group(r"\(65\)\s*공개번호\s*([0-9-]+)", text),
        "title": _search_group(r"\(54\)\s*발명의 명칭\s*(.+)", text) or db_metadata.get("title_final"),
        "assignee": _extract_assignee_list(text),
        "inventors": _extract_inventor_list(text),
        "filing_date": _normalize_korean_date(_search_group(r"\(22\)\s*출원일자\s*([0-9년월일]+)", text))
        or db_metadata.get("application_date"),
        "registration_date": _normalize_korean_date(_search_group(r"\(24\)\s*등록일자\s*([0-9년월일]+)", text))
        or db_metadata.get("registration_date"),
        "publication_date": _normalize_korean_date(_search_group(r"\(45\)\s*공고일자\s*([0-9년월일]+)", text)),
        "ipc": _extract_classifications(text, "국제특허분류"),
        "cpc": _extract_classifications(text, "CPC특허분류"),
        "examiner": _search_group(r"심사관\s*[:：]\s*([^\n ]+)", text),
        "claim_count": _extract_int(r"전체\s*청구항\s*수\s*[:：]\s*총\s*(\d+)\s*항", text),
        "prior_art": _extract_prior_art(text),
    }
    metadata["country"] = metadata["country"] or "KR"
    return metadata


def extract_sections(text: str) -> dict[str, str]:
    sections = {value: "" for value in SECTION_ALIASES.values()}
    sections["abstract"] = postprocess_agent_text(_extract_abstract(text))
    if not sections["abstract"]:
        sections["abstract"] = postprocess_agent_text(_extract_foreign_abstract(text))

    headings = _find_section_headings(text)
    for index, (heading, start) in enumerate(headings):
        key = SECTION_ALIASES.get(heading)
        if not key:
            continue
        end = headings[index + 1][1] if index + 1 < len(headings) else len(text)
        content_start = _line_end(text, start)
        content = text[content_start:end].strip()
        if key == "claims_text":
            sections[key] = postprocess_claims_text(strip_section_heading_lines(content))
        else:
            sections[key] = postprocess_agent_text(strip_section_heading_lines(content))

    return sections


def merge_foreign_sections(base: dict[str, str], foreign: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    for key, value in foreign.items():
        if key == "abstract" and value:
            merged[key] = value
        elif value and not merged.get(key):
            merged[key] = value
    return merged


def extract_claims(claims_text: str) -> list[dict[str, Any]]:
    claims_text = truncate_at_next_major_section(claims_text)
    matches = list(re.finditer(r"(?m)^-?\s*청구항\s+(\d+)\s*(.*)$", claims_text))
    claims: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        claim_no = int(match.group(1))
        first_line = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(claims_text)
        body = claims_text[start:end].strip()
        text = postprocess_agent_text(f"{first_line}\n{body}".strip())
        dependency = _extract_claim_dependency(text)
        claims.append(
            {
                "claim_no": claim_no,
                "text": text,
                "is_independent": dependency is None,
                "dependency": dependency,
            }
        )
    return claims


def extract_english_claims(claims_text: str) -> list[dict[str, Any]]:
    claims_text = truncate_at_next_major_section(claims_text)
    matches = list(re.finditer(r"(?:^|(?<=\s))(\d+)\.\s+(?=[A-Z])", claims_text, re.M))
    claims: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        claim_no = int(match.group(1))
        end = matches[index + 1].start(1) - 1 if index + 1 < len(matches) else len(claims_text)
        text = postprocess_agent_text(claims_text[match.end() : end].strip())
        dependency = _extract_english_claim_dependency(text)
        claims.append(
            {
                "claim_no": claim_no,
                "text": text,
                "is_independent": dependency is None,
                "dependency": dependency,
            }
        )
    return claims


def _extract_claim_dependency(text: str) -> int | None:
    return _extract_int(r"(?:청구항|제)\s*(\d+)\s*항?\s*(?:에 있어서|내지|또는|및|중)", text)


def _extract_english_claim_dependency(text: str) -> int | None:
    return _extract_int(r"(?i)\bclaim\s+(\d+)\b", text)


def build_claim_stats(reported_claim_count: int | None, claims: list[dict[str, Any]]) -> dict[str, Any]:
    active_numbers = [claim["claim_no"] for claim in claims]
    independent_numbers = [claim["claim_no"] for claim in claims if claim.get("is_independent")]
    dependent_numbers = [claim["claim_no"] for claim in claims if not claim.get("is_independent")]
    expected_numbers = set(range(1, (reported_claim_count or max(active_numbers, default=0)) + 1))
    active_set = set(active_numbers)
    return {
        "reported_claim_count": reported_claim_count,
        "active_claim_count": len(active_numbers),
        "active_claim_numbers": active_numbers,
        "independent_claim_numbers": independent_numbers,
        "dependent_claim_numbers": dependent_numbers,
        "has_deleted_claims_gap": bool(expected_numbers - active_set) if expected_numbers else False,
    }


def build_agent_inputs(
    metadata: dict[str, Any],
    sections: dict[str, str],
    claims: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    representative_claims = [claim for claim in claims if claim.get("is_independent")]
    if not representative_claims and claims:
        representative_claims = [claims[0]]

    return {
        "summary": {
            "metadata": _pick(
                metadata,
                ["title", "application_number", "registration_number", "ipc", "cpc"],
            ),
            "abstract": sections.get("abstract", ""),
            "technical_field": sections.get("technical_field", ""),
            "problem": sections.get("problem", ""),
            "solution": sections.get("solution", ""),
            "effect": sections.get("effect", ""),
            "representative_claims": representative_claims,
        },
        "valuation": {
            "metadata": _pick(
                metadata,
                [
                    "title",
                    "application_number",
                    "registration_number",
                    "assignee",
                    "assignee_count",
                    "has_co_assignee",
                    "filing_date",
                    "registration_date",
                    "prior_art",
                    "ipc",
                    "cpc",
                ],
            ),
            "claims": claims,
            "effect": sections.get("effect", ""),
            "detailed_description": sections.get("detailed_description", ""),
        },
        "search_planning": {
            "metadata": _pick(
                metadata,
                ["title", "application_number", "registration_number", "ipc", "cpc"],
            ),
            "abstract": sections.get("abstract", ""),
            "problem": sections.get("problem", ""),
            "solution": sections.get("solution", ""),
            "key_claims": representative_claims,
        },
    }


def render_agent_input(agent_input: dict[str, Any]) -> str:
    parts = []
    metadata = agent_input.get("metadata", {})
    if metadata:
        parts.append("메타데이터")
        for key, value in metadata.items():
            if value not in (None, "", []):
                parts.append(f"- {key}: {value}")
    for key, value in agent_input.items():
        if key == "metadata" or value in (None, "", []):
            continue
        parts.append(f"\n{key}")
        if isinstance(value, list):
            for item in value:
                parts.append(f"- {item}")
        else:
            parts.append(str(value))
    return "\n".join(parts).strip()


def validate_preprocessed_patent(
    metadata: dict[str, Any],
    sections: dict[str, str],
    claims: list[dict[str, Any]],
    *,
    source_text: str | None = None,
) -> dict[str, Any]:
    missing_fields = []
    warnings = []
    for field in ["title", "application_number", "registration_number"]:
        if not metadata.get(field):
            missing_fields.append(f"metadata.{field}")
    for field in ["abstract", "claims_text", "technical_field"]:
        if not sections.get(field):
            missing_fields.append(f"sections.{field}")
    if not claims:
        missing_fields.append("claims")
    if not metadata.get("ipc") and not metadata.get("cpc"):
        warnings.append("IPC/CPC classification was not extracted.")
    assignee_text = " ".join(metadata.get("assignee") or [])
    if re.search(r"\b(References Cited|Prokoski|Notice|705/2)\b", assignee_text, re.I):
        warnings.append("assignee_contains_reference_noise")
    abstract_text = sections.get("abstract", "") or ""
    if re.search(r"\b(Foreign Application Priority Data|Int\. Cl\.|9 Claims|Drawing Sheets)\b", abstract_text, re.I):
        warnings.append("abstract_contains_frontpage_noise")
    inventor_text = " ".join(metadata.get("inventors") or [])
    if len(metadata.get("inventors") or []) == 1 and source_text and ";" in source_text and inventor_text:
        warnings.append("inventor_multi_value_may_be_truncated")
    if metadata.get("claim_count") and claims and metadata["claim_count"] != len(claims):
        warnings.append("Extracted active claim count differs from total claim count.")
    if source_text:
        assignee_candidates = _extract_assignee_candidates(source_text)
        inventor_candidates = _extract_inventor_candidates(source_text)
        missing_assignees = sorted(set(assignee_candidates) - set(metadata.get("assignee") or []))
        missing_inventors = sorted(set(inventor_candidates) - set(metadata.get("inventors") or []))
        if missing_assignees:
            warnings.append(f"possible_missing_assignees: {', '.join(missing_assignees)}")
        if missing_inventors:
            warnings.append(f"possible_missing_inventors: {', '.join(missing_inventors)}")
    return {
        "is_valid": not missing_fields,
        "missing_fields": missing_fields,
        "warnings": warnings,
    }


def _find_section_headings(text: str) -> list[tuple[str, int]]:
    pattern = re.compile(
        r"(?m)^#?\s*(명세서|청구범위|발명의 설명|기술분야|배경기술|발명의 내용|해결하려는 과제|과제의 해결 수단|발명의 효과|도면의 간단한 설명|발명을 실시하기 위한 구체적인 내용|부호의 설명)\s*$"
    )
    return [(match.group(1), match.start()) for match in pattern.finditer(text)]


def _extract_abstract(text: str) -> str:
    match = re.search(r"\(57\)\s*요\s*약\s*(.+?)(?=\n#{0,6}\s*명세서|\n청구범위|\n####|\n명세서)", text, re.S)
    return normalize_blank_lines(match.group(1)) if match else ""


def _extract_foreign_abstract(text: str) -> str:
    text = _extract_foreign_frontpage_text(text)
    match = re.search(
        r"(?is)\babstract\b[:\s]*(.+?)(?=\b(what\s+is\s+claimed\s+is|the\s+invention\s+claimed\s+is|claims|references\s+cited|u\.s\.\s+patent|sheet\s+2\s+of|description|technical\s+field)\b|$)",
        text,
    )
    if not match:
        return ""
    abstract = match.group(1).strip()
    abstract = re.sub(r"\(\d{2}\).*", "", abstract)
    return normalize_blank_lines(abstract)


def extract_us_patent_sections(raw_text: str, *, cleaned_text: str = "") -> dict[str, str]:
    text = normalize_uspto_ocr_text(raw_text or cleaned_text or "")
    return {
        "abstract": postprocess_agent_text(_extract_uspto_abstract(text)),
        "claims_text": postprocess_claims_text(_extract_uspto_claims_text(text)),
        "technical_field": postprocess_agent_text(_extract_uspto_section(text, "TECHNICAL FIELD")),
        "background_art": postprocess_agent_text(_extract_uspto_section(text, "BACKGROUND ART", "BACKGROUND")),
        "problem": postprocess_agent_text(_extract_uspto_section(text, "DISCLOSURE", "Technical Problem")),
        "solution": postprocess_agent_text(_extract_uspto_section(text, "Technical Solution")),
        "effect": postprocess_agent_text(_extract_uspto_section(text, "Advantageous Effects")),
        "detailed_description": postprocess_agent_text(
            _extract_uspto_section(text, "DETAILED DESCRIPTION", "Description")
        ),
    }


def normalize_uspto_ocr_text(text: str) -> str:
    if not text:
        return ""
    text = remove_image_markdown(text)
    text = re.sub(r"(?im)^\s*US\s+\d[\d,]*\s*[A-Z]\d?\s*$", "", text)
    text = re.sub(r"(?im)^\s*U\.S\.\s+Patent(?:ed)?\b.*$", "", text)
    text = re.sub(r"(?im)^\s*Sheet\s+\d+\s+of\s+\d+\s*$", "", text)
    text = re.sub(r"(?im)^\s*Page\s+\d+\s*$", "", text)
    text = re.sub(r"(?im)^\s*\d+\s*$", "", text)
    text = _normalize_uspto_classification_ocr(text)
    text = re.sub(r"\bAl\b", "AI", text)
    text = re.sub(r"\bleaming\b", "learning", text, flags=re.I)
    return normalize_blank_lines(text)


def _extract_uspto_abstract(text: str) -> str:
    lines = text.splitlines()
    capture = False
    collected: list[str] = []
    continued_mode = False
    skip_until_page_two = False
    for raw_line in lines:
        line = raw_line.strip()
        if not capture:
            if re.search(r"(?i)(?:\(\s*(?:57|67)\s*\)\s*)?ABSTRACT\b", line):
                capture = True
                cleaned = _clean_uspto_abstract_line(line)
                if cleaned:
                    collected.append(cleaned)
            continue
        if _is_uspto_abstract_stop_line(line):
            break
        if re.search(r"(?i)^\(?Continued\)?$", line):
            continued_mode = True
            skip_until_page_two = True
            continue
        if skip_until_page_two:
            if re.search(r"(?i)(?:\bUS\s+\d[\d,]*\s*[A-Z]\d?\s+Page\s+2\b|\bSheet\s+2\s+of\b)", line):
                skip_until_page_two = False
                continue
            if line and re.match(r"(?i)^(?:and|modified|wherein|when|while|by|for|to|of|in)\b", line):
                skip_until_page_two = False
            else:
                continue
        if continued_mode and re.search(r"(?i)^(?:U\.S\.\s+Patent\b.*|US\s+\d[\d,]*\s*[A-Z]\d?.*\bPage\s+\d+\b|Sheet\s+\d+\s+of\s+\d+.*)$", line):
            continue
        if _is_uspto_abstract_noise_line(line):
            continue
        line = _clean_uspto_abstract_line(line)
        if not line:
            continue
        collected.append(line)
    return normalize_blank_lines("\n".join(collected))


def _extract_uspto_claims_text(text: str) -> str:
    pattern = re.compile(
        r"(?is)\b(?:What is claimed is|The invention claimed is)\s*:?\s*(.+?)(?=^\s*(?:TECHNICAL FIELD|BACKGROUND ART|BACKGROUND|DISCLOSURE|BRIEF DESCRIPTION OF DRAWINGS|DESCRIPTION|DETAILED DESCRIPTION)\b|\Z)",
        re.M,
    )
    match = pattern.search(text)
    return normalize_blank_lines(match.group(1)) if match else ""


def _extract_uspto_section(text: str, *labels: str) -> str:
    for label in labels:
        pattern = re.compile(
            rf"(?is)^\s*{re.escape(label)}\b[:\s]*(.+?)(?=^\s*(?:TECHNICAL FIELD|BACKGROUND ART|BACKGROUND|DISCLOSURE|BRIEF DESCRIPTION OF DRAWINGS|DESCRIPTION|DETAILED DESCRIPTION|What is claimed is|The invention claimed is)\b|\Z)",
            re.M,
        )
        match = pattern.search(text)
        if match:
            return normalize_blank_lines(match.group(1))
    return ""


def strip_section_heading_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        heading = stripped.lstrip("#").strip()
        if heading in SECTION_HEADINGS:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def truncate_at_next_major_section(text: str) -> str:
    match = re.search(r"(?m)^#?\s*(발명의 설명|기술분야|배경기술|발명의 내용|해결하려는 과제|과제의 해결 수단|발명의 효과|도면의 간단한 설명|발명을 실시하기 위한 구체적인 내용|부호의 설명)\s*$", text)
    return text[: match.start()].strip() if match else text


def postprocess_agent_text(text: str) -> str:
    text = strip_section_heading_lines(text)
    text = remove_paragraph_numbers(text)
    text = merge_section_broken_lines(text)
    return normalize_blank_lines(text)


def postprocess_claims_text(text: str) -> str:
    text = strip_section_heading_lines(text)
    text = remove_paragraph_numbers(text)
    text = _remove_uspto_claim_noise(text)
    text = re.sub(r"(?m)^\s*-\s*(청구항\s+\d+)", r"- \1", text)
    lines = text.splitlines()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        if re.match(r"^-?\s*청구항\s+\d+", stripped):
            result.append(stripped)
            continue
        if result and result[-1] and not re.match(r"^-?\s*청구항\s+\d+", result[-1]):
            result[-1] = result[-1].rstrip() + " " + stripped
        else:
            result.append(stripped)
    merged = "\n".join(result)
    merged = re.sub(r"([가-힣A-Za-z0-9])\n{2,}([가-힣A-Za-z0-9])", r"\1\2", merged)
    merged = re.sub(r"\s{2,}", " ", merged)
    return normalize_blank_lines(merged)


def remove_paragraph_numbers(text: str) -> str:
    text = re.sub(r"(?m)^-?\s*\[(\d{4})\]\s*", "", text)
    text = re.sub(r"(?m)^\[(\d{4})\]\s*", "", text)
    return text


def extract_paragraph_numbers(text: str) -> list[str]:
    return _dedupe(re.findall(r"\[(\d{4})\]", text))


def merge_section_broken_lines(text: str) -> str:
    text = re.sub(r"([A-Za-z])-\s*\n\s*([a-z])", r"\1\2", text)
    text = re.sub(r"([A-Za-z])/\s*\n\s*([a-z])", r"\1/\2", text)
    text = re.sub(r"([가-힣A-Za-z0-9])\n{2,}([가-힣A-Za-z0-9])", r"\1\2", text)
    text = re.sub(r"([가-힣A-Za-z0-9,;:./])\n([가-힣A-Za-z0-9])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", text)
    text = re.sub(r"([A-Za-z])/\s+([a-z])", r"\1/\2", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,;.])", r"\1", text)
    return text


def _line_end(text: str, start: int) -> int:
    index = text.find("\n", start)
    return len(text) if index == -1 else index + 1


def _search_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _extract_int(pattern: str, text: str) -> int | None:
    value = _search_group(pattern, text)
    return int(value) if value and value.isdigit() else None


def _normalize_korean_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", value)
    if not match:
        return value
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _extract_classifications(text: str, label: str) -> list[str]:
    values: list[str] = []
    capture_remaining = 0
    for line in text.splitlines():
        if label in line:
            capture_remaining = 8
        if capture_remaining <= 0:
            continue
        values.extend(re.findall(r"[A-HY]\d{2}[A-Z]\s*\d+/\d+", line))
        if re.search(r"\(\d{2}\)|명\s*세\s*서|청구범위", line) and label not in line:
            capture_remaining = 0
            continue
        capture_remaining -= 1
    return _dedupe([re.sub(r"^([A-HY]\d{2}[A-Z])\s*(\d+/\d+)$", r"\1 \2", value) for value in values])


def extract_foreign_frontpage_metadata(text: str) -> dict[str, Any]:
    normalized = normalize_uspto_ocr_text(str(text or ""))
    frontpage = _extract_foreign_frontpage_text(normalized)
    title = (
        _search_group(r"(?im)^\(\s*54\s*\)\s*(.+)$", frontpage)
        or _search_group(r"(?im)^Title\s*[:.]?\s*(.+)$", frontpage)
    )
    patent_number = (
        _search_group(r"(?im)^(?:Patent No\.?|Patent No)\s*[:.]?\s*([A-Z]*\s*[\d,]+(?:\s*[A-Z]\d?)?)", frontpage)
        or _search_group(r"(?im)^US\s*([\d,]+\s*[A-Z]\d?)$", frontpage)
    )
    registration_date = _search_group(r"(?im)^(?:Date of Patent)\s*[:.]?\s*(.+)$", frontpage)
    filing_date = _search_group(r"(?im)^(?:\(\s*21\s*\)\s*)?(?:Filed|PCT Filed)\s*[:.]?\s*(.+)$", frontpage)
    representative_ipc = _extract_foreign_representative_ipc(frontpage)
    ipc_values = _extract_foreign_classifications(frontpage, "Int. Cl.")
    if not ipc_values:
        ipc_values = _extract_foreign_classification_fallback(frontpage)
    if representative_ipc and representative_ipc in ipc_values:
        ipc_values = [representative_ipc, *[value for value in ipc_values if value != representative_ipc]]
    assignee_values = _extract_foreign_labeled_people(frontpage, ("(73) Assignee", "Assignee:", "Assignee")) or _extract_foreign_labeled_people(
        frontpage, ("(71) Applicant", "Applicant:", "Applicant")
    )
    inventor_values = _extract_foreign_labeled_people(
        " " + frontpage,
        ("(72) Inventors", "(72) Inventor", "Inventors:", "Inventor:", "Inventors", "Inventor"),
    )
    return {
        "ipc": ipc_values,
        "representative_ipc": representative_ipc or (ipc_values[0] if ipc_values else ""),
        "cpc": [],
        "assignee": assignee_values,
        "inventors": inventor_values,
        "applicant": _extract_foreign_labeled_people(frontpage, ("(71) Applicant", "Applicant:", "Applicant")),
        "claim_count": _extract_int(r"(?im)^\s*(\d+)\s+Claims\b", frontpage),
        "title": title,
        "patent_number": patent_number,
        "registration_date": registration_date,
        "filing_date": filing_date,
        "metadata_source": {
            "ipc": "ocr_front_page" if ipc_values else "",
            "cpc": "",
            "assignee": "ocr_front_page" if assignee_values else "",
            "inventors": "ocr_front_page" if inventor_values else "",
            "title": "ocr_front_page" if title else "",
            "patent_number": "ocr_front_page" if patent_number else "",
            "registration_date": "ocr_front_page" if registration_date else "",
            "filing_date": "ocr_front_page" if filing_date else "",
            "representative_ipc": "ocr_front_page" if representative_ipc else "",
        },
    }


def _extract_foreign_classifications(text: str, label: str) -> list[str]:
    text = _extract_foreign_frontpage_text(text)
    if label != "Int. Cl.":
        return []
    values: list[str] = []
    lines = text.splitlines()
    capture = False
    for raw_line in lines:
        line = _normalize_uspto_classification_ocr(raw_line.strip())
        if not capture:
            if re.search(r"(?i)(?:\(\s*51\s*\)\s*)?Int\.?\s*C[lh]\.?\b", line):
                capture = True
                values.extend(US_CLASSIFICATION_RE.findall(_ipc_line_before_next_uspto_label(line)))
            continue
        if _is_ipc_stop_line(line):
            break
        values.extend(US_CLASSIFICATION_RE.findall(_ipc_line_before_next_uspto_label(line)))
    return _normalize_us_classification_values(values)


def _extract_foreign_representative_ipc(text: str) -> str:
    text = _extract_foreign_frontpage_text(text)
    lines = text.splitlines()
    capture = False
    for raw_line in lines:
        line = _normalize_uspto_classification_ocr(raw_line.strip())
        if not capture:
            if re.search(r"(?i)(?:\(\s*51\s*\)\s*)?Int\.?\s*C[lh]\.?\b", line):
                capture = True
                matches = US_CLASSIFICATION_RE.findall(_ipc_line_before_next_uspto_label(line))
                if matches:
                    normalized = _normalize_us_classification_values(matches)
                    return normalized[0] if normalized else ""
            continue
        if _is_ipc_stop_line(line):
            break
        matches = US_CLASSIFICATION_RE.findall(_ipc_line_before_next_uspto_label(line))
        if matches:
            normalized = _normalize_us_classification_values(matches)
            return normalized[0] if normalized else ""
    return ""


def _ipc_line_before_next_uspto_label(line: str) -> str:
    return re.split(
        r"(?i)\(\s*52\s*\)\s*U\.?S\.?\s*C[IL]\b|\(\s*58\s*\)\s*Field\s+of\s+Classification|"
        r"\(\s*56\s*\)\s*References\s+Cited|\bCPC\b",
        line,
        maxsplit=1,
    )[0]


def _extract_foreign_classification_fallback(text: str) -> list[str]:
    normalized = _normalize_uspto_classification_ocr(_extract_foreign_frontpage_text(text))
    return _normalize_us_classification_values(
        re.findall(r"\bG06F\s+11/34\b|\bG06F\s+11/30\b|\bG06N\s+5/045\b", normalized)
    )


def _extract_foreign_labeled_people(text: str, labels: tuple[str, ...]) -> list[str]:
    text = _extract_foreign_frontpage_text(text)
    names: list[str] = []
    is_company_label = any("Applicant" in label or "Assignee" in label for label in labels)
    value = _clean_foreign_people_block(_extract_foreign_labeled_block(text, labels))
    if value:
        if is_company_label:
            names.extend(_split_foreign_company_values(value))
        else:
            names.extend(_split_foreign_people_values(value))
    return _dedupe(names)


def _split_foreign_people_values(value: str) -> list[str]:
    value = _clean_foreign_people_block(value)
    parts = [part.strip() for part in re.split(r"[;|]", value) if part.strip()]
    if len(parts) > 1:
        return [_normalize_foreign_person_name(part) for part in parts if _normalize_foreign_person_name(part)]
    # OCR often joins multiple English names without delimiters; split only on strong name boundaries.
    spaced = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}", value)
    normalized = [_normalize_foreign_person_name(part) for part in spaced]
    normalized = [part for part in normalized if part]
    return normalized or [_normalize_foreign_person_name(value)]


def _split_foreign_company_values(value: str) -> list[str]:
    value = _clean_foreign_people_block(value)
    if not value:
        return []
    value = re.split(
        r"\b(References Cited|Notice|Prior Publication Data|Foreign Application Priority Data|U\.S\. PATENT DOCUMENTS)\b",
        value,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,;")
    if not re.search(r",\s*(?:LTD\.?|INC\.?|LLC)\s*$", value, re.I):
        value = re.sub(r",\s*[^,]+(?:\([A-Z]{2}\))?$", "", value).strip()
    return [value]


def _extract_foreign_frontpage_text(text: str) -> str:
    if not text:
        return ""
    has_continued = bool(re.search(r"(?im)^\s*\(?Continued\)?\s*$", text))
    stop_markers = [
        r"\bWhat\s+is\s+claimed\s+is\b",
        r"\bThe\s+invention\s+claimed\s+is\b",
        r"\bDescription\b",
        r"\bDETAILED\s+DESCRIPTION\b",
        r"\bTECHNICAL FIELD\b",
        r"\bBACKGROUND ART\b",
        r"\bBACKGROUND\b",
        r"\bDISCLOSURE\b",
        r"\bBRIEF\s+DESCRIPTION\s+OF\s+(?:THE\s+)?DRAWINGS\b",
    ]
    if not has_continued:
        stop_markers.extend(
            [
                r"\bU\.S\.\s+Patent\b.*?\bSheet\s+2\s+of\b",
                r"\bUS\s+\d[\d,]*\s*[A-Z]\d?\s+Page\s+2\b",
                r"\bSheet\s+2\s+of\b",
                r"\bPage\s+2\b",
            ]
        )
    end = len(text)
    for pattern in stop_markers:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            end = min(end, match.start())
    return text[:end].strip()


def _is_uspto_abstract_stop_line(line: str) -> bool:
    if not line:
        return False
    return bool(
        re.search(
            r"(?i)^(?:\(\s*\d{2}\s*\)\s*)?(?:References Cited|What is claimed is|The invention claimed is|TECHNICAL FIELD|BACKGROUND ART|BACKGROUND|DISCLOSURE|BRIEF DESCRIPTION OF DRAWINGS|DESCRIPTION|DETAILED DESCRIPTION)\b",
            line,
        )
        or re.search(r"(?i)^\(\s*(?:51|52|56|58)\s*\)\s*$", line)
        or re.search(r"(?i)\b\d+\s+Claims\b", line)
        or re.search(r"(?i)\bDrawing Sheets\b", line)
        or re.search(r"(?i)\bFIG\.\s*\d+\b", line)
    )


def _is_uspto_abstract_noise_line(line: str) -> bool:
    if not line:
        return True
    if re.search(
        r"(?i)^(?:\(\s*\d{2}\s*\)\s*)?(?:Foreign Application Priority Data|Prior Publication Data|Int\.?\s*Cl\.?|U\.S\.?\s*Cl\.?|CPC|Patent No\.?|Date of Patent|Applicant|Assignee|Inventors?|Field of Classification Search|References Cited|Notice|Other Publications|Primary Examiner|Attorney, Agent)",
        line,
    ):
        return True
    if re.search(r"(?i)^US\s+\d{4}/\d+", line):
        return True
    if re.search(r"(?i)^[A-Z][a-z]{2}\.\s+\d{1,2},\s+\d{4}\s+\([A-Z]{2}\)\s+\d", line):
        return True
    if re.search(r"(?i)^\(?Continued\)?$", line):
        return True
    if US_CLASSIFICATION_RE.search(_normalize_uspto_classification_ocr(line)):
        return True
    if re.search(r"(?i)^[|/@$€£¥~_=<>\\\[\]{}()\"'`*+\-]{3,}$", line):
        return True
    if re.search(r"(?i)(analysis model|model library|workflow model build|real-time operation)", line):
        return True
    return False


def _clean_uspto_abstract_line(line: str) -> str:
    if not line:
        return ""
    line = re.sub(r"(?i)^.*?\bABSTRACT\b[:\s]*", "", line).strip()
    line = re.sub(r"(?i)\bUS\s+\d[\d,]*\s*[A-Z]\d?\s+Page\s+\d+\b", " ", line)
    line = re.sub(r"(?i)\bUS\s+\d{4}/\d+\s+A[I1l]\b\s+[A-Z][a-z]{2}\.\s+\d{1,2},\s+\d{4}", " ", line)
    line = re.sub(r"(?i)\(\s*30\s*\)\s*Foreign Application Priority Data", " ", line)
    line = re.sub(r"(?i)\(\s*65\s*\)\s*Prior Publication Data", " ", line)
    line = re.sub(r"(?i)[A-Z][a-z]{2}\.\s+\d{1,2},\s+\d{4}\s+\([A-Z]{2}\)\s+[0-9A-Za-z,\-]+", " ", line)
    line = re.sub(r"\b10-\d{4}-\d{7}\b", " ", line)
    line = re.sub(r"(?i)\(\s*51\s*\)\s*Int\.?\s*Ch?\.?", " ", line)
    line = re.sub(r"(?i)\bInt\.?\s*Cl\.?\b", " ", line)
    line = re.sub(US_CLASSIFICATION_RE, " ", line)
    line = re.sub(r"(?i)\(?Continued\)?.*?\bUS\s+\d[\d,]*\s*[A-Z]\d?\s+Page\s+2", " ", line)
    line = re.sub(r"(?i)\(?Continued\)?.*$", " ", line)
    line = re.sub(r"\(\s*\d{4}\.\d{2}\s*\)", " ", line)
    line = re.sub(r"(?i)\b\d+\s+Claims\b.*$", "", line)
    line = re.sub(r"(?i)\bDrawing Sheets\b.*$", "", line)
    line = re.sub(r"\s+\d+\s*$", "", line)
    line = re.sub(r"\s+", " ", line).strip(" ,;:-")
    return line


def _is_ipc_stop_line(line: str) -> bool:
    if not line:
        return False
    return bool(
        re.search(
            r"(?i)^(?:\(\s*\d{2}\s*\)\s*)?(?:U\.S\.?\s*Cl\.?|CPC|Applicant|Assignee|Inventors?|Field of Classification Search|References Cited|Notice|Prior Publication Data|Foreign Application Priority Data|ABSTRACT|What is claimed is|The invention claimed is|TECHNICAL FIELD|BACKGROUND ART|BACKGROUND|DISCLOSURE|DESCRIPTION|DETAILED DESCRIPTION)\b",
            line,
        )
        or re.search(r"(?i)^\(?Continued\)?$", line)
        or re.search(r"(?i)\b9\s+Claims\b", line)
    )


def _extract_foreign_labeled_block(
    text: str,
    labels: tuple[str, ...],
    *,
    extra_stop_labels: tuple[str, ...] = (),
) -> str:
    lines = text.splitlines()
    capture = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        matched_label = next(
            (
                label
                for label in labels
                if stripped.startswith(label) or re.match(rf"^\(\s*\d{{2}}\s*\)\s*{re.escape(label)}", stripped)
            ),
            None,
        )
        if matched_label:
            capture = True
            value = re.sub(rf"^\(\s*\d{{2}}\s*\)\s*{re.escape(matched_label)}", "", stripped).strip()
            value = re.sub(rf"^{re.escape(matched_label)}", "", value).strip(" :")
            if value:
                collected.append(value)
            continue
        if not capture:
            continue
        if extra_stop_labels and any(
            stripped.startswith(stop) or re.match(rf"^\(\s*\d{{2}}\s*\)\s*{re.escape(stop)}", stripped)
            for stop in extra_stop_labels
        ):
            break
        if USPTO_LABEL_STOP_RE.match(stripped):
            break
        if stripped:
            collected.append(stripped)
    return " ".join(collected)


def _normalize_us_classification_values(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        value = _normalize_uspto_classification_ocr(value)
        normalized = re.sub(r"\(\d{4}\.\d{2}\)", "", value)
        normalized = re.sub(r"\s*/\s*", "/", normalized)
        normalized = re.sub(r"\b(G06F)\s+1130\b", r"\1 11/30", normalized)
        normalized = re.sub(r"\b(G06F)\s+11/734\b", r"\1 11/34", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        cleaned.append(normalized)
    return _dedupe(cleaned)


def _normalize_uspto_classification_ocr(text: str) -> str:
    if not text:
        return ""

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        fixed = token
        replacements = {
            "GO6F": "G06F",
            "GOOF": "G06F",
            "GO6N": "G06N",
            "GOON": "G06N",
            "GOGN": "G06N",
            "GOGF": "G06F",
            "GOSB": "G05B",
            "GOIR": "G01R",
            "HOIL": "H01L",
            "A6IB": "A61B",
        }
        for wrong, correct in replacements.items():
            fixed = re.sub(rf"\b{wrong}(?=\s*\d+\s*/?\s*\d+)", correct, fixed, flags=re.I)
        return fixed

    return US_CLASSIFICATION_CANDIDATE_RE.sub(replace, text)


def _clean_foreign_people_block(value: str) -> str:
    value = re.sub(r"\(\d{2}\).*", "", value)
    value = re.split(
        r"\b(Field of Classification Search|References Cited|U\.S\. PATENT DOCUMENTS|OTHER PUBLICATIONS|See application file for complete search history|Notice|Prior Publication Data|Foreign Application Priority Data)\b",
        value,
        maxsplit=1,
        flags=re.I,
    )[0]
    return re.sub(r"\s+", " ", value).strip(" ,;")


def _normalize_foreign_person_name(value: str) -> str:
    value = re.sub(r",\s*[^,]+(?:\([A-Z]{2}\))?$", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        return ""
    if not re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}", value):
        return ""
    return value


def _remove_uspto_claim_noise(text: str) -> str:
    text = re.sub(r"(?im)\bUS\s+\d[\d,]*\s*[A-Z]\d?\s+\d+\b", " ", text)
    text = re.sub(r"(?im)\bUS\s+\d[\d,]*\s*[A-Z]\d?\b", " ", text)
    text = re.sub(r"(?im)^\s*(?:\d+\s+){2,}\d+\s*$", "", text)
    text = re.sub(r"(?im)^\s*\d+\s*$", "", text)
    text = re.sub(r"\bleaming\b", "learning", text, flags=re.I)
    text = re.sub(r"\bAl\b", "AI", text)
    return text


def _extract_prior_art(text: str) -> list[str]:
    patterns = [
        r"\bKR\s*\d{7,13}\s*[A-Z]\d?\*?",
        r"\bJP\s*\d{7,13}\s*[A-Z]\d?\*?",
        r"\bUS\s*\d{7,13}\s*[A-Z]\d?\*?",
        r"\bUS\s*\d{4}/\d{6,8}\s*[A-Z]\d?\*?",
    ]
    values: list[str] = []
    for pattern in patterns:
        values.extend(re.findall(pattern, text))
    return _dedupe([re.sub(r"\s+", " ", value) for value in values])


def _extract_assignee_list(text: str) -> list[str]:
    return _extract_assignee_candidates(text)


def _extract_inventor_list(text: str) -> list[str]:
    return _extract_inventor_candidates(text)


def _extract_assignee_candidates(text: str) -> list[str]:
    chunks = re.findall(
        r"\(73\)\s*특허권자\s*(.+?)(?=\(72\)|\(74\)|\(56\)|전체 청구항|명세서|청구범위|\(73\)|$)",
        text,
        re.S,
    )
    names: list[str] = []
    for chunk in chunks:
        value = re.sub(r"\s+", " ", normalize_blank_lines(chunk))
        names.extend(_company_names_from_chunk(value))
        if not names:
            names.extend([part.strip() for part in re.split(r"[,，]", _split_before_address(value)) if part.strip()])
    return _dedupe([name for name in names if name])


def _extract_inventor_candidates(text: str) -> list[str]:
    chunks = re.findall(
        r"\(72\)\s*발명자\s*(.+?)(?=\(74\)|\(56\)|전체 청구항|명세서|청구범위|\(72\)|$)",
        text,
        re.S,
    )
    names: list[str] = []
    for chunk in chunks:
        value = re.sub(r"\s+", " ", normalize_blank_lines(chunk))
        names.extend(
            re.findall(
                r"([가-힣]{2,5})\s+(?=서울|경기|경기도|대전|부산|인천|광주|대구|울산|세종|강원|충청|충북|충남|전라|전북|전남|경상|경북|경남|제주)",
                value,
            )
        )
    return _dedupe(names)


def _company_names_from_chunk(value: str) -> list[str]:
    suffix_pattern = r"(?:주식회사|유한회사|학교법인|대학교|연구원|연구소|공사|공단|재단|회사|Inc\.?|LLC|Ltd\.?)"
    candidates: list[str] = []
    for match in re.finditer(suffix_pattern, value):
        prefix = value[: match.end()].strip()
        prefix = re.split(r"[()]\s*", prefix)[-1].strip() if ")" in prefix else prefix
        prefix = _remove_leading_address(prefix)
        words = prefix.split()
        if len(words) > 6:
            words = words[-6:]
        candidate = " ".join(words).strip(" ,，")
        if candidate:
            candidates.append(candidate)
    return candidates


def _remove_leading_address(value: str) -> str:
    address_tokens = _address_tokens()
    last_position = -1
    last_token = ""
    for token in address_tokens:
        position = value.rfind(token)
        if position > last_position:
            last_position = position
            last_token = token
    if last_position <= 0:
        return value
    before = value[:last_position]
    after = value[last_position + len(last_token) :].strip()
    if re.search(r"\d|로|길|구|군|시|읍|면|동", before) and after:
        return after
    return value


def _split_before_address(value: str) -> str:
    address_tokens = _address_tokens()
    positions = [value.find(token) for token in address_tokens if token in value]
    return value[: min(positions)].strip() if positions else value.strip()


def _address_tokens() -> list[str]:
    return [
        "서울",
        "서울특별시",
        "경기",
        "경기도",
        "대전",
        "대전광역시",
        "부산",
        "부산광역시",
        "인천",
        "인천광역시",
        "광주",
        "광주광역시",
        "대구",
        "대구광역시",
        "울산",
        "울산광역시",
        "세종",
        "세종특별자치시",
        "강원",
        "충청",
        "전라",
        "경상",
        "제주",
    ]


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        clean = value.strip().rstrip("*")
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


def _pick(data: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: data.get(key) for key in keys}
