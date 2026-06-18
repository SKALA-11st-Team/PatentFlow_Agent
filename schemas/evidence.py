from typing import Any, Literal

from pydantic import BaseModel, Field


# @author 배세은
# @date 2026-05-06
# @relatedFR FR-007
# @relatedUI UI-005
# @description 평가 근거(Evidence) 스키마. 외부 검색·뉴스·공시·경쟁특허·산업 리포트·특허 API 등에서 수집한
# 근거 항목의 공통 형식(출처 유형·제목·URL·본문·관련 축·신뢰도)을 정의한다. 축별 근거 라우팅은 source_type을 따른다.
EvidenceSourceType = Literal[
    "web_search",
    "news",
    "company_disclosure",
    "competitor_patent",
    "industry_report",
    "patent_api",
    "unknown",
]


class Evidence(BaseModel):
    evidence_id: str
    source_type: EvidenceSourceType | str = "unknown"
    source: str
    title: str | None = None
    url: str | None = None
    published_at: str | None = None
    collected_at: str
    content: str | None = None
    summary: str | None = None
    raw_text: str | None = None
    related_axis: list[str] = Field(default_factory=list)
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.content is None:
            self.content = self.summary or self.raw_text or ""
