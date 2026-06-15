from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import contextvars
import json
import re

from app.config import settings
from services.patent.kipris_patent_service import (
    download_and_parse_patent_pdf,
    fetch_foreign_patent_rights_data,
    fetch_kipris_bibliography,
    get_patent,
    resolve_foreign_prior_art_evidence,
)
from services.patent.markdown_preprocess_service import build_preprocessed_patent
from services.patent.prior_art_patent_service import (
    build_prior_art_patent_context,
    prior_art_context_citation_documents,
)
from services.patent.patent_structuring_service import (
    COMPARISON_TARGET_COUNT,
    structure_target_and_comparisons,
)
from services.patent.portfolio_service import analyze_portfolio_siblings, save_portfolio_evidence_result
from services.evidence.compression_service import (
    DEFAULT_RAG_SCORE_THRESHOLD,
    compress_evidence_items,
    save_compressed_evidence_result,
)
from services.evidence.external_search_service import MAX_SEARCH_QUERIES, collect_external_evidence, rewrite_search_queries
from services.evidence.news_filter_service import filter_news_evidence, save_filtered_news_result
from services.evidence.news_localization import is_foreign_country, resolve_domestic_locale
from services.evidence.skax_site_search_service import collect_skax_site_evidence
from services.evidence.store_service import (
    save_filtered_evidence_bundle,
    save_skax_site_search_result,
)
from services.rag.industry_rag_service import (
    resolve_patent_industries,
    search_and_save_patent_industry_evidence,
)
from services.observability.langsmith_service import trace
from agents.summary import run_summary_agent
from agents.writing.final_report import parse_report_sections, run_final_report_agent
from workflow.state import PatentWorkflowState


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
        country = str(patent.get("country") or "").strip().upper()
        if country and country != "KR":
            state.kipris_api_data = fetch_foreign_patent_rights_data(
                patent,
                output_dir=artifact_subdir(state, "patent_markdown"),
                collect_pdf=True,
            )
            parsed_pdf = state.kipris_api_data.get("parsed_pdf") or {}
            if parsed_pdf:
                state.parsed_pdf = parsed_pdf
                pdf_path = parsed_pdf.get("pdf_path")
                state.pdf_paths = [pdf_path] if pdf_path else []
        else:
            state.kipris_api_data = fetch_kipris_bibliography(patent["application_number"])
        state.kipris_family_patents = state.kipris_api_data.get("family_patents", [])
        state.citation_evidence = state.kipris_api_data.get("citation_evidence", {})
        state.patent_structured = {
            **patent,
            "kipris_api": {
                "source_type": state.kipris_api_data["source_type"],
                "metadata": state.kipris_api_data["metadata"],
                "claim_stats": state.kipris_api_data["claim_stats"],
                "family_patents": state.kipris_family_patents,
                "citation_stats": state.kipris_api_data.get("citation_stats", {}),
                "citing_stats": state.kipris_api_data.get("citing_stats", {}),
                "citing_documents": state.kipris_api_data.get("citing_document_records", []),
            },
        }
    patent_country = str((patent or {}).get("country") or "").strip().upper()
    if patent and state.user_input.get("collect_pdf") and not state.parsed_pdf and patent_country in {"", "KR"}:
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
    country = str((preprocessed.get("metadata") or {}).get("country") or "").upper()
    prior_art = (preprocessed.get("metadata") or {}).get("prior_art") or []
    if country and country != "KR" and prior_art:
        enriched = resolve_foreign_prior_art_evidence(prior_art)
        citation_evidence = {
            **(state.citation_evidence or {}),
            "foreign_claim_lookup_candidates": enriched["foreign_claim_lookup_candidates"],
            "foreign_citation_documents": enriched["foreign_citation_documents"],
            "foreign_identifier_only_documents": enriched["foreign_identifier_only_documents"],
            "prior_art_collection": enriched["prior_art_collection"],
            "warnings": [
                *((state.citation_evidence or {}).get("warnings") or []),
                *enriched["warnings"],
            ],
        }
        state.citation_evidence = citation_evidence
        if state.kipris_api_data is not None:
            state.kipris_api_data["citation_evidence"] = citation_evidence
    state.preprocessed_patent = preprocessed
    if state.parsed_pdf:
        state.parsed_pdf = {key: value for key, value in state.parsed_pdf.items() if key != "markdown_text"}
    state.patent_structured = patent
    state.current_stage = "patent_check"
    return state


