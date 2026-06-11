from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

# @relatedFR FR-006
# @relatedUI UI-005
# @description 평가 워크플로 진행 단계(특허 이해→근거 수집→…→완료)를 patent_id별로
# 보관하는 스레드 안전 인메모리 레지스트리. BE(Spring)가
# GET /api/v1/ai/patents/{patent_id}/evaluate/progress 를 프록시해 FE에 노출한다.
# 에이전트 단일 프로세스 전제(평가는 요청 스레드에서 직접 실행)이며,
# 멀티 레플리카 배포 시에는 외부 스토어로 교체가 필요하다.

# 진행 단계(순서 고정)와 표시용 한국어 라벨.
STAGE_LABELS: dict[str, str] = {
    "PREPARING": "특허 이해",
    "EVIDENCE_COLLECTION": "근거 수집",
    "EVIDENCE_COMPRESSION": "근거 압축",
    "VALUATION": "4축 평가",
    "WRITING": "레포트 작성",
    "VALIDATION": "검증",
    "DONE": "완료",
}

_LOCK = Lock()
_PROGRESS: dict[str, dict[str, str]] = {}


def set_stage(patent_id: str | int | None, stage: str) -> None:
    # 알 수 없는 단계/빈 patent_id는 조용히 무시한다(진행 표시는 비치명 부가 기능).
    key = str(patent_id or "").strip()
    if not key or stage not in STAGE_LABELS:
        return
    entry = {
        "stage": stage,
        "stageLabel": STAGE_LABELS[stage],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        _PROGRESS[key] = entry


def get(patent_id: str | int | None) -> dict[str, str] | None:
    key = str(patent_id or "").strip()
    with _LOCK:
        entry = _PROGRESS.get(key)
        return dict(entry) if entry else None


def clear(patent_id: str | int | None) -> None:
    key = str(patent_id or "").strip()
    with _LOCK:
        _PROGRESS.pop(key, None)
