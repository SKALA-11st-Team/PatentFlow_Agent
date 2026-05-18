from pathlib import Path

from app.config import settings
from services.patent.kipris_patent_service import (
    download_and_parse_patent_pdf,
    fetch_kipris_bibliography,
    get_patent,
)
from services.patent.markdown_preprocess_service import build_preprocessed_patent
from services.patent.portfolio_service import analyze_portfolio_siblings, save_portfolio_evidence_result
from services.evidence.compression_service import (
    DEFAULT_RAG_SCORE_THRESHOLD,
    compress_evidence_items,
    save_compressed_evidence_result,
)
from services.evidence.external_search_service import MAX_SEARCH_QUERIES, collect_external_evidence, rewrite_search_queries
from services.evidence.news_filter_service import filter_news_evidence, save_filtered_news_result
from services.evidence.store_service import save_filtered_evidence_bundle
from services.rag.industry_rag_service import search_and_save_patent_industry_evidence
from services.observability.langsmith_service import trace
from agents.summary import run_summary_agent
from agents.valuation import run_valuation_agent
from workflow.state import PatentWorkflowState


@trace(run_type="tool")
def patent_resolve_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.current_stage = "patent_check"
    return state


@trace(run_type="tool")
def patent_fetch_node(state: PatentWorkflowState) -> PatentWorkflowState:
    patent = get_patent(
        patent_id=state.user_input.get("patent_id"),
        application_number=state.user_input.get("application_number"),
        registration_number=state.user_input.get("registration_number"),
        management_number=state.user_input.get("management_number"),
    )
    state.patent_structured = patent
    if patent and (state.user_input.get("collect_kipris_api") or state.user_input.get("collect_pdf")):
        state.kipris_api_data = fetch_kipris_bibliography(patent["application_number"])
        state.kipris_family_patents = state.kipris_api_data.get("family_patents", [])
        state.patent_structured = {
            **patent,
            "kipris_api": {
                "source_type": state.kipris_api_data["source_type"],
                "metadata": state.kipris_api_data["metadata"],
                "claim_stats": state.kipris_api_data["claim_stats"],
                "family_patents": state.kipris_family_patents,
            },
        }
    if patent and state.user_input.get("collect_pdf"):
        try:
            parsed_pdf = download_and_parse_patent_pdf(
                patent["application_number"],
                output_dir=artifact_subdir(state, "patent_markdown"),
                prefer_announcement=patent.get("status") == "등록",
            )
            state.parsed_pdf = parsed_pdf
            state.pdf_paths = [parsed_pdf["pdf_path"]]
            state.patent_structured = {
                **(state.patent_structured or patent),
                "pdf": {
                    "selected_type": parsed_pdf["selected_type"],
                    "doc_name": parsed_pdf["doc_name"],
                    "pdf_path": parsed_pdf["pdf_path"],
                    "markdown_paths": parsed_pdf["markdown_paths"],
                },
            }
        except Exception as exc:
            state.patent_structured = {
                **(state.patent_structured or patent),
                "pdf": {
                    "warning": f"pdf_fetch_failed:{exc.__class__.__name__}:{str(exc)[:300]}",
                },
            }
    state.current_stage = "patent_check"
    return state


@trace(run_type="tool")
def portfolio_sibling_node(state: PatentWorkflowState) -> PatentWorkflowState:
    if not should_collect_portfolio(state):
        return state

    patent = state.patent_structured or {}
    if not patent:
        return state

    result = analyze_portfolio_siblings_safely(
        target_patent=patent,
        target_api_data=state.kipris_api_data,
        patent_id=patent.get("id"),
        output_dir=artifact_subdir(state, "portfolio_evidence"),
        save=not state.user_input.get("no_save", False),
    )
    state.portfolio_evidence = result.get("items", [])
    state.portfolio_result = {
        "output_path": result.get("output_path"),
        "stats": result.get("stats", {}),
        "warnings": result.get("warnings", []),
    }
    return state


