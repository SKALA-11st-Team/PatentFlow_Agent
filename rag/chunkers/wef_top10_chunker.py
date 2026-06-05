from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rag.industry_report_chunker import (
    build_chunk_id,
    clean_markdown,
    keep_chunk,
    normalize_section_text,
    split_long_text,
    split_pages,
)


WEF_TOKEN_LIMIT = 420
WEF_TOKEN_OVERLAP = 70
MIN_BODY_PAGE = 9
FRONT_SECTION_NAMES = {
    "Foreword",
    "Building strategic foresight",
    "Introduction",
    "Methodology",
}
EXCLUDED_SECTION_NAMES = {
    "Contents",
    "Contributors",
    "Acknowledgements",
    "Endnotes",
}
CLOSING_START_PAGE = 39
CLOSING_END_PAGE = 40
TECHNOLOGY_SPECS = [
    {"rank": 1, "technology_name": "Structural battery composites", "start_page": 9, "industry": "이차전지"},
    {"rank": 2, "technology_name": "Osmotic power systems", "start_page": 12, "industry": "공통"},
    {"rank": 3, "technology_name": "Advanced nuclear technologies", "start_page": 15, "industry": "공통"},
    {"rank": 4, "technology_name": "Engineered living therapeutics", "start_page": 18, "industry": "바이오헬스"},
    {"rank": 5, "technology_name": "GLP-1s for neurodegenerative disease", "start_page": 21, "industry": "바이오헬스"},
    {"rank": 6, "technology_name": "Autonomous biochemical sensing", "start_page": 24, "industry": "바이오헬스"},
    {"rank": 7, "technology_name": "Green nitrogen fixation", "start_page": 27, "industry": "공통"},
    {"rank": 8, "technology_name": "Nanozymes", "start_page": 30, "industry": "바이오헬스"},
    {"rank": 9, "technology_name": "Collaborative sensing", "start_page": 33, "industry": "정보통신기기"},
    {"rank": 10, "technology_name": "Generative watermarking", "start_page": 36, "industry": "AI"},
]
TECHNOLOGY_RANGES = [
    {
        **spec,
        "end_page": TECHNOLOGY_SPECS[index + 1]["start_page"] - 1 if index + 1 < len(TECHNOLOGY_SPECS) else 38,
    }
    for index, spec in enumerate(TECHNOLOGY_SPECS)
]


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
    raw_pages = split_pages(clean_markdown(source_text))
    chunks: list[dict[str, Any]] = []
    effective_limit = min(token_limit, WEF_TOKEN_LIMIT)
    effective_overlap = min(token_overlap, WEF_TOKEN_OVERLAP)
    counters: dict[tuple[str, str], int] = {}
    current_technology: dict[str, Any] | None = None
    current_section = "Overview"

    for page in raw_pages:
        if page.page is None:
            continue

        page_text = clean_wef_page(normalize_section_text(page.text))
        if not page_text:
            continue

        if page.page < MIN_BODY_PAGE:
            section_name = detect_front_section(page_text)
            if section_name in EXCLUDED_SECTION_NAMES:
                continue
            if section_name not in FRONT_SECTION_NAMES:
                continue
            for sub_text in split_long_text(page_text, token_limit=effective_limit, overlap=effective_overlap):
                if not keep_chunk(sub_text):
                    continue
                key = ("공통", section_name)
                counters[key] = counters.get(key, 0) + 1
                page_part = f"p{page.page}"
                chunk_id = build_chunk_id(normalize_source_name(source_name), published_year, "공통", page_part, counters[key])
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "text": sub_text,
                        "metadata": {
                            "source_type": "industry_report",
                            "source_name": normalize_source_name(source_name),
                            "published_year": published_year,
                            "industry": "공통",
                            "section": "Front matter",
                            "heading": section_name,
                            "page": page.page,
                            "chunk_id": chunk_id,
                        },
                    }
                )
            continue

        detected = detect_technology_from_text(page_text) or technology_for_page(page.page)
        if detected:
            current_technology = detected
            current_section = "Overview"

        if page.page >= CLOSING_START_PAGE:
            if page.page > CLOSING_END_PAGE:
                continue
            if "From weak signals to" not in page_text and page.page == CLOSING_START_PAGE:
                continue
            heading = "From weak signals to societal transformation"
            for sub_text in split_long_text(page_text, token_limit=effective_limit, overlap=effective_overlap):
                if not keep_chunk(sub_text):
                    continue
                key = ("공통", "Closing synthesis")
                counters[key] = counters.get(key, 0) + 1
                page_part = f"p{page.page}"
                chunk_id = build_chunk_id(normalize_source_name(source_name), published_year, "공통", page_part, counters[key])
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "text": sub_text,
                        "metadata": {
                            "source_type": "industry_report",
                            "source_name": normalize_source_name(source_name),
                            "published_year": published_year,
                            "industry": "공통",
                            "section": "Closing synthesis",
                            "heading": heading,
                            "page": page.page,
                            "chunk_id": chunk_id,
                        },
                    }
                )
            continue

        if not current_technology:
            continue

        inferred_section = infer_section_name(page_text)
        if inferred_section:
            current_section = inferred_section
        section_name = current_section
        heading = build_heading(current_technology["technology_name"], section_name)
        industry = str(current_technology["industry"])
        key = (industry, str(current_technology["technology_name"]))

        for sub_text in split_long_text(page_text, token_limit=effective_limit, overlap=effective_overlap):
            if not keep_chunk(sub_text):
                continue
            counters[key] = counters.get(key, 0) + 1
            page_part = f"p{page.page}"
            chunk_id = build_chunk_id(normalize_source_name(source_name), published_year, industry, page_part, counters[key])
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "text": sub_text,
                    "metadata": {
                        "source_type": "industry_report",
                        "source_name": normalize_source_name(source_name),
                        "published_year": published_year,
                        "industry": industry,
                        "technology_name": current_technology["technology_name"],
                        "rank": current_technology["rank"],
                        "section": section_name,
                        "heading": heading,
                        "page": page.page,
                        "chunk_id": chunk_id,
                    },
                }
            )
    return chunks


