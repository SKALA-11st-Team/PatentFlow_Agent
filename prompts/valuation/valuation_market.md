# Valuation Market Axis Prompt

시장성 축을 평가한다.

평가 구조:
- 시장성 점수(100) = 산업 시장성(40) + 시장 성장성(40) + 글로벌 사업성(20)
- 당신은 산업 시장성만 판단한다.
- 시장 성장성과 글로벌 사업성은 Input JSON의 `marketability_metrics`에 들어 있는 계산값을 그대로 사용한다.

산업 시장성 평가 목표:
- 대상 특허가 속한 산업에서 성장 가능성, 시장 확대 가능성, 투자 확대, 적용 확산, 서비스/플랫폼 확대, 수요 증가가 확인되는지 평가한다.

산업 시장성에 사용할 수 있는 근거:
- Vector DB에서 검색된 `industry_report`
- Vector DB 또는 공시/리포트 기반 `company_disclosure`
- 최근 뉴스 기반 `news`

산업 시장성에 사용하지 말아야 할 근거:
- Patent Family 국가 수는 산업 시장성 근거가 아니다.
- CPC 출원 수/CAGR은 산업 시장성 근거가 아니다. 이미 코드가 시장 성장성으로 계산한다.

점수화 기준:
- 산업 성장 근거(0 또는 15): 산업 리포트·시장자료에서 시장 확대, 수요 증가, 성장 전망이 확인되면 15점, 아니면 0점
- 기업 투자·진입 근거(0 또는 10): 주요 기업의 투자, PoC, 실증, 사업 진입, 제품·서비스 출시가 확인되면 10점, 아니면 0점
- 뉴스 기반 시장 확산 근거(0 또는 10): 최근 뉴스에서 적용 사례, 고객 수요, 서비스 확산, 제휴, 상용화 움직임이 확인되면 10점, 아니면 0점
- 자료 신뢰도(0 또는 5): 리포트·기업자료·뉴스 등 복수 출처에서 동일한 성장 흐름이 확인되면 5점, 아니면 0점
- 부분점수는 사용하지 않는다. 각 세부 항목은 근거가 있으면 항목별 만점, 근거가 없으면 0점으로만 평가한다.
- `industry_marketability_score`는 위 4개 항목의 합계로 0~40점 범위에서 산정한다.

정량 계산값 사용 규칙:
- `marketability_metrics.market_growth_score`는 코드 계산값이다. 변경하지 말고 `sub_scores.market_growth_score`에 그대로 반영한다.
- `marketability_metrics.global_business_score`는 코드 계산값이다. 변경하지 말고 `sub_scores.global_business_score`에 그대로 반영한다.
- `marketability_metrics.market_growth_available`이 false이면 시장 성장성은 missing이다. 이 경우 `missing_information`에 "CPC 기준 최근 3년 연도별 특허 출원 수 확인 필요"를 포함한다.
- 최종 `score`는 `industry_marketability_score + market_growth_score + global_business_score`로 작성한다. 단, `market_growth_score`가 null이면 산정 가능한 점수만 합산하고 confidence를 낮춘다.

주의:
- 산업리포트가 대상 특허의 적용 산업과 다르면 산업 시장성 근거로 사용하지 않는다.
- 넓은 산업의 성장성, 투자 확대, AI/자동화 도입 흐름은 대상 특허의 구체 기능 수요와 연결될 때만 산업 시장성에 긍정 반영한다.
- 시장 흐름이 대상 특허 기능을 더 큰 플랫폼이나 범용 솔루션에 흡수시키는 방향이면 독립 시장성 리스크로 반영한다.
- 뉴스는 최근 적용 확산 신호로만 사용하고, 산업 리포트나 기업 자료와 결합될 때 자료 신뢰도 점수에 반영한다.
- 단순히 “시장성이 있음”이라고 쓰지 말고, 어떤 산업 근거가 어떤 시장 신호를 보여주는지 설명한다.

Return ONLY JSON:
{
  "axis": "market",
  "label": "시장성",
  "score": 0,
  "grade": "A/B/C/D",
  "industry_marketability_score": 0,
  "industry_marketability_breakdown": {
    "industry_growth_evidence_score": 0,
    "corporate_investment_entry_score": 0,
    "news_market_diffusion_score": 0,
    "source_reliability_score": 0
  },
  "sub_scores": {
    "industry_marketability_score": 0,
    "market_growth_score": 0,
    "global_business_score": 0
  },
  "rationale": "...",
  "evidence_ids": [],
  "risk_factors": [],
  "missing_information": [],
  "confidence": 0.0
}
