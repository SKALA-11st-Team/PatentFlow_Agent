from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from agents.valuation_axes.common import grade_for_score, normalize_text, select_by_source_types
from agents.valuation_axes.market import clamp_int, extract_patent_country, extract_representative_cpc, extract_representative_ipc
from agents.valuation_axes.payload_common import (
    build_base_input_payload,
    build_claim_context,
    build_element_structure_payload,
)
from services.patent.prior_art_patent_service import build_prior_art_patent_context, has_prior_art_fulltext
from services.patent.similar_patent_service import build_similar_patent_context
from workflow.state import PatentWorkflowState


AXIS = "technology"
LABEL = "기술성"
PROMPT_PATH = "valuation/valuation_technology.md"
TECHNOLOGY_COMPARISON_TARGET_COUNT = 3
COMPARISON_CLAIM_TEXT_LIMIT = 1200
COMPARISON_TECHNICAL_CONTENT_LIMITS = {
    "problem": 1200,
    "solution": 2500,
    "effect": 1500,
    "detailed_description": 5000,
}
TECHNOLOGY_DETAIL_SCORE_CANDIDATES = {
    "technical_differentiation": {
        "configuration_operation_differentiation": (0, 10, 20, 30),
        "effect_differentiation": (0, 6, 12, 18),
        "imitation_avoidance_difficulty": (0, 4, 8, 12),
    },
    "implementation_specificity": {
        "component_specificity": (0, 6, 11, 16),
        "procedure_specificity": (0, 6, 11, 16),
        "implementation_utilization_specificity": (0, 2, 6, 8),
    },
}
TECHNOLOGY_SUBSCORE_MAX_SCORES = {
    "technical_differentiation": 60,
    "implementation_specificity": 40,
}


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    metrics = build_technology_metrics(state)
    payload = build_input_payload(state=state, evidence=evidence)
    payload["technology_metrics"] = metrics
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
        axis=AXIS,
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    return apply_technology_scores(result, metrics)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    # AG-01(EVID-02 복원): KIPRIS 경쟁특허 근거를 기술성 축에 포함 — 인접 출원 대비 차별성 판단.
    return select_by_source_types(
        items,
        source_types={"portfolio_context", "industry_report", "patent_api", "competitor_patent"},
    )


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base_input_payload(
        state=state,
        evidence=evidence,
        claim_context=build_claim_context(state, include_dependent_claims=True),
    )
    payload["patent"]["technical_context"] = build_target_technical_context(state)
    # 주요구성요소(구조화 결과)도 비교 근거로 함께 제공한다(하이브리드).
    payload["element_structure"] = build_element_structure_payload(state)
    return payload


def build_target_technical_context(state: PatentWorkflowState) -> dict[str, Any]:
    preprocessed = state.preprocessed_patent or {}
    sections = preprocessed.get("sections") if isinstance(preprocessed.get("sections"), dict) else {}
    drawing_context = (
        preprocessed.get("drawing_context")
        if isinstance(preprocessed.get("drawing_context"), dict)
        else {}
    )
    return {
        "technical_field": normalize_text(sections.get("technical_field")),
        "background_art": normalize_text(sections.get("background_art")),
        "problem": normalize_text(sections.get("problem")),
        "solution": normalize_text(sections.get("solution")),
        "effect": normalize_text(sections.get("effect")),
        "detailed_description": normalize_text(sections.get("detailed_description")),
        "figure_description": normalize_text(drawing_context.get("figure_description")),
        "representative_figure_detail": normalize_text(
            drawing_context.get("representative_figure_detail")
        ),
    }


