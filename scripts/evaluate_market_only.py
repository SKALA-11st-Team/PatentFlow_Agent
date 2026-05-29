from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.valuation import run_axis_valuation_agent
from services.patent.kipris_patent_service import (
    download_and_parse_patent_pdf,
    fetch_kipris_bibliography,
    get_patent,
    parse_single_patent_pdf,
)
from services.patent.markdown_preprocess_service import build_preprocessed_patent
from services.rag.industry_rag_service import search_and_save_patent_industry_evidence
from workflow.state import PatentWorkflowState


LOCAL_JDK_HOME = PROJECT_ROOT / ".jdk" / "jdk-17.0.19+10" / "Contents" / "Home"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run only the market valuation axis for one patent.")
    parser.add_argument("management_number", help="Internal patent management number. Example: P201702001-KR0.")
    parser.add_argument("--artifact-dir", help="Output directory. Defaults to artifacts/runs/manual/market_only_<timestamp>_<management_number>.")
    parser.add_argument("--industry", default=None, help="Optional industry filter for industry RAG. Example: 반도체")
    parser.add_argument("--top-k", type=int, default=5, help="Industry RAG top-k evidence count.")
    parser.add_argument("--pdf-path", help="Use an existing PDF path instead of downloading from KIPRIS.")
    parser.add_argument("--skip-pdf", action="store_true", help="Do not parse PDF; use KIPRIS/DB metadata only.")
    parser.add_argument("--no-save", action="store_true", help="Do not save intermediate valuation input payloads.")
    return parser.parse_args()


def configure_local_java() -> None:
    if not LOCAL_JDK_HOME.exists():
        return
    os.environ["JAVA_HOME"] = str(LOCAL_JDK_HOME)
    os.environ["PATH"] = f"{LOCAL_JDK_HOME / 'bin'}:{os.environ.get('PATH', '')}"


