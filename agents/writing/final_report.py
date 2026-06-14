from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from services.evidence.compression_service import parse_json_object
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
    # WRIT-SC: 점수-서술 일관성 self-critique 1회 + 불일치 시 교정 1회. 실패는 비치명(원본 유지).
    report_markdown, critique_meta = apply_report_self_critique(
        state=state,
        valuation_result=valuation_result,
        report_markdown=report_markdown,
    )
    valuation_result["final_report_markdown"] = report_markdown
    if critique_meta is not None:
        valuation_result["report_self_critique"] = critique_meta
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


def apply_report_self_critique(
    *,
    state: PatentWorkflowState,
    valuation_result: dict[str, Any],
    report_markdown: str,
) -> tuple[str, dict[str, Any] | None]:
    """WRIT-SC: 점수-서술 일관성 critique 1회 + 불일치 시 교정 1회를 적용한다.

    critique/교정 어느 단계가 실패해도 원본 보고서를 그대로 반환한다(비치명).
    """
    if not settings.report_self_critique_enabled:
        return report_markdown, None

    try:
        critique = run_report_critique_llm(valuation_result, report_markdown)
    except Exception as exc:
        return report_markdown, {
            "checked": False,
            "revised": False,
            "warning": f"report_critique_failed:{exc.__class__.__name__}:{str(exc)[:200]}",
        }

    meta: dict[str, Any] = {
        "checked": True,
        "consistent": critique["consistent"],
        "issues": critique["issues"],
        "revised": False,
    }
    if critique["consistent"] or not critique["issues"]:
        return report_markdown, meta

    try:
        revised = run_report_revision_llm(
            state=state,
            valuation_result=valuation_result,
            report_markdown=report_markdown,
            issues=critique["issues"],
        )
    except Exception as exc:
        meta["warning"] = f"report_revision_failed:{exc.__class__.__name__}:{str(exc)[:200]}"
        return report_markdown, meta
    if not revised:
        meta["warning"] = "report_revision_empty"
        return report_markdown, meta
    meta["revised"] = True
    return revised, meta


def run_report_critique_llm(valuation_result: dict[str, Any], report_markdown: str) -> dict[str, Any]:
    # critique는 판정 작업이므로 supervisor 모델을 사용한다(작성 모델보다 저렴·빠름).
    template = load_prompt("writing/report_critique.md").strip()
    payload = {
        "valuation_result": compact_final_report_valuation_result(valuation_result),
        "report_markdown": report_markdown,
    }
    raw = call_llm(
        f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}",
        model=settings.openai_supervisor_model,
        temperature=0,
        reasoning_effort=settings.openai_supervisor_reasoning_effort,
    )
    parsed = parse_json_object(raw)
    if not parsed:
        raise ValueError("Report critique response was not a valid JSON object.")
    return {
        "consistent": bool(parsed.get("consistent", True)),
        "issues": [str(issue).strip() for issue in (parsed.get("issues") or []) if str(issue).strip()],
    }


def run_report_revision_llm(
    *,
    state: PatentWorkflowState,
    valuation_result: dict[str, Any],
    report_markdown: str,
    issues: list[str],
) -> str:
    # 교정은 서술 품질 유지를 위해 작성(writing) 모델을 사용한다.
    rights_scope_context = build_rights_scope_context(state)
    revised = sanitize_final_report_markdown(
        call_llm(
            build_report_revision_prompt(valuation_result, report_markdown, issues),
            model=settings.openai_writing_model,
            reasoning_effort=settings.openai_writing_reasoning_effort,
            verbosity=settings.openai_writing_verbosity,
            timeout=settings.openai_writing_timeout_seconds,
        ).strip(),
        include_rights_scope_reference=bool(
            (rights_scope_context or {}).get("representative_drawing")
        ),
    )
    return revised.strip()


def build_report_revision_prompt(
    valuation_result: dict[str, Any],
    report_markdown: str,
    issues: list[str],
) -> str:
    instructions = "\n".join(
        [
            "당신은 특허 가치평가 최종 보고서의 점수-서술 불일치를 교정하는 Agent입니다.",
            "아래 Input JSON의 report_markdown 보고서를 issues에 나열된 불일치만 고쳐 다시 작성하세요.",
            "- valuation_result의 점수·등급·종합 검토 의견(recommendation)을 정답 기준으로 삼고, 서술이 점수와 어긋나는 부분만 수정하세요.",
            "- 보고서의 구조(섹션 제목·표·총점 표기)와 점수·등급 수치는 변경하지 말고 그대로 유지하세요.",
            "- 수정이 필요 없는 문장·표·링크는 그대로 두세요.",
            "- 결과는 수정된 보고서 Markdown 전문만 출력하세요(설명·코드펜스 금지).",
        ]
    )
    payload = {
        "issues": issues,
        "valuation_result": compact_final_report_valuation_result(valuation_result),
        "report_markdown": report_markdown,
    }
    return f"{instructions}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


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
                "source": item.get("source"),
                "source_domain": item.get("source_domain")
                or ((item.get("metadata") or {}).get("source_domain") if isinstance(item.get("metadata"), dict) else None),
                "source_tier": item.get("source_tier")
                or ((item.get("metadata") or {}).get("source_tier") if isinstance(item.get("metadata"), dict) else None),
                "title": item.get("title") or item.get("source"),
                "citation_title": item.get("title") or item.get("source"),
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
