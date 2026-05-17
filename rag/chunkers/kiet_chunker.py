from __future__ import annotations

from pathlib import Path
from typing import Any

from rag.industry_report_chunker import (
    build_chunks_from_sections,
    clean_markdown,
    extract_chapter_ranges,
    keep_section,
    split_by_headings,
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
    chapter_ranges = extract_chapter_ranges(source_text)
    sections = split_by_headings(clean_markdown(source_text), chapter_ranges=chapter_ranges)
    sections = [section for section in sections if keep_section(section)]
    return build_chunks_from_sections(
        sections=sections,
        source_name=source_name,
        published_year=published_year,
        token_limit=token_limit,
        token_overlap=token_overlap,
    )
