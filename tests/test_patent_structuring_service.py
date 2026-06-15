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


import json

import pytest

import services.patent.patent_structuring_service as svc


def _fake_passes():
    """schema 검증을 통과하는 최소 pass1/pass2 payload를 step별로 반환하는 fake."""
    def fake_pass(prompt_path, payload, *, step):
        if step == "step1":
            return {
                "doc_id": "D1",
                "key_elements": [
                    {
                        "key_element_id": "K1",
                        "key_element_name": "부재",
                        "why_essential": "필수",
                        "core_role": "essential",
                        "observability": "external",
                    }
                ],
                "key_flow": [],
            }
        return {
            "claims": [{"claim_no": "1", "type": "독립항", "claim_elements": []}],
            "key_element_clarity": [
                {"key_element_id": "K1", "claim_clarity": "self_clear", "in_independent_claim": True}
            ],
        }

    return fake_pass


@pytest.fixture
def cache_env(monkeypatch, tmp_path):
    monkeypatch.setattr(svc.settings, "structuring_cache_dir", tmp_path)
    monkeypatch.setattr(svc.settings, "structuring_cache_enabled", True)
    monkeypatch.setattr(svc.settings, "openai_structuring_model", "gpt-5-mini")
    calls = {"n": 0}
    fake = _fake_passes()

    def counting_pass(prompt_path, payload, *, step):
        calls["n"] += 1
        return fake(prompt_path, payload, step=step)

    monkeypatch.setattr(svc, "_run_structuring_pass", counting_pass)
    return calls, tmp_path


def test_structure_cache_key_changes_with_model_and_content():
    base = svc._structure_cache_key("spec", "claims", "draw", "gpt-5-mini", "low")
    assert base == svc._structure_cache_key("spec", "claims", "draw", "gpt-5-mini", "low")
    assert base != svc._structure_cache_key("spec2", "claims", "draw", "gpt-5-mini", "low")
    assert base != svc._structure_cache_key("spec", "claims", "draw", "gpt-5", "low")
    assert base != svc._structure_cache_key("spec", "claims", "draw", "gpt-5-mini", "high")


def test_structure_one_patent_hits_cache_on_second_call(cache_env):
    calls, _ = cache_env
    inp = {"doc_id": "D1", "specification_text": "본문", "claims_text": "1. 청구항", "drawings_text": ""}

    first = svc.structure_one_patent(inp)
    assert calls["n"] == 2  # step1 + step2

    second = svc.structure_one_patent(inp)
    assert calls["n"] == 2  # 캐시 히트 → LLM 패스 추가 호출 0
    assert second == first


def test_structure_cache_invalidates_when_model_changes(cache_env, monkeypatch):
    calls, _ = cache_env
    inp = {"doc_id": "D1", "specification_text": "본문", "claims_text": "1. 청구항", "drawings_text": ""}

    svc.structure_one_patent(inp)
    assert calls["n"] == 2

    # 모델을 바꾸면 키가 달라져 캐시 미스 → 재구조화
    monkeypatch.setattr(svc.settings, "openai_structuring_model", "gpt-5")
    svc.structure_one_patent(inp)
    assert calls["n"] == 4


def test_structure_cache_body_excludes_comparison_source_but_applied_on_load(cache_env):
    calls, tmp_path = cache_env
    base_inp = {"doc_id": "D1", "specification_text": "본문", "claims_text": "1. 청구항", "drawings_text": ""}

    svc.structure_one_patent(base_inp)  # 캐시 생성(타깃 역할, 출처 없음)
    # 같은 특허를 비교군으로 재사용 → 캐시 히트 + comparison_source 덧입힘
    cmp_result = svc.structure_one_patent({**base_inp, "comparison_source": "prior_art"})
    assert calls["n"] == 2  # 히트
    assert cmp_result["comparison_source"] == "prior_art"

    # 캐시 파일 본문에는 comparison_source가 없어야 한다(역할 무관 재사용 위해).
    cache_file = next(tmp_path.glob("*.json"))
    assert "comparison_source" not in json.loads(cache_file.read_text(encoding="utf-8"))


def test_structure_cache_corrupted_file_is_treated_as_miss(cache_env):
    calls, tmp_path = cache_env
    inp = {"doc_id": "D1", "specification_text": "본문", "claims_text": "1. 청구항", "drawings_text": ""}

    svc.structure_one_patent(inp)
    assert calls["n"] == 2
    cache_file = next(tmp_path.glob("*.json"))
    cache_file.write_text("{ broken json", encoding="utf-8")

    result = svc.structure_one_patent(inp)
    assert calls["n"] == 4  # 손상 캐시 → 미스 처리 후 재생성
    assert result["doc_id"] == "D1"


def test_structure_cache_disabled_skips_cache(cache_env, monkeypatch):
    calls, _ = cache_env
    monkeypatch.setattr(svc.settings, "structuring_cache_enabled", False)
    inp = {"doc_id": "D1", "specification_text": "본문", "claims_text": "1. 청구항", "drawings_text": ""}

    svc.structure_one_patent(inp)
    svc.structure_one_patent(inp)
    assert calls["n"] == 4  # 캐시 off → 매번 재구조화
