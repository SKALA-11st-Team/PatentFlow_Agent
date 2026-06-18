"""해외특허 domestic 뉴스 채널의 국가→로케일 매핑.

한국 특허는 기존 naver_news(게이트웨이, 한국어) 경로를 그대로 쓴다. 해외특허는
domestic 뉴스를 대상국 현지 뉴스로 봐야 하므로, Tavily `country` 파라미터로
국가를 한정하고 query rewriting을 대상국 현지어로 생성한다.

- tavily_country: Tavily search의 `country` 파라미터(소문자 풀네임). 단일국으로
  매핑되지 않는 코드(EP 등)는 None → country 미지정 영어 폴백.
- language_label: query rewriting 프롬프트에 주입할 검색어 언어명(영문 표기).
"""
from __future__ import annotations

# 검색어 기본 언어(한국 특허 및 폴백). query_rewriting 프롬프트의 {{domestic_language}}에 주입된다.
DEFAULT_DOMESTIC_LANGUAGE = "Korean"
FALLBACK_FOREIGN_LANGUAGE = "English"

# 국가 코드(대문자) → (tavily_country, language_label)
# tavily_country가 None이면 Tavily country 미지정(영어 폴백)으로 검색한다.
COUNTRY_LOCALE_MAP: dict[str, tuple[str | None, str]] = {
    "US": ("united states", "English"),
    "JP": ("japan", "Japanese"),
    "CN": ("china", "Chinese"),
    "DE": ("germany", "German"),
    "GB": ("united kingdom", "English"),
    "FR": ("france", "French"),
    "TW": ("taiwan", "Chinese"),
    "CA": ("canada", "English"),
    "AU": ("australia", "English"),
    "IN": ("india", "English"),
    "ES": ("spain", "Spanish"),
    "IT": ("italy", "Italian"),
    "NL": ("netherlands", "Dutch"),
    # EP(유럽특허청)는 단일국이 아니므로 country 미지정 + 영어 폴백.
    "EP": (None, "English"),
    "WO": (None, "English"),
}


def normalize_country_code(country: str | None) -> str:
    return str(country or "").strip().upper()


def is_foreign_country(country: str | None) -> bool:
    """KR·빈 값은 국내, 그 외는 해외로 본다(market.py/legal.py와 동일 규칙)."""
    code = normalize_country_code(country)
    return bool(code and code != "KR")


# @author 배세은
# @date 2026-06-13
# @relatedFR FR-007
# @relatedUI UI-005
# @description 해외특허 국가 코드를 (Tavily country, 검색어 언어)로 매핑해 본국 현지 뉴스를
# 현지어로 수집하게 한다 — 해외특허 시장성 평가 근거 수집의 로케일 결정점.
def resolve_domestic_locale(country: str | None) -> tuple[str | None, str]:
    """해외특허 국가 코드 → (tavily_country, language_label).

    매핑되지 않은 해외 국가는 (None, English)로 graceful 폴백한다. KR/빈 값은
    호출부에서 foreign이 아니므로 진입하지 않지만, 방어적으로 폴백을 돌려준다.
    """
    code = normalize_country_code(country)
    if not is_foreign_country(code):
        return (None, DEFAULT_DOMESTIC_LANGUAGE)
    return COUNTRY_LOCALE_MAP.get(code, (None, FALLBACK_FOREIGN_LANGUAGE))
