# 사업연계성 제품·기능 매칭도 LLM 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사업연계성 "제품·기능 직접 매칭도(45점)"를 코드 literal 단어매칭에서 LLM 인용 기반 채점으로 전환하고, 제품 식별을 게이트로 key_elements/key_flow를 입력에 추가한다.

**Architecture:** 코드는 더 이상 이 subscore를 채점하지 않는다. 룰베이스 정답 생성·채점 함수를 삭제하고, business_fit 입력 payload에 구조화 결과(`state.target_structure`의 key_elements/key_flow)를 넣는다. 채점 기준(밴드 0/24/36/45, 제품 게이트, 인용 강제)은 프롬프트에 정의한다. 사업 문맥 적합성(25점)은 매칭도와 역할이 겹치지 않도록 프롬프트를 정제한다.

**Tech Stack:** Python, pytest. 변경 파일은 `agents/valuation_axes/business_fit.py`, `prompts/valuation/valuation_business_fit.md`, `prompts/supervisor/supervisor_business_fit_check.md`, `tests/test_valuation.py`.

---

## File Structure

- `agents/valuation_axes/business_fit.py` — (1) `build_patent_structure_payload` 추가 + payload 배선, (2) 룰베이스 제품·기능 매칭 함수·상수 삭제, (3) `build_business_fit_quantitative_metrics`에서 `product_function_direct_match` 제거.
- `prompts/valuation/valuation_business_fit.md` — 제품·기능 매칭도 섹션 교체, 문맥 적합성 섹션 정제, 출력 규칙의 매칭도 밴드에서 `12` 제거.
- `prompts/supervisor/supervisor_business_fit_check.md` — 매칭도 밴드 문구에서 `12` 제거.
- `tests/test_valuation.py` — payload 구조 테스트 추가, 삭제 함수/매칭 관련 테스트 제거·정리.

참고(검증 완료):
- `state.target_structure`는 그래프상 `patent_structuring` 노드가 valuation 축보다 먼저 실행되어 business_fit 시점에 채워져 있다.
- `reconcile_business_fit_scores`는 subscore를 `[0, max_score]`로 clamp만 하며 밴드 목록을 검증하지 않는다 → 코드 변경 불필요. `schemas/valuation.py`에도 밴드 검증 없음 → "12 제거"는 프롬프트·supervisor 텍스트만 수정.

---

## Task 1: business_fit payload에 patent_structure(key_elements/key_flow) 추가

**Files:**
- Modify: `agents/valuation_axes/business_fit.py` (`build_input_payload` 함수 + 새 헬퍼)
- Test: `tests/test_valuation.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_valuation.py` 맨 끝에 추가:

