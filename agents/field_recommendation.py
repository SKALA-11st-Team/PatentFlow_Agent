from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import settings
from services.evidence.compression_service import parse_json_object
from services.llm.client_service import call_llm
from services.llm.prompt_service import load_prompt


def load_taxonomy() -> dict[str, list[str]]:
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


def recommend_fields(
    *,
    title: str | None = None,
    management_number: str | None = None,
    application_number: str | None = None,
    technology_area: str | None = None,
    business_area: str | None = None,
) -> dict[str, Any]:
    taxonomy = load_taxonomy()
    payload = {
        "patent": {
            "title": title or "",
            "managementNumber": management_number or "",
            "applicationNumber": application_number or "",
            "currentTechnologyArea": technology_area or "",
            "currentBusinessArea": business_area or "",
        },
        "taxonomy": taxonomy,
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
    confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.0)))
    confidence_text = parsed.get("confidenceText") or confidence_text_for_score(confidence)
    return {
        "businessArea": parsed.get("businessArea") or business_area or "",
        "technologyArea": parsed.get("technologyArea") or technology_area or "",
        "confidence": confidence,
        "confidenceText": confidence_text,
        "reason": parsed.get("reason") or "",
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
