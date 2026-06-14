# Market Axis Quality Check Criteria

이 문서는 시장성 평가 결과를 검토하기 위한 축별 품질 기준입니다.
라우팅을 결정하는 Supervisor 프롬프트가 아니며, `next_action`을 출력하지 않습니다.
최종 라우팅은 4개 축의 status를 결정적으로 집계해 수행하며, 별도의 라우팅 LLM은 없습니다.

## 검토 대상
- valuation.axes.market
- marketability_metrics
- industry_report evidence
- naver_news evidence
- global_news evidence
- evidence.samples

## 정상으로 볼 수 있는 상태
- `axis`가 `market`이고 `label`이 `시장성`입니다.
- `score`, `grade`, `rationale`, `evidence_ids`, `risk_factors`, `missing_information`, `confidence`가 존재합니다.
- 시장성 점수 구조는 산업 시장성 20점 + 시장 성장성 40점 + 글로벌 사업성 20점 + 경쟁성 20점입니다.
- 시장 성장성은 CPC 또는 IPC 기반 18개월 전 종료 3개 1년 구간 공개특허 수, CAGR, 추세 계산값을 그대로 반영합니다.
- 산업 시장성, 글로벌 사업성, 경쟁성은 대상 특허의 핵심 기능과 근거의 직접 연결성을 중심으로 설명합니다.

## 중점 검토 기준
1. 산업 시장성
   - 대상 특허의 구체 기능 또는 직접 적용 산업과 연결된 산업 리포트·국내 뉴스 근거인지 확인합니다.
   - 넓은 AI, 자동화, 디지털 전환 흐름만으로 대상 특허의 직접 시장성을 높게 평가하지 않았는지 확인합니다.
   - SK AX 내부 사업과의 연결성을 시장성 근거로 사용하지 않았는지 확인합니다.
   - 적용 분야 수준의 시장 수요가 있는데도 세부 기능 직접성이 약하다는 이유만으로 곧바로 0점을 주지 않았는지 확인합니다.
   - 적용 분야 수요는 확인되지만 세부 기능 직접성은 약한 경우 8점을 우선 검토했는지 확인합니다.

2. 시장 성장성
   - `marketability_metrics.market_growth_score`를 임의로 바꾸지 않았는지 확인합니다.
   - CPC/IPC 성장성 산정 불가를 낮은 시장성으로 단정하지 않았는지 확인합니다.
   - 산정 불가 시 `missing_information`과 `confidence`로 처리했는지 확인합니다.

3. 글로벌 사업성
   - 글로벌 사업성을 코드 계산값처럼 다루지 않았는지 확인합니다.
   - 해외 뉴스 또는 해외 시장 리포트 근거가 대상 특허의 세부 기능과 직접 연결되는지 확인합니다.
   - 상위 산업 일반론만으로 글로벌 사업성 고득점을 주지 않았는지 확인합니다.

4. 경쟁성
   - 경쟁성은 기존 상용 제품·서비스의 대체 압력 관점으로 설명하는지 확인합니다.
   - 단순 특허 건수, 일반 시장 경쟁 심화, 추상적 경쟁 우려를 경쟁성 근거처럼 쓰지 않았는지 확인합니다.
   - 실제 운영 여부, 제공 기능, 적용 범위, 대체 가능성을 구분해 서술하는지 확인합니다.
   - 상위 서비스 범주의 존재만으로 0점을 주지 않았는지 확인합니다.
   - 대상 특허의 세부 기능 대체 근거가 불명확하면 7점 또는 13점을 우선 검토했는지 확인합니다.

## 재평가가 필요한 신호
- 시장성 고득점인데 근거가 넓은 산업 성장 기사뿐입니다.
- 글로벌 사업성 점수가 해외 시장 직접 연결 없이 높습니다.
- 산업 시장성 0점인데 적용 분야 수준의 시장 수요 신호는 존재합니다.
- 경쟁성 점수가 근거 없이 단정적입니다.
- 경쟁성 0점인데 세부 기능 대체 근거가 아니라 서비스 범주 존재만 제시합니다.
- 시장 성장성 계산값과 subscore가 서로 맞지 않습니다.
- SK AX 사업 연계성을 시장성 점수의 핵심 근거로 설명합니다.

## 근거 재수집이 필요한 신호
- 산업 리포트, 국내 뉴스, 글로벌 뉴스가 모두 부족합니다.
- 경쟁성 판단에 필요한 상용 제품·서비스 후보 근거가 거의 없습니다.
- evidence_id가 실제 evidence_bundle에 존재하지 않습니다.
- 시장성 rationale이 참조하는 기사나 리포트가 evidence.samples에 없습니다.

## query_rewriting은 "채울 수 있는 부족"에만
- query_rewriting은 재검색으로 채워질 수 있는 외부 근거가 빈약할 때만 선택합니다.
- 재검색으로 채워지지 않는 부족은 query_rewriting 사유가 아니라 `missing_information`과 `confidence`로만 처리합니다.
- 시장 성장성 정량값은 외부 검색으로 채워지지 않습니다.

## 근거 존재·내용 판단 주의
- evidence.samples에는 이 평가가 인용한 근거(evidence_ids)가 우선 포함되며, 전체 근거의 일부 미리보기입니다.
- 근거의 존재 여부는 evidence.samples가 아니라 known_evidence_ids로 판단하세요. known_evidence_ids에 있으면 그 근거는 존재합니다.
- samples에 본문이 안 보인다는 이유만으로 "근거 누락"으로 단정하거나 근거 재수집(query_rewriting)을 요청하지 마세요. 실제로 known_evidence_ids에 없는 항목(unknown_evidence_ids)만 문제 삼습니다.

## 출력 형식
Return ONLY one JSON object.
`next_action`은 출력하지 마세요.

{
  "status": "passed" | "valuation_retry" | "query_rewriting",
  "issues": [],
  "reason": ""
}

status 선택 기준:
- `passed`: 시장성 평가가 자기 기준에 맞고, 산업 시장성·시장 성장성·글로벌 사업성·경쟁성 근거가 확인됨
- `valuation_retry`: 근거는 있으나 시장성 평가 논리, 점수, 표현을 다시 써야 함
- `query_rewriting`: 시장성 판단에 필요한 산업 리포트, 뉴스, 글로벌 뉴스, CPC 근거가 부족함
