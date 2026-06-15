"""Pass1↔Pass2 구조화 병합의 방어 로직 회귀 테스트.

- claim_clarity 누락 시 'unresolved' backfill(스키마 Literal 폭발 방지)
- key_element_id 표기 흔들림(K01 vs K1)을 정규화로 흡수
"""

from services.patent.patent_structuring_service import (
    _merge_structuring_passes,
    _normalize_key_element_id,
)


def test_normalize_key_element_id_canonical_form():
    # 공백 제거 + 대문자 + 숫자 앞자리 0 제거 → 표준형.
    assert _normalize_key_element_id("K01") == "K1"
    assert _normalize_key_element_id("K1") == "K1"
    assert _normalize_key_element_id(" k 1 ") == "K1"
    assert _normalize_key_element_id("K010") == "K10"


def test_merge_backfills_missing_claim_clarity_to_unresolved():
    pass1 = {"key_elements": [{"key_element_id": "K1"}, {"key_element_id": "K2"}]}
    # Pass2가 K2 명확성을 빠뜨림(흔한 LLM 누락).
    pass2 = {
        "key_element_clarity": [
            {"key_element_id": "K1", "claim_clarity": "self_clear", "in_independent_claim": True}
        ]
    }

    merged = _merge_structuring_passes(doc_id="doc-1", pass1=pass1, pass2=pass2)
    by_id = {element["key_element_id"]: element for element in merged["key_elements"]}

    assert by_id["K1"]["claim_clarity"] == "self_clear"
    assert by_id["K1"]["in_independent_claim"] is True
    # K2는 None 대신 안전 기본값으로 backfill → 검증 폭발 없이 보수적으로 처리.
    assert by_id["K2"]["claim_clarity"] == "unresolved"
    assert by_id["K2"]["in_independent_claim"] is False


def test_merge_matches_key_element_id_despite_formatting_drift():
    pass1 = {"key_elements": [{"key_element_id": "K1"}]}
    # Pass2가 같은 구성요소를 'k 01'로 표기해도 정규화로 매칭되어야 한다.
    pass2 = {"key_element_clarity": [{"key_element_id": "k 01", "claim_clarity": "spec_resolved"}]}

    merged = _merge_structuring_passes(doc_id="doc-1", pass1=pass1, pass2=pass2)

    # 매칭 성공 시 backfill('unresolved')이 아니라 Pass2 값이 들어간다.
    assert merged["key_elements"][0]["claim_clarity"] == "spec_resolved"
