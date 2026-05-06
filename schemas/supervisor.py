from typing import Any, Literal
from pydantic import BaseModel, Field


SupervisorStage = Literal[
    "patent_check",
    "summary_check",
    "evidence_check",
    "valuation_check",
    "final_check",
]


class SupervisorDecision(BaseModel):
    passed: bool
    next_action: str
    issues: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

