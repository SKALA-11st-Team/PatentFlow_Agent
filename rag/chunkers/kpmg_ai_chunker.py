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


# @author 배세은
# @date 2026-05-17
# @relatedFR FR-007
# @relatedUI TODO-UI-ID
# @description KPMG AI 보고서를 페이지 단위로 분할·청킹하는 전용 청커.


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
    sections = split_kpmg_ai_pages(clean_markdown(source_text), token_limit=token_limit, token_overlap=token_overlap)
    return build_chunks_from_sections(
        sections=sections,
        source_name=source_name,
        published_year=published_year,
        token_limit=token_limit,
        token_overlap=token_overlap,
        default_industry="AI",
    )


def split_kpmg_ai_pages(text: str, *, token_limit: int, token_overlap: int) -> list[Section]:
    raw_sections = split_pages(text)
    sections: list[Section] = []
    for section in raw_sections:
        page_text = remove_kpmg_footer(normalize_section_text(section.text))
        if not keep_kpmg_ai_page(section.page, page_text):
            continue
        heading = infer_ai_heading(page_text) or section.heading
        for chunk in split_long_text(page_text, token_limit=max(350, token_limit // 2), overlap=min(token_overlap, 60)):
            if not keep_chunk(chunk):
                continue
            sections.append(
                Section(
                    heading=heading,
                    industry="AI",
                    page=section.page,
                    text=chunk,
                )
            )
    return sections


def keep_kpmg_ai_page(page: int | None, text: str) -> bool:
    if page is not None and page < 3:
        return False
    if not text:
        return False
    if text.startswith("Contents "):
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


def infer_ai_heading(text: str) -> str | None:
    patterns = [
        "Executive Summary",
        "AI 시장과 기술, 어디까지 왔나",
        "AI 생태계 및 기술 진화 방향",
        "AI 활용 목적 및 AI 비즈니스 모델 구조",
        "AI 활용 목적 및 AI 도입 기업의 AI 활용 목적",
        "AI를 활용한 수익화 유형",
        "AI로 재편되는 산업별 수익화 전략",
        "AI가 바꾸는 미래 변화상 및 기업의 기회",
        "글로벌 주요국의 AI 지원 및 규제 정책",
        "글로벌 AI 생태계 속 한국의 비즈니스 기회",
        "빅테크·M7 기업의 경쟁·협력 구도 및 한국의 대응 방안",
    ]
    for pattern in patterns:
        if pattern in text:
            return pattern
    industry_match = re.search(
        r"\b(IT|통신|모빌리티|헬스케어|유통|소비재|광고·미디어|금융)\s*산업\b",
        text,
    )
    if industry_match:
        return f"{industry_match.group(1)} 산업"
    for pattern in ("Thought Leadership I", "Thought Leadership II", "Thought Leadership III"):
        if pattern in text:
            return pattern
    return None