def should_collect_portfolio(state: PatentWorkflowState) -> bool:
    if "collect_portfolio" in state.user_input:
        return bool(state.user_input.get("collect_portfolio"))
    return bool(state.user_input.get("collect_kipris_api"))


def analyze_portfolio_siblings_safely(
    *,
    target_patent: dict,
    target_api_data: dict | None = None,
    patent_id: str | int | None,
    output_dir: Path,
    save: bool,
) -> dict:
    try:
        result = analyze_portfolio_siblings(target_patent=target_patent, target_api_data=target_api_data)
        output_path = None
        if save:
            output_path = save_portfolio_evidence_result(
                patent_id=patent_id,
                result=result,
                output_dir=output_dir,
            )
        return {
            **result,
            "output_path": str(output_path) if output_path else None,
        }
    except Exception as exc:
        return {
            "items": [],
            "stats": {
                "candidate_count": 0,
                "enriched_count": 0,
                "portfolio_evidence_count": 0,
                "group_size": 1 if target_patent else 0,
            },
            "warnings": [f"portfolio_analysis_failed:{exc.__class__.__name__}:{str(exc)[:200]}"],
            "output_path": None,
        }


@trace(run_type="tool")
def common_preprocess_node(state: PatentWorkflowState) -> PatentWorkflowState:
    if not state.parsed_pdf and not state.kipris_api_data:
        state.current_stage = "patent_check"
        return state

    patent = state.patent_structured or {}
    parsed_pdf = state.parsed_pdf or {}
    markdown_paths = parsed_pdf.get("markdown_paths") or []
    source = {
        "file_name": markdown_paths[0].split("/")[-1] if markdown_paths else None,
        "application_number": patent.get("application_number"),
        "registration_number": patent.get("registration_number"),
        "pdf_path": parsed_pdf.get("pdf_path"),
        "markdown_paths": markdown_paths,
    }
    preprocessed = build_preprocessed_patent(
        parsed_pdf.get("markdown_text", ""),
        source=source,
        db_metadata=patent,
        api_data=state.kipris_api_data,
    )
    state.preprocessed_patent = preprocessed
    if state.parsed_pdf:
        state.parsed_pdf = {key: value for key, value in state.parsed_pdf.items() if key != "markdown_text"}
    state.patent_structured = patent
    state.current_stage = "patent_check"
    return state


@trace(run_type="tool")
def final_merge_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.final_report = {
        "summary": state.summary_result,
        "valuation": state.valuation_result,
        "evidence": state.evidence_bundle,
    }
    return state


@trace(run_type="tool")
def summary_node(state: PatentWorkflowState) -> PatentWorkflowState:
    return run_summary_agent(state)


@trace(run_type="tool")
def query_rewriting_node(state: PatentWorkflowState) -> PatentWorkflowState:
    preprocessed = state.preprocessed_patent or {}
    if not preprocessed:
        state.current_stage = "evidence_check"
        return state

    rewritten = rewrite_search_queries(
        preprocessed_patent=preprocessed,
        missing_evidence=state.missing_evidence,
        previous_queries=state.search_queries,
        retry_count=state.retry_count,
        use_llm=True,
    )
    state.search_queries = compact_workflow_queries(
        [*state.search_queries, *rewritten.get("ko", []), *rewritten.get("en", [])]
    )
    state.query_plan = {
        "source": "query_rewriting",
        "ko_queries": rewritten.get("ko", []),
        "en_queries": rewritten.get("en", []),
        "rewrite_meta": rewritten.get("meta", {}),
    }
    state.current_stage = "query_rewriting"
    return state


