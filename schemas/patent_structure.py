"""특허 구성요소 구조화 결과의 JSON 형식 검증용 스키마.

LLM이 생성한 구조화 JSON이 정해진 형식을 지켰는지만 코드로 확인한다.
(내용의 타당성은 검증하지 않는다 — enum/필수필드/교차참조 형식만 본다.)
권리성·기술성 축이 element 단위로 비교하기 위한 입력 계약(contract)이다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_NULLISH_STRINGS = {"null", "none", "n/a", "nan", ""}


def _none_if_nullish(value: Any) -> Any:
    """문자열 "null"/"none"/"" 등을 실제 None으로 정규화한다.

    LLM이 JSON null 대신 문자열 "null"을 넣는 흔한 실수를 흡수한다.
    """
    if isinstance(value, str) and value.strip().lower() in _NULLISH_STRINGS:
        return None
    return value


def _normalize_claim_type_value(value: Any) -> str | None:
    """청구항 유형 표기를 한국어 enum('독립항'/'종속항')으로 정규화한다.

    해외특허 구조화 시 LLM이 현지어 표기(独立項, 独立权利要求, independent claim,
    '独立항' 같은 혼용 등)를 낼 수 있어 이를 흡수한다. 매핑 불가면 None을 반환한다.
    ('independent'가 'dependent'를 부분 문자열로 포함하므로 독립을 먼저 판정한다.)
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if any(token in text for token in ("독립", "独立", "獨立")) or "independent" in lowered:
        return "독립항"
    if any(token in text for token in ("종속", "従属", "従屬", "從屬", "从属")) or "dependent" in lowered:
        return "종속항"
    return None


class SpecSupport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section: str = ""
    location: str = ""
    mapped_spec_content: str = ""


class DrawingSupport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    figure: str = ""
    reference_numbers: list[str] = Field(default_factory=list)
    mapped_drawing_content: str = ""


class KeyElement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key_element_id: str
    key_element_name: str = ""
    why_essential: str = ""
    core_role: Literal["essential", "supporting"]
    spec_support: list[SpecSupport] = Field(default_factory=list)
    drawing_support: list[DrawingSupport] = Field(default_factory=list)
    in_independent_claim: bool = False
    claim_clarity: Literal["self_clear", "spec_resolved", "unresolved"]
    clarity_issue: str = ""
    observability: Literal["external", "inferable", "internal"]


class KeyFlow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key_element_id: str
    next_key_element_id: str
    relation_summary: str = ""
    coupling_strength: Literal["strong", "weak"]


class ClaimElement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim_element_id: str
    claim_element_text: str = ""
    # 해당하는 구성요소가 없는 순수 한정구이면 null.
    maps_to_key_element_id: str | None = None
    role: Literal["essential", "supporting", "limiter"]
    impl_lock: list[str] = Field(default_factory=list)
    antecedent_ok: bool = True
    term_consistent: bool = True

    @field_validator("maps_to_key_element_id", mode="before")
    @classmethod
    def _normalize_maps_to(cls, value: Any) -> Any:
        return _none_if_nullish(value)


class Claim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim_no: str
    type: Literal["독립항", "종속항"]
    category: str = ""
    depends_on: str | None = None
    added_limitation: str | None = None
    claim_elements: list[ClaimElement] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_claim_type(cls, data: Any) -> Any:
        # 해외특허(외국어) 구조화 시 LLM이 청구항 유형을 현지 표기로 내보낼 수 있다.
        # 검증으로 막아 전체 구조화를 실패시키는 대신, 항상 한국어 enum으로 강제 변환한다.
        # 1) 알려진 다국어 표기는 매핑, 2) 매핑 불가(예상 못한 언어 등)면 depends_on 유무로
        #    독립/종속을 언어 무관하게 추론 → 어떤 입력이 와도 깨지지 않는다.
        if not isinstance(data, dict):
            return data
        normalized = _normalize_claim_type_value(data.get("type"))
        if normalized is None:
            normalized = "종속항" if _none_if_nullish(data.get("depends_on")) else "독립항"
        if normalized != data.get("type"):
            data = {**data, "type": normalized}
        return data

    @field_validator("depends_on", "added_limitation", mode="before")
    @classmethod
    def _normalize_nullable(cls, value: Any) -> Any:
        return _none_if_nullish(value)


# @author 배세은
# @date 2026-06-11
# @relatedFR FR-006, FR-007
# @relatedUI UI-005
# @description 특허 한 건의 구성요소 구조화 결과 전체. 권리성·기술성 축이 element 단위로 비교하기 위한
# 입력 계약(청구항 구성요소·핵심요소·흐름)을 정의한다.
class PatentStructure(BaseModel):
    """특허 한 건의 구조화 결과 전체."""

    model_config = ConfigDict(extra="ignore")

    doc_id: str = ""
    key_elements: list[KeyElement] = Field(default_factory=list)
    key_flow: list[KeyFlow] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cross_references(self) -> "PatentStructure":
        # claim_element.maps_to_key_element_id 는 실제 존재하는 K… 이거나 null이어야 한다.
        known_ids = {element.key_element_id for element in self.key_elements}
        for claim in self.claims:
            for element in claim.claim_elements:
                mapped = element.maps_to_key_element_id
                if mapped is not None and mapped not in known_ids:
                    raise ValueError(
                        f"claim_element {element.claim_element_id} maps_to_key_element_id "
                        f"'{mapped}' is not a defined key_element_id"
                    )
        # key_flow 의 양끝 id도 실제 구성요소를 가리켜야 한다.
        for flow in self.key_flow:
            for field_name, value in (
                ("key_element_id", flow.key_element_id),
                ("next_key_element_id", flow.next_key_element_id),
            ):
                if value not in known_ids:
                    raise ValueError(
                        f"key_flow.{field_name} '{value}' is not a defined key_element_id"
                    )
        return self


def validate_patent_structure(parsed: dict) -> PatentStructure:
    """파싱된 dict를 형식 검증한다. 형식 위반 시 pydantic ValidationError를 던진다."""
    return PatentStructure.model_validate(parsed)
