from typing import Any, Literal
from pydantic import BaseModel, Field


# @author 배세은
# @date 2026-05-06
# @relatedFR FR-006, FR-007, FR-008
# @relatedUI UI-005
# @description supervisor 판정 스키마. 워크플로 품질 게이트의 결과(통과 여부·다음 팀/액션·검사 단계·
# 반려 사유·누락 근거)를 표준화해 노드 간 라우팅 결정을 전달한다.
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
