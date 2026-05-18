from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rag.industry_report_chunker import (
    Section,
    build_chunks_from_sections,
    clean_markdown,
    keep_section,
    normalize_section_text,
    split_by_headings,
)


AI_INDEX_TOKEN_LIMIT = 500
AI_INDEX_TOKEN_OVERLAP = 60
MIN_MERGED_CHARS = 350
NOISE_MARKERS = (
    "appendix",
    "references",
    "bibliography",
    "doi.org",
    "figure a",
    "table a",
    "endnotes",
    "acknowledgments",
)


def chunk_report(
    *,
    markdown_path: Path,
    source_text: str,
    source_name: str,
    published_year: int | None,
    token_limit: int,
    token_overlap: int,
) -> list[dict[str, Any]]:
    del markdown_path
    sections = split_ai_index_sections(clean_markdown(source_text))
    sections = merge_short_sections(
        [
            Section(
                heading=section.heading,
                industry=section.industry,
                page=section.page,
                text=strip_embedded_chart_ocr_tail(section.text),
            )
            for section in sections
            if keep_section(section) and not is_noise_section(section)
        ]
    )
    chunks = build_chunks_from_sections(
        sections=sections,
        source_name=source_name,
        published_year=published_year,
        token_limit=min(token_limit, AI_INDEX_TOKEN_LIMIT),
        token_overlap=min(token_overlap, AI_INDEX_TOKEN_OVERLAP),
        default_industry="AI",
    )
    return [chunk for chunk in chunks if not is_ai_index_chunk_noise(chunk.get("text", ""))]


def split_ai_index_sections(text: str) -> list[Section]:
    sections = split_by_headings(text)
    result: list[Section] = []
    current_topic = "AI"
    for section in sections:
        heading = normalize_ai_heading(section.heading, section.text)
        if heading:
            current_topic = heading
        result.append(
            Section(
                heading=heading or current_topic,
                industry="AI",
                page=section.page,
                text=normalize_section_text(section.text),
            )
        )
    return result


def is_noise_section(section: Section) -> bool:
    if section.page is not None and section.page >= 385:
        return True
    haystack = f"{section.heading}\n{section.text[:1200]}".casefold()
    if any(marker in haystack for marker in NOISE_MARKERS):
        return True
    if is_figure_only_section(section.text):
        return True
    if is_chart_ocr_noise(section.text):
        return True
    if is_data_source_only_section(section.text):
        return True
    if is_footnote_only_section(section.text):
        return True
    if is_report_heading_only_section(section.text):
        return True
    return False


def is_ai_index_chunk_noise(text: str) -> bool:
    return (
        is_figure_only_section(text)
        or is_chart_ocr_noise(text)
        or is_data_source_only_section(text)
        or is_footnote_only_section(text)
        or is_report_heading_only_section(text)
    )


def strip_embedded_chart_ocr_tail(text: str) -> str:
    normalized = normalize_section_text(text)
    for marker in (" Source:", " Data source:"):
        marker_index = normalized.find(marker)
        while marker_index >= 0:
            tail = normalized[marker_index:]
            if is_probable_embedded_chart_tail(tail):
                sentence_end = normalized.rfind(". ", 0, marker_index)
                if sentence_end >= 0 and marker_index - sentence_end <= 180:
                    return normalized[: sentence_end + 1].strip()
                return normalized[:marker_index].strip()
            marker_index = normalized.find(marker, marker_index + len(marker))
    return normalized


def is_probable_embedded_chart_tail(text: str) -> bool:
    normalized = normalize_section_text(text)
    tokens = normalized.split()
    if len(tokens) < 25:
        return False
    single_alpha_tokens = sum(1 for token in tokens if re.fullmatch(r"[A-Za-z]", token))
    short_tokens = sum(1 for token in tokens if len(token) <= 2)
    has_chart_marker = "Chart:" in normalized or "Figure " in normalized or "Data source:" in normalized
    if not has_chart_marker:
        return False
    return single_alpha_tokens / len(tokens) >= 0.15 or short_tokens / len(tokens) >= 0.45


