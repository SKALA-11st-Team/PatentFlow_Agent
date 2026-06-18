from pathlib import Path

from app.config import settings


# @author 배세은
# @date 2026-05-06
# @relatedFR FR-005, FR-006, FR-007, FR-008
# @relatedUI UI-005, UI-008
# @description prompts/ 디렉터리에서 평가·요약·보고서·supervisor 프롬프트 마크다운을 로드하는 단일 진입점.
# 평가 기준 프롬프트(설정 화면에서 관리)도 이 경로로 읽혀 프롬프트가 단일 출처로 유지된다.
def load_prompt(name: str) -> str:
    prompt_path = settings.project_root / "prompts" / name
    return prompt_path.read_text(encoding="utf-8")