```python
def test_build_input_payload_includes_patent_structure():
    from agents.valuation_axes.business_fit import build_input_payload

    state = PatentWorkflowState(
        patent_structured={"title_final": "AI 모델 서빙 시스템", "related_product": "AccuInsight+ Runtime"},
        target_structure={
            "key_elements": [
                {
                    "key_element_id": "K1",
                    "key_element_name": "모델 서빙부",
                    "why_essential": "추론 요청을 처리한다",
                    "core_role": "essential",
                    "spec_support": [{"section": "효과"}],  # 경량화에서 빠져야 하는 필드
                }
            ],
            "key_flow": [
                {
                    "key_element_id": "K1",
                    "next_key_element_id": "K2",
                    "relation_summary": "K1 결과를 K2가 사용",
                    "coupling_strength": "strong",
                }
            ],
        },
    )

    payload = build_input_payload(state=state, evidence=[])
    ps = payload["business_fit_context"]["patent_structure"]

    assert ps["key_elements"][0]["key_element_name"] == "모델 서빙부"
    assert ps["key_elements"][0]["core_role"] == "essential"
    assert "spec_support" not in ps["key_elements"][0]  # 경량화 확인
    assert ps["key_flow"][0]["coupling_strength"] == "strong"


def test_build_input_payload_patent_structure_empty_when_absent():
    from agents.valuation_axes.business_fit import build_input_payload

    state = PatentWorkflowState(patent_structured={"title_final": "AI 모델 서빙 시스템"})
    payload = build_input_payload(state=state, evidence=[])

    assert payload["business_fit_context"]["patent_structure"] == {"key_elements": [], "key_flow": []}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `venv/bin/python -m pytest tests/test_valuation.py::test_build_input_payload_includes_patent_structure -v`
Expected: FAIL — `KeyError: 'patent_structure'`

- [ ] **Step 3: 헬퍼 추가 + payload 배선**

`agents/valuation_axes/business_fit.py`의 `build_input_payload` 함수 바로 위에 헬퍼를 추가한다:

```python
def build_patent_structure_payload(state: PatentWorkflowState) -> dict[str, Any]:
    """구조화 결과(target_structure)에서 제품·기능 매칭 판단에 필요한 부분만 경량화해 전달한다.

    key_elements는 식별·역할 정보만, key_flow는 구성요소 간 관계만 남긴다(명세서 위치·도면 등 제외).
    """
    structure = state.target_structure if isinstance(state.target_structure, dict) else {}
    key_elements = [
        {
            "key_element_id": element.get("key_element_id"),
            "key_element_name": element.get("key_element_name"),
            "why_essential": element.get("why_essential"),
            "core_role": element.get("core_role"),
        }
        for element in (structure.get("key_elements") or [])
        if isinstance(element, dict)
    ]
    key_flow = [
        {
            "key_element_id": flow.get("key_element_id"),
            "next_key_element_id": flow.get("next_key_element_id"),
            "relation_summary": flow.get("relation_summary"),
            "coupling_strength": flow.get("coupling_strength"),
        }
        for flow in (structure.get("key_flow") or [])
        if isinstance(flow, dict)
    ]
    return {"key_elements": key_elements, "key_flow": key_flow}