def normalize_source_name(source_name: str) -> str:
    lowered = source_name.casefold()
    if "wef" in lowered and "top" in lowered and "emerging" in lowered and "technologies" in lowered:
        return "WEF Top 10 Emerging Technologies of 2025"
    if source_name.lower().endswith(".pdf"):
        return Path(source_name).stem.replace("-", " ").replace("_", " ").strip().title()
    return source_name


def clean_wef_page(text: str) -> str:
    text = re.sub(r"\bTop 10 Emerging Technologies of 2025\s+\d+\b", "", text)
    text = re.sub(r"\bJune 2025 Top 10 Emerging Technologies of 2025\b", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return normalize_section_text(text)


def technology_for_page(page: int) -> dict[str, Any] | None:
    for item in TECHNOLOGY_RANGES:
        if page < item["start_page"]:
            continue
        if page > item["end_page"]:
            continue
        return item
    return None


def detect_technology_from_text(text: str) -> dict[str, Any] | None:
    compact = text.casefold().replace("‑", "-")
    for item in TECHNOLOGY_RANGES:
        name = str(item["technology_name"]).casefold().replace("‑", "-")
        if name in compact:
            return item
    return None


def detect_front_section(text: str) -> str | None:
    for name in ("Contents", "Foreword", "Building strategic foresight", "Introduction", "Methodology"):
        if name.casefold() in text.casefold():
            return name
    return None


def infer_section_name(text: str) -> str | None:
    if "Strategic outlook" in text:
        return "Strategic outlook"
    if detect_technology_from_text(text):
        return "Overview"
    return None


def build_heading(technology_name: str, section_name: str) -> str:
    if section_name == "Overview":
        return technology_name
    return f"{technology_name} - {section_name}"