@trace(run_type="tool")
def prior_art_fulltext_node(state: PatentWorkflowState) -> PatentWorkflowState:
    if state.prior_art_context is not None:
        return state
    preprocessed = state.preprocessed_patent or {}
    metadata = preprocessed.get("metadata") if isinstance(preprocessed.get("metadata"), dict) else {}
    if not metadata.get("prior_art"):
        state.prior_art_context = {
            "comparison_mode": "prior-art",
            "candidate_count": 0,
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": ["prior_art_candidates_not_found"],
        }
        return state

    artifact_dir = state.user_input.get("artifact_dir") if state.user_input else None
    output_dir = Path(artifact_dir) / "prior_art_patents" if artifact_dir else None
    try:
        home_country = str((state.patent_structured or {}).get("country") or "").strip().upper() or None
        state.prior_art_context = build_prior_art_patent_context(
            target_metadata=metadata,
            kipris_api_data=state.kipris_api_data,
            collect_pdf=bool(output_dir),
            output_dir=output_dir,
            pdf_text_limit=None,
            home_country=home_country,
            target_fulltext_count=COMPARISON_TARGET_COUNT if output_dir else None,
        )
    except Exception as exc:
        state.prior_art_context = {
            "comparison_mode": "prior-art",
            "candidate_count": len(metadata.get("prior_art") or []),
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": [f"prior_art_fulltext_collection_failed:{exc.__class__.__name__}"],
        }
        return state
    fulltext_documents = prior_art_context_citation_documents(state.prior_art_context)
    if fulltext_documents:
        state.citation_evidence = merge_prior_art_citation_evidence(
            state.citation_evidence,
            fulltext_documents,
            candidate_count=int(state.prior_art_context.get("candidate_count") or 0),
        )
        if state.kipris_api_data is not None:
            state.kipris_api_data["citation_evidence"] = state.citation_evidence
    return state


@trace(run_type="tool")
def patent_structuring_node(state: PatentWorkflowState) -> PatentWorkflowState:
    """타깃 + 비교 특허군(선행문헌)을 동일 schema로 구조화한다.

    구조화 결과(target_structure/comparison_structures)는 권리성·기술성이
    element 단위 비교에 쓴다. 비교 특허는 prior_art_context(선행문헌, pdf_text 보유)에서
    직접 가져온다 — 기술성 비교군 조립과는 분리한다.
    """
    if state.target_structure is not None or state.comparison_structures:
        return state

    target_input = build_target_structuring_input(state)
    comparison_inputs = build_comparison_structuring_inputs(state)
    if not target_input and not comparison_inputs:
        return state

    target_structure, comparison_structures, failures = structure_target_and_comparisons(
        target_input=target_input or {},
        comparison_inputs=comparison_inputs,
    )
    state.target_structure = target_structure
    state.comparison_structures = comparison_structures
    save_patent_structures(
        state,
        target_structure,
        comparison_structures,
        failures,
        comparison_input_count=len(comparison_inputs),
    )
    return state


