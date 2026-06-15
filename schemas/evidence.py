from typing import Any, Literal

from pydantic import BaseModel, Field


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
