from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace
from workflow.state import PatentWorkflowState


VALUATION_AXES = ["legal", "technology", "market", "business_fit"]

AXIS_LABELS = {
    "legal": "권리성",
    "technology": "기술성",
    "market": "시장성",
    "business_fit": "사업 연계성",
}


@trace(name="valuation_agent", run_type="chain")
def run_valuation_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    if state.user_input.get("use_llm_valuation", True) is False:
        raise RuntimeError("LLM valuation is required, but use_llm_valuation is disabled.")
    if state.user_input.get("use_llm_final_report", True) is False:
        raise RuntimeError("LLM final report is required, but use_llm_final_report is disabled.")

    axes: dict[str, dict[str, Any]] = {}
    for axis in VALUATION_AXES:
        evidence = select_axis_evidence(axis, state)
        axes[axis] = run_axis_llm_required(axis, state=state, evidence=evidence)

    state.valuation_result = build_final_valuation_result(axes, state=state)
    state.current_stage = "valuation_check"
    return state


def run_axis_llm_required(
    axis: str,
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = call_llm(build_axis_prompt(axis, state=state, evidence=evidence))
    parsed = parse_json_object(raw)
    if not parsed:
        raise RuntimeError(f"LLM valuation response for {axis} was not valid JSON.")
    return normalize_axis_llm_result(axis, parsed, evidence=evidence)


def build_axis_prompt(axis: str, *, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> str:
    common_template = load_prompt("valuation/common_valuation.md").strip()
    template = load_prompt(f"valuation/valuation_{axis}.md").strip()
    payload = build_axis_input_payload(axis=axis, state=state, evidence=evidence)
    save_valuation_input_payload(state, f"{axis}_input", payload)
    return f"{common_template}\n\n{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def build_axis_input_payload(
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
    axis: str | None = None,
) -> dict[str, Any]:
    representative_claims = valuation_representative_claims(state)
    full_claims = valuation_claims(state) if axis == "legal" else []
    claim_stats = ((state.kipris_api_data or {}).get("claim_stats") or {})
    return {
        "patent": {
            "metadata": state.patent_structured or {},
            "kipris_metadata": ((state.kipris_api_data or {}).get("metadata") or {}),
            "claim_stats": claim_stats,
            "representative_claims": representative_claims,
            "claims": full_claims,
            "claim_availability": {
                "claim_stats_provided": bool(claim_stats),
                "representative_claims_provided": bool(representative_claims),
                "full_claims_provided": bool(full_claims),
            },
        },
        "summary_result": state.summary_result,
        "evidence": [valuation_evidence_payload(item) for item in evidence],
    }


def valuation_claims(state: PatentWorkflowState) -> list[dict[str, Any]]:
    claims = []
    preprocessed = state.preprocessed_patent or {}
    if isinstance(preprocessed.get("claims"), list):
        claims = preprocessed["claims"]
    elif isinstance((state.kipris_api_data or {}).get("claims"), list):
        claims = (state.kipris_api_data or {})["claims"]

    return [
        {
            "claim_no": claim.get("claim_no"),
            "is_independent": claim.get("is_independent"),
            "dependency": claim.get("dependency"),
            "text": normalize_text(claim.get("text")),
        }
        for claim in claims
        if claim.get("text")
    ]


def valuation_representative_claims(state: PatentWorkflowState, *, limit: int = 3, text_limit: int = 1500) -> list[dict[str, Any]]:
    claims = valuation_claims(state)

    selected = [claim for claim in claims if claim.get("is_independent") and claim.get("text")]
    if not selected:
        selected = [claim for claim in claims if claim.get("text")]

    result = []
    for claim in selected[:limit]:
        result.append(
            {
                "claim_no": claim.get("claim_no"),
                "is_independent": claim.get("is_independent"),
                "dependency": claim.get("dependency"),
                "text": normalize_text(claim.get("text"))[:text_limit],
            }
        )
    return result


def valuation_evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "source_type": item.get("source_type"),
        "source": item.get("source"),
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "collected_at": item.get("collected_at"),
        "related_axes": item.get("related_axes") or item.get("related_axis") or [],
        "compressed_summary": item.get("compressed_summary"),
        "key_facts": item.get("key_facts") or [],
        "sibling_patents": item.get("sibling_patents") or [],
        "group_size": item.get("group_size"),
    }


