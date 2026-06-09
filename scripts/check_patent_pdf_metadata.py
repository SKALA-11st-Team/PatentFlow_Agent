from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.patent.kipris_patent_service import (
    download_and_parse_patent_pdf,
    extract_pdf_text_left_then_right,
    extract_pdf_text_with_ocr,
    fetch_foreign_patent_rights_data,
    find_cached_foreign_patent_pdf,
    get_patent,
    has_meaningful_pdf_text,
    parse_single_patent_pdf,
    should_run_ocr_fallback,
)
from services.patent.markdown_preprocess_service import build_preprocessed_patent


LOCAL_JDK_HOME = PROJECT_ROOT / ".jdk" / "jdk-17.0.19+10" / "Contents" / "Home"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse one patent PDF and print extracted metadata/abstract without running valuation.",
    )
    id_group = parser.add_mutually_exclusive_group(required=False)
    id_group.add_argument("--patent-id", type=int, help="Patent row id in data/patents.sqlite3.")
    id_group.add_argument("--management-number", help="Internal patent management number. Example: P202012001-US0.")
    id_group.add_argument("--application-number", help="Patent application number. Example: 18/020,829.")
    id_group.add_argument("--registration-number", help="Patent registration number.")
    parser.add_argument("--pdf-path", help="Use an existing PDF path instead of downloading.")
    parser.add_argument("--artifact-dir", help="Output directory for parsed markdown and check JSON.")
    parser.add_argument("--java-home", help="Optional JDK home. If omitted, common macOS JDK paths are searched.")
    parser.add_argument("--save-cleaned-markdown", action="store_true", help="Save cleaned markdown text.")
    parser.add_argument("--json", action="store_true", help="Print the full check result as JSON.")
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print results to stdout. By default this script only saves artifacts and stays silent.",
    )
    return parser.parse_args()


def configure_java(java_home: str | None = None) -> Path | None:
    detected = find_java_home(java_home)
    if not detected:
        return None
    os.environ["JAVA_HOME"] = str(detected)
    os.environ["PATH"] = f"{detected / 'bin'}:{os.environ.get('PATH', '')}"
    return detected


