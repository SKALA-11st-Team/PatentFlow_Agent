from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import settings
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace
from workflow.state import PatentWorkflowState


@trace(name="final_report_agent", run_type="chain")
def run_final_report_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    valuation_result = dict(state.valuation_result or {})
    if not valuation_result:
        raise RuntimeError("Final report generation requires valuation_result.")

    body_markdown = run_final_report_llm_required(state, valuation_result)
    valuation_result["final_report_markdown"] = build_complete_final_report_markdown(
        state,
        body_markdown,
    )
    state.valuation_result = valuation_result
    state.current_stage = "final_check"
    return state


def run_final_report_llm_required(
    state: PatentWorkflowState,
    valuation_result: dict[str, Any],
) -> str:
    if state.user_input.get("use_llm_final_report", True) is False:
        raise RuntimeError("LLM final report is required, but use_llm_final_report is disabled.")
    markdown = sanitize_final_report_markdown(
        call_llm(build_final_report_prompt(state=state, valuation_result=valuation_result)).strip()
    )
    if not markdown:
        raise RuntimeError("LLM final report response was empty.")
    return markdown


def sanitize_final_report_markdown(markdown: str) -> str:
    lines = []
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if re.fullmatch(r"\(?세부\s*점수\s*반영\s*:.*\)?", stripped):
            continue
        if stripped.startswith("[참고 근거]") or stripped.startswith("참고 근거"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def build_final_report_prompt(*, state: PatentWorkflowState, valuation_result: dict[str, Any]) -> str:
    template = load_prompt("writing/final_report.md").strip()
    payload = build_final_report_input_payload(state=state, valuation_result=valuation_result)
    save_final_report_input_payload(state, "final_report_input", payload)
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def build_final_report_input_payload(*, state: PatentWorkflowState, valuation_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "patent": {
            "metadata": final_report_patent_metadata(state),
            "summary_result": state.summary_result,
        },
        "evidence_references": build_evidence_references(state),
        "valuation_result": final_report_valuation_result(valuation_result),
    }


def final_report_valuation_result(valuation_result: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in valuation_result.items()
        if key != "final_report_markdown"
    }
    axes = result.get("axes")
    if not isinstance(axes, dict):
        return result
    sanitized_axes = {}
    for axis, axis_result in axes.items():
        if isinstance(axis_result, dict):
            sanitized_axes[axis] = {
                key: value
                for key, value in axis_result.items()
                if key != "technology_metrics"
            }
        else:
            sanitized_axes[axis] = axis_result
    return {
        **result,
        "axes": sanitized_axes,
    }


def build_complete_final_report_markdown(
    state: PatentWorkflowState,
    body_markdown: str,
) -> str:
    body = (body_markdown or "").strip()
    if not body:
        raise RuntimeError("LLM final report body was empty.")
    return "\n\n".join(
        section
        for section in [
            build_patent_basic_info_markdown(final_report_patent_metadata(state)),
            body,
        ]
        if section.strip()
    )


def build_patent_basic_info_markdown(metadata: dict[str, Any]) -> str:
    title = normalize_text(metadata.get("title")) or "N/A"
    rows = [
        ("관리번호", metadata.get("management_number")),
        ("출원번호", metadata.get("application_number")),
        ("등록번호", metadata.get("registration_number")),
        ("관련 제품", metadata.get("related_product")),
        ("사업 분야", metadata.get("business_area")),
        ("기술 분야", metadata.get("technology_area")),
        ("상태", metadata.get("status")),
        ("출원일", metadata.get("application_date")),
        ("등록일", metadata.get("registration_date")),
        ("예상 소멸일", metadata.get("expected_expiration_date")),
    ]
    lines = [
        "# 특허 가치판단 종합 보고서",
        "",
        f"### {normalize_markdown_table_text(title)}",
        "",
        "## 특허 기본 정보",
        "",
        "| 항목 | 내용 |",
        "| --- | --- |",
    ]
    for label, value in rows:
        lines.append(f"| {label} | {normalize_markdown_table_text(value) or 'N/A'} |")
    return "\n".join(lines)


def final_report_patent_metadata(state: PatentWorkflowState) -> dict[str, Any]:
    patent = state.patent_structured or {}
    kipris_metadata = ((state.kipris_api_data or {}).get("metadata") or {})
    return {
        "patent_id": patent.get("id"),
        "management_number": patent.get("management_number"),
        "application_number": patent.get("application_number") or kipris_metadata.get("application_number"),
        "registration_number": patent.get("registration_number") or kipris_metadata.get("registration_number"),
        "title": patent.get("title_final") or patent.get("title_draft") or kipris_metadata.get("title"),
        "related_product": patent.get("related_product"),
        "business_area": patent.get("business_area"),
        "technology_area": patent.get("technology_area"),
        "status": patent.get("status") or kipris_metadata.get("register_status"),
        "application_date": patent.get("application_date") or kipris_metadata.get("filing_date"),
        "registration_date": patent.get("registration_date") or kipris_metadata.get("registration_date"),
        "expected_expiration_date": patent.get("expected_expiration_date"),
        "assignee": kipris_metadata.get("assignee") or [],
        "ipc": kipris_metadata.get("ipc") or [],
        "cpc": kipris_metadata.get("cpc") or [],
    }


def build_evidence_references(state: PatentWorkflowState) -> list[dict[str, Any]]:
    references = []
    for item in state.evidence_bundle or []:
        if item.get("source_type") not in {"news", "industry_report", "company_disclosure", "portfolio_context"}:
            continue
        references.append(
            {
                "evidence_id": item.get("evidence_id"),
                "source_type": item.get("source_type"),
                "source": item.get("source"),
                "title": item.get("title") or item.get("source"),
                "citation_title": item.get("title") or item.get("source"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "related_axes": item.get("related_axes") or item.get("related_axis") or [],
                "compressed_summary": item.get("compressed_summary"),
                "key_facts": item.get("key_facts") or [],
            }
        )
    return references


def save_final_report_input_payload(state: PatentWorkflowState, name: str, payload: dict[str, Any]) -> Path | None:
    if state.user_input.get("no_save", False):
        return None
    output_dir = final_report_input_output_dir(state)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def final_report_input_output_dir(state: PatentWorkflowState) -> Path:
    artifact_dir = state.user_input.get("artifact_dir")
    if artifact_dir:
        return Path(artifact_dir) / "valuation_inputs"
    return settings.output_dir / "valuation_inputs"


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_markdown_table_text(value: Any) -> str:
    return normalize_text(value).replace("|", "/").replace("\n", " ")
