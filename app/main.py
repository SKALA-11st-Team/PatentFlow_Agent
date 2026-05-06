import argparse
import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from app.config import settings
from services.evidence.external_search_service import collect_external_evidence
from workflow.graph import run_workflow
from workflow.state import PatentWorkflowState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the patent valuation workflow.")
    patent_group = parser.add_mutually_exclusive_group(required=True)
    patent_group.add_argument("--patent-id", type=int, help="Patent row id in data/patents.sqlite3.")
    patent_group.add_argument("--application-number", help="Patent application number.")
    patent_group.add_argument("--registration-number", help="Patent registration number.")
    patent_group.add_argument("--management-number", help="Internal patent management number.")
    parser.add_argument(
        "--collect-pdf",
        action="store_true",
        help="Download the KIPRIS fulltext PDF and parse it with OpenDataLoader.",
    )
    parser.add_argument(
        "--collect-kipris-api",
        action="store_true",
        help="Collect structured KIPRIS bibliography data without downloading the PDF.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the final workflow state as JSON.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save optional workflow outputs to artifacts/runs.",
    )
    parser.add_argument(
        "--save-cleaned-markdown",
        action="store_true",
        help="Also save the cleaned markdown debug artifact.",
    )
    parser.add_argument(
        "--collect-api-evidence",
        action="store_true",
        help="Call unified APIs, normalize responses, and save evidence JSON files.",
    )
    parser.add_argument(
        "--api-base-url",
        default=settings.unified_api_base_url,
        help="Unified API backend base URL (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--api-query-limit",
        type=int,
        default=settings.search_query_count,
        help="Number of search queries to run for each news API.",
    )
    parser.add_argument(
        "--dart-corp-code",
        help="Optional DART corp_code (8 digits) to collect disclosure evidence.",
    )
    parser.add_argument(
        "--dart-bgn-de",
        help="Optional DART start date YYYYMMDD.",
    )
    parser.add_argument(
        "--dart-end-de",
        help="Optional DART end date YYYYMMDD.",
    )
    return parser


def build_run_id(args: argparse.Namespace) -> str:
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    identifier = (
        args.patent_id
        or args.application_number
        or args.registration_number
        or args.management_number
        or "unknown"
    )
    safe_identifier = str(identifier).replace("/", "_")
    return f"{timestamp}_{safe_identifier}"


