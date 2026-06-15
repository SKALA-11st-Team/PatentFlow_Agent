from pathlib import Path
from typing import Any
import re
import unicodedata


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
    "摘要": "abstract",
    "权利要求书": "claims_text",
    "技术领域": "technical_field",
    "背景技术": "background_art",
    "发明内容": "solution",
    "附图说明": "figure_description",
    "具体实施方式": "detailed_description",
    "要約": "abstract",
    "特許請求の範囲": "claims_text",
    "技術分野": "technical_field",
    "背景技術": "background_art",
    "発明が解決しようとする課題": "problem",
    "課題を解決するための手段": "solution",
    "発明の効果": "effect",
    "図面の簡単な説明": "figure_description",
    "発明を実施するための形態": "detailed_description",
    "ABSTRACT": "abstract",
    "CLAIMS": "claims_text",
    "FIELD OF THE INVENTION": "technical_field",
    "TECHNICAL FIELD": "technical_field",
    "BACKGROUND": "background_art",
    "BACKGROUND OF THE INVENTION": "background_art",
    "SUMMARY": "solution",
    "SUMMARY OF THE INVENTION": "solution",
    "BRIEF DESCRIPTION OF THE DRAWINGS": "figure_description",
    "DETAILED DESCRIPTION": "detailed_description",
    "DETAILED DESCRIPTION OF THE EMBODIMENTS": "detailed_description",
}

HEADER_NORMALIZE_MAP = {
    "명 세 서": "명세서",
    "기 술 분 야": "기술분야",
    "배 경 기 술": "배경기술",
}

FOREIGN_BRACKET_HEADINGS = {
    "特許請求の範囲",
    "発明の詳細な説明",
    "技術分野",
    "背景技術",
    "発明の概要",
    "発明が解決しようとする課題",
    "課題を解決するための手段",
    "発明の効果",
    "図面の簡単な説明",
    "発明を実施するための形態",
}

STRUCTURAL_HEADERS.update(SECTION_ALIASES)
STRUCTURAL_HEADERS.update({"発明の詳細な説明", "発明の概要"})
SECTION_HEADINGS.update(SECTION_ALIASES)
SECTION_HEADINGS.update({"発明の詳細な説明", "発明の概要"})

