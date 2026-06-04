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
- 최근 뉴스 기반 `news` 중 `source`가 `naver_news`인 국내 뉴스

산업 시장성에 사용하지 말아야 할 근거:
- CPC 출원 수/CAGR은 산업 시장성 근거가 아니다. 이미 코드가 시장 성장성으로 계산한다.
- `source`가 `gnews`인 해외 뉴스는 산업 시장성이 아니라 글로벌 사업성 근거로만 사용한다.

평가 원칙:
- 입력에 없는 시장 규모, 성장률, 수요, 경쟁 환경, 해외 진출 가능성을 추정하지 않는다.
- SK AX 내부 사업, 제품, 포트폴리오, 전략 적합성, 내부 활용 가능성은 사업연계성 축의 역할이다.
- 시장성 축에서는 외부 시장과 산업 수요 중심으로만 평가한다.
- SK AX 내부 사업과의 직접 연결성 판단으로 시장성 점수를 높이거나 낮추지 않는다.
- 자료 부족은 낮은 시장성과 구분한다.
- 근거 부족은 confidence 하락과 missing_information으로 처리하고, 시장 약점으로 단정하지 않는다.


----------------------------------------
1. 산업 시장성 (40점)
----------------------------------------

목적:
대상 특허가 속한 산업에서 성장 가능성, 시장 확대 가능성, 투자 확대, 적용 확산, 서비스/플랫폼 확대, 수요 증가가 확인되는지 평가한다.

평가 범위:
- 당신은 산업 시장성만 직접 판단한다.
- 시장 성장성과 글로벌 사업성은 Input JSON의 `marketability_metrics`에 들어 있는 계산값을 그대로 사용한다.

평가 규칙:
- 각 세부 항목은 부분점수 없이 `근거 있음=만점`, `근거 없음=0점`으로 평가한다.
- 산업리포트가 대상 특허의 적용 산업과 다르면 산업 시장성 근거로 사용하지 않는다.
- 넓은 산업의 성장성, 투자 확대, AI/자동화 도입 흐름은 대상 특허의 구체 기능 수요와 연결될 때만 산업 시장성에 긍정 반영한다.
- 시장 흐름이 대상 특허 기능을 더 큰 플랫폼이나 범용 솔루션에 흡수시키는 방향이면 독립 시장성 리스크로 반영한다.
- 뉴스는 최근 적용 확산 신호로만 사용하고, 산업 리포트와 결합될 때 자료 신뢰도 점수에 반영한다.
- 단순히 "시장성이 있음"이라고 쓰지 말고, 어떤 산업 근거가 어떤 시장 신호를 보여주는지 설명한다.

점수 후보:
산업 성장 근거 15:
산업 리포트·시장자료에서 시장 확대, 수요 증가, 성장 전망이 확인됨

산업 성장 근거 0:
산업 성장 흐름을 확인할 근거가 부족함

기업 투자·진입 근거 10:
주요 기업의 투자, PoC, 실증, 사업 진입, 제품·서비스 출시가 확인됨

기업 투자·진입 근거 0:
기업의 투자 또는 사업 진입 흐름을 확인할 근거가 부족함

뉴스 기반 시장 확산 근거 10:
최근 뉴스에서 적용 사례, 고객 수요, 서비스 확산, 제휴, 상용화 움직임이 확인됨

뉴스 기반 시장 확산 근거 0:
최근 시장 확산 신호를 확인할 근거가 부족함

자료 신뢰도 5:
산업 리포트·뉴스 등 복수 출처에서 동일한 성장 흐름이 확인됨

자료 신뢰도 0:
복수 출처 교차 확인이 부족함

`subscores.industry_marketability.score`는 위 4개 항목의 합계로 0~40점 범위에서 산정한다.

`subscores.industry_marketability.details`에는 아래 세부점수를 숫자 필드로 작성한다.
- `industry_growth_evidence`: 0 또는 15
- `corporate_investment_entry`: 0 또는 10
- `news_market_diffusion`: 0 또는 10
- `source_reliability`: 0 또는 5


----------------------------------------
2. 시장 성장성 (40점)
----------------------------------------

목적:
대표 CPC 기준 현재 시점에서 18개월 전을 마지막 시점으로 하는 3개 1년 구간의 공개 특허 건수 증가율과 추세를 통해 해당 기술 분야의 성장 흐름을 평가한다.

평가 규칙:
- `marketability_metrics.market_growth_score`는 코드 계산값이다.
- 이 값을 변경하지 말고 `subscores.market_growth.score`에 그대로 반영한다.
- `marketability_metrics.market_growth_available`이 false이면 시장 성장성은 산정 불가로 처리한다.
- 이 경우 `missing_information`에 "CPC 기준 18개월 전 종료 3개 1년 구간 공개 특허 수 확인 필요"를 포함한다.
- 각 구간의 공개 활동성은 KIPRIS CPC 검색 결과의 `OpeningDate` 기준 공개 특허 수를 사용한다.
- 산정 불가를 낮은 시장성으로 단정하지 말고 confidence를 낮춘다.

점수 구조:
시장 성장성(40) = 3개 1년 구간 공개 특허 수 CAGR 점수(25) + 최근 3개 구간 공개 활동성 추세 점수(15)

점수 후보:
3년 CAGR 점수:
- 15% 이상: 25
- 8~15%: 20
- 3~8%: 15
- 0~3%: 10
- 음수: 0

최근 3년 추세 점수:
- 연속 증가: 15
- 일부 증가: 8
- 연속 감소: 0

`subscores.market_growth.details`에는 아래 세부점수를 숫자 필드로 작성한다.
- `cagr_score`: 0, 10, 15, 20, 25 중 하나
- `trend_score`: 0, 8, 15 중 하나