@trace(run_type="tool")
def evidence_search_node(state: PatentWorkflowState) -> PatentWorkflowState:
    preprocessed = state.preprocessed_patent or {}
    patent = state.patent_structured or {}
    if not preprocessed:
        state.current_stage = "evidence_check"
        return state

    query_plan = state.query_plan or {}
    result = collect_external_evidence(
        preprocessed_patent=preprocessed,
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        application_number=patent.get("application_number"),
        query_limit_per_axis=MAX_SEARCH_QUERIES,
        include_kipris=False,
        ko_queries_override=query_plan.get("ko_queries", []),
        en_queries_override=query_plan.get("en_queries", []),
        output_dir=artifact_subdir(state, "api_evidence"),
        save=not state.user_input.get("no_save", False),
    )
    state.search_queries = compact_workflow_queries(
        [*state.search_queries, *result.get("queries", []), *result.get("gnews_queries", [])]
    )
    raw_items = result.get("items", [])
    news_filter_result = filter_news_safely(
        items=[item for item in raw_items if item.get("source_type") == "news"],
        preprocessed_patent=preprocessed,
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        output_dir=artifact_subdir(state, "filtered_evidence") / "news",
        save=not state.user_input.get("no_save", False),
    )
    non_news_items = [item for item in raw_items if item.get("source_type") != "news"]
    evidence_items = [*non_news_items, *news_filter_result.get("kept", [])]
    industry_result = search_industry_rag_safely(
        preprocessed_patent=preprocessed,
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        output_dir=artifact_subdir(state, "industry_rag"),
        save=not state.user_input.get("no_save", False),
    )
    if industry_result.get("items"):
        evidence_items = [*evidence_items, *industry_result["items"]]
    filtered_evidence_path = save_filtered_evidence_safely(
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        news_items=news_filter_result.get("kept", []),
        industry_items=industry_result.get("items", []),
        other_items=non_news_items,
        output_dir=artifact_subdir(state, "filtered_evidence"),
        save=not state.user_input.get("no_save", False),
    )
    state.evidence_bundle = evidence_items
    state.query_plan = {
        **query_plan,
        "selected_ko_queries": result.get("queries", []),
        "selected_en_queries": result.get("gnews_queries", []),
        "search_warnings": result.get("warnings", []),
        "news_filter": {
            "output_path": news_filter_result.get("output_path"),
            "stats": news_filter_result.get("stats", {}),
            "warning": news_filter_result.get("warning"),
        },
        "industry_rag": {
            "query": industry_result.get("query"),
            "output_path": industry_result.get("output_path"),
            "warning": industry_result.get("warning"),
        },
        "filtered_evidence": {
            "output_path": filtered_evidence_path,
            "news_count": len(news_filter_result.get("kept", [])),
            "industry_report_count": len(industry_result.get("items", [])),
            "other_count": len(non_news_items),
        },
    }
    state.retry_count += 1
    state.current_stage = "evidence_check"
    return state


def compact_workflow_queries(queries: list[str]) -> list[str]:
    compacted: list[str] = []
    seen: set[str] = set()
    for query in queries:
        value = " ".join(str(query or "").split())
        if not value or value in seen:
            continue
        seen.add(value)
        compacted.append(value)
    return compacted


@trace(run_type="tool")
def evidence_compression_node(state: PatentWorkflowState) -> PatentWorkflowState:
    preprocessed = state.preprocessed_patent or {}
    patent = state.patent_structured or {}
    result = compress_evidence_safely(
        items=state.evidence_bundle,
        portfolio_items=state.portfolio_evidence,
        preprocessed_patent=preprocessed,
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        output_dir=artifact_subdir(state, "compressed_evidence"),
        save=not state.user_input.get("no_save", False),
    )
    state.evidence_bundle = result.get("items", [])
    state.query_plan = {
        **(state.query_plan or {}),
        "compressed_evidence": {
            "output_path": result.get("output_path"),
            "stats": result.get("stats", {}),
            "warnings": result.get("warnings", []),
            "rag_score_threshold": DEFAULT_RAG_SCORE_THRESHOLD,
        },
    }
    state.current_stage = "evidence_check"
    return state


