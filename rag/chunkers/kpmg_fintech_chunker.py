from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rag.industry_report_chunker import (
    Section,
    build_chunks_from_sections,
    clean_markdown,
    keep_chunk,
    normalize_section_text,
    split_long_text,
    split_pages,
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
    sections = split_kpmg_pages(clean_markdown(source_text), token_limit=token_limit, token_overlap=token_overlap)
    return build_chunks_from_sections(
        sections=sections,
        source_name=source_name,
        published_year=published_year,
        token_limit=token_limit,
        token_overlap=token_overlap,
        default_industry="핀테크",
    )


def split_kpmg_pages(text: str, *, token_limit: int, token_overlap: int) -> list[Section]:
    raw_sections = split_pages(text)
    sections: list[Section] = []
    for section in raw_sections:
        page_text = remove_kpmg_footer(normalize_section_text(section.text))
        if not keep_fintech_page(section.page, page_text):
            continue
        heading = infer_fintech_heading(page_text) or section.heading
        for chunk in split_long_text(page_text, token_limit=max(350, token_limit // 2), overlap=min(token_overlap, 60)):
            if not keep_chunk(chunk):
                continue
            sections.append(
                Section(
                    heading=heading,
                    industry="핀테크",
                    page=section.page,
                    text=chunk,
                )
            )
    return sections


def keep_fintech_page(page: int | None, text: str) -> bool:
    if page is not None and page < 3:
        return False
    if not text:
        return False
    if "[Appendix]" in text or "Methodology" in text:
        return False
    if is_kpmg_boilerplate(text):
        return False
    return True


def is_kpmg_boilerplate(text: str) -> bool:
    markers = [
        "Business Contacts",
        "Contact us",
        "home.kpmg/kr",
        "The information contained herein is of a general nature",
        "무단 배포",
    ]
    return any(marker in text for marker in markers)


def remove_kpmg_footer(text: str) -> str:
    return normalize_section_text(
        re.sub(
            r"© 2026 KPMG Samjong Accounting Corp\..*?All rights reserved\.",
            "",
            text,
        )
    )


def infer_fintech_heading(text: str) -> str | None:
    patterns = [
        "Executive Summary",
        "2026년 핀테크 시장 전망",
        "글로벌 핀테크 투자 동향과 전망",
        "글로벌 핀테크 투자 동향",
        "섹터별 핀테크 투자 동향과 전망",
        "섹터별 핀테크 투자 동향",
        "지역별 핀테크 투자 동향과 전망",
        "지역별 핀테크 투자 동향",
        "디지털자산",
        "지급결제",
        "인슈어테크",
        "웰스테크",
        "사이버보안",
        "레그테크",
    ]
    for pattern in patterns:
        if pattern in text or pattern.replace(" ", "") in text:
            return pattern
    return None