def save_patent_structures(
    state: PatentWorkflowState,
    target_structure: dict | None,
    comparison_structures: list[dict],
    failures: list[dict] | None = None,
    *,
    comparison_input_count: int | None = None,
) -> None:
    """구조화 결과(타깃 + 비교군)를 artifact_dir/patent_structures 아래 JSON으로 저장한다."""
    if state.user_input.get("no_save", False):
        return
    failures = failures or []
    output_dir = artifact_subdir(state, "patent_structures")
    output_dir.mkdir(parents=True, exist_ok=True)

    if target_structure is not None:
        _write_json(output_dir / "target.json", target_structure)

    for index, structure in enumerate(comparison_structures, start=1):
        doc_id = _safe_filename(str((structure or {}).get("doc_id") or f"comparison_{index}"))
        _write_json(output_dir / f"comparison_{index:02d}_{doc_id}.json", structure)

    # 타깃 + 비교군을 한 파일로도 묶어 둔다(전체 조회용 + 누락/실패 추적용).
    _write_json(
        output_dir / "all_structures.json",
        {
            "target": target_structure,
            "comparisons": comparison_structures,
            "target_structured": target_structure is not None,
            "comparison_input_count": comparison_input_count
            if comparison_input_count is not None
            else len(comparison_structures) + len(failures),
            "comparison_structured_count": len(comparison_structures),
            "failure_count": len(failures),
            "failures": failures,
        },
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]", "_", value).strip("_")
    return cleaned[:80] or "unknown"


def build_target_structuring_input(state: PatentWorkflowState) -> dict | None:
    preprocessed = state.preprocessed_patent or {}
    sections = preprocessed.get("sections") if isinstance(preprocessed.get("sections"), dict) else {}
    specification_text = "\n\n".join(
        str(sections.get(key) or "").strip()
        for key in ("solution", "detailed_description", "effect")
        if str(sections.get(key) or "").strip()
    )
    claims_text = str(sections.get("claims_text") or "").strip()
    if not claims_text:
        claims_text = "\n".join(
            str((claim or {}).get("text") or "").strip()
            for claim in (preprocessed.get("claims") or [])
            if str((claim or {}).get("text") or "").strip()
        )
    if not specification_text and not claims_text:
        return None
    structured = state.patent_structured or {}
    doc_id = str(
        structured.get("application_number")
        or structured.get("registration_number")
        or (preprocessed.get("metadata") or {}).get("application_number")
        or ""
    )
    return {
        "doc_id": doc_id,
        "specification_text": specification_text,
        "claims_text": claims_text,
        "drawings_text": str(sections.get("drawings") or "").strip(),
    }


def build_comparison_structuring_inputs(state: PatentWorkflowState) -> list[dict]:
    # 비교 특허는 prior_art_context(선행문헌, pdf_text 보유)에서 직접 가져온다.
    context = state.prior_art_context or {}
    items = context.get("prior_art_patents") or context.get("similar_patents") or []
    inputs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        specification_text = str(item.get("pdf_text") or item.get("pdf_text_excerpt") or "").strip()
        if not specification_text:
            continue
        doc_id = str(item.get("display_number") or item.get("application_number") or item.get("title") or "")
        inputs.append(
            {
                "doc_id": doc_id,
                "specification_text": specification_text,
                "claims_text": "",
                "drawings_text": "",
                "comparison_source": "prior_art",
            }
        )
        if len(inputs) >= COMPARISON_TARGET_COUNT:
            break
    return inputs