def default_artifact_dir(management_number: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_management_number = management_number.replace("/", "_")
    return PROJECT_ROOT / "artifacts" / "runs" / "manual" / f"market_only_{timestamp}_{safe_management_number}"


def existing_pdf_for_application(application_number: str) -> Path | None:
    compact = "".join(ch for ch in str(application_number or "") if ch.isdigit())
    if not compact:
        return None
    path = PROJECT_ROOT / "data" / "patent_pdf" / f"{compact}.pdf"
    return path if path.exists() else None


def parse_or_download_pdf(
    *,
    patent: dict[str, Any],
    output_dir: Path,
    pdf_path: str | None,
    skip_pdf: bool,
) -> dict[str, Any] | None:
    if skip_pdf:
        return None

    application_number = patent.get("application_number")
    if not application_number:
        return None

    selected_pdf = Path(pdf_path) if pdf_path else existing_pdf_for_application(application_number)
    if selected_pdf and selected_pdf.exists():
        parsed = parse_single_patent_pdf(
            selected_pdf,
            output_dir=output_dir / "patent_markdown" / selected_pdf.stem,
        )
        return {
            "application_number": application_number,
            "selected_type": "existing_pdf",
            "pdf_path": str(selected_pdf),
            **parsed,
        }

    return download_and_parse_patent_pdf(
        application_number,
        output_dir=output_dir / "patent_markdown",
        prefer_announcement=patent.get("status") == "등록",
    )


def build_rag_queries(patent: dict[str, Any], preprocessed: dict[str, Any]) -> list[str]:
    metadata = preprocessed.get("metadata") or {}
    sections = preprocessed.get("sections") or {}
    parts = [
        patent.get("title_final"),
        metadata.get("title"),
        patent.get("business_area"),
        patent.get("technology_area"),
        patent.get("related_product"),
        sections.get("abstract"),
        "시장 투자 수요 성장 산업 적용 확산 서비스 플랫폼",
    ]
    query = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    return [query] if query else []


def run_market_only(args: argparse.Namespace) -> dict[str, Any]:
    configure_local_java()
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else default_artifact_dir(args.management_number)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_step(f"artifact_dir={artifact_dir}")

    log_step(f"patent lookup start: {args.management_number}")
    patent = get_patent(management_number=args.management_number)
    if not patent:
        raise RuntimeError(f"Patent not found: {args.management_number}")
    log_step(f"patent lookup done: application={patent.get('application_number')}, title={patent.get('title_final')}")

    kipris_api_data: dict[str, Any] | None = None
    kipris_warning: str | None = None
    try:
        log_step("KIPRIS bibliography/family fetch start")
        kipris_api_data = fetch_kipris_bibliography(patent["application_number"])
        log_step(f"KIPRIS fetch done: family_count={len((kipris_api_data or {}).get('family_patents', []))}")
    except Exception as exc:
        kipris_warning = f"kipris_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
        log_step(f"KIPRIS fetch warning: {kipris_warning}")

    log_step("PDF parse start")
    parsed_pdf = parse_or_download_pdf(
        patent=patent,
        output_dir=artifact_dir,
        pdf_path=args.pdf_path,
        skip_pdf=args.skip_pdf,
    )
    if parsed_pdf:
        log_step(f"PDF parse done: markdown_count={len(parsed_pdf.get('markdown_paths') or [])}")
    else:
        log_step("PDF parse skipped")

    markdown_paths = (parsed_pdf or {}).get("markdown_paths") or []
    log_step("patent preprocessing start")
    preprocessed = build_preprocessed_patent(
        (parsed_pdf or {}).get("markdown_text", ""),
        source={
            "application_number": patent.get("application_number"),
            "registration_number": patent.get("registration_number"),
            "pdf_path": (parsed_pdf or {}).get("pdf_path"),
            "markdown_paths": markdown_paths,
            "file_name": Path(markdown_paths[0]).name if markdown_paths else None,
        },
        db_metadata=patent,
        api_data=kipris_api_data,
    )
    log_step(
        "patent preprocessing done: "
        f"cpc_count={len((preprocessed.get('metadata') or {}).get('cpc') or [])}"
    )

    log_step("industry RAG search start")
    rag = search_and_save_patent_industry_evidence(
        preprocessed_patent=preprocessed,
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        rag_queries=build_rag_queries(patent, preprocessed),
        top_k=args.top_k,
        industry=args.industry,
        output_dir=artifact_dir / "industry_rag",
        save=not args.no_save,
    )
    log_step(f"industry RAG search done: evidence_count={len(rag.get('items') or [])}")

    state = PatentWorkflowState(
        user_input={
            "artifact_dir": str(artifact_dir),
            "use_llm_valuation": True,
            "no_save": args.no_save,
        },
        patent_structured=patent,
        kipris_api_data=kipris_api_data,
        kipris_family_patents=(kipris_api_data or {}).get("family_patents", []),
        parsed_pdf=parsed_pdf,
        preprocessed_patent=preprocessed,
        evidence_bundle=rag.get("items", []),
    )
    log_step("market valuation start")
    state = run_axis_valuation_agent("market", state)
    market_result = state.valuation_result["axes"]["market"]
    log_step(
        "market valuation done: "
        f"score={market_result.get('score')}, subscores={market_result.get('subscores')}"
    )

    result = {
        "artifact_dir": str(artifact_dir),
        "patent": patent,
        "kipris_warning": kipris_warning,
        "kipris_family_patents": (kipris_api_data or {}).get("family_patents", []),
        "pdf": {
            "pdf_path": (parsed_pdf or {}).get("pdf_path"),
            "markdown_paths": markdown_paths,
        },
        "preprocessed_metadata": preprocessed.get("metadata") or {},
        "industry_rag": rag,
        "market_result": market_result,
    }

    output_path = artifact_dir / "market_eval_result.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = artifact_dir / "market_eval_report.md"
    report_path.write_text(render_market_report(result), encoding="utf-8")
    log_step(f"saved JSON: {output_path}")
    log_step(f"saved report: {report_path}")
    result["output_path"] = str(output_path)
    result["report_path"] = str(report_path)
    return result


def log_step(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def render_market_report(result: dict[str, Any]) -> str:
    patent = result.get("patent") or {}
    market = result.get("market_result") or {}
    metrics = market.get("marketability_metrics") or {}
    subscores = market.get("subscores") or {}
    industry = subscores.get("industry_marketability") or {}
    market_growth = subscores.get("market_growth") or {}
    global_business = subscores.get("global_business") or {}
    industry_breakdown = industry.get("details") or {}
    evidence_items = (result.get("industry_rag") or {}).get("items") or []
    used_evidence = [
        item
        for item in evidence_items
        if item.get("evidence_id") in set(market.get("evidence_ids") or [])
    ]

    lines = [
        "# 시장성 평가 리포트",
        "",
        f"- 관리번호: {patent.get('management_number') or ''}",
        f"- 특허명: {patent.get('title_final') or patent.get('title_draft') or ''}",
        f"- 대표 CPC: {metrics.get('representative_cpc') or '-'}",
        f"- 시장 성장성 기준 종료일: {metrics.get('market_growth_reference_date') or '-'}",
        "",
        "## 평가 결과 요약",
        "",
        f"최종 시장성 점수: {market.get('score', 0)} / 100",
        "",
        "| 평가 항목 | 점수 |",
        "| --- | ---: |",
        f"| 산업 시장성 | {format_score(industry.get('score'))} / 40 |",
        f"| 시장 성장성 | {format_score(market_growth.get('score'))} / 40 |",
        f"| 글로벌 사업성 | {format_score(global_business.get('score'))} / 20 |",
        "",
        "## 산업 시장성 근거",
        "",
        f"산업 시장성 점수: {format_score(industry.get('score'))} / 40",
        "",
        "| 세부 항목 | 점수 |",
        "| --- | ---: |",
        f"| 산업 성장 근거 | {format_score(industry_breakdown.get('industry_growth_evidence_score'))} / 15 |",
        f"| 기업 투자·진입 근거 | {format_score(industry_breakdown.get('corporate_investment_entry_score'))} / 10 |",
        f"| 뉴스 기반 시장 확산 근거 | {format_score(industry_breakdown.get('news_market_diffusion_score'))} / 10 |",
        f"| 자료 신뢰도 | {format_score(industry_breakdown.get('source_reliability_score'))} / 5 |",
        "",
        market.get("rationale") or "산업 시장성 판단 근거가 없습니다.",
        "",
        "사용 근거:",
    ]
    if used_evidence:
        for item in used_evidence:
            title = item.get("title") or item.get("heading") or item.get("evidence_id")
            source = item.get("source") or ""
            page = item.get("page")
            page_text = f", p{page}" if page else ""
            lines.append(f"- {title} ({source}{page_text})")
    else:
        lines.append("- 사용된 산업 리포트 근거 없음")

    lines.extend(
        [
            "",
            "## 시장 성장성 근거",
            "",
            f"시장 성장성 점수: {format_score(market_growth.get('score'))} / 40",
            "",
            "| 기간 | 공개 특허 수 |",
            "| --- | ---: |",
        ]
    )
    counts = metrics.get("cpc_application_counts") or []
    if counts:
        for item in counts:
            label = item.get("label") or build_window_label(item)
            lines.append(f"| {label} | {item.get('count')} |")
    else:
        lines.append("| - | - |")

    lines.extend(
        [
            "",
            f"- CAGR: {format_percent(metrics.get('cagr'))}",
            f"- CAGR 점수: {format_score(metrics.get('cagr_score'))} / 25",
            f"- 최근 3개 구간 추세: {trend_label(metrics.get('trend_status'))}",
            f"- 추세 점수: {format_score(metrics.get('trend_score'))} / 15",
        ]
    )
    if metrics.get("missing_reason"):
        lines.append(f"- Missing reason: {metrics.get('missing_reason')}")

    lines.extend(
        [
            "",
            "## 글로벌 사업성 근거",
            "",
            f"글로벌 사업성 점수: {format_score(global_business.get('score'))} / 20",
            "",
            f"- Patent Family 국가: {', '.join(metrics.get('family_countries') or []) or '-'}",
            f"- 해외 패밀리 국가: {', '.join(metrics.get('foreign_family_countries') or []) or '-'}",
            f"- 판단 상태: {global_status_label(metrics.get('global_business_status'))}",
        ]
    )

    risk_factors = market.get("risk_factors") or []
    if risk_factors:
        lines.extend(["", "## 리스크"])
        lines.extend(f"- {item}" for item in risk_factors)

    missing = market.get("missing_information") or []
    if missing:
        lines.extend(["", "## 보완 필요 정보"])
        lines.extend(f"- {item}" for item in missing)

    return "\n".join(lines).rstrip() + "\n"


def format_score(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def format_percent(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def build_window_label(item: dict[str, Any]) -> str:
    start_date = item.get("start_date")
    end_date = item.get("end_date")
    if start_date and end_date:
        return f"{start_date}~{end_date}"
    return "-"


def trend_label(value: Any) -> str:
    labels = {
        "continuous_increase": "연속 증가",
        "partial_increase": "일부 증가",
        "continuous_decrease": "연속 감소",
        "flat_or_mixed": "유지 또는 혼합",
        "insufficient_data": "자료 부족",
    }
    return labels.get(str(value), str(value or "-"))


def global_status_label(value: Any) -> str:
    labels = {
        "priority_country_family": "미국/중국/일본 포함 다국가 출원",
        "foreign_family": "해외 출원 존재",
        "domestic_only": "국내 단독 출원",
    }
    return labels.get(str(value), str(value or "-"))


def main() -> None:
    result = run_market_only(parse_args())
    print(
        json.dumps(
            {
                "output_path": result["output_path"],
                "report_path": result["report_path"],
                "market_result": result["market_result"],
                "evidence_count": len((result.get("industry_rag") or {}).get("items", [])),
                "family_countries": [
                    item.get("country_code")
                    for item in result.get("kipris_family_patents", [])
                    if item.get("country_code")
                ],
                "cpc": (result.get("preprocessed_metadata") or {}).get("cpc") or [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
