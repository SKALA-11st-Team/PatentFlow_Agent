from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.valuation import run_axis_valuation_agent
from scripts.evaluate_market_only import configure_local_java, log_step, parse_or_download_pdf
from services.patent.kipris_patent_service import fetch_kipris_bibliography, get_patent
from services.patent.markdown_preprocess_service import build_preprocessed_patent
from workflow.state import PatentWorkflowState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run only the technology valuation axis for one patent.")
    parser.add_argument("management_number", help="Internal patent management number. Example: P201702001-KR0.")
    parser.add_argument(
        "--artifact-dir",
        help="Output directory. Defaults to artifacts/runs/manual/technology_only_<timestamp>_<management_number>.",
    )
    parser.add_argument("--pdf-path", help="Use an existing PDF path instead of downloading from KIPRIS.")
    parser.add_argument("--skip-pdf", action="store_true", help="Do not parse target PDF; use KIPRIS/DB metadata only.")
    parser.add_argument("--no-save", action="store_true", help="Do not save intermediate valuation input payloads.")
    parser.add_argument(
        "--mode",
        choices=["similar", "prior-art", "hybrid"],
        default="similar",
        help="Technology comparison source: similar patents, prior-art references, or both.",
    )
    return parser.parse_args()


def default_artifact_dir(management_number: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_management_number = management_number.replace("/", "_")
    return PROJECT_ROOT / "artifacts" / "runs" / "manual" / f"technology_only_{timestamp}_{safe_management_number}"


def run_technology_only(args: argparse.Namespace) -> dict[str, Any]:
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
        log_step("KIPRIS bibliography/claims fetch start")
        kipris_api_data = fetch_kipris_bibliography(patent["application_number"])
        claim_count = len((kipris_api_data or {}).get("claims") or [])
        abstract_found = bool(((kipris_api_data or {}).get("sections") or {}).get("abstract"))
        log_step(f"KIPRIS fetch done: claims={claim_count}, abstract_found={abstract_found}")
    except Exception as exc:
        kipris_warning = f"kipris_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}"
        log_step(f"KIPRIS fetch warning: {kipris_warning}")

    log_step("target PDF parse start")
    parsed_pdf = parse_or_download_pdf(
        patent=patent,
        output_dir=artifact_dir,
        pdf_path=args.pdf_path,
        skip_pdf=args.skip_pdf,
    )
    if parsed_pdf:
        log_step(f"target PDF parse done: markdown_count={len(parsed_pdf.get('markdown_paths') or [])}")
    else:
        log_step("target PDF parse skipped")

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
        f"cpc_count={len((preprocessed.get('metadata') or {}).get('cpc') or [])}, "
        f"claim_count={len(preprocessed.get('claims') or [])}"
    )

    state = PatentWorkflowState(
        user_input={
            "artifact_dir": str(artifact_dir),
            "use_llm_valuation": True,
            "no_save": args.no_save,
            "technology_comparison_mode": args.mode,
        },
        patent_structured=patent,
        kipris_api_data=kipris_api_data,
        parsed_pdf=parsed_pdf,
        preprocessed_patent=preprocessed,
        evidence_bundle=[],
    )
    log_step("technology valuation start")
    log_step(f"technology comparison mode: {args.mode}")
    state = run_axis_valuation_agent("technology", state)
    technology_result = state.valuation_result["axes"]["technology"]
    metrics = technology_result.get("technology_metrics") or {}
    log_step(
        "technology valuation done: "
        f"score={technology_result.get('score')}, "
        f"comparison_count={len(metrics.get('similar_patents') or [])}, "
        f"candidate_count={metrics.get('candidate_count')}"
    )

    result = {
        "artifact_dir": str(artifact_dir),
        "patent": patent,
        "kipris_warning": kipris_warning,
        "pdf": {
            "pdf_path": (parsed_pdf or {}).get("pdf_path"),
            "markdown_paths": markdown_paths,
        },
        "preprocessed_metadata": preprocessed.get("metadata") or {},
        "technology_result": technology_result,
    }

    output_path = artifact_dir / "technology_eval_result.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = artifact_dir / "technology_eval_report.md"
    report_path.write_text(render_technology_report(result), encoding="utf-8")
    log_step(f"saved JSON: {output_path}")
    log_step(f"saved report: {report_path}")
    result["output_path"] = str(output_path)
    result["report_path"] = str(report_path)
    return result