def merge_prior_art_citation_evidence(
    evidence: dict | None,
    fulltext_documents: list[dict],
    *,
    candidate_count: int,
) -> dict:
    merged = dict(evidence or {})
    by_number = {
        prior_art_document_key(item): dict(item)
        for item in merged.get("foreign_citation_documents") or []
        if isinstance(item, dict)
    }
    for document in fulltext_documents:
        key = prior_art_document_key(document)
        existing = by_number.get(key, {})
        by_number[key] = {
            **existing,
            **document,
            "abstract": document.get("abstract") or existing.get("abstract"),
            "representative_claims": document.get("representative_claims") or existing.get("representative_claims") or [],
        }
    documents = list(by_number.values())
    for item in documents:
        if item.get("representative_claims"):
            item["comparison_status"] = "claim_comparison_ready"
        elif item.get("comparison_status") in {None, "comparison_ready"} and item.get("abstract"):
            item["comparison_status"] = "abstract_only"
    claim_ready_numbers = {
        prior_art_document_key(item)
        for item in documents
        if item.get("comparison_status") == "claim_comparison_ready"
    }
    abstract_only_numbers = {
        prior_art_document_key(item)
        for item in documents
        if item.get("comparison_status") == "abstract_only"
    }
    claims_unparsed_numbers = {
        prior_art_document_key(item)
        for item in documents
        if item.get("comparison_status") == "fulltext_claims_unparsed"
    }
    resolved_numbers = claim_ready_numbers | abstract_only_numbers | claims_unparsed_numbers
    unresolved = [
        item
        for item in merged.get("foreign_claim_lookup_candidates") or []
        if prior_art_document_key(item) not in resolved_numbers
    ]
    merged.update(
        {
            "foreign_citation_documents": documents,
            "foreign_identifier_only_documents": unresolved,
            "prior_art_collection": {
                "candidate_count": candidate_count,
                "comparison_ready_count": len(claim_ready_numbers),
                "claim_comparison_ready_count": len(claim_ready_numbers),
                "abstract_only_count": len(abstract_only_numbers),
                "fulltext_claims_unparsed_count": len(claims_unparsed_numbers),
                "identifier_only_count": max(0, candidate_count - len(resolved_numbers)),
                "comparison_status": (
                    "claim_comparison_ready"
                    if claim_ready_numbers
                    else "abstract_only"
                    if abstract_only_numbers
                    else "fulltext_claims_unparsed"
                    if claims_unparsed_numbers
                    else "unknown"
                ),
            },
        }
    )
    return merged


def prior_art_document_key(item: dict) -> str:
    display_number = item.get("display_number")
    if display_number:
        return re.sub(r"[^0-9A-Z]", "", str(display_number).upper())
    return "|".join(
        str(item.get(key) or "").upper()
        for key in ("country_code", "document_number", "kind_code")
    )