def build_user_input(args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir = settings.run_outputs_dir / build_run_id(args)
    user_input: dict[str, Any] = {
        "collect_pdf": True,
        "collect_kipris_api": True,
        "no_save": args.no_save,
        "artifact_dir": str(artifact_dir),
    }
    if args.patent_id is not None:
        user_input["patent_id"] = args.patent_id
    if args.application_number:
        user_input["application_number"] = args.application_number
    if args.registration_number:
        user_input["registration_number"] = args.registration_number
    if args.management_number:
        user_input["management_number"] = args.management_number
    return user_input


def print_summary(state: PatentWorkflowState) -> None:
    patent = state.patent_structured or {}
    decision = state.supervisor_decision or {}
    if state.user_input.get("artifact_dir"):
        print(f"Artifacts: {state.user_input.get('artifact_dir')}")
    print(f"Patent: {patent.get('title_final') or patent.get('title_draft') or 'N/A'}")
    print(f"Application number: {patent.get('application_number') or 'N/A'}")
    print(f"Supervisor passed: {decision.get('passed')}")
    print(f"Next action: {decision.get('next_action')}")
    if state.pdf_paths:
        print(f"PDF: {state.pdf_paths[0]}")
    if state.kipris_api_data:
        print("KIPRIS API: collected")
    if state.parsed_pdf:
        print("Parsed markdown:")
        for path in state.parsed_pdf.get("markdown_paths", []):
            print(f"- {path}")
    if state.preprocessed_patent:
        validation = state.preprocessed_patent.get("validation", {})
        print(f"Preprocessed valid: {validation.get('is_valid')}")
        if validation.get("warnings"):
            print(f"Preprocess warnings: {validation.get('warnings')}")
    print_query_rewriting_summary(state)


def print_query_rewriting_summary(state: PatentWorkflowState) -> None:
    query_plan = state.query_plan or {}
    if not query_plan:
        return

    rewrite_meta = query_plan.get("rewrite_meta") or {}
    if rewrite_meta:
        print(f"Query rewriting source: {rewrite_meta.get('rewrite_source')}")
        if rewrite_meta.get("llm_error"):
            print(f"Query rewriting warning: {rewrite_meta.get('llm_error')}")

    ko_queries = query_plan.get("ko_queries") or []
    en_queries = query_plan.get("en_queries") or []
    selected_ko_queries = query_plan.get("selected_ko_queries") or query_plan.get("queries") or []
    selected_en_queries = query_plan.get("selected_en_queries") or query_plan.get("gnews_queries") or []

    print_queries("Generated Korean queries", ko_queries)
    print_queries("Generated English queries", en_queries)

    warnings = query_plan.get("search_warnings") or []
    if warnings:
        print("Evidence search warnings:")
        for warning in warnings:
            print(f"- {warning}")

    compressed = query_plan.get("compressed_evidence") or {}
    if compressed:
        stats = compressed.get("stats") or {}
        print(f"Compressed evidence output: {compressed.get('output_path') or 'none'}")
        print(
            "Compressed evidence count: "
            f"{stats.get('compressed_count', 0)} / {stats.get('candidate_count', 0)} candidates"
        )
        compression_warnings = compressed.get("warnings") or []
        if compression_warnings:
            print("Evidence compression warnings:")
            for warning in compression_warnings:
                print(f"- {warning}")


def print_queries(label: str, queries: list[str]) -> None:
    if not queries:
        print(f"{label}: none")
        return
    print(f"{label}:")
    for query in queries:
        print(f"- {query}")


def save_outputs(state: PatentWorkflowState, *, save_cleaned_markdown: bool = False) -> dict[str, Path]:
    saved: dict[str, Path] = {}
    if state.preprocessed_patent:
        output_dir = artifact_subdir(state, "preprocessed_patents")
        output_dir.mkdir(parents=True, exist_ok=True)
        patent_id = state.preprocessed_patent.get("patent_id") or "unknown_patent"
        safe_patent_id = str(patent_id).replace("/", "_")
        json_path = output_dir / f"{safe_patent_id}.json"
        json_payload = {
            key: value
            for key, value in state.preprocessed_patent.items()
            if key not in {"debug", "cleaned_markdown"}
        }
        json_path.write_text(
            json.dumps(json_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        saved["preprocessed_json"] = json_path

        if save_cleaned_markdown:
            cleaned_path = output_dir / f"{safe_patent_id}_cleaned.md"
            cleaned_path.write_text(
                state.preprocessed_patent.get("cleaned_markdown", ""),
                encoding="utf-8",
            )
            saved["cleaned_markdown"] = cleaned_path
    if state.final_report:
        output_dir = artifact_subdir(state, "final")
        output_dir.mkdir(parents=True, exist_ok=True)
        patent_id = (state.preprocessed_patent or {}).get("patent_id") or "unknown_patent"
        safe_patent_id = str(patent_id).replace("/", "_")
        final_path = output_dir / f"{safe_patent_id}_final_report.json"
        final_path.write_text(
            json.dumps(state.final_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        saved["final_report"] = final_path
    return saved


def artifact_subdir(state: PatentWorkflowState, name: str) -> Path:
    artifact_dir = state.user_input.get("artifact_dir")
    if artifact_dir:
        return Path(artifact_dir) / name
    return settings.output_dir / name


def print_saved_outputs(saved: dict[str, Path]) -> None:
    if not saved:
        return
    print("Saved outputs:")
    for label, path in saved.items():
        print(f"- {label}: {path}")


def main() -> None:
    args = build_parser().parse_args()
    initial_state = PatentWorkflowState(user_input=build_user_input(args))
    final_state = run_workflow(initial_state)
    if args.collect_api_evidence:
        if not final_state.preprocessed_patent:
            print("API evidence collection skipped: preprocessed_patent is missing.")
        else:
            patent = final_state.patent_structured or {}
            evidence_result = collect_external_evidence(
                preprocessed_patent=final_state.preprocessed_patent,
                patent_id=patent.get("id") or final_state.preprocessed_patent.get("patent_id"),
                application_number=patent.get("application_number"),
                api_base_url=args.api_base_url,
                query_limit_per_axis=args.api_query_limit,
                dart_corp_code=args.dart_corp_code,
                dart_bgn_de=args.dart_bgn_de,
                dart_end_de=args.dart_end_de,
                output_dir=artifact_subdir(final_state, "api_evidence"),
                save=not args.no_save,
            )
            final_state.search_queries = evidence_result.get("queries", [])
            final_state.evidence_bundle = evidence_result.get("items", [])
            final_state.query_plan = {
                "source": "query_rewriting",
                "ko_queries": evidence_result.get("ko_queries", []),
                "en_queries": evidence_result.get("en_queries", []),
                "selected_ko_queries": evidence_result.get("queries", []),
                "selected_en_queries": evidence_result.get("gnews_queries", []),
                "rewrite_meta": evidence_result.get("rewrite_meta", {}),
                "search_warnings": evidence_result.get("warnings", []),
            }
            if evidence_result.get("warnings"):
                print("API evidence collection warnings:")
                for warning in evidence_result["warnings"]:
                    print(f"- {warning}")
            if evidence_result.get("saved_collections"):
                print("Saved API evidence files:")
                for path in evidence_result["saved_collections"]:
                    print(f"- {path}")
    saved = {} if args.no_save else save_outputs(
        final_state,
        save_cleaned_markdown=args.save_cleaned_markdown,
    )
    if args.json:
        print(json.dumps(final_state.model_dump(), ensure_ascii=False, indent=2))
    else:
        print_summary(final_state)
        print_saved_outputs(saved)


if __name__ == "__main__":
    main()