def build_technology_metrics(state: PatentWorkflowState) -> dict[str, Any]:
    preprocessed = state.preprocessed_patent or {}
    sections = (preprocessed.get("sections") or {}) if isinstance(preprocessed, dict) else {}
    claims = (preprocessed.get("claims") or []) if isinstance(preprocessed, dict) else []
    drawing_context = (
        preprocessed.get("drawing_context")
        if isinstance(preprocessed.get("drawing_context"), dict)
        else {}
    )
    metadata = {
        **(state.patent_structured or {}),
        **((preprocessed.get("metadata")) or {}),
        "abstract": sections.get("abstract"),
        "claims_text": sections.get("claims_text"),
        "solution": sections.get("solution"),
        "detailed_description": sections.get("detailed_description"),
        "representative_claim_text": "\n".join(str((claim or {}).get("text") or "") for claim in claims[:3]),
        "independent_claim_text": "\n".join(
            str((claim or {}).get("text") or "")
            for claim in claims
            if (claim or {}).get("is_independent")
        ),
    }
    country_code = extract_patent_country(state)
    foreign_patent = bool(country_code and country_code != "KR")
    representative_cpc = extract_representative_cpc(state)
    representative_ipc = extract_representative_ipc(state)
    artifact_dir = state.user_input.get("artifact_dir") if state.user_input else None
    similar_dir = Path(artifact_dir) / "similar_patents" if artifact_dir else None
    prior_art_dir = Path(artifact_dir) / "prior_art_patents" if artifact_dir else None
    metrics = build_hybrid_context(
        metadata=metadata,
        kipris_api_data=state.kipris_api_data,
        representative_cpc=representative_cpc,
        representative_ipc=representative_ipc,
        country_code=country_code if foreign_patent else None,
        similar_dir=similar_dir,
        prior_art_dir=prior_art_dir,
        prior_art_context=state.prior_art_context,
    )
    metrics["target_document_indicators"] = build_document_indicators(
        text_parts=[
            sections.get("solution"),
            sections.get("effect"),
            sections.get("detailed_description"),
            sections.get("figure_description"),
            drawing_context.get("representative_figure_detail"),
        ],
        claims=claims,
    )
    metrics["similar_patents"] = [
        attach_comparison_document_indicators(item)
        for item in metrics.get("similar_patents") or []
    ]
    return metrics


DOCUMENT_INDICATOR_PATTERNS = {
    "embodiment_mentions": r"실시예|실시\s*형태|example|embodiment|実施例|実施形態|实施例|具体实施方式",
    "figure_mentions": r"(?:도|도면|fig(?:ure)?\.?|図|附图)\s*\d+",
    "formula_mentions": r"수학식|산식|equation|formula|式\s*\d+|公式",
    "parameter_mentions": r"파라미터|매개변수|임계값|가중치|parameter|threshold|weight|閾値|参数|阈值",
    "data_structure_mentions": r"데이터\s*구조|테이블|리스트|배열|행렬|벡터|그래프|data\s*structure|table|list|array|matrix|vector|graph",
    "input_output_mentions": r"입력|출력|input|output|入力|出力|输入|输出",
    "condition_branch_mentions": r"조건|분기|경우|이면|반복|\b(?:condition|branch|if|when|loop)\b|条件|分岐|分支",
    "control_mentions": r"제어|피드백|상태|동기화|control|feedback|state|synchroni[sz]|制御|控制|反馈",
    "step_reference_mentions": r"\bS\d{2,4}\b|단계|step\s*\d+|ステップ|步骤",
}


