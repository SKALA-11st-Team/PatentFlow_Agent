from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import argparse
import io
import json
import re
import subprocess
import unicodedata

from app.config import settings


INDUSTRY_NAMES = [
    "자동차",
    "조선",
    "일반기계",
    "철강",
    "정유",
    "석유화학",
    "섬유",
    "가전",
    "정보통신기기",
    "디스플레이",
    "이차전지",
    "바이오헬스",
    "반도체",
]

TOKEN_LIMIT = 1000
TOKEN_OVERLAP = 150
MIN_PAGE = 16
MIN_CHUNK_CHARS = 120
BODY_START_PATTERNS = [
    r"^□\s*",
    r"^○\s*",
    r"^\(\d+\)\s*",
    r"^\d+\.\s+",
]


@dataclass
class Section:
    heading: str
    industry: str | None
    page: int | None
    text: str


@dataclass(frozen=True)
class ChapterRange:
    industry: str
    start_page: int
    end_page: int | None = None


def convert_industry_pdfs_to_markdown(
    input_dir: Path | None = None,
    output_dir: Path | None = None,
) -> list[Path]:
    input_dir = input_dir or settings.data_dir / "industry_reports"
    output_dir = output_dir or settings.output_dir / "industry_markdown"
    pdf_paths = sorted(path for path in input_dir.glob("*.pdf") if path.is_file())
    if not pdf_paths:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    converted: list[Path] = []
    pdfplumber_dir = output_dir / "pdfplumber_no_tables"
    pdfplumber_dir.mkdir(parents=True, exist_ok=True)

    try:
        for pdf_path in pdf_paths:
            markdown_path = pdfplumber_dir / f"{pdf_path.stem}.md"
            markdown_path.write_text(
                extract_pdf_text_without_tables(pdf_path),
                encoding="utf-8",
            )
            converted.append(markdown_path)
    except Exception:
        # pdfplumber missing (ImportError) or a corrupted/encrypted PDF raising
        # PDFSyntaxError/KeyError/OSError must not abort the batch — fall through
        # to the opendataloader / pypdf fallbacks below.
        converted = []

    if converted:
        return converted

    try:
        import opendataloader_pdf

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            opendataloader_pdf.convert(
                input_path=[str(path) for path in pdf_paths],
                output_dir=str(output_dir),
                format="markdown-with-images",
            )
        converted.extend(sorted(output_dir.rglob("*.md")))
    except (ImportError, subprocess.CalledProcessError):
        converted = []

    if converted:
        return converted

    fallback_dir = output_dir / "pypdf_fallback"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    for pdf_path in pdf_paths:
        markdown_path = fallback_dir / f"{pdf_path.stem}.md"
        markdown_path.write_text(extract_pdf_text_with_pypdf(pdf_path), encoding="utf-8")
        converted.append(markdown_path)
    return converted


def extract_pdf_text_without_tables(pdf_path: Path) -> str:
    import pdfplumber
    from pypdf import PdfReader

    pages: list[str] = []
    table_count = 0
    fallback_reader = PdfReader(str(pdf_path))
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            table_bboxes = [table.bbox for table in page.find_tables()]
            table_count += len(table_bboxes)
            words = page.extract_words(
                x_tolerance=1,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=True,
            )
            kept_words = [word for word in words if not is_word_inside_any_bbox(word, table_bboxes)]
            text = words_to_text(kept_words)
            if should_fallback_to_pypdf(text):
                fallback_text = fallback_reader.pages[page_index - 1].extract_text() or ""
                if len(normalize_extracted_page_text(fallback_text)) > len(normalize_extracted_page_text(text)):
                    text = fallback_text
            pages.append(f"\n# Page {page_index}\n\n{text.strip()}")
    return "\n\n".join(pages).strip() + f"\n\n<!-- removed_tables: {table_count} -->\n"


def should_fallback_to_pypdf(text: str) -> bool:
    normalized = normalize_extracted_page_text(text)
    if len(normalized) < 120:
        return True
    return False


def normalize_extracted_page_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact


def is_word_inside_any_bbox(word: dict[str, Any], bboxes: list[tuple[float, float, float, float]]) -> bool:
    x_center = (float(word["x0"]) + float(word["x1"])) / 2
    y_center = (float(word["top"]) + float(word["bottom"])) / 2
    return any(x0 <= x_center <= x1 and top <= y_center <= bottom for x0, top, x1, bottom in bboxes)


def words_to_text(words: list[dict[str, Any]]) -> str:
    if not words:
        return ""

    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (round(float(item["top"])), float(item["x0"]))):
        if not lines:
            lines.append([word])
            continue
        current_top = float(lines[-1][0]["top"])
        if abs(float(word["top"]) - current_top) <= 3:
            lines[-1].append(word)
        else:
            lines.append([word])

    rendered_lines = []
    for line in lines:
        ordered = sorted(line, key=lambda item: float(item["x0"]))
        rendered_lines.append(" ".join(str(word["text"]) for word in ordered))
    return "\n".join(rendered_lines)


def extract_pdf_text_with_pypdf(pdf_path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(f"\n# Page {page_index}\n\n{text.strip()}")
    return "\n\n".join(pages).strip()


def chunk_industry_reports(
    markdown_paths: list[Path],
    *,
    output_path: Path | None = None,
    token_limit: int = TOKEN_LIMIT,
    token_overlap: int = TOKEN_OVERLAP,
) -> list[dict[str, Any]]:
    output_path = output_path or settings.data_dir / "vector_db" / "industry_report_chunks.jsonl"
    chunks: list[dict[str, Any]] = []

    for markdown_path in markdown_paths:
        source_text = markdown_path.read_text(encoding="utf-8", errors="ignore")
        source_name = infer_source_name(markdown_path)
        published_year = infer_published_year(source_name)
        chunks.extend(
            chunk_report_by_type(
                markdown_path=markdown_path,
                source_text=source_text,
                source_name=source_name,
                published_year=published_year,
                token_limit=token_limit,
                token_overlap=token_overlap,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks),
        encoding="utf-8",
    )
    return chunks


def chunk_report_by_type(
    *,
    markdown_path: Path,
    source_text: str,
    source_name: str,
    published_year: int | None,
    token_limit: int,
    token_overlap: int,
) -> list[dict[str, Any]]:
    report_type = detect_report_type(source_name)
    if report_type == "kiet":
        from rag.chunkers.kiet_chunker import chunk_report
    elif report_type == "ai_index":
        from rag.chunkers.ai_index_chunker import chunk_report
    elif report_type == "mckinsey_tech_trends":
        from rag.chunkers.mckinsey_tech_trends_chunker import chunk_report
    elif report_type == "wef_top10":
        from rag.chunkers.wef_top10_chunker import chunk_report
    elif report_type == "kpmg_ai":
        from rag.chunkers.kpmg_ai_chunker import chunk_report
    elif report_type == "kpmg_fintech":
        from rag.chunkers.kpmg_fintech_chunker import chunk_report
    elif report_type == "machine_whitepaper":
        from rag.chunkers.machine_whitepaper_chunker import chunk_report
    else:
        from rag.chunkers.generic_chunker import chunk_report

    return chunk_report(
        markdown_path=markdown_path,
        source_text=source_text,
        source_name=source_name,
        published_year=published_year,
        token_limit=token_limit,
        token_overlap=token_overlap,
    )


def detect_report_type(source_name: str) -> str:
    normalized = source_name.casefold()
    if "kiet" in normalized or "경제ㆍ산업_전망" in source_name or "경제산업_전망" in source_name:
        return "kiet"
    if "ai_index" in normalized or "ai index" in normalized:
        return "ai_index"
    if "mckinsey" in normalized and "technology" in normalized and "trends" in normalized:
        return "mckinsey_tech_trends"
    if "wef" in normalized and "top" in normalized and "emerging" in normalized and "technologies" in normalized:
        return "wef_top10"
    if "world economic forum" in normalized and "emerging technologies" in normalized:
        return "wef_top10"
    if "kpmg" in normalized and ("ai" in normalized or "수익" in source_name):
        return "kpmg_ai"
    if "kpmg" in normalized or "핀테크" in source_name:
        return "kpmg_fintech"
    if "기계산업" in source_name or "기계산업" in source_name:
        return "machine_whitepaper"
    return "generic"


def build_chunks_from_sections(
    *,
    sections: list[Section],
    source_name: str,
    published_year: int | None,
    token_limit: int,
    token_overlap: int,
    default_industry: str = "공통",
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    per_industry_counter: dict[str, int] = {}

    for section in sections:
        industry = section.industry or infer_industry(section.heading) or default_industry
        sub_chunks = [
            normalize_section_text(remove_table_artifact_lines(chunk))
            for chunk in split_long_text(section.text, token_limit=token_limit, overlap=token_overlap)
            if keep_chunk(chunk)
        ]
        for sub_text in sub_chunks:
            per_industry_counter[industry] = per_industry_counter.get(industry, 0) + 1
            page_part = f"p{section.page}" if section.page else "p000"
            chunk_no = per_industry_counter[industry]
            chunk_id = build_chunk_id(source_name, published_year, industry, page_part, chunk_no)
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "text": sub_text,
                    "metadata": {
                        "source_type": "industry_report",
                        "source_name": source_name,
                        "published_year": published_year,
                        "industry": industry,
                        "chunk_id": chunk_id,
                        "heading": section.heading,
                        "page": section.page,
                    },
                }
            )
    return chunks


