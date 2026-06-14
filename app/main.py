import argparse
import json
import os
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from app.config import settings
from services.evidence.external_search_service import collect_external_evidence
from workflow.graph import run_workflow
from workflow.state import PatentWorkflowState


LOCAL_JDK_HOME = settings.project_root / ".jdk" / "jdk-17.0.19+10" / "Contents" / "Home"


def configure_local_java() -> None:
    if not LOCAL_JDK_HOME.exists():
        return
    os.environ["JAVA_HOME"] = str(LOCAL_JDK_HOME)
    os.environ["PATH"] = f"{LOCAL_JDK_HOME / 'bin'}:{os.environ.get('PATH', '')}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the patent valuation workflow.")
    parser.add_argument(
        "identifier",
        nargs="?",
        help="Internal patent management number. Example: P202012001-US0.",
    )
    patent_group = parser.add_mutually_exclusive_group()
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
        "--skip-news-evidence",
        action="store_true",
        help="Skip Naver News and GNews collection in the workflow; industry RAG still runs.",
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
        "--use-llm-valuation",
        action="store_true",
        help="Deprecated: LLM valuation is enabled by default.",
    )
    parser.add_argument(
        "--use-llm-final-report",
        action="store_true",
        help="Deprecated: LLM final report generation is enabled by default.",
    )
    parser.add_argument(
        "--no-llm-valuation",
        action="store_true",
        help="Disable LLM prompts for axis-level valuation. Valuation will fail because deterministic fallback is not supported.",
    )
    parser.add_argument(
        "--no-llm-final-report",
        action="store_true",
        help="Disable the LLM final report prompt. Valuation will fail because deterministic fallback is not supported.",
    )
    parser.add_argument(
        "--no-llm-summary",
        action="store_true",
        help="Disable the LLM summary prompt. Summary generation will fail because deterministic fallback is not supported.",
    )
    parser.add_argument(
        "--no-llm-supervisor",
        action="store_true",
        help="Disable LLM judge calls in supervisor nodes. Rule-based supervisor routing still runs.",
    )
    return parser


def build_run_id(args: argparse.Namespace) -> str:
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    identifier = (
        args.identifier
        or args.patent_id
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
        "use_llm_summary": not args.no_llm_summary,
        "use_llm_valuation": not args.no_llm_valuation,
        "use_llm_final_report": not args.no_llm_final_report,
        "use_llm_supervisor": not (
            args.no_llm_supervisor
            or args.no_llm_summary
            or args.no_llm_valuation
            or args.no_llm_final_report
        ),
        "skip_news_evidence": args.skip_news_evidence,
    }
    if args.patent_id is not None:
        user_input["patent_id"] = args.patent_id
    if args.identifier:
        user_input["management_number"] = args.identifier
    if args.application_number:
        user_input["application_number"] = args.application_number
    if args.registration_number:
        user_input["registration_number"] = args.registration_number
    if args.management_number:
        user_input["management_number"] = args.management_number
    return user_input


def validate_identifier_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    explicit_identifiers = [
        args.patent_id is not None,
        bool(args.application_number),
        bool(args.registration_number),
        bool(args.management_number),
    ]
    if args.identifier and any(explicit_identifiers):
        parser.error("positional management number cannot be used together with --patent-id/--application-number/--registration-number/--management-number")
    if not args.identifier and not any(explicit_identifiers):
        parser.error("one patent identifier is required. Use a management number like P202012001-US0 or pass --patent-id/--application-number/--registration-number/--management-number")


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
    if state.portfolio_result:
        portfolio = state.portfolio_result
        stats = portfolio.get("stats") or {}
        print(f"Portfolio evidence output: {portfolio.get('output_path') or 'none'}")
        print(f"Portfolio evidence count: {stats.get('portfolio_evidence_count', 0)}")
        warnings = portfolio.get("warnings") or []
        if warnings:
            print("Portfolio evidence warnings:")
            for warning in warnings:
                print(f"- {warning}")
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

    domestic_queries = query_plan.get("domestic_queries") or []
    en_queries = query_plan.get("en_queries") or []
    selected_domestic_queries = query_plan.get("selected_domestic_queries") or query_plan.get("queries") or []
    selected_en_queries = query_plan.get("selected_en_queries") or query_plan.get("gnews_queries") or []

    print_queries("Generated domestic queries", domestic_queries)
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
    if state.summary_result:
        output_dir = artifact_subdir(state, "summary")
        output_dir.mkdir(parents=True, exist_ok=True)
        patent_id = (
            (state.preprocessed_patent or {}).get("patent_id")
            or (state.patent_structured or {}).get("id")
            or "unknown_patent"
        )
        safe_patent_id = str(patent_id).replace("/", "_")
        summary_path = output_dir / f"{safe_patent_id}_summary.json"
        summary_path.write_text(
            json.dumps(state.summary_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        saved["summary_json"] = summary_path
        markdown = (state.summary_result.get("summary_markdown") or "").strip()
        if markdown:
            markdown_path = output_dir / f"{safe_patent_id}_summary.md"
            markdown_path.write_text(f"{markdown}\n", encoding="utf-8")
            saved["summary_markdown"] = markdown_path
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
        markdown = ((state.final_report.get("valuation") or {}).get("final_report_markdown") or "").strip()
        if markdown:
            markdown_path = output_dir / f"{safe_patent_id}_final_report.md"
            markdown_path.write_text(f"{markdown}\n", encoding="utf-8")
            saved["final_report_markdown"] = markdown_path
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
    configure_local_java()
    parser = build_parser()
    args = parser.parse_args()
    validate_identifier_args(args, parser)
    initial_state = PatentWorkflowState(user_input=build_user_input(args))
    final_state = run_workflow(initial_state)
    if args.collect_api_evidence:
        if final_state.query_plan and final_state.evidence_bundle:
            print("API evidence collection skipped: workflow evidence already exists.")
        elif not final_state.preprocessed_patent:
            print("API evidence collection skipped: preprocessed_patent is missing.")
        else:
            patent = final_state.patent_structured or {}
            evidence_result = collect_external_evidence(
                preprocessed_patent=final_state.preprocessed_patent,
                patent_id=patent.get("id") or final_state.preprocessed_patent.get("patent_id"),
                application_number=patent.get("application_number"),
                api_base_url=args.api_base_url,
                query_limit_per_axis=args.api_query_limit,
                output_dir=artifact_subdir(final_state, "api_evidence"),
                save=not args.no_save,
            )
            final_state.search_queries = evidence_result.get("queries", [])
            final_state.evidence_bundle = evidence_result.get("items", [])
            final_state.query_plan = {
                "source": "query_rewriting",
                "domestic_queries": evidence_result.get("domestic_queries", []),
                "en_queries": evidence_result.get("en_queries", []),
                "selected_domestic_queries": evidence_result.get("queries", []),
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
