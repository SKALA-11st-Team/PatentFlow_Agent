# 사업연계성 점수 재설계: 제품·기능 직접 매칭도 + 사업 문맥 적합성

작성일: 2026-06-12
대상: `agents/valuation_axes/business_fit.py`, `prompts/valuation/valuation_business_fit.md`, `prompts/supervisor/supervisor_business_fit_check.md`

## 배경 / 문제

사업연계성(business_fit) 축의 3개 subscore 중 **제품·기능 직접 매칭도(45점)** 는
코드의 literal 단어매칭(`score_product_function_direct_match`)으로 채점한다. 이 방식이
다음 문제를 일으킨다.

1. **brittle한 literal 매칭**: 제품명 전체("AccuInsight+ Runtime")가 evidence 본문에
   통째로 박혀야 매칭된다. 공식 페이지가 패밀리명("AccuInsight+")이나 다른 브랜드명
   ("AIOps Platform")으로 부르면 매칭 실패.
2. **오염된 핵심어 분모**: 제목 토큰에서 핵심어를 뽑다 보니 출원인 법인명·명세서
   boilerplate·통문장이 섞여, 매칭 비율(`strong_core_match_ratio`)이 구조적으로 0에 깔린다.
3. **코드 0 vs LLM 12 충돌**: 코드가 quantitative_metrics로 0을 내는데 LLM은 subscores로
   12를 내, supervisor가 불일치로 `valuation_retry`를 반복한다.

핵심 인식: 코드 단어매칭은 채점자가 아니라 **LLM 부풀림을 막는 guardrail**로 도입됐다.
하지만 literal 매칭은 리브랜딩·동의어를 놓치는 false negative가 크고, 단어 엔지니어링
유지비가 높다.

## 두 subscore의 역할 분리 (핵심 원칙)

| | 제품·기능 직접 매칭도 (45) | 사업 문맥 적합성 (25) |
|--|------|------|
| 묻는 것 | **정확한 그 제품이 SK AX에서 상용화·운영된 근거가 있나** | 이 특허가 **SK AX 사업 방향과 맞나** |
| 특허 쪽 기준 | related_product + key_elements/key_flow | 특허의 문제·해결수단·적용대상 |
| 증거 강도 | 직접 (제품명 확인이 게이트) | 간접 (방향 정합) |
| 제품명 없으면 | **0점** | 점수 가능 (방향만 맞아도) |

→ 제품 식별이 게이트인 "직접 증거"(매칭도)와, 제품 없이도 방향만 보는 "정합"(문맥)으로
역할이 갈려 이중계산이 없다. 리브랜딩·흐름만 있는 케이스는 매칭도가 아니라 문맥이 받는다.

## 설계: 제품·기능 직접 매칭도 (45점)

- **채점 주체를 LLM으로 전환.** 코드 단어매칭(정답 생성 + 채점)은 폐기한다.
- **제품 식별이 게이트**: 관련제품명 또는 핵심 브랜드 단축형이 evidence에서 직접
  확인되지 않으면 0점. 회사명·사업영역('AI'/'Data'/'AIOps') 언급만으로는 제품 식별이 아니다.
- 제품 확인 위에서 **key_elements/key_flow**를 evidence와 의미적으로 대조해 점수를 올린다.
  단어 조각이 흩어져 잡히는 것과 **흐름(구성요소 간 관계)까지 설명되는 것**을 구분한다.
- **인용 강제**: 제품·구성요소·흐름 확인 주장 시 evidence 문장 ↔ 제품명/key_element_id/흐름
  연결을 rationale에 인용. 인용 불가 시 인정하지 않는다(부풀림 방어).

### 점수 후보 (A안: 45/36/24/0)

- **45점**: 정확한 제품명/브랜드 확인 + 핵심 구성요소 대부분과 그 흐름까지 evidence에서 확인.
- **36점**: 정확한 제품명/브랜드 확인 + 핵심 구성요소 일부 확인(흐름은 부분적/불명확).
- **24점**: 정확한 제품명/브랜드는 확인되나 특허 핵심 기능과의 직접 연결은 약함.
- **0점**: 정확한 제품명/브랜드가 evidence에서 확인되지 않음.

(기존 `12점` 밴드 삭제 — "산업군 수준 연결"은 매칭도가 아니라 문맥 소관.)