def render_technology_report(result: dict[str, Any]) -> str:
    patent = result.get("patent") or {}
    technology = result.get("technology_result") or {}
    metrics = technology.get("technology_metrics") or {}
    mode = str(metrics.get("comparison_mode") or "similar")
    source_heading = comparison_heading(mode)
    items = metrics.get("similar_patents") or []
    sub_scores = technology.get("sub_scores") or {}

    lines = [
        "# 기술성 평가 리포트",
        "",
        f"- 관리번호: {patent.get('management_number') or ''}",
        f"- 특허명: {patent.get('title_final') or patent.get('title_draft') or ''}",
        f"- 대표 CPC: {metrics.get('representative_cpc') or '-'}",
        "",
        "## 평가 결과 요약",
        "",
        f"최종 기술성 점수: {format_score(technology.get('score'))} / 100",
        "",
        "| 평가 항목 | 점수 |",
        "| --- | ---: |",
        f"| 기술 차별성 | {format_score(sub_scores.get('technical_differentiation_score'))} / 60 |",
        f"| 구현 구체성 | {format_score(sub_scores.get('implementation_specificity_score'))} / 40 |",
        "",
        "## 기술 차별성 근거",
        "",
        f"기술 차별성 점수: {format_score(sub_scores.get('technical_differentiation_score'))} / 60",
        "",
        technology.get("rationale") or "기술성 판단 근거가 없습니다.",
    ]

    append_breakdown_table(
        lines,
        title="기술 차별성 세부 점수",
        breakdown=technology.get("technical_differentiation_breakdown") or {},
        rows=[
            ("신규 구성요소 존재", "new_component_score", 15),
            ("기술 조합 차별성", "combination_difference_score", 15),
            ("처리 구조 차별성", "processing_structure_difference_score", 15),
            ("해결 방식 차별성", "solution_approach_difference_score", 10),
            ("차별 근거 명확성", "evidence_clarity_score", 5),
        ],
    )

    lines.extend(
        [
            "",
            "## 구현 구체성 근거",
            "",
            f"구현 구체성 점수: {format_score(sub_scores.get('implementation_specificity_score'))} / 40",
        ]
    )
    append_breakdown_table(
        lines,
        title="구현 구체성 세부 점수",
        breakdown=technology.get("implementation_specificity_breakdown") or {},
        rows=[
            ("입력 데이터 명시", "input_data_score", 4),
            ("처리 대상 명시", "processing_target_score", 3),
            ("핵심 변수 명시", "core_variable_score", 3),
            ("출력 결과 구조", "output_structure_score", 3),
            ("구성요소 연결성", "component_linkage_score", 2),
            ("처리 절차 제시", "procedure_score", 6),
            ("처리 로직 설명", "logic_score", 6),
            ("조건·파라미터 존재", "condition_parameter_score", 5),
            ("계산·판단 구조 존재", "calculation_decision_score", 5),
            ("예외·반복·업데이트 구조", "exception_iteration_update_score", 3),
        ],
    )

    lines.extend(
        [
            "",
            f"## {source_heading}",
            "",
            f"- 비교 방식: {mode}",
            f"- 사용 비교 문헌 수: {len(items)}",
            "",
            "| 순위 | 구분 | 표시번호 | 출원번호 | 출원일 | 상태 | PDF | 원문자수 | 제목 |",
            "| ---: | --- | --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    if items:
        for index, item in enumerate(items, start=1):
            pdf_state = "수집" if item.get("pdf_collected") else "미수집"
            lines.append(
                "| "
                f"{index} | "
                f"{escape_table(item.get('source_label') or comparison_item_label(item))} | "
                f"{escape_table(item.get('display_number') or '-')} | "
                f"{item.get('application_number') or '-'} | "
                f"{item.get('application_date') or '-'} | "
                f"{item.get('status') or '-'} | "
                f"{pdf_state} | "
                f"{format_score(item.get('pdf_text_chars'))} | "
                f"{escape_table(item.get('title') or '-')} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    warnings = metrics.get("warnings") or []
    if warnings:
        lines.extend(["", "## 수집 경고"])
        lines.extend(f"- {item}" for item in warnings)

    risk_factors = technology.get("risk_factors") or []
    if risk_factors:
        lines.extend(["", "## 리스크"])
        lines.extend(f"- {item}" for item in risk_factors)

    missing = technology.get("missing_information") or []
    if missing:
        lines.extend(["", "## 보완 필요 정보"])
        lines.extend(f"- {item}" for item in missing)

    return "\n".join(lines).rstrip() + "\n"


def append_breakdown_table(
    lines: list[str],
    *,
    title: str,
    breakdown: dict[str, Any],
    rows: list[tuple[str, str, int]],
) -> None:
    if not breakdown:
        return
    lines.extend(["", f"### {title}", "", "| 세부 항목 | 점수 |", "| --- | ---: |"])
    for label, key, maximum in rows:
        lines.append(f"| {label} | {format_score(breakdown.get(key))} / {maximum} |")


def format_score(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)

def escape_table(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def comparison_heading(mode: str) -> str:
    if mode == "prior-art":
        return "선행기술조사문헌 근거"
    if mode == "hybrid":
        return "선행기술/유사특허 비교 근거"
    return "유사 특허 근거"


def comparison_item_label(item: dict[str, Any]) -> str:
    if item.get("comparison_source") == "prior-art":
        return "선행문헌"
    return "유사특허"


def main() -> None:
    result = run_technology_only(parse_args())
    print(f"report_path={result['report_path']}")
    print(f"output_path={result['output_path']}")


if __name__ == "__main__":
    main()
