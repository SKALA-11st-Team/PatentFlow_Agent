from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.valuation_axes.market import build_invention_market_linkage_context
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
    report_markdown = build_complete_final_report_markdown(state, body_markdown)
    valuation_result["final_report_markdown"] = report_markdown
    state.valuation_result = valuation_result
    state.current_stage = "final_check"
    return state


def run_final_report_llm_required(
    state: PatentWorkflowState,
    valuation_result: dict[str, Any],
) -> str:
    if state.user_input.get("use_llm_final_report", True) is False:
        raise RuntimeError("LLM final report is required, but use_llm_final_report is disabled.")
    rights_scope_context = build_rights_scope_context(state)
    markdown = sanitize_final_report_markdown(
        call_llm(
            build_final_report_prompt(state=state, valuation_result=valuation_result),
            model=settings.openai_writing_model,
            reasoning_effort=settings.openai_writing_reasoning_effort,
            verbosity=settings.openai_writing_verbosity,
            timeout=settings.openai_writing_timeout_seconds,
        ).strip(),
        include_rights_scope_reference=bool(
            (rights_scope_context or {}).get("representative_drawing")
        ),
    )
    if not markdown:
        raise RuntimeError("LLM final report response was empty.")
    return markdown


def sanitize_final_report_markdown(
    markdown: str,
    *,
    include_rights_scope_reference: bool = True,
) -> str:
    lines = []
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if re.fullmatch(r"\(?세부\s*점수\s*반영\s*:.*\)?", stripped):
            continue
        if stripped.startswith("[참고 근거]") or stripped.startswith("참고 근거"):
            continue
        lines.append(line)
    sanitized = "\n".join(lines).strip()
    if not include_rights_scope_reference:
        sanitized = re.sub(
            r"\n*\*\*권리범위 참고도 및 이해\*\*\s*.*?(?=^### 4\.1 권리성\s*$)",
            "\n\n",
            sanitized,
            flags=re.DOTALL | re.MULTILINE,
        )
    return sanitized.strip()


def build_final_report_prompt(*, state: PatentWorkflowState, valuation_result: dict[str, Any]) -> str:
    template = load_prompt("writing/final_report.md").strip()
    payload = build_final_report_input_payload(state=state, valuation_result=valuation_result)
    save_final_report_input_payload(state, "final_report_input", payload)
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def build_final_report_input_payload(*, state: PatentWorkflowState, valuation_result: dict[str, Any]) -> dict[str, Any]:
    patent = {
        "metadata": final_report_patent_metadata(state),
        "summary_result": state.summary_result,
        "invention_market_linkage_context": build_invention_market_linkage_context(state),
    }
    rights_scope_context = build_rights_scope_context(state)
    if rights_scope_context:
        patent["rights_scope_context"] = rights_scope_context

    return {
        "patent": patent,
        "evidence_references": build_evidence_references(state, valuation_result),
        "valuation_result": compact_final_report_valuation_result(valuation_result),
    }


def build_rights_scope_context(state: PatentWorkflowState) -> dict[str, Any] | None:
    country = normalize_text((state.patent_structured or {}).get("country")).upper()
    if country != "KR":
        return None

    drawing_context = (state.preprocessed_patent or {}).get("drawing_context")
    if not isinstance(drawing_context, dict):
        return None

    context: dict[str, Any] = {}
    figure_description = normalize_text(drawing_context.get("figure_description"))
    if figure_description:
        context["figure_description"] = figure_description

    representative_detail = normalize_text(drawing_context.get("representative_figure_detail"))
    if representative_detail:
        context["representative_figure_detail"] = representative_detail

    representative = drawing_context.get("representative_drawing")
    if isinstance(representative, dict):
        image_markdown = build_representative_drawing_markdown(state, representative)
        if image_markdown:
            context["representative_drawing"] = {
                "figure_number": normalize_text(representative.get("figure_number")) or "대표도",
                "image_markdown": image_markdown,
            }

    return context or None


def build_representative_drawing_markdown(
    state: PatentWorkflowState,
    representative: dict[str, Any],
) -> str | None:
    image_path = normalize_text(representative.get("image_path"))
    if not image_path:
        return None

    display_path = image_path
    markdown_path = normalize_text(representative.get("markdown_path"))
    artifact_dir = normalize_text((state.user_input or {}).get("artifact_dir"))
    if markdown_path and artifact_dir:
        absolute_image_path = Path(markdown_path).parent / image_path
        final_dir = Path(artifact_dir) / "final"
        display_path = os.path.relpath(absolute_image_path, final_dir)

    display_path = display_path.replace("\\", "/")
    if looks_like_local_path(display_path) and not display_path.startswith("../"):
        return None

    figure_number = normalize_text(representative.get("figure_number")) or "도면"
    return f"![권리범위 참고도 {figure_number}]({display_path})"


def compact_final_report_valuation_result(valuation_result: dict[str, Any]) -> dict[str, Any]:
    axes = valuation_result.get("axes") or {}
    return {
        key: valuation_result.get(key)
        for key in (
            "total_score",
            "average_score",
            "recommendation",
            "decision_rationale",
            "required_actions",
            "missing_information",
            "review_checklist",
        )
        if key in valuation_result
    } | {
        "axes": {
            axis: compact_final_report_axis_result(axis_result)
            for axis, axis_result in axes.items()
            if isinstance(axis_result, dict)
        }
    }