def clean_markdown(text: str) -> str:
    text = re.sub(r"!\[image\s+\d+\]\(<[^>]+>\)", "", text)
    text = remove_page_footers(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def remove_page_footers(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\s+2026년\s+경제[ㆍ·]?산업\s+전망", stripped):
            continue
        if re.match(r"^\d+\s+KIET\s+경제", stripped):
            continue
        if re.match(r"^제\d+장\s+.+\s+\d+$", stripped):
            continue
        lines.append(line)
    return "\n".join(lines)


def split_by_headings(text: str, chapter_ranges: list[ChapterRange] | None = None) -> list[Section]:
    chapter_ranges = chapter_ranges or []
    lines = text.splitlines()
    sections: list[Section] = []
    current_heading = "문서 개요"
    current_industry: str | None = None
    current_page: int | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        content = normalize_section_text("\n".join(buffer))
        if content:
            section_industry = industry_for_page(current_page, chapter_ranges) or current_industry
            sections.append(
                Section(
                    heading=current_heading,
                    industry=section_industry,
                    page=current_page,
                    text=content,
                )
            )
        buffer = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            buffer.append("")
            continue

        page = detect_page(stripped)
        if page is not None:
            flush()
            current_page = page
            current_heading = f"Page {page}"
            page_industry = industry_for_page(page, chapter_ranges)
            if page_industry:
                current_industry = page_industry
            continue

        heading = detect_heading(stripped)
        if heading:
            flush()
            current_heading = heading
            detected_industry = infer_industry(heading)
            if detected_industry and (current_page is None or current_page >= MIN_PAGE):
                current_industry = detected_industry
            continue

        buffer.append(stripped)

    flush()
    return sections


def split_pages(text: str) -> list[Section]:
    sections: list[Section] = []
    current_page: int | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        content = normalize_section_text("\n".join(buffer))
        if content:
            sections.append(
                Section(
                    heading=f"Page {current_page}" if current_page else "문서 개요",
                    industry=None,
                    page=current_page,
                    text=content,
                )
            )
        buffer = []

    for line in text.splitlines():
        stripped = line.strip()
        page = detect_page(stripped)
        if page is not None:
            flush()
            current_page = page
            continue
        buffer.append(stripped)

    flush()
    return sections


def extract_chapter_ranges(text: str) -> list[ChapterRange]:
    starts: list[tuple[int, str]] = []
    for line in text.splitlines():
        stripped = line.strip().replace("\x00", "")
        match = re.match(
            r"^제\d+장\s+(.+?산업)\s*[·.\s]{3,}\s*(\d{1,3})$",
            stripped,
        )
        if not match:
            continue

        industry = infer_industry(match.group(1))
        if not industry:
            continue

        # The report's printed page number is one behind the extracted PDF page marker.
        actual_page = int(match.group(2)) + 1
        starts.append((actual_page, industry))

    deduped: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for item in sorted(starts):
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)

    ranges: list[ChapterRange] = []
    for index, (start_page, industry) in enumerate(deduped):
        next_start = deduped[index + 1][0] if index + 1 < len(deduped) else None
        end_page = next_start - 1 if next_start else None
        ranges.append(ChapterRange(industry=industry, start_page=start_page, end_page=end_page))
    return ranges


def industry_for_page(page: int | None, chapter_ranges: list[ChapterRange]) -> str | None:
    if page is None:
        return None
    for chapter_range in chapter_ranges:
        if page < chapter_range.start_page:
            continue
        if chapter_range.end_page is not None and page > chapter_range.end_page:
            continue
        return chapter_range.industry
    return None


def keep_section(section: Section) -> bool:
    if section.page is not None and section.page < MIN_PAGE:
        return False

    if is_table_heading(section.heading):
        section.text = keep_text_after_body_start(section.text)
        if not section.text:
            return False
        section.heading = infer_body_heading(section.text) or "본문"

    section.text = remove_table_artifact_lines(section.text)
    section.text = normalize_section_text(section.text)
    if not section.text:
        return False

    if count_table_refs(section.text) >= 3:
        return False
    if is_note_or_symbol_only(section.text):
        return False
    if is_publication_info(section.text):
        return False
    if len(section.text.strip()) < MIN_CHUNK_CHARS:
        return False
    return True


def keep_chunk(text: str) -> bool:
    stripped = normalize_section_text(remove_table_artifact_lines(text))
    if not stripped:
        return False
    if count_table_refs(stripped) >= 3:
        return False
    if is_note_or_symbol_only(stripped):
        return False
    if is_publication_info(stripped):
        return False
    if len(stripped) < MIN_CHUNK_CHARS:
        return False
    return True


def is_table_heading(heading: str) -> bool:
    return bool(re.search(r"<\s*표\s*\d+[-–]\d+\s*>", heading))


def keep_text_after_body_start(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if is_body_start_line(stripped):
            return normalize_section_text("\n".join(lines[index:]))
    return ""


def is_body_start_line(line: str) -> bool:
    return any(re.match(pattern, line) for pattern in BODY_START_PATTERNS)


def infer_body_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\(\d+\)\s*", stripped) or stripped.startswith("□"):
            return stripped[:80]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return None


def remove_table_artifact_lines(text: str) -> str:
    kept_lines = []
    for line in text.splitlines():
        stripped = line.strip().replace("\x00", "")
        if not stripped:
            kept_lines.append("")
            continue
        if is_table_heading(stripped):
            continue
        if is_note_or_symbol_only(stripped):
            continue
        if is_table_symbol_line(stripped):
            continue
        if re.match(r"^(주|자료|단위)\s*:", stripped):
            continue
        if re.match(r"^<!--\s*removed_tables:", stripped):
            continue
        kept_lines.append(stripped)
    return "\n".join(kept_lines)


def is_table_symbol_line(line: str) -> bool:
    if "☂" not in line and "☼" not in line and "☁" not in line:
        return False
    if len(line) <= 240:
        return True
    symbol_count = len(re.findall(r"[☂☼☁]", line))
    return symbol_count >= 2


def count_table_refs(text: str) -> int:
    return len(re.findall(r"<\s*표\s*\d+[-–]\d+\s*>", text))


def is_note_or_symbol_only(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.replace("\x00", "")).strip()
    if not normalized:
        return True
    note_patterns = [
        r"^주\s*:",
        r"^자료\s*:",
        r"^단위\s*:",
    ]
    symbol_noise = re.sub(r"[☂☼ㆍ·\s,.:;()（）전년대비영향정도큰폭감소다소증가]", "", normalized)
    if len(normalized) <= 160 and any(re.match(pattern, normalized) for pattern in note_patterns):
        return True
    if len(normalized) <= 160 and not symbol_noise:
        return True
    return False


def is_publication_info(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text)
    markers = ["발 행 일", "발 행 인", "발 행 처", "ISSN", "무단 복제"]
    return sum(marker in normalized for marker in markers) >= 2


def detect_heading(line: str) -> str | None:
    if line.startswith("⋅") or is_note_or_symbol_only(line) or is_table_symbol_line(line):
        return None

    markdown = re.match(r"^#{1,6}\s+(.+)$", line)
    if markdown:
        return markdown.group(1).strip()

    if is_table_heading(line):
        return line

    if infer_industry(line) and len(line) <= 40:
        return line

    outline = re.match(
        r"^((?:[0-9]{1,2}[.)])|(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)]?)|(?:[가-힣][.)]))\s+(.{2,80})$",
        line,
    )
    if outline:
        return outline.group(2).strip()

    return None


def detect_page(line: str) -> int | None:
    line = line.lstrip("#").strip()
    patterns = [
        r"^-\s*(\d+)\s*-$",
        r"^page\s*(\d+)$",
        r"^페이지\s*(\d+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, line, re.I)
        if match:
            return int(match.group(1))
    return None


def normalize_section_text(text: str) -> str:
    text = text.replace("\x00", "")
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"([가-힣A-Za-z0-9,;:.])\n([가-힣A-Za-z0-9])", r"\1 \2", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def split_long_text(text: str, *, token_limit: int, overlap: int) -> list[str]:
    tokens = tokenize(text)
    if len(tokens) <= token_limit:
        return [text]

    # Clamp overlap so the sliding window always advances; overlap >= token_limit
    # would otherwise leave start unchanged (infinite loop) or move it backward
    # (re-chunking the same tokens).
    overlap = min(max(overlap, 0), token_limit - 1)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + token_limit, len(tokens))
        chunk = detokenize(tokens[start:end])
        if chunk:
            chunks.append(chunk)
        if end == len(tokens):
            break
        start = max(0, end - overlap)
    return chunks


def tokenize(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def detokenize(tokens: list[str]) -> str:
    return " ".join(tokens).strip()


def infer_industry(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    for industry in INDUSTRY_NAMES:
        if industry in compact:
            return industry
    return None


def infer_source_name(path: Path) -> str:
    parts = list(path.parts)
    if "industry_reports" in parts:
        return path.name
    if "pypdf_fallback" in parts or "pdfplumber_no_tables" in parts:
        return f"{path.stem}.pdf"
    return path.stem.replace("_images", "")


def infer_published_year(source_name: str) -> int | None:
    years = [int(year) for year in re.findall(r"20\d{2}", source_name)]
    normalized_name = unicodedata.normalize("NFC", source_name)
    if not years and "기계산업" in normalized_name and "기술백서" in normalized_name:
        return 2025
    return max(years) if years else None


def build_chunk_id(
    source_name: str,
    published_year: int | None,
    industry: str,
    page_part: str,
    chunk_no: int,
) -> str:
    source_key = re.sub(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣᄀ-ᇿ]+", "_", Path(source_name).stem).strip("_")
    industry_key = re.sub(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣᄀ-ᇿ]+", "_", industry).strip("_")
    if not source_key:
        source_key = "industry_report"
    year = published_year or "unknown"
    return f"{source_key}_{year}_{industry_key}_{page_part}_{chunk_no:03d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk industry report PDFs for RAG.")
    parser.add_argument("--input-dir", type=Path, default=settings.data_dir / "industry_reports")
    parser.add_argument("--markdown-dir", type=Path, default=settings.output_dir / "industry_markdown")
    parser.add_argument("--output", type=Path, default=settings.data_dir / "vector_db" / "industry_report_chunks.jsonl")
    parser.add_argument("--token-limit", type=int, default=TOKEN_LIMIT)
    parser.add_argument("--overlap", type=int, default=TOKEN_OVERLAP)
    args = parser.parse_args()

    markdown_paths = convert_industry_pdfs_to_markdown(args.input_dir, args.markdown_dir)
    chunks = chunk_industry_reports(
        markdown_paths,
        output_path=args.output,
        token_limit=args.token_limit,
        token_overlap=args.overlap,
    )
    print(f"Markdown files: {len(markdown_paths)}")
    print(f"Chunks: {len(chunks)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