def is_report_heading_only_section(text: str) -> bool:
    normalized = normalize_section_text(text)
    if len(normalized) >= 220:
        return False
    return "AI INDEX REPORT" in normalized and ". " not in normalized


def is_figure_only_section(text: str) -> bool:
    normalized = normalize_section_text(text)
    if len(normalized) > 900:
        return False
    figure_count = normalized.count("Figure ")
    table_count = normalized.count("Table ")
    sentence_count = normalized.count(". ")
    if figure_count + table_count >= 3 and sentence_count <= 2:
        return True
    if figure_count + table_count >= 2 and sentence_count <= 1:
        return True
    if len(normalized) < 180 and "Figure " in normalized and "Source:" in normalized:
        return True
    return False


def is_chart_ocr_noise(text: str) -> bool:
    normalized = normalize_section_text(text)
    if len(normalized) > 260:
        return False
    tokens = normalized.split()
    if len(tokens) < 18:
        return False
    single_alpha_tokens = sum(1 for token in tokens if re.fullmatch(r"[A-Za-z]", token))
    short_tokens = sum(1 for token in tokens if len(token) <= 2)
    has_chart_marker = "Figure " in normalized or "Data source:" in normalized or "Source:" in normalized
    if single_alpha_tokens / len(tokens) >= 0.35:
        return True
    if short_tokens / len(tokens) >= 0.75:
        return True
    return has_chart_marker and single_alpha_tokens / len(tokens) >= 0.25


def is_data_source_only_section(text: str) -> bool:
    normalized = normalize_section_text(text)
    if len(normalized) > 260:
        return False
    if normalized.count("Data source:") >= 1 and re.match(r"^\d+\s+Data source:", normalized):
        return True
    lowered = normalized.casefold()
    return bool(
        re.match(r"^\d+\s+", normalized)
        and (
            "for the sake of brevity" in lowered
            or "figures may not add up" in lowered
            or "percentage points are rounded" in lowered
        )
    )


def is_footnote_only_section(text: str) -> bool:
    normalized = normalize_section_text(text)
    if len(normalized) > 500:
        return False
    return bool(re.match(r"^\d+\s+(Each|Due|For more|The full|National|All data|The LinkedIn)\b", normalized))


def merge_short_sections(sections: list[Section]) -> list[Section]:
    merged: list[Section] = []
    for section in sections:
        if (
            merged
            and len(section.text) < MIN_MERGED_CHARS
            and section.heading == merged[-1].heading
            and section.page == merged[-1].page
        ):
            previous = merged[-1]
            merged[-1] = Section(
                heading=previous.heading,
                industry=previous.industry,
                page=previous.page,
                text=normalize_section_text(f"{previous.text}\n{section.text}"),
            )
        else:
            merged.append(section)
    return merged


def normalize_ai_heading(heading: str, text: str) -> str:
    if not heading.lower().startswith("page "):
        return heading
    for line in text.splitlines()[:8]:
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        section_title = extract_numbered_heading(stripped)
        if section_title:
            return section_title
        if "AI INDEX REPORT" in stripped and "|" in stripped:
            parts = [part.strip() for part in stripped.split("|") if part.strip()]
            if len(parts) >= 2:
                return parts[0][:100]
        if stripped.isupper() and any(char.isalpha() for char in stripped):
            return stripped[:100]
    return heading


def extract_numbered_heading(line: str) -> str | None:
    match = re.search(r"\b(\d+\.\d+\s+[A-Z][A-Za-z0-9&/,\- ]{2,120})", line)
    if not match:
        return None
    heading = match.group(1)
    for marker in (" The ", " This ", " In ", " As ", " Across ", " Since ", " While ", " Source:", " Figure "):
        marker_index = heading.find(marker)
        if marker_index > 0:
            heading = heading[:marker_index]
    return heading.strip()[:100]