def compact_final_report_axis_result(axis_result: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: axis_result.get(key)
        for key in (
            "axis",
            "label",
            "score",
            "grade",
            "rationale",
            "evidence_ids",
            "risk_factors",
            "missing_information",
            "confidence",
        )
        if key in axis_result
    }
    if isinstance(axis_result.get("subscores"), dict):
        compact["subscores"] = compact_mapping(axis_result["subscores"])
    for key in (
        "technical_differentiation_score",
        "implementation_specificity_score",
        "technical_differentiation_breakdown",
        "implementation_specificity_breakdown",
        "industry_marketability_score",
        "industry_marketability_breakdown",
    ):
        if key in axis_result:
            compact[key] = compact_mapping(axis_result[key]) if isinstance(axis_result[key], dict) else axis_result[key]
    return compact


def compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): compact_scalar_or_list(item)
        for key, item in value.items()
        if is_report_safe_key(str(key)) and is_report_safe_value(item)
    }


def compact_scalar_or_list(value: Any) -> Any:
    if isinstance(value, dict):
        return compact_mapping(value)
    if isinstance(value, list):
        return [compact_scalar_or_list(item) for item in value[:5] if is_report_safe_value(item)]
    return value


def is_report_safe_value(value: Any) -> bool:
    if isinstance(value, dict):
        return True
    if isinstance(value, list):
        return True
    return not isinstance(value, str) or not looks_like_local_path(value)


def is_report_safe_key(key: str) -> bool:
    return key not in {
        "pdf_text",
        "pdf_text_excerpt",
        "pdf_text_chars",
        "pdf_text_truncated",
        "pdf_drawings_removed",
        "pdf_collected",
        "pdf_path",
        "markdown_paths",
        "raw",
        "raw_html",
        "raw_body",
        "body",
        "debug",
        "candidate_results",
        "search_request_url",
    }


def looks_like_local_path(value: str) -> bool:
    text = normalize_text(value)
    return text.startswith(("/", "./", "../")) or "/artifacts/" in text or "/Users/" in text


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
        ("보고서 생성일", datetime.now().strftime("%Y-%m-%d")),
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
        "country": patent.get("country") or kipris_metadata.get("country"),
        "status": patent.get("status") or kipris_metadata.get("register_status"),
        "application_date": patent.get("application_date") or kipris_metadata.get("filing_date"),
        "registration_date": patent.get("registration_date") or kipris_metadata.get("registration_date"),
        "expected_expiration_date": patent.get("expected_expiration_date"),
        "assignee": kipris_metadata.get("assignee") or [],
        "ipc": kipris_metadata.get("ipc") or [],
        "cpc": kipris_metadata.get("cpc") or [],
    }


# 외부 근거의 title/source는 본문(content)과 달리 sanitize를 거치지 않고 final_report 프롬프트로
# 유입될 수 있어, 제어문자 제거 + 공백 정규화 + 길이 제한으로 본문과 같은 방어 수준을 맞춘다(SEC-03).
_REFERENCE_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_reference_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = _REFERENCE_CONTROL_CHARS_RE.sub("", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:300]


def build_evidence_references(state: PatentWorkflowState, valuation_result: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_axis_usage = collect_evidence_axis_usage(valuation_result)
    if not evidence_axis_usage:
        return []

    references = []
    for item in state.evidence_bundle or []:
        evidence_id = item.get("evidence_id")
        if evidence_id not in evidence_axis_usage:
            continue
        if item.get("source_type") not in {"news", "industry_report", "company_disclosure", "portfolio_context"}:
            continue
        references.append(
            {
                "evidence_id": evidence_id,
                "source_type": item.get("source_type"),
                "source": _sanitize_reference_text(item.get("source")),
                "source_domain": item.get("source_domain")
                or ((item.get("metadata") or {}).get("source_domain") if isinstance(item.get("metadata"), dict) else None),
                "source_tier": item.get("source_tier")
                or ((item.get("metadata") or {}).get("source_tier") if isinstance(item.get("metadata"), dict) else None),
                "title": _sanitize_reference_text(item.get("title") or item.get("source")),
                "citation_title": _sanitize_reference_text(item.get("title") or item.get("source")),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "cited_by_axes": evidence_axis_usage[evidence_id],
            }
        )
    return references


def collect_evidence_axis_usage(valuation_result: dict[str, Any]) -> dict[Any, list[str]]:
    usage: dict[Any, list[str]] = {}
    axes = valuation_result.get("axes") or {}
    if not isinstance(axes, dict):
        return usage
    for axis, axis_result in axes.items():
        if not isinstance(axis_result, dict):
            continue
        evidence_ids = axis_result.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            continue
        for evidence_id in evidence_ids:
            if evidence_id and axis not in usage.setdefault(evidence_id, []):
                usage[evidence_id].append(axis)
    return usage


def collect_used_evidence_ids(valuation_result: dict[str, Any]) -> set[Any]:
    return set(collect_evidence_axis_usage(valuation_result))


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
