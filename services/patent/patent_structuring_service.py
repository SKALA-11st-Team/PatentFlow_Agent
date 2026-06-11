"""특허 한 건을 정해진 JSON 스키마로 구조화하는 서비스.

타깃 특허와 비교 특허군(선행문헌·CPC유사)을 동일한 schema(key_elements/
key_flow/claims)로 구조화한다. 결과는 권리성·기술성 축이 element 단위 비교에
사용한다. LLM이 생성한 JSON은 schemas.patent_structure로 형식만 검증한다.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import ValidationError

from app.config import settings
from schemas.patent_structure import validate_patent_structure
from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt
from services.observability.langsmith_service import trace

PROMPT_PATH = "valuation/patent_structuring.md"
COMPARISON_TARGET_COUNT = 5
MAX_STRUCTURING_WORKERS = 6


def structure_target_and_comparisons(
    *,
    target_input: dict[str, Any],
    comparison_inputs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """타깃 1건 + 비교 N건을 병렬로 구조화한다.

    각 input dict 형태:
        {"doc_id": str, "specification_text": str, "claims_text": str, "drawings_text": str}
    반환: (target_structure | None, comparison_structures)
    실패한 건은 None으로 떨어지고 comparison 목록에서는 제외된다.
    """
    inputs = [("target", target_input), *[("comparison", item) for item in comparison_inputs]]
    workers = max(1, min(MAX_STRUCTURING_WORKERS, len(inputs)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda pair: _structure_one_safe(pair[1]), inputs))

    target_structure = results[0]
    comparison_structures = [item for item in results[1:] if item is not None]
    return target_structure, comparison_structures


def _structure_one_safe(patent_input: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return structure_one_patent(patent_input)
    except Exception:
        # 한 건 실패가 전체 가치평가를 막지 않도록 None으로 흡수한다.
        return None


@trace(name="patent_structuring", run_type="chain")
def structure_one_patent(patent_input: dict[str, Any]) -> dict[str, Any] | None:
    """특허 1건을 구조화한다. 형식 검증 통과 시 dict, 실패 시 None."""
    doc_id = str(patent_input.get("doc_id") or "")
    specification_text = str(patent_input.get("specification_text") or "").strip()
    claims_text = str(patent_input.get("claims_text") or "").strip()
    if not specification_text and not claims_text:
        return None

    prompt = _build_structuring_prompt(
        doc_id=doc_id,
        specification_text=specification_text,
        claims_text=claims_text,
        drawings_text=str(patent_input.get("drawings_text") or "").strip(),
    )
    raw = call_llm(
        prompt,
        model=settings.openai_valuation_model,
        temperature=0,
        reasoning_effort=settings.openai_valuation_reasoning_effort,
    )
    parsed = parse_json_object(raw)
    if not parsed:
        return None
    if not parsed.get("doc_id"):
        parsed["doc_id"] = doc_id
    validated = _validate(parsed)
    # 비교군 출처(prior_art/similar)를 구조화 결과에 실어, 권리성의 선행문헌-only 필터에 쓴다.
    if validated is not None and patent_input.get("comparison_source"):
        validated["comparison_source"] = str(patent_input["comparison_source"])
    return validated


def _validate(parsed: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return validate_patent_structure(parsed).model_dump()
    except ValidationError:
        return None


def _build_structuring_prompt(
    *,
    doc_id: str,
    specification_text: str,
    claims_text: str,
    drawings_text: str,
) -> str:
    template = load_prompt(PROMPT_PATH).strip()
    payload = {
        "doc_id": doc_id,
        "specification_text": specification_text,
        "claims_text": claims_text,
        "drawings_text": drawings_text,
    }
    return f"{template}\n\nInput JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
