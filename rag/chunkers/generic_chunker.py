from __future__ import annotations

from pathlib import Path
from typing import Any

from rag.industry_report_chunker import (
    build_chunks_from_sections,
    clean_markdown,
    keep_section,
    split_by_headings,
)


# @author 배세은
# @date 2026-05-17
# @relatedFR FR-007
# @relatedUI TODO-UI-ID
# @description 전용 청커가 없는 일반 산업보고서를 제목 기준으로 청킹하는 기본 청커.


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
    sections = split_by_headings(clean_markdown(source_text))
    sections = [section for section in sections if keep_section(section)]
    return build_chunks_from_sections(
        sections=sections,
        source_name=source_name,
        published_year=published_year,
        token_limit=token_limit,
        token_overlap=token_overlap,
    )
