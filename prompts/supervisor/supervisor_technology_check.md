# Technology Axis Quality Check Criteria

이 문서는 기술성 평가 결과를 검토하기 위한 축별 품질 기준입니다.
라우팅을 결정하는 Supervisor 프롬프트가 아니며, `next_action`을 출력하지 않습니다.
최종 라우팅은 4개 축의 status를 결정적으로 집계해 수행하며, 별도의 라우팅 LLM은 없습니다.

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
- 구현 구체성은 단순 존재/부재가 아니라 설명의 충분성에 따라 중간 점수 구간을 사용할 수 있습니다.

## 중점 검토 기준
1. 기술 차별성
   - 비교군 특허 또는 선행문헌과 기술 구성, 동작 방식, 효과 차이를 비교했는지 확인합니다.
   - 비교군 개수만 나열하지 않고, 대상 특허의 차이가 실제 기술 문제 해결 방식에 어떤 의미가 있는지 설명했는지 확인합니다.
   - “최신 AI 기술”, “시장 성장” 같은 넓은 표현만으로 고득점을 설명하지 않았는지 확인합니다.
   - 규칙 기반 처리, 단순 전처리, 기존 기술 조합 가능성이 확인되면 기술 난이도와 대체 가능성을 과도하게 높게 보지 않았는지 확인합니다.

2. 구현 구체성
   - 명세서의 구성 요소, 처리 절차, 입력/출력, 조건, 예외 또는 변형 설명을 근거로 판단했는지 확인합니다.
   - 구성 요소 명칭만 있고 역할·상호관계가 약한데 만점 처리하지 않았는지 확인합니다.
   - 처리 절차가 개략적이거나 구현 조건이 부족하면 중간 점수로 평가했는지 확인합니다.
   - 구현 예시가 풍부한 경우 구체성 점수는 높을 수 있으나, 이것만으로 기술 차별성까지 높게 설명하지 않았는지 확인합니다.

3. 자료 부족 처리
   - 비교 문헌 PDF가 없거나 제목/초록만 있는 경우 그 한계를 `missing_information` 또는 confidence에 반영했는지 확인합니다.
   - 입력에 없는 성능 수치, benchmark, 제품 구현 사례를 만들지 않았는지 확인합니다.

## 재평가가 필요한 신호
- 기술성 점수가 높은데 비교군 대비 차별 설명이 거의 없습니다.
- 넓은 AI/NLP/자동화 시장 트렌드를 기술성 근거로 사용합니다.
- 구현 구체성 근거만으로 기술 차별성까지 과도하게 높게 평가합니다.
- 구성요소/절차/구현설명이 일부만 확인되는데 구현 구체성을 만점에 가깝게 평가합니다.
- 권리범위, 침해 가능성, 시장 규모, 사업 적용 여부를 기술성 점수 근거로 사용합니다.
- SK AX 제품 적용 가능성이나 산업 수요를 기술성 점수 근거로 사용합니다.

## 평가 범위 주의
- 기술성은 대상 특허의 명세서·청구항·비교군(CPC 유사특허)·선행문헌으로만 판단합니다.
- 이 근거는 Naver/글로벌 뉴스/산업 RAG 외부 검색으로 보강되지 않습니다. 따라서 기술성은 외부 근거 재수집(query_rewriting)을 요청하지 않습니다.
- 특허 설명·비교군·선행문헌 정보 자체가 거의 없으면, 이는 특허 수집 단계(patent_check)의 문제이며 이 체크의 재수집 대상이 아닙니다. 주어진 정보로 평가 논리가 타당한지만 봅니다.

## 근거 존재·내용 판단 주의
- evidence.samples에는 이 평가가 인용한 근거(evidence_ids)가 우선 포함되며, 전체 근거의 일부 미리보기입니다.
- 근거의 존재 여부는 evidence.samples가 아니라 known_evidence_ids로 판단하세요. known_evidence_ids에 있으면 그 근거는 존재합니다.
- samples에 본문이 안 보인다는 이유만으로 "근거 누락"으로 단정하지 마세요. 실제로 known_evidence_ids에 없는 항목(unknown_evidence_ids)만 문제 삼습니다.
- 비교군 특허·선행문헌·청구항 텍스트는 evidence_bundle(뉴스·산업 RAG 근거)이 아니라 특허 수집 데이터(claim_context, technology_metrics, citation_evidence)에서 옵니다. 평가가 이를 인용했다고 해서 evidence_ids/samples에 없는 것을 "근거 누락"이나 재평가 사유로 삼지 마세요.

## 출력 형식
Return ONLY one JSON object.
`next_action`은 출력하지 마세요.

{
  "status": "passed" | "valuation_retry",
  "issues": [],
  "reason": ""
}

status 선택 기준:
- `passed`: 기술성 평가가 자기 기준에 맞고, 기술 차별성·구현 구체성 근거가 확인됨
- `valuation_retry`: 기술성 평가 논리, 점수, 표현을 다시 써야 함
