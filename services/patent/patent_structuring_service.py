"""특허 한 건을 정해진 JSON 스키마로 구조화하는 서비스.

타깃 특허와 비교 특허군(선행문헌·CPC유사)을 동일한 schema(key_elements/
key_flow/claims)로 구조화한다. 결과는 권리성·기술성 축이 element 단위 비교에
사용한다. LLM이 생성한 JSON은 schemas.patent_structure로 형식만 검증한다.
"""

from __future__ import annotations

import contextvars
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import ValidationError

from app.config import settings
from schemas.patent_structure import validate_patent_structure
from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace

logger = logging.getLogger(__name__)

STEP1_PROMPT_PATH = "valuation/patent_structuring_step1.md"
STEP2_PROMPT_PATH = "valuation/patent_structuring_step2.md"
COMPARISON_TARGET_COUNT = 3
MAX_STRUCTURING_WORKERS = 6


class PatentStructuringError(Exception):
    """구조화 실패(JSON 파싱·형식검증 등). 사유 문자열을 메시지로 담는다."""


def structure_target_and_comparisons(
    *,
    target_input: dict[str, Any],
    comparison_inputs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, str]]]:
    """타깃 1건 + 비교 N건을 병렬로 구조화한다.

    각 input dict 형태:
        {"doc_id": str, "specification_text": str, "claims_text": str, "drawings_text": str}
    반환: (target_structure | None, comparison_structures, failures)
    실패한 건은 comparison 목록에서 제외되고, 사유가 failures에 기록된다.
    failures 항목: {"role": "target|comparison", "doc_id": str, "reason": str}
    """
    inputs = [("target", target_input), *[("comparison", item) for item in comparison_inputs]]
    workers = max(1, min(MAX_STRUCTURING_WORKERS, len(inputs)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # copy_context로 현재 LangSmith run tree를 워커 스레드에 전파한다. 그래야 각
        # 특허 구조화의 LLM 호출이 patent_structuring 노드 아래 계층형으로 묶이고,
        # 따로 떨어진 root 트레이스로 흩어지지 않는다(compression_service와 동일 패턴).
        futures = [
            executor.submit(contextvars.copy_context().run, _structure_one_outcome, role, item)
            for role, item in inputs
        ]
        outcomes = [future.result() for future in futures]

    target_structure = outcomes[0]["structure"]
    comparison_structures = [o["structure"] for o in outcomes[1:] if o["structure"] is not None]
    failures = [
        {"role": o["role"], "doc_id": o["doc_id"], "reason": o["reason"]}
        for o in outcomes
        if o["structure"] is None and o["reason"] is not None
    ]
    logger.info(
        "patent_structuring 완료: 타깃=%s, 비교군 %d/%d, 실패 %d건",
        "성공" if target_structure is not None else "실패/누락",
        len(comparison_structures),
        len(comparison_inputs),
        len(failures),
    )
    return target_structure, comparison_structures, failures


def _structure_one_outcome(role: str, patent_input: dict[str, Any]) -> dict[str, Any]:
    """구조화 1건을 수행하고 (성공 dict 또는 실패 사유)를 캡처한다.

    한 건 실패가 전체 가치평가를 막지 않도록 예외를 흡수하되, 사유는 로그 +
    반환값으로 남겨 어느 특허가 왜 빠졌는지 추적할 수 있게 한다.
    """
    doc_id = str(patent_input.get("doc_id") or "")
    try:
        structure = structure_one_patent(patent_input)
    except Exception as exc:  # APITimeoutError, PatentStructuringError 등
        reason = f"{type(exc).__name__}: {str(exc)[:200]}".strip()
        logger.warning("patent_structuring 실패 [%s] doc_id=%s: %s", role, doc_id, reason)
        return {"role": role, "doc_id": doc_id, "structure": None, "reason": reason}
    if structure is None:
        # 본문·청구항이 비어 구조화 대상이 아님(실패가 아니라 스킵).
        return {"role": role, "doc_id": doc_id, "structure": None, "reason": None}
    return {"role": role, "doc_id": doc_id, "structure": structure, "reason": None}


@trace(name="patent_structuring", run_type="chain")
def structure_one_patent(patent_input: dict[str, Any]) -> dict[str, Any] | None:
    """특허 1건을 2-pass로 구조화한다. 성공 시 dict, 빈 입력 시 None, 실패 시 예외.

    한 콜에서 전체 JSON을 뽑으면 출력이 길어 truncation·매핑 오류가 잦으므로 두 번에 나눈다.
    Pass1: 명세서 → key_elements + key_flow (청구항 미입력)
    Pass2: 청구항 + Pass1 key_elements → claims 분해 + 각 구성요소 명확성 backfill
    """
    doc_id = str(patent_input.get("doc_id") or "")
    specification_text = str(patent_input.get("specification_text") or "").strip()
    claims_text = str(patent_input.get("claims_text") or "").strip()
    drawings_text = str(patent_input.get("drawings_text") or "").strip()
    if not specification_text and not claims_text:
        return None

    # Pass1 — 명세서에서 구성요소·흐름 추출
    pass1 = _run_structuring_pass(
        STEP1_PROMPT_PATH,
        {
            "doc_id": doc_id,
            "specification_text": specification_text,
            "drawings_text": drawings_text,
        },
        step="step1",
    )

    # Pass2 — 청구항 분해 + 구성요소 명확성. Pass1의 구성요소 목록을 입력으로 준다.
    pass2 = _run_structuring_pass(
        STEP2_PROMPT_PATH,
        {
            "doc_id": doc_id,
            "key_elements": _key_elements_for_step2(pass1.get("key_elements") or []),
            "claims_text": claims_text,
        },
        step="step2",
    )

    merged = _merge_structuring_passes(doc_id=doc_id, pass1=pass1, pass2=pass2)
    try:
        validated = validate_patent_structure(merged).model_dump()
    except ValidationError as exc:
        raise PatentStructuringError(f"schema_validation_failed: {str(exc)[:200]}")
    # 비교군 출처(prior_art/similar)를 구조화 결과에 실어, 권리성의 선행문헌-only 필터에 쓴다.
    if patent_input.get("comparison_source"):
        validated["comparison_source"] = str(patent_input["comparison_source"])
    return validated


def _run_structuring_pass(prompt_path: str, payload: dict[str, Any], *, step: str) -> dict[str, Any]:
    """구조화 한 패스를 실행하고 파싱된 dict를 반환한다(JSON 실패 시 예외)."""
    template = load_prompt(prompt_path).strip()
    prompt = f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    # 구조화는 추출·청구항 분해라 가치평가 축보다 가벼운 모델/추론으로 비용을 낮춘다.
    # 모델 미지정 시 openai_chat_model(gpt-5-mini)로 폴백.
    raw = call_llm(
        prompt,
        model=settings.openai_structuring_model or settings.openai_chat_model,
        temperature=0,
        reasoning_effort=settings.openai_structuring_reasoning_effort,
        timeout=settings.openai_valuation_timeout_seconds,
    )
    parsed = parse_json_object(raw)
    if not parsed:
        raise PatentStructuringError(f"{step}_response_not_json")
    return parsed


def _key_elements_for_step2(key_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass2 입력용으로 구성요소를 경량화한다(청구항 매핑에 필요한 식별 정보만)."""
    compact: list[dict[str, Any]] = []
    for element in key_elements:
        if not isinstance(element, dict):
            continue
        compact.append(
            {
                "key_element_id": element.get("key_element_id"),
                "key_element_name": element.get("key_element_name"),
                "why_essential": element.get("why_essential"),
                "core_role": element.get("core_role"),
            }
        )
    return compact


def _merge_structuring_passes(
    *,
    doc_id: str,
    pass1: dict[str, Any],
    pass2: dict[str, Any],
) -> dict[str, Any]:
    """Pass1(구성요소·흐름) + Pass2(청구항·명확성)를 최종 구조로 합친다."""
    clarity_by_id = {
        str(item.get("key_element_id")): item
        for item in (pass2.get("key_element_clarity") or [])
        if isinstance(item, dict) and item.get("key_element_id")
    }
    key_elements: list[dict[str, Any]] = []
    for element in pass1.get("key_elements") or []:
        if not isinstance(element, dict):
            continue
        clarity = clarity_by_id.get(str(element.get("key_element_id")), {})
        key_elements.append(
            {
                **element,
                "in_independent_claim": bool(clarity.get("in_independent_claim", False)),
                "claim_clarity": clarity.get("claim_clarity"),
                "clarity_issue": str(clarity.get("clarity_issue") or ""),
            }
        )
    return {
        "doc_id": doc_id or pass1.get("doc_id") or pass2.get("doc_id") or "",
        "key_elements": key_elements,
        "key_flow": pass1.get("key_flow") or [],
        "claims": pass2.get("claims") or [],
    }