IMAGE_MARKDOWN_RE = re.compile(r"!\[image\s+(\d+)\]\(<([^>]+)>\)")
REPRESENTATIVE_FIGURE_RE = re.compile(
    r"(?:대\s*표\s*도|대표도)\s*[-:]\s*도\s*(\d+)",
)
REPRESENTATIVE_LABEL_RE = re.compile(r"(?:대\s*표\s*도|대표도)")
DRAWING_SECTION_HEADING_RE = re.compile(r"(?:^|\n)#?\s*도면\s*(?:\n|$)")
DRAWING_ITEM_RE = re.compile(r"^\s*-?\s*도면\s*(\d+)\s*$", re.MULTILINE)
FOREIGN_DRAWING_HEADING_RE = re.compile(
    r"(?im)^(?:#{1,6}\s*)?(?:图|図|FIG\.?)\s*([1１])\s*$"
)


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
            foreign_drawing_match = FOREIGN_DRAWING_HEADING_RE.search(raw_text or "")
            if not foreign_drawing_match:
                return None
            image_match = IMAGE_MARKDOWN_RE.search(raw_text, foreign_drawing_match.end())
            if not image_match:
                return None
            markdown_paths = (source or {}).get("markdown_paths") or []
            drawing = {
                "figure_number": "도1",
                "image_path": image_match.group(2),
                "image_source": "foreign_drawing_section",
            }
            if markdown_paths:
                drawing["markdown_path"] = str(markdown_paths[0])
            return drawing
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
    for heading in FOREIGN_BRACKET_HEADINGS:
        text = text.replace(f"【{heading}】", f"\n{heading}\n")
    text = re.sub(r"(?im)^\s*What\s+is\s+claimed\s+is\s*:\s*$", "\nCLAIMS\n", text)
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
    if re.match(r"^-?\s*\d+\s*[.)]\s+", stripped):
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
    drawing_below_full_text = extract_text_after_drawings(cleaned_text, country=country)
    if drawing_below_full_text:
        sections["full_text_after_drawings"] = drawing_below_full_text
    drawing_context = build_drawing_context(raw_text, sections, source=source)

    if api_data:
        metadata = merge_api_metadata(
            extract_pdf_support_metadata(cleaned_text, db_metadata=db_metadata),
            api_data.get("metadata") or {},
        )
        api_sections = api_data.get("sections") or {}
        if api_sections.get("abstract"):
            sections["abstract"] = postprocess_agent_text(api_sections["abstract"])
        api_claims = api_data.get("claims") or []
        if api_claims:
            claims = [
                {
                    "claim_no": claim["claim_no"],
                    "text": postprocess_agent_text(claim["text"]),
                    "is_independent": claim.get("is_independent", False),
                    "dependency": claim.get("dependency"),
                }
                for claim in api_claims
            ]
            claims = _repair_foreign_api_claims_if_needed(claims, sections.get("claims_text", ""))
        else:
            claims = extract_claims(sections.get("claims_text", ""))
        claim_stats = {
            key: value
            for key, value in (api_data.get("claim_stats") or {}).items()
            if key != "deleted_claim_numbers"
        }
        if not claim_stats or (claims and not claim_stats.get("active_claim_count")):
            claim_stats = build_claim_stats(metadata.get("reported_claim_count"), claims)
    else:
        metadata = extract_pdf_fallback_metadata(cleaned_text, db_metadata=db_metadata)
        claims = extract_claims(sections.get("claims_text", ""))
        claim_stats = build_claim_stats(metadata.get("claim_count"), claims)

    metadata = merge_db_context_metadata(metadata, db_metadata=db_metadata)
    metadata["prior_art"] = remove_self_prior_art(metadata.get("prior_art") or [], metadata)
    metadata["claim_count"] = claim_stats["active_claim_count"] or metadata.get("claim_count")
    metadata["assignee_count"] = len(metadata.get("assignee") or [])
    metadata["has_co_assignee"] = metadata["assignee_count"] > 1

    validation = validate_preprocessed_patent(metadata, sections, claims, source_text=cleaned_text)
    patent_id = _first_non_empty(
        metadata.get("registration_number"),
        metadata.get("application_number"),
        source.get("application_number") if source else None,
    )

    country_prefix = str(metadata.get("country") or (db_metadata or {}).get("country") or "KR").upper()
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
        "citing_documents": (api_data or {}).get("citing_document_records", []),
        "citing_stats": (api_data or {}).get("citing_stats", {}),
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

    # CPC and prior art are usually richer in the PDF markdown for this project.
    for field in ["cpc", "prior_art"]:
        api_values = api_metadata.get(field) or []
        pdf_values = pdf_metadata.get(field) or []
        merged[field] = _dedupe([*api_values, *pdf_values])
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
    return {
        "country": db_metadata.get("country"),
        "application_number": db_metadata.get("application_number"),
        "registration_number": db_metadata.get("registration_number"),
        "title": db_metadata.get("title_final"),
        # 해외특허는 KIPRIS 서지 API가 IPC를 비워주는 경우가 있어(CN 등) 본문 (51) Int.Cl. 표기에서
        # 직접 보강한다. API가 IPC를 주면 merge_api_metadata가 그쪽을 우선하고, 없을 때만 본문값을 쓴다.
        "ipc": _extract_classifications(text, "국제특허분류", "Int.Cl", "Int. Cl"),
        "cpc": _extract_classifications(text, "CPC특허분류"),
        "prior_art": _extract_prior_art(text),
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
    if not matches:
        matches = list(re.finditer(r"【請求項\s*([0-9０-９]+)】\s*(.*?)(?=【請求項\s*[0-9０-９]+】|$)", claims_text, re.S))
    if not matches:
        matches = list(re.finditer(r"(?m)^-?\s*(\d+)\s*[.)]\s*(.*)$", claims_text))
    claims: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        claim_no = int(normalize_fullwidth_digits(match.group(1)))
        first_line = match.group(2).strip()
        if match.re.flags & re.S:
            body = ""
        else:
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


# EXT-06: 종속 청구항 인용은 인용 종결어미(에 있어서/에 따른/에 기재된/의 등)를 반드시 동반한다.
# 단순 구성요소 나열(예: "제1 또는 제2 위치")을 종속 인용으로 오판(false-positive)하지 않으면서,
# "제1항에 따른/기재된/의" 같은 인용 표현 누락(false-negative)도 방지한다.
_CLAIM_DEPENDENCY_PATTERN = (
    r"(?:청구항|제)\s*(\d+)\s*항?"
    r"(?:\s*(?:내지|또는|및)\s*(?:청구항|제)?\s*\d+\s*항?)*"
    r"\s*(?:중\s*)?(?:어느\s*(?:한|하나의?)\s*항)?"
    r"\s*(?:에\s*있어서|에\s*기재된|에\s*따른|에\s*의한|에\s*있어|에서|의\s|에\s)"
)


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


def _repair_foreign_api_claims_if_needed(
    claims: list[dict[str, Any]],
    claims_text: str,
) -> list[dict[str, Any]]:
    if len(claims) != 1:
        return claims
    only_claim = claims[0] if claims else {}
    only_text = str(only_claim.get("text") or "")
    if not only_text:
        return claims

    # Some foreign bibliographic APIs return every claim concatenated into a
    # single claimText entry. In that case the trailing "claim 1" references in
    # later claims incorrectly mark the whole blob as dependent. Re-split from
    # the richer claims_text section when numbered English claims are visible.
    split_source = claims_text or only_text
    reparsed = extract_english_claims(split_source)
    if len(reparsed) <= 1:
        return claims
    if not any(claim.get("is_independent") for claim in reparsed):
        return claims
    return reparsed


def _extract_english_claim_dependency(text: str) -> int | None:
    dependency = _extract_int(
        r"\bclaim\s+(\d+)\b(?:\s*(?:,|and|or)\s*claim\s+\d+\b)*",
        text,
    )
    if dependency is not None:
        return dependency
    dependency = _extract_int(
        r"\baccording to claim\s+(\d+)\b",
        text,
    )
    if dependency is not None:
        return dependency
    return None


def extract_text_after_drawings(text: str, *, country: str = "") -> str:
    normalized_country = str(country or "").upper()
    if normalized_country and normalized_country != "KR":
        extracted = _extract_foreign_text_after_figure_pages(text)
        if extracted:
            return extracted

    start_labels = ["도면의 간단한 설명", "도면"]
    end_labels = ["발명을 실시하기 위한 구체적인 내용", "발명의 설명", "기술분야", "배경기술"]
    return _extract_after_heading_block(text, start_labels=start_labels, end_labels=end_labels)


def _extract_claim_dependency(text: str) -> int | None:
    dependency = _extract_int(_CLAIM_DEPENDENCY_PATTERN, text)
    if dependency is not None:
        return dependency
    dependency = _extract_int(r"claims?\s*(\d+)", text)
    if dependency is not None:
        return dependency
    compact_japanese = re.sub(r"\s+", "", text)
    if re.match(
        r"請求項[0-9０-９]+(?:(?:又は|若しくは|ないし|乃至|～|〜|-)[0-9０-９]+)?"
        r"記載の.+?(?:システム|装置|プログラム|記録媒体)であって",
        compact_japanese,
        re.S,
    ):
        return None
    value = _search_group(
        r"請求項([0-9０-９]+)"
        r"(?:(?:又は|若しくは|ないし|乃至|～|〜|-)[0-9０-９]+)?"
        r"(?:のいずれか(?:１|1)項)?"
        r"(?:に記載|記載)",
        compact_japanese,
    )
    return int(normalize_fullwidth_digits(value)) if value else None


def normalize_fullwidth_digits(value: str | None) -> str:
    return str(value or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def remove_self_prior_art(values: list[str], metadata: dict[str, Any]) -> list[str]:
    country = str(metadata.get("country") or "").upper()
    registration = normalize_document_identifier(metadata.get("registration_number"))
    if not country or not registration:
        return values
    target_prefix = f"{country}{registration}"
    return [
        value
        for value in values
        if not normalize_document_identifier(value).startswith(target_prefix)
    ]


def normalize_document_identifier(value: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


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
    country = str(metadata.get("country") or "KR").upper()
    required_sections = []
    if country in {"", "KR"} or not any(
        sections.get(field) for field in ("technical_field", "solution", "detailed_description")
    ):
        required_sections.append("technical_field")
    if country in {"", "KR"} or not sections.get("solution"):
        required_sections.append("abstract")
    if not claims:
        required_sections.append("claims_text")
    for field in required_sections:
        if not sections.get(field):
            missing_fields.append(f"sections.{field}")
    if not claims:
        missing_fields.append("claims")
    if not metadata.get("ipc") and not metadata.get("cpc"):
        warnings.append("IPC/CPC classification was not extracted.")
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
    headings = "|".join(
        sorted((re.escape(heading) for heading in SECTION_ALIASES), key=len, reverse=True)
    )
    pattern = re.compile(rf"(?im)^#{{0,6}}\s*({headings})\s*$")
    return [(match.group(1), match.start()) for match in pattern.finditer(text)]


def _extract_after_heading_block(text: str, *, start_labels: list[str], end_labels: list[str]) -> str:
    if not text.strip():
        return ""
    start_pattern = re.compile(
        r"(?im)^#?\s*(?:"
        + "|".join(re.escape(label) for label in start_labels)
        + r")\s*$"
    )
    end_pattern = re.compile(
        r"(?im)^#?\s*(?:"
        + "|".join(re.escape(label) for label in end_labels)
        + r")\b[:\s]*$"
    )
    start_match = start_pattern.search(text)
    if not start_match:
        return ""

    search_start = start_match.end()
    end_match = end_pattern.search(text, search_start)
    if end_match:
        content_start = _line_end(text, end_match.start())
        return postprocess_agent_text(text[content_start:].strip())

    return postprocess_agent_text(text[search_start:].strip())


def _extract_foreign_text_after_figure_pages(text: str) -> str:
    figure_matches = list(re.finditer(r"(?im)^\s*FIG(?:S)?\s*\.\s*[\d\s,toand]+", text))
    if not figure_matches:
        return ""

    last_figure = figure_matches[-1]
    after_figure = text[last_figure.end() :]
    page_break = re.search(r"(?m)^\s*(?:US\s+\d[\d,\s]*[A-Z]?\d?\s*$|[\d,]+\s+[A-Z]\d?\s*$|\d+\s*$)\s*$", after_figure)
    if not page_break:
        page_break = re.search(r"(?m)^\s*[\d,]+\s+[A-Z]\d?\s+\d+\s*$", after_figure)
    if not page_break:
        return ""

    content_start = last_figure.end() + page_break.end()
    return postprocess_agent_text(text[content_start:].strip())


def _extract_abstract(text: str) -> str:
    patterns = [
        r"\(57\)\s*요\s*약\s*(.+?)(?=\n#{0,6}\s*명세서|\n청구범위|\n####|\n명세서)",
        r"\(57\)\s*摘要\s*(.+?)(?=\n(?:CN\s*\d+\s*[A-Z]?|1\.\s*一种|权利要求书|技术领域|背景技术|发明内容|附图说明|具体实施方式)\s*$)",
        r"\(57\)\s*ABSTRACT\s*(.+?)(?=\n#{0,6}\s*(?:BACKGROUND|FIELD OF THE INVENTION|TECHNICAL FIELD|CLAIMS?)\s*$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.S | re.M | re.I)
        if match:
            return normalize_blank_lines(match.group(1))
    return ""


def extract_us_patent_sections(raw_text: str, *, cleaned_text: str = "") -> dict[str, str]:
    text = normalize_uspto_ocr_text(raw_text or cleaned_text or "")
    return {
        "abstract": postprocess_agent_text(_extract_uspto_abstract(text)),
        "claims_text": postprocess_claims_text(_extract_uspto_claims_text(text)),
        "technical_field": postprocess_agent_text(_extract_uspto_section(text, "TECHNICAL FIELD", "FIELD OF THE INVENTION")),
        "background_art": postprocess_agent_text(_extract_uspto_section(text, "BACKGROUND ART", "BACKGROUND")),
        "problem": postprocess_agent_text(_extract_uspto_section(text, "DISCLOSURE", "Technical Problem")),
        "solution": postprocess_agent_text(_extract_uspto_section(text, "Technical Solution", "SUMMARY OF THE INVENTION", "SUMMARY")),
        "effect": postprocess_agent_text(_extract_uspto_section(text, "Advantageous Effects")),
        "detailed_description": postprocess_agent_text(
            # PCT 국내단계(WIPO 표준) 명세서는 상세설명을 "BEST MODE"/"MODE FOR CARRYING OUT
            # THE INVENTION" 헤딩 아래 둔다. 기존의 단독 "DESCRIPTION" 라벨은 "DESCRIPTION OF
            # (THE) DRAWINGS"를 먼저 매칭해 도면 설명을 상세설명으로 잘못 잡았으므로 제거하고,
            # WIPO 국내단계 헤딩 변형을 명시적으로 인식한다.
            _extract_uspto_section(
                text,
                "DETAILED DESCRIPTION",
                "DESCRIPTION OF THE EMBODIMENTS",
                "DESCRIPTION OF EMBODIMENTS",
                "BEST MODE FOR CARRYING OUT THE INVENTION",
                "MODE FOR CARRYING OUT THE INVENTION",
                "MODE FOR THE INVENTION",
                "BEST MODE",
            )
        ),
    }


def normalize_uspto_ocr_text(text: str) -> str:
    if not text:
        return ""
    text = remove_image_markdown(text)
    text = re.sub(r"(?im)^\s*US\s+\d[\d,]*\s*[A-Z]\d?\s*$", "", text)
    text = re.sub(r"(?im)^\s*U\.S\.\s+Patent(?:ed)?\b.*$", "", text)
    text = re.sub(r"(?im)^\s*(?:Sheet|heet)\s+\d+\s+of\s+\d+\s*$", "", text)
    text = re.sub(r"(?im)^\s*Page\s+\d+\s*$", "", text)
    text = re.sub(r"(?im)^\s*\d+\s*$", "", text)
    text = re.sub(r"\bAl\b", "AI", text)
    text = re.sub(r"\bleaming\b", "learning", text, flags=re.I)
    return normalize_blank_lines(text)


def _extract_uspto_abstract(text: str) -> str:
    match = re.search(
        r"(?is)\b(?:\(\s*(?:57|67)\s*\)\s*)?ABSTRACT\b[:\s]*(.+?)(?=^\s*(?:TECHNICAL FIELD|FIELD OF THE INVENTION|BACKGROUND ART|BACKGROUND|DISCLOSURE|SUMMARY OF THE INVENTION|SUMMARY|BRIEF DESCRIPTION OF DRAWINGS|DESCRIPTION|DETAILED DESCRIPTION|BEST MODE)\b|\Z)",
        text,
        re.M,
    )
    return normalize_blank_lines(match.group(1)) if match else ""


def _extract_uspto_claims_text(text: str) -> str:
    match = re.search(
        r"(?is)\b(?:What is claimed is|The invention claimed is)\s*:?\s*(.+?)(?=^\s*(?:TECHNICAL FIELD|BACKGROUND ART|BACKGROUND|DISCLOSURE|BRIEF DESCRIPTION OF DRAWINGS|DESCRIPTION|DETAILED DESCRIPTION|BEST MODE)\b|\Z)",
        text,
        re.M,
    )
    return normalize_blank_lines(match.group(1)) if match else ""


def _extract_uspto_section(text: str, *labels: str) -> str:
    stop_labels = (
        "TECHNICAL FIELD",
        "FIELD OF THE INVENTION",
        "BACKGROUND ART",
        "BACKGROUND",
        "DISCLOSURE",
        "SUMMARY OF THE INVENTION",
        "SUMMARY",
        "BRIEF DESCRIPTION OF THE DRAWINGS",
        "BRIEF DESCRIPTION OF DRAWINGS",
        "DESCRIPTION OF THE DRAWINGS",
        "DESCRIPTION OF DRAWINGS",
        "DESCRIPTION",
        "DETAILED DESCRIPTION",
        "BEST MODE FOR CARRYING OUT THE INVENTION",
        "MODE FOR CARRYING OUT THE INVENTION",
        "BEST MODE",
        "What is claimed is",
        "The invention claimed is",
    )
    for label in labels:
        pattern = re.compile(
            rf"(?is)^\s*{re.escape(label)}\b[:\s]*(.+?)(?=^\s*(?:{'|'.join(re.escape(item) for item in stop_labels)})\b|\Z)",
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
    text = re.sub(r"(?m)^\s*-\s*(청구항\s+\d+)", r"- \1", text)
    lines = text.splitlines()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        if re.match(r"^-?\s*(?:청구항\s+)?\d+\s*[.)]?", stripped):
            result.append(stripped)
            continue
        if result and result[-1] and not re.match(r"^-?\s*(?:청구항\s+)?\d+\s*[.)]?", result[-1]):
            result[-1] = result[-1].rstrip() + " " + stripped
        else:
            result.append(stripped)
    merged = "\n".join(result)
    merged = re.sub(r"([가-힣A-Za-z0-9])\n{2,}([가-힣A-Za-z0-9])", r"\1\n\2", merged)
    merged = re.sub(r"[ \t]{2,}", " ", merged)
    return normalize_blank_lines(merged)


def remove_paragraph_numbers(text: str) -> str:
    text = re.sub(r"(?m)^-?\s*\[(\d{4})\]\s*", "", text)
    text = re.sub(r"(?m)^\[(\d{4})\]\s*", "", text)
    text = re.sub(r"【[０-９]{4}】\s*", "", text)
    return text


def extract_paragraph_numbers(text: str) -> list[str]:
    return _dedupe(re.findall(r"\[(\d{4})\]", text))


def merge_section_broken_lines(text: str) -> str:
    text = re.sub(r"([가-힣A-Za-z0-9])\n{2,}([가-힣A-Za-z0-9])", r"\1\2", text)
    text = re.sub(r"([가-힣A-Za-z0-9,;:.])\n([가-힣A-Za-z0-9])", r"\1 \2", text)
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


def _extract_classifications(text: str, *labels: str) -> list[str]:
    # 라벨(한국어 "국제특허분류" 또는 해외 PDF의 "Int.Cl." 등)을 만나면 이후 몇 줄에서
    # IPC/CPC 코드를 수집한다. 해외특허는 KIPRIS 서지 API가 분류를 비워주는 경우가 있어
    # 본문의 (51) Int.Cl. 표기에서 직접 추출해야 한다.
    lowered_labels = [label.lower() for label in labels]
    values: list[str] = []
    capture_remaining = 0
    for line in text.splitlines():
        is_label_line = any(label in line.lower() for label in lowered_labels)
        if is_label_line:
            capture_remaining = 8
        if capture_remaining <= 0:
            continue
        values.extend(re.findall(r"[A-HY]\d{2}[A-Z]\s*\d+/\d+", line))
        if re.search(r"\(\d{2}\)|명\s*세\s*서|청구범위", line) and not is_label_line:
            capture_remaining = 0
            continue
        capture_remaining -= 1
    return _dedupe([re.sub(r"^([A-HY]\d{2}[A-Z])\s*(\d+/\d+)$", r"\1 \2", value) for value in values])


def _extract_prior_art(text: str) -> list[str]:
    normalized_text = unicodedata.normalize("NFKC", text).replace("−", "-").replace("–", "-")
    patterns = [
        r"\bKR\s*\d{7,13}\s*[A-Z]\d?\*?",
        r"\bJP\s*\d{7,13}\s*[A-Z]\d?\*?",
        r"\bUS\s*\d{7,13}\s*[A-Z]\d?\*?",
        r"\bUS\s*\d{4}/\d{6,8}\s*[A-Z]\d?\*?",
    ]
    values: list[str] = []
    for pattern in patterns:
        values.extend(re.findall(pattern, normalized_text))

    for era, year, serial in re.findall(r"特開(?:(昭|平|令))?(\d{1,4})-(\d{5,7})", normalized_text):
        publication_year = _japanese_publication_year(era, int(year))
        values.append(f"JP{publication_year}{serial} A")

    for year, serial, kind in re.findall(
        r"米国特許出願公開第(\d{4})/(\d{6,8})\s*\(\s*US\s*,\s*(A\d?)\s*\)",
        normalized_text,
        flags=re.IGNORECASE,
    ):
        values.append(f"US{year}{serial} {kind.upper()}")

    return _dedupe([re.sub(r"\s+", " ", value).replace("*", "") for value in values])


def _japanese_publication_year(era: str, year: int) -> int:
    era_start_years = {
        "昭": 1925,
        "平": 1988,
        "令": 2018,
    }
    return era_start_years.get(era, 0) + year if era else year


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