@trace(run_type="tool")
def final_merge_node(state: PatentWorkflowState) -> PatentWorkflowState:
    state.final_report = {
        "summary": state.summary_result,
        "valuation": state.valuation_result,
        "validation": {
            "summary": state.summary_validation_result,
            "report": state.report_validation_result,
            "aggregate": state.validation_result,
        },
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

    country = (state.patent_structured or {}).get("country")
    is_foreign = is_foreign_country(country)
    domestic_country, domestic_language = resolve_domestic_locale(country)
    rewritten = rewrite_search_queries(
        preprocessed_patent=preprocessed,
        missing_evidence=state.missing_evidence,
        previous_queries=state.search_queries,
        retry_count=state.retry_count,
        use_llm=True,
        domestic_language=domestic_language,
        is_foreign=is_foreign,
    )
    state.search_queries = compact_workflow_queries(
        [
            *state.search_queries,
            *rewritten.get("domestic", []),
            *rewritten.get("en", []),
            *rewritten.get("industry_rag", []),
            *rewritten.get("skax_site", []),
        ]
    )
    state.query_plan = {
        "source": "query_rewriting",
        "domestic_queries": rewritten.get("domestic", []),
        "en_queries": rewritten.get("en", []),
        "industry_rag_queries": rewritten.get("industry_rag", []),
        "skax_site_queries": rewritten.get("skax_site", []),
        "rewrite_meta": rewritten.get("meta", {}),
        "is_foreign": is_foreign,
        "domestic_country": domestic_country,
        "domestic_language": domestic_language,
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
    skip_news_evidence = bool(state.user_input.get("skip_news_evidence"))
    patent_id = patent.get("id") or preprocessed.get("patent_id")
    no_save = state.user_input.get("no_save", False)

    # 뉴스 검색·Industry RAG·SK AX 검색은 서로 독립이므로 동시에 시작한다.
    # 뉴스 필터는 뉴스 검색 결과에 의존하므로 뉴스 작업 안에서 체이닝하고,
    # 세 작업이 모두 끝난 뒤 merge/save 한다.
    def _news_task() -> tuple[dict, dict]:
        result = collect_external_evidence(
            preprocessed_patent=preprocessed,
            patent_id=patent_id,
            application_number=patent.get("application_number"),
            query_limit_per_axis=MAX_SEARCH_QUERIES,
            include_naver=not skip_news_evidence,
            include_gnews=not skip_news_evidence,
            # EVID-02: 경쟁특허 근거(KIPRIS)를 기본 수집한다(application_number 있을 때만 실효).
            include_kipris=state.user_input.get("include_kipris_competitor", True),
            # 해외특허는 domestic 채널을 Tavily(country=대상국, 현지어)로 대체한다.
            is_foreign=bool(query_plan.get("is_foreign")),
            domestic_country=query_plan.get("domestic_country"),
            domestic_queries_override=query_plan.get("domestic_queries") or None,
            en_queries_override=query_plan.get("en_queries") or None,
            output_dir=artifact_subdir(state, "api_evidence"),
            save=not no_save,
        )
        if skip_news_evidence:
            news_filter_result = {
                "kept": [],
                "output_path": None,
                "stats": {"input_count": 0, "kept_count": 0, "rejected_count": 0},
                "warning": None,
            }
        else:
            news_filter_result = filter_news_safely(
                items=[item for item in result.get("items", []) if item.get("source_type") == "news"],
                preprocessed_patent=preprocessed,
                patent_id=patent_id,
                output_dir=artifact_subdir(state, "filtered_evidence") / "news",
                save=not no_save,
            )
        return result, news_filter_result

    def _industry_task() -> dict:
        return search_industry_rag_safely(
            preprocessed_patent=preprocessed,
            patent_id=patent_id,
            rag_queries=query_plan.get("industry_rag_queries", []),
            # EVID-03: 특허 분야→산업 매핑 필터에 patent_structured 컨텍스트를 전달한다.
            patent_context=patent,
            output_dir=artifact_subdir(state, "industry_rag"),
            save=not no_save,
        )

    def _skax_task() -> dict:
        skax_context = build_skax_patent_context_from_state(state)
        return collect_skax_site_evidence_safely(
            skax_context,
            queries_override=query_plan.get("skax_site_queries") or None,
        )

    # copy_context로 현재 LangSmith run tree를 워커 스레드에 전파해 트레이스가
    # 워크플로우 노드 아래에 중첩되게 한다(흩어짐 방지).
    with ThreadPoolExecutor(max_workers=3) as executor:
        news_future = executor.submit(contextvars.copy_context().run, _news_task)
        industry_future = executor.submit(contextvars.copy_context().run, _industry_task)
        skax_future = executor.submit(contextvars.copy_context().run, _skax_task)
        result, news_filter_result = news_future.result()
        industry_result = industry_future.result()
        skax_result = skax_future.result()

    # 세 작업이 모두 끝난 뒤 merge/save.
    state.search_queries = compact_workflow_queries(
        [*state.search_queries, *result.get("queries", []), *result.get("gnews_queries", [])]
    )
    raw_items = result.get("items", [])
    non_news_items = [item for item in raw_items if item.get("source_type") != "news"]
    evidence_items = [*non_news_items, *news_filter_result.get("kept", [])]
    if industry_result.get("items"):
        evidence_items = [*evidence_items, *industry_result["items"]]
    skax_items = skax_result.get("items", [])
    if skax_items:
        evidence_items = merge_evidence_items(evidence_items, skax_items)
    filtered_evidence_path = save_filtered_evidence_safely(
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        news_items=news_filter_result.get("kept", []),
        industry_items=industry_result.get("items", []),
        other_items=[*non_news_items, *skax_items],
        output_dir=artifact_subdir(state, "filtered_evidence"),
        save=not state.user_input.get("no_save", False),
    )
    # 뉴스처럼 SK AX 검색의 raw 결과·진단(실제 전송 쿼리·후보 URL·필터 통계)을 별도 artifact로 남긴다.
    skax_search_path = save_skax_site_search_safely(
        patent_id=patent.get("id") or preprocessed.get("patent_id"),
        skax_result=skax_result,
        output_dir=artifact_subdir(state, "skax_site_search"),
        save=not no_save,
    )
    state.evidence_bundle = evidence_items
    state.query_plan = {
        **query_plan,
        "selected_domestic_queries": result.get("queries", []),
        "selected_en_queries": result.get("gnews_queries", []),
        "search_warnings": result.get("warnings", []),
        "rewrite_meta": result.get("rewrite_meta", query_plan.get("rewrite_meta", {})),
        "news_filter": {
            "output_path": news_filter_result.get("output_path"),
            "stats": news_filter_result.get("stats", {}),
            "warning": news_filter_result.get("warning"),
        },
        "industry_rag": {
            "query": industry_result.get("query"),
            "queries": industry_result.get("queries", []),
            "industries": industry_result.get("industries"),
            "output_path": industry_result.get("output_path"),
            "warning": industry_result.get("warning"),
            "item_count": len(industry_result.get("items", [])),
        },
        "skax_site_search": {
            "queries": skax_result.get("queries", []),
            "stats": skax_result.get("stats", {}),
            "item_count": len(skax_items),
            "failed_urls": skax_result.get("failed_urls", []),
            "warning": skax_result.get("warning"),
            "output_path": skax_search_path,
        },
        # EXT-03: 외부 게이트웨이 호출이 전부 실패해 증거 0건이면 missing_reason으로 표면화한다.
        "external_evidence": {
            "attempted_calls": result.get("attempted_calls", 0),
            "failed_calls": result.get("failed_calls", 0),
            "gateway_unreachable": result.get("gateway_unreachable", False),
            "missing_reason": result.get("missing_reason"),
        },
        "filtered_evidence": {
            "output_path": filtered_evidence_path,
            "news_count": len(news_filter_result.get("kept", [])),
            "industry_report_count": len(industry_result.get("items", [])),
            "other_count": len(non_news_items) + len(skax_items),
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


def build_skax_patent_context_from_state(state: PatentWorkflowState) -> dict[str, str]:
    patent = state.patent_structured or {}
    return {
        "management_number": first_non_empty_text(patent.get("management_number"), patent.get("관리번호")),
        "title_final": first_non_empty_text(patent.get("title_final"), patent.get("발명의 명칭(최종)")),
        "title_draft": first_non_empty_text(patent.get("title_draft"), patent.get("발명의 명칭(가제)")),
        "business_area": first_non_empty_text(
            patent.get("business_area"),
            patent.get("관련 사업 분야"),
            patent.get("관련사업 분야"),
        ),
        "technology_area": first_non_empty_text(
            patent.get("technology_area"),
            patent.get("관련 기술 분야"),
            patent.get("관련기술 분야"),
        ),
        "related_product": first_non_empty_text(patent.get("related_product"), patent.get("관련제품")),
    }


def collect_skax_site_evidence_safely(
    patent_context: dict[str, str],
    *,
    queries_override: list[str] | None = None,
) -> dict:
    if not has_skax_search_context(patent_context):
        return {
            "items": [],
            "queries": [],
            "stats": {},
            "failed_urls": [],
            "warning": "skax_site_search_skipped:missing_patent_context",
        }
    try:
        return collect_skax_site_evidence(
            patent_context,
            queries_override=queries_override,
            include_related_media=True,
        )
    except Exception as exc:
        return {
            "items": [],
            "queries": [],
            "stats": {},
            "failed_urls": [],
            "warning": f"skax_site_search_failed:{exc.__class__.__name__}:{str(exc)[:200]}",
        }


def has_skax_search_context(patent_context: dict[str, str]) -> bool:
    return any(
        patent_context.get(key)
        for key in ("title_final", "related_product", "business_area", "technology_area")
    )


def merge_evidence_items(existing: list[dict], extra: list[dict]) -> list[dict]:
    merged = list(existing)
    seen = set()
    for item in merged:
        seen.update(evidence_identity_values(item))
    for item in extra:
        identities = evidence_identity_values(item)
        if identities and any(identity in seen for identity in identities):
            continue
        merged.append(item)
        seen.update(identities)
    return merged


def evidence_identity_values(item: dict) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    evidence_id = first_non_empty_text(item.get("evidence_id"))
    url = first_non_empty_text(item.get("url"))
    title = first_non_empty_text(item.get("title"))
    source = first_non_empty_text(item.get("source"))
    if evidence_id:
        identities.add(("evidence_id", evidence_id))
    if url:
        identities.add(("url", url.lower()))
    if title and source:
        identities.add(("title_source", f"{title.lower()}|{source.lower()}"))
    return identities


def first_non_empty_text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = " ".join(str(value).split())
        if text:
            return text
    return ""


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
    # SK AX 공식 근거(company_disclosure)도 이제 압축 단계를 함께 거친다(요약 + 관련성
    # 판단). skax_items는 압축이 통째로 실패했을 때 원문을 보존하기 위한 폴백용이다.
    skax_items = [item for item in items if is_skax_official_evidence(item)]
    try:
        result = compress_evidence_items(
            items,
            preprocessed_patent=preprocessed_patent,
            rag_score_threshold=DEFAULT_RAG_SCORE_THRESHOLD,
        )
        merged_items = merge_evidence_items(result.get("items", []), portfolio_items)
        result = {
            **result,
            "items": merged_items,
            "stats": {
                **(result.get("stats") or {}),
                "portfolio_evidence_count": len(portfolio_items),
                "skax_official_evidence_count": len(skax_items),
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
        fallback_items = merge_evidence_items(skax_items, portfolio_items)
        return {
            "items": fallback_items,
            "stats": {
                "input_count": len(items),
                "candidate_count": 0,
                "compressed_count": 0,
                "rag_score_threshold": DEFAULT_RAG_SCORE_THRESHOLD,
                "skax_official_evidence_count": len(skax_items),
                "portfolio_evidence_count": len(portfolio_items),
            },
            "warnings": [f"evidence_compression_failed:{exc.__class__.__name__}:{str(exc)[:200]}"],
            "output_path": None,
        }


def is_skax_official_evidence(item: dict) -> bool:
    # SK AX 사이트 검색 근거는 source(sk_ax_official) 또는 evidence_id(skax_site_*)로
    # 식별한다. 압축 후에도 두 필드는 보존되므로 related_axes 태그는 더 이상 쓰지 않는다.
    if first_non_empty_text(item.get("source")) == "sk_ax_official":
        return True
    evidence_id = first_non_empty_text(item.get("evidence_id"))
    return evidence_id.startswith("skax_site_")


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


def save_skax_site_search_safely(
    *,
    patent_id: str | int | None,
    skax_result: dict,
    output_dir: Path,
    save: bool,
) -> str | None:
    if not save:
        return None
    try:
        return str(
            save_skax_site_search_result(
                patent_id=patent_id,
                skax_result=skax_result,
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
    rag_queries: list[str] | None = None,
    patent_context: dict | None = None,
    output_dir: Path,
    save: bool,
) -> dict:
    try:
        # EVID-03: 특허 분야를 코퍼스 산업 라벨로 매핑해 검색을 해당 산업(+공통)으로 한정한다.
        # 매핑 실패 시 None → 산업 필터 없이 기존처럼 전체 검색으로 안전 폴백한다.
        industries = resolve_patent_industries(patent_context, preprocessed_patent)
        return search_and_save_patent_industry_evidence(
            preprocessed_patent=preprocessed_patent,
            patent_id=patent_id,
            rag_queries=rag_queries,
            industries=industries,
            top_k=settings.industry_rag_top_k,
            output_dir=output_dir,
            save=save,
        )
    except Exception as exc:
        return {
            "query": None,
            "queries": [],
            "items": [],
            "industries": None,
            "output_path": None,
            "warning": f"industry_rag_failed:{exc.__class__.__name__}",
        }


def artifact_subdir(state: PatentWorkflowState, name: str) -> Path:
    artifact_dir = state.user_input.get("artifact_dir")
    if artifact_dir:
        return Path(artifact_dir) / name
    return settings.output_dir / name


@trace(run_type="tool")
def final_report_node(state: PatentWorkflowState) -> PatentWorkflowState:
    return run_final_report_agent(state)


@trace(run_type="tool")
def summary_validation_node(state: PatentWorkflowState) -> PatentWorkflowState:
    summary = state.summary_result or {}
    passed = bool(summary.get("summary_markdown"))
    state.summary_validation_result = {
        "passed": passed,
        "issues": [] if passed else ["Missing summary_markdown"],
    }
    state.current_stage = "final_check"
    return state


REQUIRED_REPORT_SECTION_NUMBERS = [str(index) for index in range(1, 7)]


@trace(run_type="tool")
def report_validation_node(state: PatentWorkflowState) -> PatentWorkflowState:
    valuation = state.valuation_result or {}
    axes = valuation.get("axes") or {}
    required_axes = ["legal", "technology", "market", "business_fit"]
    issues = []
    markdown = valuation.get("final_report_markdown") or ""
    if not markdown:
        issues.append("Missing final_report_markdown")
    missing_axes = [axis for axis in required_axes if not axes.get(axis)]
    issues.extend(f"Missing valuation axis: {axis}" for axis in missing_axes)
    if "strategy" in axes:
        issues.append("Deprecated valuation axis present: strategy")
    if markdown:
        # 섹션 존재 판정을 API의 build_report_sections와 동일한 파서(parse_report_sections)로 통일한다.
        # 검증은 통과하는데 추출에선 조용히 빠지는(또는 반대) 어긋남을 없애, 누락 시 supervisor 재생성 루프가 확실히 잡게 한다.
        present_sections = set(parse_report_sections(markdown))
        missing_sections = [f"## {number}." for number in REQUIRED_REPORT_SECTION_NUMBERS if number not in present_sections]
        if missing_sections:
            issues.append(f"Final report missing required sections: {', '.join(missing_sections)}")
        recommendation = str(valuation.get("recommendation") or "").strip()
        if recommendation:
            match = re.search(
                r"(?m)^\|\s*종합 검토 의견\s*\|\s*([^|]+?)\s*\|$",
                markdown,
            )
            report_recommendation = match.group(1).strip() if match else None
            if report_recommendation != recommendation:
                issues.append(
                    f"Final report recommendation does not match valuation recommendation ({recommendation})"
                )
        country = str((state.patent_structured or {}).get("country") or "").strip().upper()
        if country and country != "KR":
            if re.search(r"국내\s*(?:권|특허|출원|등록)", markdown):
                issues.append("Foreign patent report contains domestic-patent wording")
            if "권리범위 참고도 및 이해" in markdown:
                issues.append("Foreign patent report must omit the representative-drawing rights-scope block")
    # Forbidden expressions / evaluator tone are judged by the LLM final check
    # (it reads the report body), not by brittle substring matching here.
    passed = not issues
    state.report_validation_result = {
        "passed": passed,
        "needs_more_evidence": not passed,
        "issues": issues,
    }
    state.current_stage = "final_check"
    return state