def find_java_home(java_home: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if java_home:
        candidates.append(Path(java_home).expanduser())
    candidates.append(LOCAL_JDK_HOME)
    candidates.extend(
        [
            Path("/opt/homebrew/opt/openjdk@17"),
            Path("/opt/homebrew/opt/openjdk"),
            Path("/usr/local/opt/openjdk@17"),
            Path("/usr/local/opt/openjdk"),
        ]
    )
    candidates.extend(sorted(Path("/Library/Java/JavaVirtualMachines").glob("*/Contents/Home")))

    java_home_from_system = macos_java_home()
    if java_home_from_system:
        candidates.append(java_home_from_system)

    for candidate in candidates:
        java_bin = candidate / "bin" / "java"
        if java_bin.exists():
            return candidate
    return None


def macos_java_home() -> Path | None:
    java_home_cmd = Path("/usr/libexec/java_home")
    if not java_home_cmd.exists():
        return None
    try:
        result = subprocess.run(
            [str(java_home_cmd)],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def default_artifact_dir(identifier: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_identifier = identifier.replace("/", "_")
    return PROJECT_ROOT / "artifacts" / "runs" / "manual" / f"pdf_metadata_{timestamp}_{safe_identifier}"


def resolve_patent(args: argparse.Namespace) -> dict[str, Any]:
    if not any([args.patent_id, args.management_number, args.application_number, args.registration_number]):
        if args.pdf_path:
            return {}
        raise SystemExit("One identifier or --pdf-path is required.")
    patent = get_patent(
        patent_id=args.patent_id,
        management_number=args.management_number,
        application_number=args.application_number,
        registration_number=args.registration_number,
    )
    if not patent:
        raise SystemExit("Patent was not found in data/patents.sqlite3.")
    return patent


def parse_single_pdf_with_fallback(pdf_path: Path, *, output_dir: Path) -> dict[str, Any]:
    try:
        return parse_single_patent_pdf(pdf_path, output_dir=output_dir)
    except Exception as exc:
        markdown_text = extract_text_without_java(pdf_path)
        if not markdown_text.strip():
            raise RuntimeError(f"pdf_text_fallback_extracted_empty_text:{exc}") from exc
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = output_dir / f"{pdf_path.stem}_fallback.md"
        markdown_path.write_text(markdown_text, encoding="utf-8")
        return {
            "markdown_paths": [str(markdown_path)],
            "markdown_text": markdown_text,
            "parse_warning": f"opendataloader_failed_used_python_pdf_fallback:{exc.__class__.__name__}:{str(exc)[:500]}",
        }


def extract_text_without_java(pdf_path: Path) -> str:
    errors: list[str] = []
    try:
        text = extract_pdf_text_left_then_right(pdf_path)
        if has_meaningful_pdf_text(text):
            return text
        errors.append("pdfplumber_left_then_right:extracted_empty_text")
    except Exception as exc:
        errors.append(f"pdfplumber_left_then_right:{exc.__class__.__name__}:{str(exc)[:200]}")

    try:
        import pdfplumber

        pages: list[str] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text)
        if pages:
            return "\n\n".join(pages)
        errors.append("pdfplumber:extracted_empty_text")
    except Exception as exc:
        errors.append(f"pdfplumber:{exc.__class__.__name__}:{str(exc)[:200]}")

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        if pages:
            merged = "\n\n".join(pages)
            if has_meaningful_pdf_text(merged):
                return merged
        errors.append("pypdf:extracted_empty_text")
    except Exception as exc:
        errors.append(f"pypdf:{exc.__class__.__name__}:{str(exc)[:200]}")

    if should_run_ocr_fallback("\n\n".join(errors)):
        try:
            ocr_text = extract_pdf_text_with_ocr(pdf_path)
            if has_meaningful_pdf_text(ocr_text):
                return ocr_text
            errors.append("ocr:extracted_empty_text")
        except Exception as exc:
            errors.append(f"ocr:{exc.__class__.__name__}:{str(exc)[:200]}")

    missing_tools = [tool for tool in ("tesseract", "pdftoppm") if shutil.which(tool) is None]
    if not find_java_home():
        missing_tools.append("java")
    hint = (
        "PDF appears to have no embedded text. Install Java for opendataloader "
        "or install tesseract+poppler OCR tools."
    )
    raise RuntimeError(f"python_pdf_text_extract_failed:{errors}; missing_tools={missing_tools}; hint={hint}")


def parse_pdf(args: argparse.Namespace, patent: dict[str, Any], artifact_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.pdf_path:
        pdf_path = Path(args.pdf_path)
        if not pdf_path.exists():
            raise SystemExit(f"PDF does not exist: {pdf_path}")
        parsed = parse_single_pdf_with_fallback(
            pdf_path,
            output_dir=artifact_dir / "patent_markdown" / pdf_path.stem,
        )
        return (
            {
                "source_type": "local_pdf",
                "metadata": {},
                "sections": {"abstract": ""},
                "claims": [],
                "claim_stats": {},
            },
            {
                "selected_type": "local_pdf",
                "pdf_path": str(pdf_path),
                **parsed,
            },
        )

    country = str(patent.get("country") or "").strip().upper()
    if country and country != "KR":
        api_data = fetch_foreign_patent_rights_data(
            patent,
            output_dir=artifact_dir / "patent_markdown",
            collect_pdf=True,
        )
        parsed_pdf = api_data.get("parsed_pdf")
        if not parsed_pdf:
            cached_pdf_path = find_cached_foreign_patent_pdf(patent)
            if cached_pdf_path is None:
                raise SystemExit(f"Foreign PDF was not parsed and no cached PDF was found. warnings={api_data.get('warnings')}")
            parsed = parse_single_pdf_with_fallback(
                cached_pdf_path,
                output_dir=artifact_dir / "patent_markdown" / str(patent.get("management_number") or cached_pdf_path.stem),
            )
            parsed_pdf = {
                "selected_type": "cached_pdf_python_fallback",
                "pdf_path": str(cached_pdf_path),
                "parse_output_dir": str(artifact_dir / "patent_markdown"),
                **parsed,
            }
            api_data.setdefault("warnings", []).append(parsed.get("parse_warning", "used_cached_pdf_python_fallback"))
        return api_data, parsed_pdf

    application_number = patent.get("application_number")
    if not application_number:
        raise SystemExit("Patent application_number is missing.")
    api_data = {
        "source_type": "kipris_pdf_only",
        "metadata": {},
        "sections": {"abstract": ""},
        "claims": [],
        "claim_stats": {},
    }
    parsed_pdf = download_and_parse_patent_pdf(
        application_number,
        output_dir=artifact_dir / "patent_markdown",
        prefer_announcement=patent.get("status") == "등록",
    )
    return api_data, parsed_pdf


def build_check_result(patent: dict[str, Any], api_data: dict[str, Any], parsed_pdf: dict[str, Any]) -> dict[str, Any]:
    preprocessed = build_preprocessed_patent(
        parsed_pdf.get("markdown_text") or "",
        source={
            "pdf_path": parsed_pdf.get("pdf_path"),
            "markdown_paths": parsed_pdf.get("markdown_paths") or [],
            "selected_type": parsed_pdf.get("selected_type"),
        },
        db_metadata=patent,
        api_data=api_data,
    )
    metadata = preprocessed.get("metadata") or {}
    sections = preprocessed.get("sections") or {}
    return {
        "patent": {
            "id": patent.get("id"),
            "management_number": patent.get("management_number"),
            "country": patent.get("country"),
            "application_number": patent.get("application_number"),
            "registration_number": patent.get("registration_number"),
            "title": patent.get("title_final") or patent.get("title_draft"),
        },
    "pdf": {
            "pdf_path": parsed_pdf.get("pdf_path"),
            "markdown_paths": parsed_pdf.get("markdown_paths") or [],
            "selected_type": parsed_pdf.get("selected_type"),
            "markdown_char_count": len(parsed_pdf.get("markdown_text") or ""),
            "parse_warning": parsed_pdf.get("parse_warning"),
        },
        "extracted": {
            "title": metadata.get("title"),
            "ipc": metadata.get("ipc") or [],
            "representative_ipc": metadata.get("representative_ipc"),
            "cpc": metadata.get("cpc") or [],
            "abstract": sections.get("abstract") or "",
            "abstract_char_count": len(sections.get("abstract") or ""),
            "full_text_after_drawings": sections.get("full_text_after_drawings") or "",
            "full_text_after_drawings_char_count": len(sections.get("full_text_after_drawings") or ""),
            "claim_count": metadata.get("claim_count"),
            "active_claim_count": (preprocessed.get("claim_stats") or {}).get("active_claim_count"),
            "metadata_source": metadata.get("metadata_source") or {},
            "validation": preprocessed.get("validation") or {},
        },
        "api_debug": {
            "source_type": api_data.get("source_type"),
            "foreign_bibliography_literature_number": api_data.get("foreign_bibliography_literature_number"),
            "bibliography_attempts": api_data.get("bibliography_attempts") or [],
            "raw_bibliography": api_data.get("raw_bibliography"),
            "warnings": api_data.get("warnings") or [],
        },
        "preprocessed": preprocessed,
    }


def print_human_summary(result: dict[str, Any], output_path: Path) -> None:
    patent = result["patent"]
    extracted = result["extracted"]
    validation = extracted.get("validation") or {}
    abstract = extracted.get("abstract") or ""

    print(f"Patent: {patent.get('management_number') or patent.get('application_number') or 'local_pdf'}")
    print(f"Country: {patent.get('country') or 'N/A'}")
    print(f"PDF: {result['pdf'].get('pdf_path') or 'N/A'}")
    print(f"Markdown chars: {result['pdf'].get('markdown_char_count')}")
    if result["pdf"].get("parse_warning"):
        print(f"Parse warning: {result['pdf'].get('parse_warning')}")
    print(f"Title: {extracted.get('title') or 'N/A'}")
    print(f"IPC: {extracted.get('ipc') or []}")
    print(f"Representative IPC: {extracted.get('representative_ipc') or 'N/A'}")
    print(f"CPC: {extracted.get('cpc') or []}")
    print(f"Abstract chars: {extracted.get('abstract_char_count')}")
    print(f"Abstract preview: {abstract[:500] if abstract else 'N/A'}")
    print(f"Claims: {extracted.get('active_claim_count') or 0} active / metadata {extracted.get('claim_count') or 'N/A'}")
    print(f"Valid: {validation.get('is_valid')}")
    print(f"Missing: {validation.get('missing_fields') or []}")
    print(f"Warnings: {validation.get('warnings') or []}")
    print(f"Saved: {output_path}")


def main() -> None:
    args = parse_args()
    configure_java(args.java_home)
    patent = resolve_patent(args)
    identifier = (
        patent.get("management_number")
        or patent.get("application_number")
        or Path(args.pdf_path).stem
    )
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else default_artifact_dir(str(identifier))
    artifact_dir.mkdir(parents=True, exist_ok=True)

    try:
        api_data, parsed_pdf = parse_pdf(args, patent, artifact_dir)
    except Exception as exc:
        raise SystemExit(f"PDF parse failed: {exc}") from exc
    result = build_check_result(patent, api_data, parsed_pdf)

    output_path = artifact_dir / "pdf_metadata_check.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.save_cleaned_markdown:
        cleaned = result["preprocessed"].get("cleaned_markdown") or ""
        (artifact_dir / "cleaned.md").write_text(cleaned, encoding="utf-8")

    if args.stdout and args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.stdout:
        print_human_summary(result, output_path)


if __name__ == "__main__":
    main()
