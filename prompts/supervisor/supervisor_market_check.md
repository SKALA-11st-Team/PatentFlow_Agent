# Market Axis Quality Check Criteria

이 문서는 시장성 평가 결과를 검토하기 위한 축별 품질 기준입니다.
라우팅을 결정하는 Supervisor 프롬프트가 아니며, `next_action`을 출력하지 않습니다.
최종 라우팅은 `supervisor_valuation_check.md`에서 수행합니다.

## 검토 대상
- valuation.axes.market
- marketability_metrics
- industry_report evidence
- naver_news evidence
- gnews evidence
- evidence.samples

## 정상으로 볼 수 있는 상태
- `axis`가 `market`이고 `label`이 `시장성`입니다.
- `score`, `grade`, `rationale`, `evidence_ids`, `risk_factors`, `missing_information`, `confidence`가 존재합니다.
- 시장성 점수 구조는 산업 시장성 40점 + 시장 성장성 40점 + 글로벌 사업성 20점입니다.
- 산업 시장성은 Vector DB 산업 리포트와 Naver 뉴스 중심으로 설명합니다.
- 시장 성장성은 CPC 기반 18개월 전 종료 3개 1년 구간 공개특허 수, CAGR, 추세 계산값을 그대로 반영합니다.
- 글로벌 사업성은 GNews 해외 뉴스 근거만 보조적으로 반영합니다.

## 중점 검토 기준
1. 산업 시장성
   - 대상 특허의 구체 기능 또는 직접 적용 산업과 연결된 산업 리포트·국내 뉴스 근거인지 확인합니다.
   - 넓은 AI, 자동화, 디지털 전환 흐름만으로 대상 특허의 직접 시장성을 높게 평가하지 않았는지 확인합니다.
   - SK AX 내부 사업과의 연결성을 시장성 근거로 사용하지 않았는지 확인합니다.

2. 시장 성장성
   - `marketability_metrics.market_growth_score`를 임의로 바꾸지 않았는지 확인합니다.
   - CPC 성장성 산정 불가를 낮은 시장성으로 단정하지 않았는지 확인합니다.
   - 산정 불가 시 missing_information과 confidence로 처리했는지 확인합니다.

3. 글로벌 사업성
   - GNews 근거가 대상 특허의 핵심 기능 또는 직접 적용 분야와 연결되는지 확인합니다.
   - 해외 뉴스가 단순한 일반 투자 흐름이면 글로벌 사업성 긍정 근거로 과장하지 않았는지 확인합니다.
   - 해외 특허 패밀리 정보를 글로벌 사업성 근거로 사용하지 않았는지 확인합니다.

## 재평가가 필요한 신호
- 시장성 고득점인데 근거가 넓은 산업 성장 기사뿐입니다.
- GNews 또는 Naver 뉴스의 역할이 뒤섞여 있습니다.
- CPC 성장성 계산값과 subscore가 서로 맞지 않습니다.
- SK AX 사업 연계성을 시장성 점수의 핵심 근거로 설명합니다.

## 근거 재수집이 필요한 신호
- 산업 리포트, 국내 뉴스, GNews, CPC 계산값이 모두 부족합니다.
- evidence_id가 실제 evidence_bundle에 존재하지 않습니다.
- 시장성 rationale이 참조하는 기사나 리포트가 evidence.samples에 없습니다.

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
- `passed`: 시장성 평가가 자기 기준에 맞고, 산업 시장성·시장 성장성·글로벌 사업성 근거가 확인됨
- `valuation_retry`: 근거는 있으나 시장성 평가 논리, 점수, 표현을 다시 써야 함
- `query_rewriting`: 시장성 판단에 필요한 산업 리포트, 뉴스, GNews, CPC 근거가 부족함
