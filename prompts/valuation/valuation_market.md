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
- 해외특허의 경우 산업 시장성 판단 시 `mckinsey-technology-trends-outlook-2025.pdf`, `WEF_Top_10_Emerging_Technologies_of_2025.pdf`에서 검색된 industry report를 우선 참고한다.

산업 시장성에 사용하지 말아야 할 근거:
- 분류 기준 출원 수/CAGR은 산업 시장성 근거가 아니다. 이미 코드가 시장 성장성으로 계산한다.
- `source`가 `global_news`인 해외 뉴스는 산업 시장성 점수의 직접 근거로 쓰지 않는다(글로벌 사업성은 Patent Family 기준 코드 계산값을 사용한다).

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
국내특허는 대표 CPC 기준, 해외특허는 대표 IPC 기준 해당 국가 공개 특허를 사용해 현재 시점에서 18개월 전을 마지막 시점으로 하는 3개 1년 구간의 공개 특허 건수 증가율과 추세를 통해 해당 기술 분야의 성장 흐름을 평가한다.

평가 규칙:
- `marketability_metrics.market_growth_score`는 코드 계산값이다.
- 이 값을 변경하지 말고 `subscores.market_growth.score`에 그대로 반영한다.
- `marketability_metrics.market_growth_available`이 false이면 시장 성장성은 산정 불가로 처리한다.
- 이 경우 `missing_information`에는 입력의 계산 기준에 맞는 문구를 유지한다.
- 각 구간의 공개 활동성은 국내특허는 KIPRIS CPC 검색 결과, 해외특허는 KIPRIS IPC 검색 결과 중 해당 국가 문헌의 `OpeningDate` 기준 공개 특허 수를 사용한다.
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
- 연속 감소 또는 증가 구간 없음(평탄·혼합 포함): 0

`subscores.market_growth.details`에는 아래 세부점수를 숫자 필드로 작성한다.
- `cagr_score`: 0, 10, 15, 20, 25 중 하나
- `trend_score`: 0, 8, 15 중 하나


----------------------------------------
3. 글로벌 사업성 (20점)
----------------------------------------

목적:
Patent Family Size(출원 국가 수)를 기준으로 평가대상 기술의 글로벌 시장 확장 가능성을 평가한다. 미국·중국·일본을 포함한 다국가 출원은 글로벌 확장 의지가 강한 것으로 본다. 자국은 제외하고 해외 출원 국가만 본다.

평가 규칙:
- `marketability_metrics.global_business_score`는 Patent Family(출원 국가 수) 기반 코드 계산값이다.
- 이 값을 변경하지 말고 `subscores.global_business.score`에 그대로 반영한다.
- 글로벌 사업성은 특허 패밀리의 해외 출원 국가 수 기준이며, 뉴스 건수나 SK AX 내부 해외 사업 전략과의 적합성 판단이 아니다.
- `marketability_metrics.foreign_country_codes`(자국 제외 해외 출원 국가)와 `family_size`를 근거로 글로벌 확장 범위를 설명한다.
- 미국·중국·일본 등 주요 시장을 포함한 다국가 출원이면 글로벌 확장 가능성이 높은 것으로 설명한다.
- `global_business_score`가 null(패밀리 정보 미가용)이면 0으로 단정하지 말고 "Patent Family 정보 미확인으로 산정 불가"로 중립 서술하고 missing_information·confidence로 처리한다.

점수 후보:
20:
자국을 제외한 해외 출원 국가에 미국·중국·일본 중 하나 이상이 포함된 다국가 특허 패밀리

10:
미국·중국·일본은 없으나 해외 출원 국가가 존재하는 특허 패밀리

0:
해외 출원 없이 국내 단독으로만 출원된 특허 패밀리

작성 주의:
- `global_business_score`가 0이어도 "해외 출원이 없어 점수가 깎였다"처럼 쓰지 않는다.
- 이 경우 "현재 출원 국가 기준으로 글로벌 사업성 가점 근거는 별도로 반영되지 않았다"처럼 중립적으로 작성한다.


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
- `subscores.market_growth.rationale`에는 입력의 계산 기준에 맞춰 대표 CPC 기준 공개 특허 수 또는 대표 IPC 기준 해당 국가 공개 특허 수, CAGR, 최근 3개 구간 공개 활동성 추세 점수 판단을 요약하되 세부점수 항목명을 나열하지 않는다.
- `subscores.global_business.rationale`에는 특허 패밀리의 해외 출원 국가(범위)를 근거로 글로벌 확장 가능성을 요약한다.
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
