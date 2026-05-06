from pydantic import BaseModel, Field


class ValuationResult(BaseModel):
    total_score: float | None = None
    recommendation: str | None = None
    key_evidence_ids: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)