def normalize_axis_llm_result(axis: str, parsed: dict[str, Any], *, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    known_evidence_ids = {item.get("evidence_id") for item in evidence if item.get("evidence_id")}
    evidence_ids = [
        evidence_id
        for evidence_id in normalize_list(parsed.get("evidence_ids"))
        if evidence_id in known_evidence_ids
    ]
    required_fields = ["score", "grade", "rationale", "confidence"]
    missing_fields = [field for field in required_fields if parsed.get(field) in (None, "", [])]
    if missing_fields:
        raise RuntimeError(f"LLM valuation response for {axis} is missing: {', '.join(missing_fields)}.")
    score = max(0, min(100, int(parsed["score"])))
    return {
        "axis": axis,
        "label": AXIS_LABELS[axis],
        "score": score,
        "grade": normalize_text(parsed.get("grade")),
        "rationale": normalize_text(parsed.get("rationale")),
        "evidence_ids": evidence_ids,
        "risk_factors": normalize_list(parsed.get("risk_factors")),
        "missing_information": normalize_list(parsed.get("missing_information")),
        "confidence": max(0.0, min(1.0, float(parsed["confidence"]))),
    }


def select_axis_evidence(axis: str, state: PatentWorkflowState) -> list[dict[str, Any]]:
    items = state.evidence_bundle or []
    if axis == "legal":
        return select_by_types_or_axes(items, source_types={"portfolio_context", "competitor_patent", "patent_api"}, axes={axis})
    if axis == "technology":
        return select_by_types_or_axes(items, source_types={"portfolio_context", "industry_report", "patent_api"}, axes={axis})
    if axis == "market":
        return select_by_types_or_axes(
            items,
            source_types={"news", "industry_report", "company_disclosure"},
            axes={axis},
        )
    if axis == "business_fit":
        return select_business_fit_evidence(items, state)
    return []


def select_by_types_or_axes(
    items: list[dict[str, Any]],
    *,
    source_types: set[str],
    axes: set[str],
) -> list[dict[str, Any]]:
    selected = []
    for item in items:
        item_axes = set(item.get("related_axes") or item.get("related_axis") or [])
        if item.get("source_type") in source_types or item_axes.intersection(axes):
            selected.append(item)
    return selected[:5]


def select_business_fit_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    keywords = business_fit_keywords(state)
    direct_matches = []
    secondary_matches = []
    for item in items:
        source_type = item.get("source_type")
        if source_type in {"company_disclosure", "portfolio_context"}:
            secondary_matches.append(item)
        if source_type != "news":
            continue
        text = evidence_text(item)
        if any(keyword and keyword in text for keyword in keywords):
            direct_matches.append(item)
        else:
            secondary_matches.append(item)
    return [*direct_matches, *secondary_matches][:5]


def business_fit_keywords(state: PatentWorkflowState) -> list[str]:
    patent = state.patent_structured or {}
    metadata = ((state.kipris_api_data or {}).get("metadata") or {})
    raw_keywords: list[Any] = [
        patent.get("title_final"),
        patent.get("title_draft"),
        patent.get("related_product"),
        patent.get("technology_area"),
        patent.get("business_area"),
        patent.get("joint_applicant_name"),
        *(metadata.get("assignee") or []),
        *(metadata.get("assignee_eng") or []),
    ]
    company_context = patent.get("company_context") or state.user_input.get("company_context") or {}
    if isinstance(company_context, dict):
        raw_keywords.extend([company_context.get("company_name"), company_context.get("product_name")])
    return [normalize_text(keyword) for keyword in raw_keywords if normalize_text(keyword)]


def build_final_valuation_result(
    axes: dict[str, dict[str, Any]],
    *,
    state: PatentWorkflowState | None = None,
) -> dict[str, Any]:
    total_score = sum(int(axis.get("score") or 0) for axis in axes.values())
    final_indicator = total_score_to_indicator(total_score)
    missing_information = unique_texts(
        item for axis in axes.values() for item in axis.get("missing_information", [])
    )
    required_actions = []
    business_fit = axes.get("business_fit") or {}
    if business_fit.get("missing_information"):
        required_actions.append("사업부 적용 여부 및 향후 적용 계획 확인")
    if missing_information:
        required_actions.append("부족 정보 보완 후 최종 판단 재검토")
    result = {
        "axes": axes,
        "total_score": total_score,
        "final_indicator": final_indicator,
        "recommendation": indicator_to_recommendation(final_indicator, missing_information),
        "decision_rationale": build_decision_rationale(axes, total_score, final_indicator),
        "required_actions": unique_texts(required_actions),
        "missing_information": missing_information,
    }
    result["final_report_markdown"] = (
        build_complete_final_report_markdown(
            state,
            run_final_report_llm_required(state, result),
            result,
        )
    )
    return result


def run_final_report_llm_required(
    state: PatentWorkflowState | None,
    valuation_result: dict[str, Any],
) -> str:
    if not state:
        raise RuntimeError("LLM final report requires workflow state.")
    markdown = call_llm(build_final_report_prompt(state=state, valuation_result=valuation_result)).strip()
    if not markdown:
        raise RuntimeError("LLM final report response was empty.")
    return markdown


def build_final_report_prompt(*, state: PatentWorkflowState, valuation_result: dict[str, Any]) -> str:
    template = load_prompt("valuation/valuation_final_report.md").strip()
    payload = build_final_report_input_payload(state=state, valuation_result=valuation_result)
    save_valuation_input_payload(state, "final_report_input", payload)
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def build_final_report_input_payload(*, state: PatentWorkflowState, valuation_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "patent": {
            "metadata": final_report_patent_metadata(state),
            "summary_result": state.summary_result,
        },
        "evidence_references": build_evidence_references(state),
        "valuation_result": {
            key: value
            for key, value in valuation_result.items()
            if key != "final_report_markdown"
        },
    }


def build_complete_final_report_markdown(
    state: PatentWorkflowState,
    body_markdown: str,
    valuation_result: dict[str, Any],
) -> str:
    del valuation_result
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


def save_valuation_input_payload(state: PatentWorkflowState, name: str, payload: dict[str, Any]) -> Path | None:
    if state.user_input.get("no_save", False):
        return None
    output_dir = valuation_input_output_dir(state)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def valuation_input_output_dir(state: PatentWorkflowState) -> Path:
    artifact_dir = state.user_input.get("artifact_dir")
    if artifact_dir:
        return Path(artifact_dir) / "valuation_inputs"
    return settings.output_dir / "valuation_inputs"


def total_score_to_indicator(total_score: int) -> str:
    if total_score >= 320:
        return "유지"
    if total_score >= 240:
        return "조건부 유지"
    if total_score >= 160:
        return "포기 검토"
    return "매각 후보"


def indicator_to_recommendation(final_indicator: str, missing_information: list[str]) -> str:
    if missing_information and final_indicator in {"유지", "조건부 유지"}:
        return "추가 정보 필요"
    if final_indicator in {"유지", "조건부 유지"}:
        return "유지 권고"
    return "포기 검토"


def build_decision_rationale(axes: dict[str, dict[str, Any]], total_score: int, final_indicator: str) -> list[str]:
    strongest = max(axes.values(), key=lambda axis: axis.get("score", 0))
    weakest = min(axes.values(), key=lambda axis: axis.get("score", 0))
    return [
        f"4개 평가축 합산 점수는 {total_score}점이며 최종 종합 지표는 {final_indicator}이다.",
        f"가장 강한 축은 {strongest.get('label')}({strongest.get('score')}점)이다.",
        f"보완이 필요한 축은 {weakest.get('label')}({weakest.get('score')}점)이다.",
    ]


def evidence_text(item: dict[str, Any]) -> str:
    values = [
        item.get("title"),
        item.get("compressed_summary"),
        item.get("content"),
        item.get("context"),
        " ".join(str(fact) for fact in item.get("key_facts", [])),
    ]
    return " ".join(normalize_text(value) for value in values if normalize_text(value))


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (normalize_text(item) for item in value) if text]


def normalize_markdown_table_text(value: Any) -> str:
    return normalize_text(value).replace("|", "/").replace("\n", " ")


def unique_texts(values: Any) -> list[str]:
    result = []
    for value in values:
        text = normalize_text(value)
        if text and text not in result:
            result.append(text)
    return result
