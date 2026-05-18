from typing import Any, Literal
from pydantic import BaseModel, Field


SupervisorTeam = Literal["top", "research", "valuation", "writing", "final"]

SupervisorStage = Literal[
    "patent_check",
    "summary_check",
    "evidence_check",
    "valuation_check",
    "writing_check",
    "final_check",
]


class SupervisorDecision(BaseModel):
    passed: bool
    next_action: str
    current_team: SupervisorTeam | None = None
    next_team: SupervisorTeam | None = None
    stage: SupervisorStage | None = None
    route_reason: str = ""
    issues: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
