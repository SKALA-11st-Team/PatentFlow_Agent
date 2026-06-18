from __future__ import annotations

import json
from typing import Any

from app.config import settings
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace
from services.evidence.compression_service import parse_json_object
from workflow.state import PatentWorkflowState


# @author 배세은
# @date 2026-05-06
# @relatedFR FR-005
# @relatedUI UI-005
# @description 특허 내용 요약 생성 에이전트. 전처리된 특허(청구항·초록·섹션)를 LLM으로 요약 마크다운과
# FE 카드용 구조화 요약(summary_brief: 한줄요약·문제·핵심아이디어·구성요소·동작·기대효과)으로 만든다.
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
    summary_result["summary_brief"] = run_summary_brief_llm_required(
        state,
        summary_markdown=summary_result["summary_markdown"],
    )
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


def run_summary_brief_llm_required(
    state: PatentWorkflowState,
    *,
    summary_markdown: str,
) -> dict[str, Any]:
    raw = call_llm(
        build_summary_brief_prompt(state=state, summary_markdown=summary_markdown),
        model=settings.openai_writing_model,
        reasoning_effort=settings.openai_writing_reasoning_effort,
        verbosity=settings.openai_writing_verbosity,
        timeout=settings.openai_writing_timeout_seconds,
    ).strip()
    parsed = parse_json_object(raw)
    if parsed is None:
        raise RuntimeError("LLM summary brief response was not valid JSON.")
    return validate_summary_brief(parsed)


def build_summary_brief_prompt(
    *,
    state: PatentWorkflowState,
    summary_markdown: str,
) -> str:
    template = load_prompt("summary/summary_brief.md").strip()
    payload = {
        "summary_markdown": summary_markdown,
        "patent_structures": build_summary_structure_payload(state),
    }
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def build_summary_input_payload(*, state: PatentWorkflowState, summary_result: dict[str, Any]) -> dict[str, Any]:
    patent = state.preprocessed_patent or {}
    return {
        "patent": {
            "metadata": patent.get("metadata") or {},
            "sections": patent.get("sections") or {},
            "claim_stats": patent.get("claim_stats") or {},
            # 요약이 청구항을 빠짐없이 반영하도록 전체 청구항을 전문으로 전달한다.
            "claims": [
                {
                    "claim_no": claim.get("claim_no"),
                    "text": str(claim.get("text") or ""),
                    "is_independent": claim.get("is_independent"),
                }
                for claim in (patent.get("claims") or [])
            ],
        },
        "patent_structures": build_summary_structure_payload(state),
        "draft_summary": {
            key: value
            for key, value in summary_result.items()
            if key != "summary_markdown"
        },
    }


def build_summary_structure_payload(state: PatentWorkflowState) -> dict[str, Any]:
    return {
        "target": compact_summary_structure(state.target_structure),
        "usage_policy": {
            "target": "대상 특허의 핵심 구성과 처리 흐름 설명에 사용",
            "rule": "구성요소 ID를 노출하지 않고 사업부 담당자가 이해할 수 있는 쉬운 문장으로 설명",
        },
    }


def compact_summary_structure(structure: Any) -> dict[str, Any]:
    if not isinstance(structure, dict):
        return {}
    return {
        "doc_id": structure.get("doc_id"),
        "comparison_source": structure.get("comparison_source"),
        "key_elements": [
            {
                "key_element_id": element.get("key_element_id"),
                "key_element_name": element.get("key_element_name"),
                "why_essential": element.get("why_essential"),
                "core_role": element.get("core_role"),
                "in_independent_claim": element.get("in_independent_claim"),
            }
            for element in (structure.get("key_elements") or [])
            if isinstance(element, dict)
        ],
        "key_flow": [
            {
                "key_element_id": flow.get("key_element_id"),
                "next_key_element_id": flow.get("next_key_element_id"),
                "relation_summary": flow.get("relation_summary"),
                "coupling_strength": flow.get("coupling_strength"),
            }
            for flow in (structure.get("key_flow") or [])
            if isinstance(flow, dict)
        ],
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


def validate_summary_brief(value: dict[str, Any]) -> dict[str, Any]:
    brief = {
        "one_line_summary": normalize_brief_sentence(value.get("one_line_summary")),
        "problem": normalize_brief_sentence(value.get("problem")),
        "core_idea": normalize_brief_sentence(value.get("core_idea")),
        "key_components": normalize_brief_list(value.get("key_components"), minimum=3, maximum=6),
        "operation_steps": normalize_brief_list(value.get("operation_steps"), minimum=3, maximum=5),
        "expected_effect": normalize_brief_sentence(value.get("expected_effect")),
    }
    missing = [key for key, item in brief.items() if not item]
    if missing:
        raise RuntimeError(f"LLM summary brief missing required fields: {', '.join(missing)}")
    return brief


def normalize_brief_sentence(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_brief_list(value: Any, *, minimum: int, maximum: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [normalize_brief_sentence(item) for item in value]
    items = [item for item in items if item][:maximum]
    return items if len(items) >= minimum else []


def build_summary_brief_markdown(brief: dict[str, Any]) -> str:
    lines = [
        "# 특허 이해 요약",
        "",
        "## 한 줄 요약",
        "",
        brief["one_line_summary"],
        "",
        "## 해결하려는 문제",
        "",
        brief["problem"],
        "",
        "## 핵심 아이디어",
        "",
        brief["core_idea"],
        "",
        "## 주요 기술/구성",
        "",
        *[f"- {item}" for item in brief["key_components"]],
        "",
        f"## 작동 방식 {len(brief['operation_steps'])}단계",
        "",
        *[f"{index}. {item}" for index, item in enumerate(brief["operation_steps"], 1)],
        "",
        "## 기대 효과",
        "",
        brief["expected_effect"],
    ]
    return "\n".join(lines)


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
