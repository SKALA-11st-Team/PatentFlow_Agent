from __future__ import annotations

from pathlib import Path
from typing import Any
import re

from rag.industry_report_chunker import (
    Section,
    build_chunks_from_sections,
    clean_markdown,
    keep_chunk,
    normalize_section_text,
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
    sections = split_machine_sections(clean_markdown(source_text))
    return build_chunks_from_sections(
        sections=sections,
        source_name=source_name,
        published_year=published_year,
        token_limit=token_limit,
        token_overlap=token_overlap,
        default_industry="일반기계",
    )


def split_machine_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    current_heading = "기계산업 디지털전환"
    for section in split_pages(text):
        page_text = normalize_section_text(section.text)
        if not keep_machine_section(section.page, page_text):
            continue
        inferred_heading = infer_machine_heading(page_text)
        if inferred_heading:
            current_heading = inferred_heading
        sections.append(
            Section(
                heading=current_heading,
                industry="일반기계",
                page=section.page,
                text=page_text,
            )
        )
    return sections


def keep_machine_section(page: int | None, text: str) -> bool:
    if page is not None and page < 11:
        return False
    if not keep_chunk(text):
        return False
    if text.startswith(("표 목차", "그림 목차")):
        return False
    return True


def infer_machine_heading(text: str) -> str | None:
    cleaned = re.sub(r"제1장 제2장 제3장 제4장 제5장 붙임자료 .{0,80}", " ", text)
    cleaned = re.sub(r"\d{3}\s+한국기계연구원|DX전략연구단 / 기계정책센터 \d{3}", " ", cleaned)
    numbered = re.search(r"\b([1-5]\s+[가-힣A-Za-z0-9/･·\s]{4,60})", cleaned)
    if numbered:
        return numbered.group(1).strip()
    chapter = re.search(r"제\s*\d+\s*장\s*[∙·]?\s*([가-힣A-Za-z0-9/･·\s]{2,60})", cleaned)
    if chapter:
        return chapter.group(1).strip()
    return None
