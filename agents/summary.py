from __future__ import annotations

import json
from typing import Any

from app.config import settings
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace
from workflow.state import PatentWorkflowState


@trace(name="summary_agent", run_type="chain")
def run_summary_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    patent = state.preprocessed_patent or {}
    metadata = patent.get("metadata") or {}
    sections = patent.get("sections") or {}
    title = metadata.get("title") or metadata.get("title_eng") or "Untitled patent"
    abstract = sections.get("abstract") or ""
    claim_count = (patent.get("claim_stats") or {}).get("active_claim_count") or len(patent.get("claims") or [])

    plain_summary = abstract or f"{title} 관련 특허입니다."
    summary_result = {
        "title": title,
        "plain_summary": plain_summary,
        "key_points": [
            f"특허명: {title}",
            f"활성 청구항 수: {claim_count}",
        ],
        "notes": [],
    }
    summary_body = run_summary_llm_required(state, summary_result)
    summary_result["summary_markdown"] = build_complete_summary_markdown(patent, summary_body)
    state.summary_result = summary_result
    state.current_stage = "summary_check"
    return state


def run_summary_llm_required(state: PatentWorkflowState, summary_result: dict[str, Any]) -> str:
    if state.user_input.get("use_llm_summary", True) is False:
        raise RuntimeError("LLM summary is required, but use_llm_summary is disabled.")
    markdown = call_llm(
        build_summary_prompt(state=state, summary_result=summary_result),
        model=settings.openai_writing_model,
        reasoning_effort=settings.openai_writing_reasoning_effort,
        verbosity=settings.openai_writing_verbosity,
        timeout=settings.openai_writing_timeout_seconds,
    ).strip()
    if not markdown:
        raise RuntimeError("LLM summary response was empty.")
    return markdown


def build_summary_prompt(*, state: PatentWorkflowState, summary_result: dict[str, Any]) -> str:
    template = load_prompt("summary/summary.md").strip()
    payload = build_summary_input_payload(state=state, summary_result=summary_result)
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def build_summary_input_payload(*, state: PatentWorkflowState, summary_result: dict[str, Any]) -> dict[str, Any]:
    patent = state.preprocessed_patent or {}
    return {
        "patent": {
            "metadata": patent.get("metadata") or {},
            "sections": patent.get("sections") or {},
            "claim_stats": patent.get("claim_stats") or {},
            "claims": [
                {
                    "claim_no": claim.get("claim_no"),
                    "text": str(claim.get("text") or "")[:1200],
                    "is_independent": claim.get("is_independent"),
                }
                for claim in (patent.get("claims") or [])[:5]
            ],
        },
        "draft_summary": {
            key: value
            for key, value in summary_result.items()
            if key != "summary_markdown"
        },
    }


def build_complete_summary_markdown(patent: dict[str, Any], body_markdown: str | None) -> str:
    return "\n\n".join(
        section
        for section in [
            build_summary_basic_info_markdown(patent),
            (body_markdown or "").strip(),
        ]
        if section.strip()
    )


def build_summary_basic_info_markdown(patent: dict[str, Any]) -> str:
    metadata = patent.get("metadata") or {}
    title = normalize_markdown_table_text(metadata.get("title") or metadata.get("title_eng")) or "N/A"
    ipc_cpc = [*(metadata.get("ipc") or []), *(metadata.get("cpc") or [])]
    rows = [
        ("출원번호", metadata.get("application_number")),
        ("등록번호", metadata.get("registration_number")),
        ("출원인/권리자", ", ".join(metadata.get("assignee") or []) or None),
        ("IPC/CPC", "; ".join(ipc_cpc) or None),
        ("출원일", metadata.get("filing_date") or metadata.get("application_date")),
        ("등록일", metadata.get("registration_date")),
    ]
    lines = [
        "# 특허 요약",
        "",
        f"### {title}",
        "",
        "## 기본 정보",
        "",
        "| 항목 | 내용 |",
        "| --- | --- |",
    ]
    for label, value in rows:
        lines.append(f"| {label} | {normalize_markdown_table_text(value) or 'N/A'} |")
    return "\n".join(lines)


def normalize_markdown_table_text(value: Any) -> str:
    return str(value or "").strip().replace("|", "/").replace("\n", " ")
