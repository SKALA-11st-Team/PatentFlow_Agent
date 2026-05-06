from pathlib import Path

from app.config import settings


def load_prompt(name: str) -> str:
    prompt_path = settings.project_root / "prompts" / name
    return prompt_path.read_text(encoding="utf-8")