def build_document_indicators(
    *,
    text_parts: list[Any],
    claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = "\n".join(normalize_text(value) for value in text_parts if normalize_text(value))
    claims = claims or []
    indicators = {
        key: len(re.findall(pattern, text, flags=re.IGNORECASE))
        for key, pattern in DOCUMENT_INDICATOR_PATTERNS.items()
    }
    return {
        "text_available": bool(text),
        "independent_claim_count": sum(1 for claim in claims if claim.get("is_independent")),
        "dependent_claim_count": sum(1 for claim in claims if not claim.get("is_independent")),
        **indicators,
    }


def attach_comparison_document_indicators(item: dict[str, Any]) -> dict[str, Any]:
    technical_content = item.get("technical_content")
    if not isinstance(technical_content, dict):
        technical_content = {}
    claims = item.get("representative_claims")
    if not isinstance(claims, list):
        claims = []
    structured_text_parts = [
        technical_content.get("problem"),
        technical_content.get("solution"),
        technical_content.get("effect"),
        technical_content.get("detailed_description"),
    ]
    return {
        **item,
        "document_indicators": build_document_indicators(
            text_parts=(
                structured_text_parts
                if any(normalize_text(value) for value in structured_text_parts)
                else [item.get("pdf_text")]
            ),
            claims=[claim for claim in claims if isinstance(claim, dict)],
        ),
    }


def build_similar_context(
    *,
    metadata: dict[str, Any],
    representative_cpc: str | None,
    representative_ipc: str | None,
    country_code: str | None,
    output_dir: Path | None,
) -> dict[str, Any]:
    try:
        return {
            **build_similar_patent_context(
                target_metadata=metadata,
                representative_cpc=representative_cpc,
                representative_ipc=representative_ipc,
                country_code=country_code,
                top_k=TECHNOLOGY_COMPARISON_TARGET_COUNT,
                collect_pdf=True,
                output_dir=output_dir,
                pdf_text_limit=None,
            ),
            "comparison_mode": "similar",
        }
    except Exception as exc:
        return {
            "comparison_mode": "similar",
            "representative_cpc": representative_cpc,
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": 0,
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": [f"similar_patent_search_failed:{exc.__class__.__name__}"],
        }


def build_prior_art_context(
    *,
    metadata: dict[str, Any],
    kipris_api_data: dict[str, Any] | None,
    output_dir: Path | None,
    home_country: str | None = None,
    target_fulltext_count: int | None = TECHNOLOGY_COMPARISON_TARGET_COUNT,
) -> dict[str, Any]:
    try:
        return build_prior_art_patent_context(
            target_metadata=metadata,
            kipris_api_data=kipris_api_data,
            collect_pdf=True,
            output_dir=output_dir,
            pdf_text_limit=None,
            home_country=home_country,
            target_fulltext_count=target_fulltext_count,
        )
    except Exception as exc:
        return {
            "comparison_mode": "prior-art",
            "candidate_count": 0,
            "similar_patents": [],
            "prior_art_patents": [],
            "warnings": [f"prior_art_search_failed:{exc.__class__.__name__}"],
        }


def build_hybrid_context(
    *,
    metadata: dict[str, Any],
    kipris_api_data: dict[str, Any] | None,
    representative_cpc: str | None,
    representative_ipc: str | None,
    country_code: str | None,
    similar_dir: Path | None,
    prior_art_dir: Path | None,
    prior_art_context: dict[str, Any] | None = None,
    target_top_k: int = TECHNOLOGY_COMPARISON_TARGET_COUNT,
) -> dict[str, Any]:
    prior_art = prior_art_context or build_prior_art_context(
        metadata=metadata,
        kipris_api_data=kipris_api_data,
        output_dir=prior_art_dir,
        home_country=country_code,
    )
    prior_items = list(prior_art.get("similar_patents") or [])
    # 비교문헌은 전문(본문)이 있어야 쓸 수 있으므로, "후보 수"가 아니라 "전문 성공 수"로 충분 여부를 판정한다.
    prior_fulltext_items = [item for item in prior_items if has_prior_art_fulltext(item)]

    if len(prior_fulltext_items) >= target_top_k:
        return {
            "comparison_mode": "hybrid",
            "selection_policy": "prior-art-only",
            "representative_cpc": representative_cpc,
            "representative_ipc": representative_ipc,
            "country_code": country_code,
            "candidate_count": int(prior_art.get("candidate_count") or 0),
            "similar_patents": compact_comparison_items(prior_fulltext_items[:target_top_k]),
            "target_count": target_top_k,
            "warnings": list(prior_art.get("warnings") or []),
        }

    similar = build_similar_context(
        metadata=metadata,
        representative_cpc=representative_cpc,
        representative_ipc=representative_ipc,
        country_code=country_code,
        output_dir=similar_dir,
    )
    # 부족분만 CPC/IPC 유사특허로 채운다. 전문 있는 선행문헌을 앞에 두고, 그다음 유사특허.
    hybrid_items = merge_hybrid_items(
        prior_items=prior_fulltext_items,
        similar_items=list(similar.get("similar_patents") or []),
        target_count=target_top_k,
    )
    warnings = dedupe_texts(
        [
            *[str(item) for item in prior_art.get("warnings") or []],
            *[str(item) for item in similar.get("warnings") or []],
        ]
    )
    return {
        "comparison_mode": "hybrid",
        "selection_policy": "prior-art-first-then-similar",
        "representative_cpc": representative_cpc,
        "representative_ipc": representative_ipc,
        "country_code": country_code,
        "candidate_count": int(prior_art.get("candidate_count") or 0) + int(similar.get("candidate_count") or 0),
        "similar_patents": compact_comparison_items(hybrid_items),
        "target_count": target_top_k,
        "warnings": warnings,
    }


def merge_hybrid_items(*, prior_items: list[dict[str, Any]], similar_items: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen = set()
    for item in prior_items:
        if len(merged) >= target_count:
            break
        key = item_identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    for item in similar_items:
        if len(merged) >= target_count:
            break
        key = item_identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def compact_comparison_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drop_keys = {
        "pdf_text",
        "pdf_text_excerpt",
        "similarity_text",
        "resolved_search_matches",
        "markdown_paths",
    }
    return [
        {
            "document_role": "prior_art_or_similar_comparison",
            **{
                key: compact_comparison_value(key, value)
                for key, value in item.items()
                if key not in drop_keys
            },
        }
        for item in items
    ]


def compact_comparison_value(key: str, value: Any) -> Any:
    if key == "representative_claims" and isinstance(value, list):
        return [
            {
                **claim,
                "text": normalize_text(claim.get("text"))[:COMPARISON_CLAIM_TEXT_LIMIT],
            }
            for claim in value[:5]
            if isinstance(claim, dict) and normalize_text(claim.get("text"))
        ]
    if key == "technical_content" and isinstance(value, dict):
        return {
            field: normalize_text(value.get(field))[:limit]
            for field, limit in COMPARISON_TECHNICAL_CONTENT_LIMITS.items()
            if normalize_text(value.get(field))
        }
    return value


def apply_technology_scores(result: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    subscores = normalize_candidate_subscores(result.get("subscores") or {})
    technical_differentiation_score = int(subscores["technical_differentiation"]["score"])
    implementation_specificity_score = int(subscores["implementation_specificity"]["score"])
    score = technical_differentiation_score + implementation_specificity_score
    return {
        **result,
        "score": max(0, min(100, score)),
        "grade": grade_for_score(score),
        "subscores": subscores,
        "technology_metrics": metrics,
    }


def normalize_candidate_subscores(subscores: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in TECHNOLOGY_DETAIL_SCORE_CANDIDATES:
        item = dict(subscores.get(key) or {})
        item["score"] = normalize_technology_detail_scores(item, key=key)
        item["max_score"] = TECHNOLOGY_SUBSCORE_MAX_SCORES[key]
        normalized[key] = item
    return normalized


def normalize_technology_detail_scores(item: dict[str, Any], *, key: str) -> int:
    details = item.get("details")
    candidates_by_detail = TECHNOLOGY_DETAIL_SCORE_CANDIDATES[key]
    if not isinstance(details, dict):
        return clamp_int(
            item.get("score"),
            default=0,
            max_value=TECHNOLOGY_SUBSCORE_MAX_SCORES[key],
        )
    normalized_details = {}
    for detail_key, candidates in candidates_by_detail.items():
        raw_detail = details.get(detail_key)
        if isinstance(raw_detail, dict):
            normalized_details[detail_key] = {
                **raw_detail,
                "score": nearest_candidate_score(raw_detail.get("score"), candidates),
            }
        else:
            normalized_details[detail_key] = nearest_candidate_score(raw_detail, candidates)
    item["details"] = normalized_details
    return sum(
        detail.get("score", 0) if isinstance(detail, dict) else detail
        for detail in normalized_details.values()
    )


def nearest_candidate_score(value: Any, candidates: tuple[int, ...]) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    return min(candidates, key=lambda candidate: (abs(candidate - score), -candidate))


def item_identity(item: dict[str, Any]) -> str:
    return "|".join(
        [
            str(item.get("application_number") or ""),
            str(item.get("registration_number") or ""),
            str(item.get("display_number") or ""),
            str(item.get("title") or ""),
        ]
    )


def dedupe_texts(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
