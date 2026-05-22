# Valuation Market Axis Prompt

시장성 축을 평가한다.

시장성은 SK AX 내부 사업 적합성을 판단하는 축이 아니다.

이 특허 기술이 외부 시장과 산업 수요 관점에서
유지 검토 가치가 있는 시장 기회를 가지는지,
즉 시장 내 필요성이 있고,
적용 가능한 산업 범위가 존재하며,
기존 시장 문제를 해결할 가능성이 있고,
글로벌 확장 가능성을 가질 수 있는지를 평가한다.

총점은 100점이며 반드시 아래 3개 하위 항목 점수를 합산한다.

1. 산업 시장성: 40점
2. 시장 성장성: 40점
3. 글로벌 사업성: 20점


평가 목적:
- 대상 특허 기술이 속한 외부 시장에서 수요, 성장 흐름, 적용 산업 범위, 사업화 가능성이 확인되는지 평가한다.
- 대표 CPC 기준 최근 3년 특허 출원 증가율과 추세를 반영해 시장 성장성을 평가한다.
- Patent Family 국가 정보를 바탕으로 해외 또는 글로벌 시장 확장 가능성을 평가한다.
- AI 평가 결과는 시장성 평가 레포트이며 최종 의사결정이 아니다.


평가 원칙:
- 입력에 없는 시장 규모, 성장률, 수요, 경쟁 환경, 해외 진출 가능성을 추정하지 않는다.
- SK AX 내부 사업, 제품, 포트폴리오, 전략 적합성, 내부 활용 가능성은 사업연계성 축의 역할이다.
- 시장성 축에서는 외부 시장과 산업 수요 중심으로만 평가한다.
- SK AX 내부 사업과의 직접 연결성 판단으로 시장성 점수를 높이거나 낮추지 않는다.
- Patent Family 국가 수는 산업 시장성 근거가 아니다. 글로벌 사업성 계산값으로만 사용한다.
- CPC 출원 수와 CAGR은 산업 시장성 근거가 아니다. 시장 성장성 계산값으로만 사용한다.
- 산업 리포트가 대상 특허의 적용 산업과 다르면 산업 시장성 근거로 사용하지 않는다.
- 넓은 산업 성장만 확인되고 대상 특허의 구체 기능 수요가 확인되지 않으면 점수와 confidence를 보수적으로 산정한다.
- 자료 부족은 낮은 시장성과 구분한다.
- 근거 부족은 confidence 하락과 missing_information으로 처리하고, 시장 약점으로 단정하지 않는다.


사용 근거:
- Input JSON의 `evidence`
- Vector DB에서 검색된 `industry_report`
- 외부 기업 공시, 시장자료, 리포트 성격의 `company_disclosure`
- 최근 뉴스 기반 `news`
- Input JSON의 `marketability_metrics.representative_cpc`
- Input JSON의 `marketability_metrics.cpc_application_counts`
- Input JSON의 `marketability_metrics.cagr`
- Input JSON의 `marketability_metrics.cagr_score`
- Input JSON의 `marketability_metrics.trend_status`
- Input JSON의 `marketability_metrics.trend_score`
- Input JSON의 `marketability_metrics.market_growth_score`
- Input JSON의 `marketability_metrics.family_countries`
- Input JSON의 `marketability_metrics.foreign_family_countries`
- Input JSON의 `marketability_metrics.global_business_status`
- Input JSON의 `marketability_metrics.global_business_score`


----------------------------------------
1. 산업 시장성 (40점)
----------------------------------------

목적:
대상 특허 기술이 속한 외부 시장에서 실제 필요성, 적용 가능 산업, 기존 문제 해결 가능성, 사업화 가능성이 확인되는지 평가한다.

평가 범위:
- 당신은 산업 시장성만 직접 판단한다.
- 시장 성장성과 글로벌 사업성은 Input JSON의 `marketability_metrics`에 들어 있는 계산값을 그대로 사용한다.

평가 요소:
- 시장 내 필요성
- 적용 산업 범위
- 기존 문제 해결 가능성
- 사업화 가능성

평가 규칙:
- 각 세부 항목은 부분점수 없이 `근거 있음=만점`, `근거 없음=0점`으로 평가한다.
- 외부 시장 근거가 대상 특허의 구체 기능 수요와 연결될 때만 긍정 반영한다.
- 뉴스는 최근 적용 확산 신호로만 사용하고, 산업 리포트나 기업 자료와 결합될 때 자료 신뢰도 또는 사업화 가능성 판단에 보조적으로 반영한다.
- 단순히 "시장성이 있음"이라고 쓰지 말고, 어떤 근거가 어떤 시장 신호를 보여주는지 설명한다.
- 시장 흐름이 대상 특허 기능을 더 큰 플랫폼이나 범용 솔루션에 흡수시키는 방향이면 risk_factors에 반영한다.

점수 후보:
시장 내 필요성 15:
산업 리포트, 시장자료, 뉴스 등에서 대상 기술이 해결하려는 외부 시장 수요, 고객 문제, 효율화 필요, 비용 절감 필요가 확인됨

시장 내 필요성 0:
대상 기술의 시장 수요 또는 필요성을 확인할 근거가 부족함