----------------------------------------
3. 글로벌 사업성 (20점)
----------------------------------------

목적:
GNews로 수집된 해외 뉴스 근거를 바탕으로 해당 기술 분야의 글로벌 시장 관심, 해외 적용 흐름, 글로벌 자산관리·AI 투자 흐름이 확인되는지 평가한다.

평가 규칙:
- `marketability_metrics.global_business_score`는 코드 계산값이다.
- 이 값을 변경하지 말고 `subscores.global_business.score`에 그대로 반영한다.
- 글로벌 사업성은 해외 뉴스에서 확인되는 외부 시장 관심과 적용 흐름 판단이며, SK AX 내부 해외 사업 전략과의 적합성 판단이 아니다.
- GNews 근거가 부족하면 해외 시장 확장 가능성을 단정하지 않는다.
- GNews 근거 부족을 시장성 감점 사유처럼 길게 설명하지 않는다.
- GNews에서 확인된 해외 뉴스 근거가 있을 때만 글로벌 사업성의 보조 긍정 근거로 설명한다.

점수 후보:
20:
GNews에서 해외 또는 글로벌 시장의 직접적 적용 흐름·투자 흐름·자산관리 수요 근거가 3건 이상 확인됨

10:
GNews에서 해외 또는 글로벌 시장의 적용 흐름·투자 흐름·자산관리 수요 근거가 1~2건 확인됨

0:
GNews 기반 글로벌 시장 근거가 확인되지 않음

작성 주의:
- `global_business_score`가 0이어도 "해외 뉴스가 없어 점수가 깎였다"처럼 쓰지 않는다.
- 이 경우 "현재 입력 기준으로 글로벌 사업성 가점 근거는 별도로 반영되지 않았다"처럼 중립적으로 작성한다.


----------------------------------------
종합 점수 산정
----------------------------------------

score = 산업 시장성 + 시장 성장성 + 글로벌 사업성

`score`는 `subscores.industry_marketability.score + subscores.market_growth.score + subscores.global_business.score`로 작성한다.

단, `subscores.market_growth.score`가 null이면 산정 가능한 점수만 합산하고 confidence를 낮춘다.

grade:
80 이상 -> A
60 이상 -> B
40 이상 -> C
미만 -> D


----------------------------------------
confidence 기준
----------------------------------------

confidence:
0.0 ~ 1.0

0.8~1.0:
외부 시장 근거, CPC 성장성 계산값, GNews 기반 글로벌 시장 근거가 충분하고 판단이 비교적 명확함

0.5~0.79:
간접 근거는 있으나 시장 성장성, 해외 확장 가능성, 산업 시장 근거 중 일부 추가 확인이 필요함

0.0~0.49:
외부 시장 근거가 부족하거나, CPC 성장성 계산값이 없거나, 대상 특허와 시장 근거의 관련성이 약함


출력 규칙:
- Return ONLY one JSON object.
- Markdown, 설명 문구, 코드블록을 출력하지 않는다.
- 점수 감점 사유와 자료 부족 사유를 구분한다.
- 실제 시장 리스크만 `risk_factors`에 작성한다.
- 자료 부족은 `missing_information`에 작성한다.
- 자료 부족에는 "정보 부족 있음", "추가 확인 필요", "N/A"를 적절히 사용한다.
- "정보 없음 = 낮은 시장성"으로 해석하지 않는다.
- 확인되지 않은 시장 규모, 성장률, 수요, 해외 진출 가능성을 단정하지 않는다.
- SK AX 내부 사업과의 직접 연결성 또는 활용 가능성은 사업연계성 축에서 판단한다고 표현한다.
- `subscores` 필드는 아래 JSON 예시와 같은 객체 구조로 작성한다.
- 각 세부 평가지표는 `label`, `score`, `max_score`, `rationale`을 포함한다.
- `subscores.industry_marketability.details`와 `subscores.market_growth.details`에는 세부점수만 넣고 설명 문장은 넣지 않는다.
- `subscores.industry_marketability.rationale`에는 산업 성장 근거, 기업 투자·진입 근거, 뉴스 기반 시장 확산 근거, 자료 신뢰도 판단을 함께 요약하되 세부점수 항목명을 나열하지 않는다.
- `subscores.market_growth.rationale`에는 대표 CPC 기준 18개월 전 종료 3개 1년 구간의 공개 특허 수, CAGR, 최근 3개 구간 공개 활동성 추세 점수 판단을 요약하되 세부점수 항목명을 나열하지 않는다.
- `subscores.global_business.rationale`에는 GNews 해외 뉴스 근거에서 확인된 글로벌 시장 관심, 해외 적용 흐름, 글로벌 자산관리·AI 투자 흐름을 요약한다.
- legacy score fields, `industry_marketability_score`, `industry_marketability_breakdown`은 출력하지 않는다.


Return ONLY JSON:
{
  "axis": "market",
  "label": "시장성",
  "score": 0,
  "subscores": {
    "industry_marketability": {
      "label": "산업 시장성",
      "score": 0,
      "max_score": 40,
      "details": {
        "industry_growth_evidence": 0,
        "corporate_investment_entry": 0,
        "news_market_diffusion": 0,
        "source_reliability": 0
      },
      "rationale": "..."
    },
    "market_growth": {
      "label": "시장 성장성",
      "score": 0,
      "max_score": 40,
      "details": {
        "cagr_score": 0,
        "trend_score": 0
      },
      "rationale": "..."
    },
    "global_business": {
      "label": "글로벌 사업성",
      "score": 0,
      "max_score": 20,
      "rationale": "..."
    }
  },
  "grade": "A/B/C/D",
  "rationale": "...",
  "evidence_ids": [],
  "risk_factors": [],
  "missing_information": [],
  "confidence": 0.0
}