## 설계: 사업 문맥 적합성 (25점)

- 지금처럼 LLM 판단. 단 "정합 구체성" 사다리로 밴드를 명확히 하고, 매칭도와 분리 규칙을 둔다.
- **분리 규칙**: 문맥은 "발명이 자료에 직접 묘사됐나"(매칭도 영역)를 근거로 올리지 않는다.
  오직 *특허의 문제/적용대상 ↔ SK AX 사업 방향* 정합만 본다.
- **부풀림 방어**: "둘 다 AI니까 맞다"류는 **10점 천장**. 18~25는 evidence에서 SK AX의
  특정 서비스/업무가 확인되고 그게 특허 적용대상과 맞물려야 한다(인용 강제).

### 점수 후보 (기존 25/18/10/4/0 유지)

- **25점**: SK AX의 구체적 서비스/업무가 특허의 적용대상·문제를 직접 다룸.
- **18점**: 사업 영역/방향은 자연스럽게 맞으나 구체 업무 1:1 매핑은 부족.
- **10점**: 같은 산업/기술군 수준의 연결만(둘 다 AI·Data 등).
- **4점**: 추정에 가까운 연결, 근거 약함.
- **0점**: 문맥 연결 근거 없음.

### 두 subscore 보완 예 (AccuInsight / AIOps Platform)

| 페이지 상황 | 매칭도(45) | 문맥(25) |
|------|------|------|
| 모델 등록→배포→서빙 흐름 설명 + AccuInsight+ 직접 언급 | 45 | 25 |
| 흐름 설명하나 제품명 없이 "AIOps Platform"으로만 | 0 | 18 |
| "AI 운영 플랫폼 사업" 방향만 언급 | 0 | 18 |
| "SK AX는 AI 합니다" 수준 | 0 | 10 |

## 변경 범위

### 삭제 (제품·기능 매칭도의 룰베이스 정답+채점)
`agents/valuation_axes/business_fit.py`:
- `extract_business_fit_core_terms`, `is_noise_core_term`, 관련 상수
  (`COMPANY_NAME_MARKERS`/`PATENT_BOILERPLATE_TERMS`/`MAX_CORE_TERM_WORDS`)
- `build_product_function_match_summary`, `product_match_level_for`,
  `product_function_rationale`, `evidence_refs_for_terms`
- `score_product_function_direct_match`
- `build_business_fit_quantitative_metrics`에서 `product_function_direct_match` 키 제거
  (관련 STOPWORDS/BROAD_TERMS/WEAK_TERMS는 다른 곳에서 쓰이면 보존, 아니면 정리)

### 유지
- `score_official_evidence_presence` (공식근거 존재성 30점) — 개수 기반, 단어매칭 아님.
- evidence 수집·판별(`select_evidence`, `is_sk_ax_official_evidence` 등).
- `reconcile_business_fit_scores` — LLM subscore 정규화. 단 매칭도 밴드를
  `0/12/24/36/45` → `0/24/36/45`로 수정.

### 추가
- `build_input_payload`에 **`patent_structure`(state.target_structure: key_elements/key_flow)**
  주입 — 새 매칭도 판단의 입력.

### 일관성
- `prompts/valuation/valuation_business_fit.md`: 제품·기능 매칭도 섹션 교체, 점수 후보
  목록에서 `12` 제거.
- `prompts/supervisor/supervisor_business_fit_check.md`: "제품·기능 직접 매칭도는
  0/12/24/36/45" → "0/24/36/45".

## 테스트

- `extract_business_fit_core_terms` 등 삭제 함수의 단위 테스트 제거(`test_valuation.py`).
- `reconcile_business_fit_scores`가 매칭도 새 밴드(0/24/36/45)를 클램프하는지.
- payload에 patent_structure(key_elements/key_flow)가 포함되는지.
- 매칭도 subscore가 LLM 출력에서 0/24/36/45만 갖는지(스키마/검증).
- 회귀: business_fit 관련 기존 테스트가 새 밴드·payload에 맞게 갱신.

## 비목표 (YAGNI)
- 공식근거 존재성(30) 채점 방식은 바꾸지 않는다.
- 다른 축(권리성/기술성/시장성) 채점 방식은 건드리지 않는다.
- key_elements/key_flow 구조화 노드 자체는 변경하지 않는다(입력으로만 사용).