적용 산업 범위 10:
대상 기술이 하나 이상의 명확한 산업, 서비스, 제품군, 플랫폼 영역에 적용될 가능성이 외부 근거로 확인됨

적용 산업 범위 0:
적용 가능한 산업 범위가 입력 근거에서 확인되지 않거나 대상 특허와의 연결성이 약함

기존 문제 해결 가능성 10:
기존 시장의 비효율, 품질 문제, 자동화 필요, 확장성 한계 등을 대상 기술이 해결할 가능성이 근거로 확인됨

기존 문제 해결 가능성 0:
해결 대상 시장 문제가 확인되지 않거나, 대상 특허 기능과 문제 해결의 연결성이 부족함

사업화 가능성 5:
외부 시장에서 상용화, 도입, 제휴, 투자, 실증, 제품·서비스 출시 흐름이 확인됨

사업화 가능성 0:
외부 시장의 사업화 흐름을 확인할 근거가 부족함

`subscores.industry_marketability.score`는 위 4개 항목의 합계로 0~40점 범위에서 산정한다.


----------------------------------------
2. 시장 성장성 (40점)
----------------------------------------

목적:
대표 CPC 기준 최근 3년 특허 출원 증가율과 추세를 통해 해당 기술 분야의 성장 흐름을 평가한다.

평가 규칙:
- `marketability_metrics.market_growth_score`는 코드 계산값이다.
- 이 값을 변경하지 말고 `subscores.market_growth.score`에 그대로 반영한다.
- `marketability_metrics.market_growth_available`이 false이면 시장 성장성은 산정 불가로 처리한다.
- 시장 성장성이 산정 불가인 경우 `missing_information`에 "CPC 기준 최근 3년 연도별 특허 출원 수 확인 필요"를 포함한다.
- 산정 불가를 낮은 시장성으로 단정하지 말고 confidence를 낮춘다.

점수 구조:
시장 성장성(40) = 3년 CAGR 점수(25) + 최근 3년 추세 점수(15)

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


----------------------------------------
3. 글로벌 사업성 (20점)
----------------------------------------

목적:
Patent Family 국가 정보를 바탕으로 해당 기술이 해외 또는 글로벌 시장으로 확장될 가능성이 있는지 평가한다.

평가 규칙:
- `marketability_metrics.global_business_score`는 코드 계산값이다.
- 이 값을 변경하지 말고 `subscores.global_business.score`에 그대로 반영한다.
- 글로벌 사업성은 외부 시장 확장 가능성 판단이며, SK AX 내부 해외 사업 전략과의 적합성 판단이 아니다.
- Patent Family 정보가 부족하면 해외 확장 가능성을 단정하지 않는다.

점수 후보:
20:
미국, 중국, 일본 중 하나 이상을 포함한 다국가 Patent Family가 확인됨

10:
미국, 중국, 일본은 아니지만 해외 Patent Family가 확인됨

0:
국내 단독 출원으로 확인되거나 해외 Patent Family 정보가 확인되지 않음


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
외부 시장 근거, CPC 성장성 계산값, Patent Family 정보가 충분하고 판단이 비교적 명확함

0.5~0.79:
간접 근거는 있으나 시장 수요, 적용 산업, 성장성, 해외 확장 가능성 중 일부 추가 확인이 필요함

0.0~0.49:
외부 시장 근거가 부족하거나, CPC 성장성 계산값이 없거나, 대상 특허와 시장 근거의 관련성이 약함


출력 규칙:
- Return ONLY one JSON object.
- Markdown, 설명 문구, 코드블록을 출력하지 않는다.
- 점수 감점 사유와 자료 부족 사유를 구분한다.
- 실제 시장 리스크만 risk_factors에 작성한다.
- 자료 부족은 missing_information에 작성한다.
- 자료 부족에는 "정보 부족 있음", "추가 확인 필요", "N/A"를 적절히 사용한다.
- "정보 없음 = 낮은 시장성"으로 해석하지 않는다.
- 확인되지 않은 시장 규모, 성장률, 수요, 해외 진출 가능성을 단정하지 않는다.
- SK AX 내부 사업과의 직접 연결성 또는 활용 가능성은 사업연계성 축에서 판단한다고 표현한다.
- `subscores` 필드는 권리성 축과 같은 객체 구조로 작성한다.
- 각 세부 평가지표는 `label`, `score`, `max_score`, `rationale`을 포함한다.
- `subscores.industry_marketability.rationale`에는 산업 성장 근거, 기업 투자·진입 근거, 뉴스 기반 시장 확산 근거, 자료 신뢰도 판단을 함께 요약한다.
- `subscores.market_growth.rationale`에는 대표 CPC 기준 최근 3년 특허 출원 수, CAGR, 최근 3년 추세 점수 판단을 요약한다.
- `subscores.global_business.rationale`에는 Patent Family 국가 정보와 국내 단독/해외 출원/다국가 출원 판단을 요약한다.
- `sub_scores`, `industry_marketability_score`, `industry_marketability_breakdown`은 출력하지 않는다.


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
      "rationale": "..."
    },
    "market_growth": {
      "label": "시장 성장성",
      "score": 0,
      "max_score": 40,
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