def compress_evidence_safely(
    *,
    items: list[dict],
    portfolio_items: list[dict],
    preprocessed_patent: dict,
    patent_id: str | int | None,
    output_dir: Path,
    save: bool,
) -> dict:
    try:
        result = compress_evidence_items(
            items,
            preprocessed_patent=preprocessed_patent,
            rag_score_threshold=DEFAULT_RAG_SCORE_THRESHOLD,
        )
        result = {
            **result,
            "items": [*result.get("items", []), *portfolio_items],
            "stats": {
                **(result.get("stats") or {}),
                "portfolio_evidence_count": len(portfolio_items),
            },
        }
        output_path = None
        if save:
            output_path = save_compressed_evidence_result(
                patent_id=patent_id,
                result=result,
                output_dir=output_dir,
            )
        return {
            **result,
            "output_path": str(output_path) if output_path else None,
        }
    except Exception as exc:
        return {
            "items": [],
            "stats": {
                "input_count": len(items),
                "candidate_count": 0,
                "compressed_count": 0,
                "rag_score_threshold": DEFAULT_RAG_SCORE_THRESHOLD,
            },
            "warnings": [f"evidence_compression_failed:{exc.__class__.__name__}:{str(exc)[:200]}"],
            "output_path": None,
        }


def save_filtered_evidence_safely(
    *,
    patent_id: str | int | None,
    news_items: list[dict],
    industry_items: list[dict],
    other_items: list[dict],
    output_dir: Path,
    save: bool,
) -> str | None:
    if not save:
        return None
    try:
        return str(
            save_filtered_evidence_bundle(
                patent_id=patent_id,
                news_items=news_items,
                industry_items=industry_items,
                other_items=other_items,
                output_dir=output_dir,
            )
        )
    except Exception:
        return None


def filter_news_safely(
    *,
    items: list[dict],
    preprocessed_patent: dict,
    patent_id: str | int | None,
    output_dir: Path,
    save: bool,
) -> dict:
    try:
        result = filter_news_evidence(items, preprocessed_patent=preprocessed_patent)
        output_path = None
        if save:
            output_path = save_filtered_news_result(
                patent_id=patent_id,
                result=result,
                output_dir=output_dir,
            )
        return {
            **result,
            "output_path": str(output_path) if output_path else None,
            "warning": None,
        }
    except Exception as exc:
        return {
            "kept": [],
            "rejected": [],
            "stats": {"input_count": len(items), "kept_count": 0, "rejected_count": len(items)},
            "output_path": None,
            "warning": f"news_filter_failed:{exc.__class__.__name__}",
        }


def search_industry_rag_safely(
    *,
    preprocessed_patent: dict,
    patent_id: str | int | None,
    output_dir: Path,
    save: bool,
) -> dict:
    try:
        return search_and_save_patent_industry_evidence(
            preprocessed_patent=preprocessed_patent,
            patent_id=patent_id,
            top_k=3,
            output_dir=output_dir,
            save=save,
        )
    except Exception as exc:
        return {
            "query": None,
            "items": [],
            "output_path": None,
            "warning": f"industry_rag_failed:{exc.__class__.__name__}",
        }


def artifact_subdir(state: PatentWorkflowState, name: str) -> Path:
    artifact_dir = state.user_input.get("artifact_dir")
    if artifact_dir:
        return Path(artifact_dir) / name
    return settings.output_dir / name


@trace(run_type="tool")
def valuation_node(state: PatentWorkflowState) -> PatentWorkflowState:
    return run_valuation_agent(state)


@trace(run_type="tool")
def validation_node(state: PatentWorkflowState) -> PatentWorkflowState:
    valuation = state.valuation_result or {}
    axes = valuation.get("axes") or {}
    required_axes = ["legal", "technology", "market", "business_fit"]
    passed = all(axes.get(axis) for axis in required_axes) and "strategy" not in axes
    state.validation_result = {
        "passed": passed,
        "needs_more_evidence": not passed,
        "issues": [] if passed else ["valuation_result is incomplete"],
    }
    state.current_stage = "final_check"
    return state
