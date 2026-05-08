from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

from app.config import settings
from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace
from workflow.state import PatentWorkflowState


VALUATION_AXES = ["legal", "technology", "market", "economic", "business_fit"]

AXIS_LABELS = {
    "legal": "권리성",
    "technology": "기술성",
    "market": "시장성",
    "economic": "라이프사이클 경제성",
    "business_fit": "사업 연계성",
}

BASE_AXIS_SCORES = {
    "legal": 68,
    "technology": 70,
    "market": 64,
    "economic": 62,
    "business_fit": 58,
}


@trace(name="valuation_agent", run_type="chain")
def run_valuation_agent(state: PatentWorkflowState) -> PatentWorkflowState:
    axes: dict[str, dict[str, Any]] = {}
    for axis in VALUATION_AXES:
        evidence = select_axis_evidence(axis, state)
        axes[axis] = run_axis_llm_if_enabled(axis, state=state, evidence=evidence) or evaluate_axis(
            axis,
            state=state,
            evidence=evidence,
            prior_axes=axes,
        )

    state.valuation_result = build_final_valuation_result(axes, state=state)
    state.current_stage = "valuation_check"
    return state


def run_axis_llm_if_enabled(
    axis: str,
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if state.user_input.get("use_llm_valuation", True) is False:
        return None
    raw = call_llm(build_axis_prompt(axis, state=state, evidence=evidence))
    parsed = parse_json_object(raw)
    if not parsed:
        raise ValueError(f"{axis} valuation LLM response was not valid JSON")
    return normalize_axis_llm_result(axis, parsed, evidence=evidence)


def build_axis_prompt(axis: str, *, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> str:
    common_template = load_prompt("valuation/common_valuation.md").strip()
    template = load_prompt(f"valuation/valuation_{axis}.md").strip()
    payload = build_axis_input_payload(state=state, evidence=evidence)
    save_valuation_input_payload(state, f"{axis}_input", payload)
    return f"{common_template}\n\n{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def build_axis_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    representative_claims = valuation_representative_claims(state)
    claim_stats = ((state.kipris_api_data or {}).get("claim_stats") or {})
    return {
        "patent": {
            "metadata": state.patent_structured or {},
            "kipris_metadata": ((state.kipris_api_data or {}).get("metadata") or {}),
            "claim_stats": claim_stats,
            "representative_claims": representative_claims,
            "claim_availability": {
                "claim_stats_provided": bool(claim_stats),
                "representative_claims_provided": bool(representative_claims),
            },
        },
        "summary_result": state.summary_result,
        "evidence": [valuation_evidence_payload(item) for item in evidence],
    }


def valuation_representative_claims(state: PatentWorkflowState, *, limit: int = 3, text_limit: int = 1500) -> list[dict[str, Any]]:
    claims = []
    preprocessed = state.preprocessed_patent or {}
    if isinstance(preprocessed.get("claims"), list):
        claims = preprocessed["claims"]
    elif isinstance((state.kipris_api_data or {}).get("claims"), list):
        claims = (state.kipris_api_data or {})["claims"]

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
    score = max(0, min(100, int(parsed.get("score") or BASE_AXIS_SCORES[axis])))
    return {
        "axis": axis,
        "label": AXIS_LABELS[axis],
        "score": score,
        "grade": normalize_text(parsed.get("grade")) or score_to_grade(score),
        "rationale": normalize_text(parsed.get("rationale")) or f"{AXIS_LABELS[axis]} 축 LLM 평가 결과입니다.",
        "evidence_ids": evidence_ids,
        "risk_factors": normalize_list(parsed.get("risk_factors")) or axis_risk_factors(axis, evidence=evidence),
        "missing_information": normalize_list(parsed.get("missing_information")),
        "confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0.5))),
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
    if axis == "economic":
        return select_by_types_or_axes(items, source_types={"portfolio_context", "industry_report"}, axes={axis, "market", "technology"})
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
    fallback = []
    for item in items:
        source_type = item.get("source_type")
        if source_type in {"company_disclosure", "portfolio_context"}:
            fallback.append(item)
        if source_type != "news":
            continue
        text = evidence_text(item)
        if any(keyword and keyword in text for keyword in keywords):
            direct_matches.append(item)
        else:
            fallback.append(item)
    return [*direct_matches, *fallback][:5]


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


def evaluate_axis(
    axis: str,
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
    prior_axes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    patent = state.patent_structured or {}
    score = axis_score(axis, state=state, evidence=evidence, prior_axes=prior_axes)
    missing_information = axis_missing_information(axis, state=state, evidence=evidence)
    confidence = axis_confidence(axis, evidence=evidence, missing_information=missing_information)
    return {
        "axis": axis,
        "label": AXIS_LABELS[axis],
        "score": score,
        "grade": score_to_grade(score),
        "rationale": axis_rationale(axis, patent=patent, evidence=evidence, score=score),
        "evidence_ids": [item["evidence_id"] for item in evidence if item.get("evidence_id")],
        "risk_factors": axis_risk_factors(axis, evidence=evidence),
        "missing_information": missing_information,
        "confidence": confidence,
    }


def axis_score(
    axis: str,
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
    prior_axes: dict[str, dict[str, Any]],
) -> int:
    score = BASE_AXIS_SCORES[axis]
    score += min(len(evidence), 3) * 4
    if any(item.get("source_type") == "portfolio_context" for item in evidence):
        score += 4
    if axis == "economic":
        score += lifecycle_bonus(state.patent_structured or {})
        market_score = (prior_axes.get("market") or {}).get("score")
        technology_score = (prior_axes.get("technology") or {}).get("score")
        for prior_score in [market_score, technology_score]:
            if isinstance(prior_score, int) and prior_score >= 70:
                score += 3
    if axis == "business_fit" and has_direct_business_context(evidence, state):
        score += 8
    return max(0, min(100, score))


def lifecycle_bonus(patent: dict[str, Any]) -> int:
    expiration_date = parse_iso_date(patent.get("expected_expiration_date"))
    if not expiration_date:
        return 0
    remaining_years = (expiration_date - date.today()).days / 365
    if remaining_years >= 8:
        return 8
    if remaining_years >= 4:
        return 5
    if remaining_years >= 1:
        return 2
    return -8


def axis_missing_information(
    axis: str,
    *,
    state: PatentWorkflowState,
    evidence: list[dict[str, Any]],
) -> list[str]:
    patent = state.patent_structured or {}
    missing: list[str] = []
    if axis == "legal":
        if not ((state.kipris_api_data or {}).get("claim_stats") or patent.get("claim_stats")):
            missing.append("청구항 구조 추가 확인 필요")
        missing.append("무효/분쟁 이력 추가 확인 필요")
    elif axis == "technology":
        missing.extend(["전방 인용 수 추가 확인 필요", "실제 구현 단계 추가 확인 필요"])
    elif axis == "market":
        if not evidence:
            missing.append("시장/산업 근거 정보 부족 있음")
    elif axis == "economic":
        if not patent.get("expected_expiration_date"):
            missing.append("예상 소멸일 추가 확인 필요")
        if not patent.get("status"):
            missing.append("등록/존속 상태 추가 확인 필요")
        if not evidence:
            missing.append("시장/기술 활용 맥락 추가 확인 필요")
    elif axis == "business_fit":
        if not has_direct_business_context(evidence, state):
            missing.append("현재 제품/서비스 적용 여부 확인 필요")
            missing.append("사업부 적용 계획 확인 필요")
    return missing


def axis_confidence(axis: str, *, evidence: list[dict[str, Any]], missing_information: list[str]) -> float:
    confidence = 0.55 + min(len(evidence), 3) * 0.08
    if axis == "business_fit" and missing_information:
        confidence -= 0.12
    if missing_information:
        confidence -= min(len(missing_information), 3) * 0.04
    return round(max(0.2, min(0.9, confidence)), 2)


def axis_rationale(axis: str, *, patent: dict[str, Any], evidence: list[dict[str, Any]], score: int) -> str:
    label = AXIS_LABELS[axis]
    product = patent.get("related_product") or "대상 제품/서비스"
    if axis == "business_fit":
        return (
            f"{label}은 {product} 및 관련 뉴스/공시/포트폴리오 맥락을 기준으로 임시 평가했다. "
            "제품명이 있다는 이유만으로 실제 적용을 단정하지 않았다."
        )
    if axis == "economic":
        return (
            f"{label}은 남은 보호기간, 등록 상태, 시장/기술 맥락, 포트폴리오 보호 효과를 중심으로 "
            f"정성 평가했다. 현재 점수는 {score}점이다."
        )
    return f"{label}은 특허 메타데이터와 {len(evidence)}건의 관련 근거를 바탕으로 초기 평가했다."


def axis_risk_factors(axis: str, *, evidence: list[dict[str, Any]]) -> list[str]:
    if axis == "legal":
        return ["법적 안정성은 별도 무효/분쟁 이력 검토가 필요함"]
    if axis == "technology":
        return ["citation 및 실제 구현 단계 정보가 부족할 수 있음"]
    if axis == "market":
        return ["시장 근거가 뉴스/리포트 맥락에 의존할 수 있음"]
    if axis == "economic":
        return ["정량 재무 수치가 아닌 정성적 경제성 추정임"]
    if axis == "business_fit":
        return ["내부 적용 여부 확인 전까지 사업 연계성은 임시 판단임"]
    return ["추가 정밀 분석 필요"]


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
            run_final_report_llm_if_enabled(state, result),
            result,
        )
        if state
        else build_fallback_final_report_markdown(result)
    )
    return result


def run_final_report_llm_if_enabled(
    state: PatentWorkflowState | None,
    valuation_result: dict[str, Any],
) -> str | None:
    if not state or state.user_input.get("use_llm_final_report", True) is False:
        return None
    markdown = call_llm(build_final_report_prompt(state=state, valuation_result=valuation_result)).strip()
    if not markdown:
        raise ValueError("Final report LLM response was empty")
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
    body_markdown: str | None,
    valuation_result: dict[str, Any],
) -> str:
    body = (body_markdown or "").strip()
    if not body:
        body = build_fallback_final_report_markdown(valuation_result)
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


def build_fallback_final_report_markdown(valuation_result: dict[str, Any]) -> str:
    axes = valuation_result.get("axes") or {}
    lines = [
        "# 특허 가치판단 종합 보고서",
        "",
        "## 최종 판단",
        "",
        f"- 최종 종합 지표: {valuation_result.get('final_indicator')}",
        f"- 종합 점수: {valuation_result.get('total_score')} / 500",
        f"- AI 권고: {valuation_result.get('recommendation')}",
        "",
        "## 축별 평가",
        "",
        "| 평가축 | 점수 | 등급 | 요약 |",
        "| --- | ---: | --- | --- |",
    ]
    for axis in VALUATION_AXES:
        axis_result = axes.get(axis) or {}
        lines.append(
            "| {label} | {score} | {grade} | {rationale} |".format(
                label=axis_result.get("label") or AXIS_LABELS[axis],
                score=axis_result.get("score", "N/A"),
                grade=axis_result.get("grade", "N/A"),
                rationale=normalize_markdown_table_text(axis_result.get("rationale")),
            )
        )
    lines.extend(["", "## 판단 근거"])
    for rationale in valuation_result.get("decision_rationale", []):
        lines.append(f"- {rationale}")
    required_actions = valuation_result.get("required_actions") or []
    if required_actions:
        lines.extend(["", "## 후속 확인 필요"])
        for action in required_actions:
            lines.append(f"- {action}")
    missing_information = valuation_result.get("missing_information") or []
    if missing_information:
        lines.extend(["", "## 부족 정보"])
        for item in missing_information:
            lines.append(f"- {item}")
    return "\n".join(lines)


def total_score_to_indicator(total_score: int) -> str:
    if total_score >= 400:
        return "유지"
    if total_score >= 320:
        return "조건부 유지"
    if total_score >= 240:
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
        f"5개 평가축 합산 점수는 {total_score}점이며 최종 종합 지표는 {final_indicator}이다.",
        f"가장 강한 축은 {strongest.get('label')}({strongest.get('score')}점)이다.",
        f"보완이 필요한 축은 {weakest.get('label')}({weakest.get('score')}점)이다.",
    ]


def score_to_grade(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def has_direct_business_context(evidence: list[dict[str, Any]], state: PatentWorkflowState) -> bool:
    keywords = business_fit_keywords(state)
    for item in evidence:
        if item.get("source_type") == "company_disclosure":
            return True
        if item.get("source_type") != "news":
            continue
        text = evidence_text(item)
        if any(keyword and keyword in text for keyword in keywords):
            return True
    return False


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


def parse_iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(normalize_text(value))
    except ValueError:
        return None
