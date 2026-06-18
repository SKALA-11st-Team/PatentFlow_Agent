from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from app.config import settings
from services.evidence.compression_service import parse_json_object, to_float
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt


# @author 배세은
# @date 2026-06-04
# @relatedFR FR-003, FR-004
# @relatedUI UI-004
# @description 특허 등록/수정 화면용 관련 사업·기술 분야 자동 추천 에이전트. 관리자 관리 분류 목록
# (taxonomy) 안에서만 LLM으로 추천하며, 신뢰도와 사유를 함께 돌려준다. taxonomy 미제공 시 로컬 DB로 폴백한다.

# VAL-12: load_taxonomy가 호출마다 patents 풀스캔하던 것을 짧은 TTL 캐시로 줄인다
# (taxonomy는 관리자 관리라 변경 빈도가 낮아 수 분 캐시가 안전).
_TAXONOMY_CACHE_TTL_SECONDS = 300.0
_taxonomy_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}


def load_taxonomy() -> dict[str, list[str]]:
    now = time.monotonic()
    cached = _taxonomy_cache["value"]
    if cached is not None and now < _taxonomy_cache["expires_at"]:
        return cached
    result = _load_taxonomy_uncached()
    _taxonomy_cache["value"] = result
    _taxonomy_cache["expires_at"] = now + _TAXONOMY_CACHE_TTL_SECONDS
    return result


def _load_taxonomy_uncached() -> dict[str, list[str]]:
    excluded = ("", "기타", "etc", "ETC", "기타/미정")
    placeholders = ",".join("?" * len(excluded))
    query = f"""
        SELECT
            COALESCE(business_area, '') AS business_area,
            COALESCE(technology_area, '') AS technology_area
        FROM patents
        WHERE business_area NOT IN ({placeholders})
           OR technology_area NOT IN ({placeholders})
    """
    try:
        business_areas: set[str] = set()
        technology_areas: set[str] = set()
        with sqlite3.connect(settings.patent_db_path) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, excluded * 2).fetchall():
                if row["business_area"] and row["business_area"] not in excluded:
                    business_areas.add(row["business_area"])
                if row["technology_area"] and row["technology_area"] not in excluded:
                    technology_areas.add(row["technology_area"])
        return {
            "businessArea": sorted(business_areas),
            "technologyArea": sorted(technology_areas),
        }
    except Exception:
        return {"businessArea": [], "technologyArea": []}


_ABSTRACT_MAX_CHARS = 2000


def _resolve_abstract(abstract: str | None, application_number: str | None) -> str:
    """추천 정확도를 높이기 위한 초록 텍스트를 확보한다.

    제목만으로는 분류 신호가 약하므로(특히 모호한 제목) 특허 초록을 함께 본다.
    호출 측(BE/워크플로)이 abstract를 직접 넘겨주면 그것을 우선 쓰고, 없을 때만
    applicationNumber로 KIPRIS 초록을 best-effort 조회한다. KIPRIS는 외부 API이므로
    '공유 DB 접근은 BE 경유' 원칙과 무관하다. 조회 실패는 제목만으로 폴백.
    """
    text = (abstract or "").strip()
    if not text and application_number:
        try:
            from services.patent.kipris_patent_service import fetch_kipris_abstract

            text = (fetch_kipris_abstract(application_number) or "").strip()
        except Exception:
            text = ""
    return text[:_ABSTRACT_MAX_CHARS]


# @relatedFR FR-003, FR-004
# @relatedUI UI-004
# @description 제목·초록·기존 분류 입력으로 사업/기술 분야를 추천한다. taxonomy 범위 내에서만 추천하고
# 신뢰도(confidence)와 사유(reason)를 반환하며, LLM 실패 시 규칙 기반 폴백으로 대체한다.
def recommend_fields(
    *,
    title: str | None = None,
    management_number: str | None = None,
    application_number: str | None = None,
    technology_area: str | None = None,
    business_area: str | None = None,
    abstract: str | None = None,
    taxonomy: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    # taxonomy는 BE가 관리자 관리 분류 목록을 넘겨주는 것이 정석(에이전트는 공유 DB에 직접 접근하지 않는다).
    # 인자로 받지 못한 경우(로컬/단독 실행)에만 DB에서 폴백 적재한다.
    if taxonomy is None:
        taxonomy = load_taxonomy()
    business_taxonomy = list(taxonomy.get("businessArea") or [])
    technology_taxonomy = list(taxonomy.get("technologyArea") or [])

    payload = {
        "patent": {
            "title": title or "",
            # 제목만으론 분류 신호가 약해 초록을 1순위 근거로 함께 제공한다.
            "abstract": _resolve_abstract(abstract, application_number),
            "managementNumber": management_number or "",
            "applicationNumber": application_number or "",
            "currentTechnologyArea": technology_area or "",
            "currentBusinessArea": business_area or "",
        },
        "taxonomy": {
            "businessArea": business_taxonomy,
            "technologyArea": technology_taxonomy,
        },
    }
    prompt = (
        f"{load_prompt('field_recommendation/field_recommendation.md').strip()}\n\n"
        f"Input JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    parsed = parse_json_object(call_llm(prompt))
    if not parsed:
        return fallback_recommendation(
            business_area=business_area,
            technology_area=technology_area,
            reason="LLM 응답 파싱 실패 - 직접 입력해 주세요.",
        )
    # confidence는 LLM이 라벨(예: "높음")을 숫자 자리에 넣는 혼동을 방어해 안전 변환한다.
    # 비숫자면 to_float가 None을 돌려주고 0.0으로 폴백(다른 파싱 실패와 동일하게 graceful 처리).
    confidence = max(0.0, min(1.0, to_float(parsed.get("confidence")) or 0.0))
    # str 필드는 LLM이 비-str 값을 줄 수 있어 정규화한다(Pydantic str 응답 모델에서 500 방지).
    business = str(parsed.get("businessArea") or business_area or "")
    technology = str(parsed.get("technologyArea") or technology_area or "")
    reason = str(parsed.get("reason") or "")

    # 관리자 관리 분류는 controlled value다. taxonomy 목록이 비어있지 않은데 LLM이 목록 밖
    # 값을 만들어내면 신뢰할 수 없으므로 입력값으로 되돌리고 confidence를 낮춘다.
    rejected: list[str] = []
    if business_taxonomy and business and business not in business_taxonomy:
        rejected.append("관련사업 분야")
        business = business_area or ""
    if technology_taxonomy and technology and technology not in technology_taxonomy:
        rejected.append("관련기술 분야")
        technology = technology_area or ""
    if rejected:
        confidence = min(confidence, 0.3)
        note = f"(주의: {', '.join(rejected)} 추천이 분류 목록에 없어 제외했습니다.)"
        reason = f"{reason} {note}".strip()

    # confidenceText는 LLM 값을 신뢰하지 않고 항상 최종 confidence에서 도출해 일관성을 보장한다
    # (특히 목록 밖 거부로 confidence가 낮아진 경우 LLM이 준 텍스트는 어긋난다).
    confidence_text = confidence_text_for_score(confidence)
    return {
        "businessArea": business,
        "technologyArea": technology,
        "confidence": confidence,
        "confidenceText": confidence_text,
        "reason": reason,
    }


def fallback_recommendation(
    *,
    business_area: str | None,
    technology_area: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "businessArea": business_area or "",
        "technologyArea": technology_area or "",
        "confidence": 0.0,
        "confidenceText": "낮음",
        "reason": reason,
    }


def confidence_text_for_score(confidence: float) -> str:
    if confidence >= 0.7:
        return "높음"
    if confidence >= 0.4:
        return "보통"
    return "낮음"
