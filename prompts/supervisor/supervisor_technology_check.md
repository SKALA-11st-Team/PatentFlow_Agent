# Technology Axis Quality Check Criteria

이 문서는 기술성 평가 결과를 검토하기 위한 축별 품질 기준입니다.
라우팅을 결정하는 Supervisor 프롬프트가 아니며, `next_action`을 출력하지 않습니다.
최종 라우팅은 `supervisor_valuation_check.md`에서 수행합니다.

## 검토 대상
- valuation.axes.technology
- patent sections
- technology_metrics
- prior_art_candidates
- citation_evidence
- evidence.samples

## 정상으로 볼 수 있는 상태
- `axis`가 `technology`이고 `label`이 `기술성`입니다.
- `score`, `grade`, `rationale`, `evidence_ids`, `risk_factors`, `missing_information`, `confidence`가 존재합니다.
- `subscores`가 기술 차별성 60점, 구현 구체성 40점 구조와 맞습니다.
- `score`는 기술 차별성 + 구현 구체성 합계와 모순되지 않습니다.
- 법적 권리범위, 시장성, 사업 적용 여부를 기술성 점수 근거로 사용하지 않습니다.

## 중점 검토 기준
1. 기술 차별성
   - 비교군 특허 또는 선행문헌과 기술 구성, 동작 방식, 효과 차이를 비교했는지 확인합니다.
   - “최신 AI 기술”, “시장 성장” 같은 넓은 표현만으로 고득점을 설명하지 않았는지 확인합니다.
   - 규칙 기반 처리, 단순 전처리, 기존 기술 조합 가능성이 확인되면 기술 난이도와 대체 가능성을 과도하게 높게 보지 않았는지 확인합니다.

2. 구현 구체성
   - 명세서의 구성 요소, 처리 절차, 입력/출력, 조건, 예외 또는 변형 설명을 근거로 판단했는지 확인합니다.
   - 구현 예시가 풍부한 경우 구체성 점수는 높을 수 있으나, 이것만으로 기술 차별성까지 높게 설명하지 않았는지 확인합니다.

3. 자료 부족 처리
   - 비교 문헌 PDF가 없거나 제목/초록만 있는 경우 그 한계를 `missing_information` 또는 confidence에 반영했는지 확인합니다.
   - 입력에 없는 성능 수치, benchmark, 제품 구현 사례를 만들지 않았는지 확인합니다.

## 재평가가 필요한 신호
- 기술성 점수가 높은데 비교군 대비 차별 설명이 거의 없습니다.
- 넓은 AI/NLP/자동화 시장 트렌드를 기술성 근거로 사용합니다.
- 구현 구체성 근거만으로 기술 차별성까지 과도하게 높게 평가합니다.
- 권리범위, 침해 가능성, 시장 규모, 사업 적용 여부를 기술성 점수 근거로 사용합니다.

## 근거 재수집이 필요한 신호
- 대상 특허의 초록, 해결수단, 상세설명, 청구항 정보가 대부분 없습니다.
- 비교군 특허 또는 선행문헌 정보가 거의 없어 기술 차별성 판단 근거가 비어 있습니다.
- evidence_id가 실제 evidence_bundle에 존재하지 않습니다.

## 출력 형식
Return ONLY one JSON object.
`next_action`은 출력하지 마세요.

{
  "status": "passed" | "valuation_retry" | "query_rewriting",
  "issues": [],
  "reason": ""
}

status 선택 기준:
- `passed`: 기술성 평가가 자기 기준에 맞고, 기술 차별성·구현 구체성 근거가 확인됨
- `valuation_retry`: 근거는 있으나 기술성 평가 논리, 점수, 표현을 다시 써야 함
- `query_rewriting`: 기술성 판단에 필요한 특허 설명, 비교군, 선행문헌 근거가 부족함
