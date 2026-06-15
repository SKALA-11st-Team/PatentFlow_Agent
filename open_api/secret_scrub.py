from __future__ import annotations

import os
import re

# EXT-10: KIPRIS ServiceKey 등 시크릿이 에러 메시지·경고로 평문 노출되지 않도록 마스킹한다.
# (게이트웨이 _sanitize_external_error와 에이전트 KiprisError 경로가 공유한다.)
_SECRET_ENV_NAMES = (
    "GNEWS_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "DART_KEY",
    "KIPRIS_SERVICE_KEYS",
    "KIPRIS_SERVICE_KEY",
    "KIPRIS_API_KEY",
    "SERVICE_KEY",
)

# 쿼리스트링으로 전달된 per-request 인증키(env에 없어 값 replace로 못 가림)도 마스킹한다.
_QUERY_SECRET_RE = re.compile(r"(?i)(ServiceKey|serviceKey|accessKey|crtfc_key)=[^&\s'\"]+")


def scrub_secrets(text: str) -> str:
    """문자열에서 알려진 시크릿(env 값)과 쿼리스트링 인증키를 마스킹한다."""
    if not text:
        return text
    result = str(text)
    # 빈 문자열을 replace에 넘기면 모든 문자 사이에 ***가 삽입되므로 truthy 값만 마스킹한다.
    for env_name in _SECRET_ENV_NAMES:
        secret = os.getenv(env_name)
        if secret:
            result = result.replace(secret, "***")
    return _QUERY_SECRET_RE.sub(r"\1=***", result)
