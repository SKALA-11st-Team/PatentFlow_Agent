from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock

# @author 유건욱
# @date 2026-06-12
# @relatedFR FR-006
# @relatedUI UI-005
# @description 평가 워크플로 진행 단계(특허 이해→근거 수집→…→완료)를 patent_id별로
# 보관하는 스레드 안전 인메모리 레지스트리. BE(Spring)가
# GET /api/v1/ai/patents/{patent_id}/evaluate/progress 를 프록시해 FE에 노출한다.
# 에이전트 단일 프로세스 전제(평가는 요청 스레드에서 직접 실행)이며,
# 멀티 레플리카 배포 시에는 외부 스토어로 교체가 필요하다.
# 평가 도중 프로세스가 죽거나 폴링이 끝까지 오지 않아 DONE/clear에 도달하지 못한
# 엔트리가 영구히 쌓이지 않도록, set_stage 시 updatedAt 기반 TTL 만료 엔트리를
# 정리하고 최대 항목 수를 LRU로 제한한다(백그라운드 스레드 없이 쓰기 시점에 정리).

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

# DONE/clear에 도달하지 못한 엔트리가 무한 적재되지 않도록 하는 상한.
# 평가는 분 단위로 끝나므로 TTL은 넉넉히, 최대 항목 수는 동시 평가 수 대비 충분히 둔다.
_ENTRY_TTL = timedelta(hours=6)
_MAX_ENTRIES = 1024

_LOCK = Lock()
_PROGRESS: dict[str, dict[str, str]] = {}


def _evict_locked(now: datetime) -> None:
    # _LOCK을 이미 보유한 상태에서 호출한다. updatedAt 기반 TTL 만료 엔트리를 제거하고,
    # 그래도 상한을 넘으면 가장 오래된(updatedAt 기준) 엔트리부터 LRU로 제거한다.
    expired = [
        key
        for key, entry in _PROGRESS.items()
        if _is_expired(entry.get("updatedAt"), now)
    ]
    for key in expired:
        _PROGRESS.pop(key, None)
    if len(_PROGRESS) > _MAX_ENTRIES:
        for key, _ in sorted(
            _PROGRESS.items(), key=lambda item: item[1].get("updatedAt", "")
        )[: len(_PROGRESS) - _MAX_ENTRIES]:
            _PROGRESS.pop(key, None)


def _is_expired(updated_at: str | None, now: datetime) -> bool:
    if not updated_at:
        return True
    try:
        ts = datetime.fromisoformat(updated_at)
    except ValueError:
        return True
    return now - ts > _ENTRY_TTL


def set_stage(patent_id: str | int | None, stage: str) -> None:
    # 알 수 없는 단계/빈 patent_id는 조용히 무시한다(진행 표시는 비치명 부가 기능).
    key = str(patent_id or "").strip()
    if not key or stage not in STAGE_LABELS:
        return
    now = datetime.now(timezone.utc)
    entry = {
        "stage": stage,
        "stageLabel": STAGE_LABELS[stage],
        "updatedAt": now.isoformat(),
    }
    with _LOCK:
        _PROGRESS[key] = entry
        _evict_locked(now)


def get(patent_id: str | int | None) -> dict[str, str] | None:
    key = str(patent_id or "").strip()
    now = datetime.now(timezone.utc)
    with _LOCK:
        entry = _PROGRESS.get(key)
        if not entry:
            return None
        # TTL이 지난 엔트리는 낡은 단계를 돌려주지 않고 제거한다(폴링이 404를 받게 함).
        if _is_expired(entry.get("updatedAt"), now):
            _PROGRESS.pop(key, None)
            return None
        return dict(entry)


def clear(patent_id: str | int | None) -> None:
    key = str(patent_id or "").strip()
    with _LOCK:
        _PROGRESS.pop(key, None)
