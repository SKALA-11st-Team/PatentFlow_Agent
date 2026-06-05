from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rag.industry_report_chunker import (
    clean_markdown,
    keep_chunk,
    normalize_section_text,
    split_long_text,
    split_pages,
)


MCKINSEY_TOKEN_LIMIT = 420
MCKINSEY_TOKEN_OVERLAP = 70
MIN_START_PAGE = 11
SECTION_PATTERNS = (
    "Latest developments",
    "Adoption developments across the globe",
    "In real life",
    "Underlying technologies",
    "Key uncertainties",
    "Big questions about the future",
    "Talent and labor markets",
    "Scoring the trend",
)
NOISE_MARKERS = (
    "Copyright © 2025 McKinsey & Company",
    "No part of this publication may be copied",
    "The authors wish to thank",
    "Research methodology",
    "Introduction 2 Contents",
)
DOMAIN_SPECS = [
    {"group": "AI revolution", "domain": "Agentic AI", "start_page": 13, "industry": "AI"},
    {"group": "AI revolution", "domain": "Artificial intelligence", "start_page": 20, "industry": "AI"},
    {
        "group": "Compute and connectivity frontiers",
        "domain": "Application-specific semiconductors",
        "start_page": 29,
        "industry": "반도체",
    },
    {
        "group": "Compute and connectivity frontiers",
        "domain": "Advanced connectivity",
        "start_page": 36,
        "industry": "정보통신기기",
    },
    {
        "group": "Compute and connectivity frontiers",
        "domain": "Cloud and edge computing",
        "start_page": 43,
        "industry": "정보통신기기",
    },
    {
        "group": "Compute and connectivity frontiers",
        "domain": "Immersive-reality technologies",
        "start_page": 50,
        "industry": "정보통신기기",
    },
    {
        "group": "Compute and connectivity frontiers",
        "domain": "Digital trust and cybersecurity",
        "start_page": 57,
        "industry": "정보통신기기",
    },
    {
        "group": "Compute and connectivity frontiers",
        "domain": "Quantum technologies",
        "start_page": 65,
        "industry": "정보통신기기",
    },
    {"group": "Cutting-edge engineering", "domain": "Future of robotics", "start_page": 72, "industry": "일반기계"},
    {"group": "Cutting-edge engineering", "domain": "Future of mobility", "start_page": 78, "industry": "자동차"},
    {"group": "Cutting-edge engineering", "domain": "Future of bioengineering", "start_page": 85, "industry": "바이오헬스"},
    {
        "group": "Cutting-edge engineering",
        "domain": "Future of space technologies",
        "start_page": 92,
        "industry": "정보통신기기",
    },
    {
        "group": "Cutting-edge engineering",
        "domain": "Future of energy and sustainability technologies",
        "start_page": 99,
        "industry": "공통",
    },
]
DOMAIN_RANGES = [
    {
        **spec,
        "end_page": DOMAIN_SPECS[index + 1]["start_page"] - 1 if index + 1 < len(DOMAIN_SPECS) else None,
    }
    for index, spec in enumerate(DOMAIN_SPECS)
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
    per_domain_counter: dict[str, int] = {}
    effective_limit = min(token_limit, MCKINSEY_TOKEN_LIMIT)
    effective_overlap = min(token_overlap, MCKINSEY_TOKEN_OVERLAP)
    current_domain_info: dict[str, Any] | None = None

    for page in raw_pages:
        if page.page is None or page.page < MIN_START_PAGE:
            continue
        page_text = clean_mckinsey_page(normalize_section_text(page.text))
        if not keep_mckinsey_page(page_text):
            continue
        detected_domain = detect_domain_from_text(page_text)
        if detected_domain:
            current_domain_info = detected_domain
        domain_info = current_domain_info or domain_for_page(page.page)
        if not domain_info:
            continue
        section_name = infer_section_name(page_text)
        heading = build_heading(domain=domain_info["domain"], section_name=section_name)
        for sub_text in split_long_text(page_text, token_limit=effective_limit, overlap=effective_overlap):
            if not keep_chunk(sub_text):
                continue
            domain = str(domain_info["domain"])
            per_domain_counter[domain] = per_domain_counter.get(domain, 0) + 1
            page_part = f"p{page.page}" if page.page else "p000"
            chunk_id = build_chunk_id(
                source_name=source_name,
                published_year=published_year,
                technology_domain=domain,
                page_part=page_part,
                chunk_no=per_domain_counter[domain],
            )
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "text": sub_text,
                    "metadata": {
                        "source_type": "industry_report",
                        "source_name": normalize_source_name(source_name),
                        "published_year": published_year,
                        "industry": domain_info["industry"],
                        "technology_domain": domain,
                        "trend_group": domain_info["group"],
                        "section": section_name,
                        "chunk_id": chunk_id,
                        "heading": heading,
                        "page": page.page,
                    },
                }
            )
    return chunks


def normalize_source_name(source_name: str) -> str:
    lowered = source_name.casefold()
    if "mckinsey" in lowered and "technology" in lowered and "trends" in lowered:
        return "McKinsey Technology Trends Outlook 2025"
    if source_name.lower().endswith(".pdf"):
        return Path(source_name).stem.replace("-", " ").replace("_", " ").strip().title()
    return source_name


def clean_mckinsey_page(text: str) -> str:
    text = re.sub(r"\b\d+\s+Technology Trends Outlook 2025\b", "", text)
    text = re.sub(r"Scoring the trend\s+[A-Z][A-Z\-\s]+", "Scoring the trend", text)
    text = re.sub(r"\s{2,}", " ", text)
    return normalize_section_text(text)


def keep_mckinsey_page(text: str) -> bool:
    if not text or len(text) < 180:
        return False
    return not any(marker in text for marker in NOISE_MARKERS)


def domain_for_page(page: int) -> dict[str, Any] | None:
    for item in DOMAIN_RANGES:
        end_page = item["end_page"]
        if page < item["start_page"]:
            continue
        if end_page is not None and page > end_page:
            continue
        return item
    return None


def detect_domain_from_text(text: str) -> dict[str, Any] | None:
    compact = text.casefold().replace("‑", "-")
    for item in DOMAIN_RANGES:
        domain = str(item["domain"]).casefold().replace("‑", "-")
        if domain in compact:
            return item
    return None


def infer_section_name(text: str) -> str:
    for marker in SECTION_PATTERNS:
        if marker in text:
            return marker
    return "Overview"


def build_heading(*, domain: str, section_name: str) -> str:
    if not section_name or section_name == "Overview":
        return domain
    return f"{domain} - {section_name}"


def build_chunk_id(
    *,
    source_name: str,
    published_year: int | None,
    technology_domain: str,
    page_part: str,
    chunk_no: int,
) -> str:
    source_key = re.sub(r"[^0-9A-Za-z]+", "_", Path(source_name).stem).strip("_") or "industry_report"
    domain_key = re.sub(r"[^0-9A-Za-z]+", "_", technology_domain).strip("_") or "domain"
    year = published_year or "unknown"
    return f"{source_key}_{year}_{domain_key}_{page_part}_{chunk_no:03d}"