```

그리고 `build_input_payload`의 `business_fit_context` 딕셔너리에 한 줄을 추가한다(`quantitative_metrics` 위):

```python
    payload["business_fit_context"] = {
        "patent_description": patent_description,
        "patent_structure": build_patent_structure_payload(state),
        "target_source_status": build_target_source_status(state),
        "skax_official_evidence": skax_evidence,
        "sk_owned_media_evidence": sk_owned_media_evidence,
        "sk_ax_relevant_news_evidence": sk_ax_relevant_news_evidence,
        "quantitative_metrics": build_business_fit_quantitative_metrics(
            state=state,
            evidence=evidence,
            patent_description=patent_description,
        ),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `venv/bin/python -m pytest tests/test_valuation.py::test_build_input_payload_includes_patent_structure tests/test_valuation.py::test_build_input_payload_patent_structure_empty_when_absent -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add agents/valuation_axes/business_fit.py tests/test_valuation.py
git commit -m "feat: business_fit payload에 구조화(key_elements/key_flow) 추가

제품·기능 직접 매칭도를 LLM이 발명 구조 기반으로 판단할 수 있도록
target_structure를 경량화해 입력에 전달한다.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 룰베이스 제품·기능 매칭 코드 삭제

**Files:**
- Modify: `agents/valuation_axes/business_fit.py`
- Modify: `tests/test_valuation.py`

- [ ] **Step 1: 매칭 채점 함수·상수 삭제**

`agents/valuation_axes/business_fit.py`에서 아래 정의를 통째로 삭제한다(모두 이 파일 내부에서만 쓰이며, 삭제 후 서로만 참조하던 것들이다):
- `score_product_function_direct_match`
- `build_product_function_match_summary`
- `product_function_rationale`
- `evidence_refs_for_terms`
- 상수 `COMPANY_NAME_MARKERS`, `PATENT_BOILERPLATE_TERMS`, `MAX_CORE_TERM_WORDS`
- `is_noise_core_term`
- `extract_business_fit_core_terms`
- `product_match_level_for`
- `ratio`  (삭제 후 사용처 없음)
- `normalize_core_term`  (삭제 후 사용처 없음)

`STOPWORDS`, `BROAD_TERMS`, `WEAK_TERMS`, `title_keyword_terms`, `strip_korean_particle`,
`is_broad_or_weak_official_evidence`는 **삭제하지 않는다**(다른 함수가 계속 사용).

- [ ] **Step 2: quantitative_metrics에서 매칭 점수 제거**

`build_business_fit_quantitative_metrics` 안에서 아래 줄을 삭제한다:

```python
    product_score = score_product_function_direct_match(description, business_evidence_items)
```

그리고 반환 딕셔너리에서 아래 키를 삭제한다:

```python
        "product_function_direct_match": product_score,
```

(반환 딕셔너리의 `official_business_evidence`, `official_evidence_count`,
`business_evidence_count`, `best_relevance_score` 등 나머지는 그대로 둔다.)

- [ ] **Step 3: 삭제된 함수에 묶인 테스트 정리**

`tests/test_valuation.py`에서:
- `test_extract_business_fit_core_terms_drops_applicant_boilerplate_and_full_title_noise` 함수 전체 삭제.
- `test_business_fit_quantitative_metrics_limits_match_when_core_terms_are_missing` 함수 전체 삭제(제품 매칭 채점만 검증하는 테스트).
- `metrics["product_function_direct_match"]["max_score"] == 45` 단언 줄 삭제.
- `product_function_direct_match`를 참조하는 다른 단언(예: `score == 36`, `product_match_level == "direct"`, `"자산배분" in ...matched_strong_core_terms`)을 삭제한다. 단 같은 테스트의 `official_business_evidence`·`official_evidence_count`·`best_relevance_score` 단언은 유지한다.

찾기: `grep -n "product_function_direct_match\|matched_strong_core_terms\|product_match_level" tests/test_valuation.py`

- [ ] **Step 4: import·구문 검증 + 테스트**

Run: `venv/bin/python -c "import agents.valuation_axes.business_fit" && venv/bin/python -m pytest tests/test_valuation.py -q`
Expected: PASS (삭제한 테스트 외 전부 통과). NameError/미정의 참조가 없어야 한다.

- [ ] **Step 5: 커밋**

```bash
git add agents/valuation_axes/business_fit.py tests/test_valuation.py
git commit -m "refactor: business_fit 제품·기능 매칭 룰베이스 채점 코드 삭제

literal 단어매칭 기반 정답 생성·채점(extract_business_fit_core_terms,
score_product_function_direct_match 등)을 제거. 이 subscore는 LLM이 담당한다.
공식근거 존재성(개수 기반)과 evidence 수집 로직은 유지.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: valuation_business_fit.md 프롬프트 갱신

**Files:**
- Modify: `prompts/valuation/valuation_business_fit.md`

- [ ] **Step 1: 제품·기능 직접 매칭도 섹션 교체**

`2. 제품·기능 직접 매칭도 (45점)` 헤더부터 그 섹션의 점수 후보 목록 끝(`- 0점: 제품/서비스 및 핵심 기능 연결이 확인되지 않음`)까지를 아래로 교체한다:

```markdown
2. 제품·기능 직접 매칭도 (45점)
----------------------------------------

목적:
대상 특허의 관련제품이 SK AX에서 실제로 상용화·운영되고 있다는 근거가 공식/계열
evidence에 있는지를 평가한다. 정확한 제품명/브랜드가 evidence에서 확인되는지가 핵심이며,
그 위에서 특허의 핵심 기능(구성요소·흐름)까지 확인되는지로 점수를 가른다.

판단 입력:
- `patent_description.related_product`: 관련 제품명/브랜드.
- `patent_structure.key_elements`: 발명의 주요 구성요소(핵심/보조)와 역할.
- `patent_structure.key_flow`: 구성요소 간 흐름(입력→처리→출력) 관계.
- SK AX 공식/계열 evidence 본문.

평가 규칙:
- 제품 식별이 게이트다: 관련제품명 또는 그 핵심 브랜드(부가어를 뺀 형태, 예:
  `AccuInsight+ Runtime`→`AccuInsight+`)가 evidence 본문에서 직접 확인되지 않으면 0점이다.
  회사명·사업영역('AI', 'Data', 'AIOps' 등) 언급만으로는 제품 식별로 보지 않는다.
  사업 방향만 맞는 경우는 '사업 문맥 적합성'에서 평가하며, 이 항목에서는 0점이다.
- 제품이 확인된 위에서 evidence가 key_elements를 (다른 표현으로라도) 설명하는지, 나아가
  key_flow의 흐름(구성요소 간 관계)까지 설명하는지 의미적으로 판단해 점수를 올린다.
- 인용 필수: 제품·구성요소·흐름 확인을 주장하면 evidence의 어느 문장이 어느 제품명/
  key_element_id/흐름과 연결되는지 rationale에 구체적으로 인용한다. 인용할 수 없으면 인정하지 않는다.
- 확인되지 않는 구성요소·기능은 `missing_information`에 적는다.
- `quantitative_metrics`에는 제품·기능 매칭 점수가 더 이상 포함되지 않는다. 이 점수는 위
  기준으로 직접 판단한다.

점수 후보:
- 45점: 정확한 제품명/브랜드가 직접 확인되고, 핵심 구성요소 대부분과 그 흐름(관계)까지
  evidence에서 확인됨.
- 36점: 정확한 제품명/브랜드가 직접 확인되고, 핵심 구성요소 일부가 확인됨(흐름은 부분적이거나
  불명확함).
- 24점: 정확한 제품명/브랜드는 확인되나, 특허 핵심 기능과의 직접 연결은 약함.
- 0점: 정확한 제품명/브랜드가 evidence에서 확인되지 않음.
```

- [ ] **Step 2: 사업 문맥 적합성 섹션 정제**

`3. 사업 문맥 적합성 (25점)` 헤더부터 그 섹션 점수 후보 끝(`- 0점: 문맥상 연결 근거가 확인되지 않음`)까지를 아래로 교체한다:

```markdown
3. 사업 문맥 적합성 (25점)
----------------------------------------

목적:
특허의 문제·해결수단·적용 대상이 SK AX 공식/계열 evidence의 서비스·업무 문맥과 얼마나
자연스럽게 연결되는지(사업 방향 정합)를 평가한다. 제품이 evidence에 직접 등장하지 않아도
SK AX가 하는 일과 이 특허의 쓰임이 맞물리면 평가한다.

평가 규칙:
- 매칭도와 분리한다: "발명이 자료에 직접 묘사됐는지"(제품·기능 직접 매칭도 영역)를 근거로 이
  점수를 올리지 않는다. 오직 특허의 문제·적용대상과 SK AX 사업 방향의 정합만 본다.
- 인용 강제: 18점 이상은 evidence에서 SK AX의 특정 서비스·업무가 확인되고 그것이 특허의
  적용대상·문제와 맞물린다는 근거를 rationale에 인용한다.
- 부풀림 방지: 'AI', 'Data', '클라우드'처럼 같은 산업·기술군 수준의 일반적 연결만 있으면
  10점 이하로 평가한다.

점수 후보:
- 25점: SK AX의 구체적 서비스·업무가 특허의 적용대상·문제를 직접 다룸.
- 18점: 사업 영역·적용 방향은 자연스럽게 연결되나 핵심 구현의 1:1 매핑은 부족함.
- 10점: 같은 산업 또는 기술군 수준의 연결만 있음(적용 문맥은 넓거나 간접적).
- 4점: 공식 evidence와 특허 문맥의 연결이 약하거나 추정에 가까움.
- 0점: 문맥상 연결 근거가 확인되지 않음.
```

- [ ] **Step 3: 출력 규칙의 매칭도 밴드 갱신**

`- \`subscores.product_function_direct_match.score\`는 0, 12, 24, 36, 45 중 하나만 사용한다.`
줄을 아래로 바꾼다:

```markdown
- `subscores.product_function_direct_match.score`는 0, 24, 36, 45 중 하나만 사용한다.
```

(`official_business_evidence`(0/8/16/24/30)·`business_context_fit`(0/4/10/18/25) 줄은 그대로 둔다.)

- [ ] **Step 4: 잔존 `12` 확인**

Run: `grep -n "12" prompts/valuation/valuation_business_fit.md`
Expected: 제품·기능 매칭도 관련 `12점`/`0, 12, 24...` 언급이 더 이상 없어야 한다(다른 숫자 12가 우연히 있으면 무관).

- [ ] **Step 5: 커밋**

```bash
git add prompts/valuation/valuation_business_fit.md
git commit -m "feat: business_fit 프롬프트 — 제품·기능 매칭도 제품게이트/인용 기반으로 교체

제품명 확인을 게이트로, key_elements/key_flow로 기능 깊이를 가르는 밴드(0/24/36/45)로
교체. 사업 문맥 적합성은 매칭도와 역할 분리·부풀림 방지 규칙 추가.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: supervisor 점검 기준의 매칭도 밴드 갱신

**Files:**
- Modify: `prompts/supervisor/supervisor_business_fit_check.md`

- [ ] **Step 1: 밴드 문구 교체**

`- 제품·기능 직접 매칭도는 0/12/24/36/45점 중 하나인지 확인합니다.` 줄을 아래로 바꾼다:

```markdown
   - 제품·기능 직접 매칭도는 0/24/36/45점 중 하나인지 확인합니다.
```

- [ ] **Step 2: 확인**

Run: `grep -n "0/12/24/36/45\|0/24/36/45" prompts/supervisor/supervisor_business_fit_check.md`
Expected: `0/24/36/45`만 보이고 `0/12/24/36/45`는 없어야 한다.

- [ ] **Step 3: 커밋**

```bash
git add prompts/supervisor/supervisor_business_fit_check.md
git commit -m "fix: supervisor 제품·기능 매칭도 밴드에서 12 제거 (0/24/36/45)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 전체 회귀 + 정적 점검

**Files:** 없음(검증만)

- [ ] **Step 1: 전체 테스트**

Run: `venv/bin/python -m pytest -q`
Expected: 기존 네트워크 의존 실패 2건(`test_collect_external_evidence_hard_surfaces_gateway_failure`, `test_collect_external_evidence_empty_results_not_flagged_as_gateway_failure`)을 제외하고 전부 통과.

- [ ] **Step 2: business_fit 미사용 참조 점검**

Run: `venv/bin/python -c "import agents.valuation_axes.business_fit"` (오류 없어야 함)
그리고: `grep -n "product_function_direct_match\|extract_business_fit_core_terms\|score_product_function_direct_match" agents/valuation_axes/business_fit.py`
Expected: 코드(주석 제외)에 위 식별자가 더 이상 정의·호출되지 않는다(프롬프트 문자열 언급은 무관).

---

## 완료 기준
- 코드는 제품·기능 직접 매칭도를 채점하지 않는다. business_fit 입력 payload에 key_elements/key_flow가 들어간다.
- 프롬프트가 제품 게이트 + 인용 기반 밴드(0/24/36/45)로 채점을 정의한다. 문맥 적합성은 매칭도와 분리된다.
- supervisor 밴드 문구가 0/24/36/45로 일치한다.
- 네트워크 의존 2건 외 전체 테스트 통과.
