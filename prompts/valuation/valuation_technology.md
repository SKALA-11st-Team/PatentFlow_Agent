# Valuation Technology Axis Prompt

기술성 축을 평가한다.

기술성은 단순히 기술이 최신인지 평가하는 축이 아니다.

이 특허가 기존 기술과 비교했을 때 기술적으로 어떤 차별 요소가 존재하는지,
그리고 특허 기술이 실제 구현 가능한 수준으로 구체적으로 설명되어 있는지를 평가한다.

총점은 100점이며 반드시 아래 2개 하위 항목 점수를 합산한다.

1. 기술 차별성: 60점
2. 구현 구체성: 40점


평가 목적:
- 선행기술조사문헌과 유사 특허 비교군을 바탕으로 대상 특허의 기술 구성, 기술 동작 방식, 기술 효과 차별성을 평가한다.
- 대상 특허 문헌만을 바탕으로 실제 구현 가능한 수준까지 구체적으로 설명되어 있는지 평가한다.
- 법적 신규성/진보성, 권리범위, 회피설계 가능성, 시장성, 사업 적용 여부는 기술성 점수에 반영하지 않는다.


평가 원칙:
- 모든 출력 값은 반드시 한국어로 작성한다.
- 입력에 없는 성능 수치, 제품 구현, 실제 도입 사례를 만들지 않는다.
- `patent.document_role=target_patent`의 청구항·요약·명세서 내용만 대상 특허 기술로 서술한다.
- `technology_metrics.similar_patents`는 선행기술 또는 유사 비교문헌이다. 여기에만 존재하는 구성, 알고리즘, 효과를 대상 특허가 포함한다고 서술하지 않는다.
- 대상 특허의 차별 요소는 대상 문헌에서 확인된 구성과 비교문헌에서 확인된 구성을 명시적으로 대조하여 작성한다.
- 예를 들어 비교문헌에만 퍼지 회귀가 있고 대상 청구항에 없다면, 퍼지 회귀를 대상 특허의 구현 또는 차별점으로 설명해서는 안 된다.
- 자료 부족은 특허의 약점처럼 단정하지 말고 `missing_information`과 `confidence`에 반영한다.
- 비교 문헌이 부족해도 평가를 회피하지 말고, 확보된 문헌 기준으로 판단하되 한계를 명시한다.
- 기술 차별성은 신규성/진보성 법률 판단이 아니라 기술적 차이 분석이다.
- 세부점수는 반드시 지정된 점수 후보 중 하나만 선택한다.
- `subscores.technical_differentiation.details`와 `subscores.implementation_specificity.details`에는 점수만 넣고 설명 문장은 넣지 않는다.
- `score`는 반드시 두 하위 항목 점수 합계와 일치해야 한다.


사용 근거:
- 대상 특허 metadata
- 대상 특허 기술분야
- 대상 특허 배경기술
- 대상 특허 초록
- 대상 특허 해결하고자 하는 과제
- 대상 특허 과제의 해결 수단
- 대상 특허 대표 청구항
- 대상 특허 전체 청구항
- 대상 특허 sections
- 발명을 실시하기 위한 구체적인 내용
- 실시예
- 도면 설명
- 시스템/장치 구성
- 처리 흐름
- 발명의 효과
- `technology_metrics.representative_cpc`
- `technology_metrics.representative_ipc`
- `technology_metrics.country_code`
- `technology_metrics.similar_patents`
- `technology_metrics.warnings`
- `element_structure.target`: 대상 특허의 구조화 결과(key_elements/key_flow/claims)
- `element_structure.comparisons`: 비교군 특허의 구조화 결과
- citation_evidence
- prior_art_candidates
- 국내특허는 CPC 기반, 해외특허는 IPC 기반 해당 국가 유사 특허 검색 결과


----------------------------------------
1. 기술 차별성 (60점)
----------------------------------------

목적:
기존 기술과 비교했을 때 기술적으로 어떤 차별 요소가 존재하는지 평가한다.

사용 데이터:
- 대상 특허: `element_structure.target`(구조화 결과) + 청구항 원문 + summary_result
- 비교군 특허: `element_structure.comparisons`(구조화 결과). 비교군의 원문 전문은 제공되지 않으며, 구조화 결과로 비교한다.
- `technology_metrics.similar_patents`: 비교군 식별자·CPC/IPC·출원인·출원일 등 메타데이터(원문 전문 제외)
- KIPRIS 메타데이터
  - IPC/CPC
  - 출원인
  - 출원일

비교군 구성 기준:
- 선행기술조사문헌과 embedding similarity 기반 유사 특허를 함께 활용하여 비교군을 구성한다.
- 선행기술조사문헌은 실제 특허 심사 과정에서 관련성이 검토된 공식 비교 문헌으로 우선 활용한다.
- 추가 유사 특허는 심사 과정에서 반영되지 않은 유사 기술 가능성을 보완하기 위해 사용한다.
- 국내특허의 추가 유사 특허는 대표 CPC 기반으로, 해외특허의 추가 유사 특허는 대표 IPC 기반 해당 국가 문헌으로 수집된 것으로 간주한다.
- 비교군은 기술 관련성 및 중복성을 고려하여 총 3개 내외 특허로 구성된 것으로 간주하고 평가한다.

평가 방식:
- 대상 특허와 비교군 특허를 비교하여 기술 구성 차이, 기술 동작 방식 차이, 기술 효과 차이를 분석한다.
- 비교군 특허는 대상 특허 출원일 이전에 공개 또는 등록된 특허로 제한된 것으로 간주한다.
- 비교는 `element_structure`(대상·비교군 구조화 결과)를 기준으로 구성요소 단위로 수행한다.
- 특정 비교군의 구조화 결과가 비어 있으면(구조화 실패) 해당 비교군은 비교에서 제외하고, 한계를 `missing_information`과 `confidence`에 반영한다.

구성요소(element) 단위 비교 (제공된 경우 우선 사용):
- `element_structure.target.key_elements`(대상 특허의 핵심 구성요소)와 각 `element_structure.comparisons[].key_elements`(비교군 특허의 핵심 구성요소)를 **구성요소 대 구성요소**로 대조한다.
- 기술 구성 차별성: 대상 key_element가 비교군 특허에 대응 구성요소가 없거나 역할이 다르면 그 비교군과 "구성 차이 있음"으로 본다.
- 기술 동작 방식 차별성: `element_structure.target.key_flow`(구성요소 간 결합·흐름)가 비교군과 다른지 본다. coupling_strength=strong인 흐름이 비교군에 없으면 동작 방식 차별성이 크다.
- 기술 효과 차별성: 대상 key_element의 `why_essential`(해결 과제·효과)이 비교군 대비 차별적인지 본다.
- 비교군 특허별로 "구성 차이/동작 차이/효과 차이가 확인된 비교군 수"를 세어 아래 점수 밴드에 반영한다.
- `element_structure`가 비어 있거나 일부만 있으면 PDF 원문·metadata 기반 비교로 보완하고, 한계를 `missing_information`·`confidence`에 반영한다.
- comparisons에만 존재하는 구성요소를 대상 특허의 차별점이나 구현으로 서술하지 않는다.

점수화 기준:

기술 구성 차별성 (20점)
- 20: 비교군 3개 특허 모두와 기술 구성 차이가 확인됨
- 13: 비교군 2개 특허와 기술 구성 차이가 확인됨
- 7: 비교군 1개 이하 특허와만 기술 구성 차이가 확인됨

기술 동작 방식 차별성 (25점)
- 25: 비교군 3개 특허 모두와 기술 동작 방식 차이가 확인됨
- 17: 비교군 2개 특허와 기술 동작 방식 차이가 확인됨
- 8: 비교군 1개 이하 특허와만 기술 동작 방식 차이가 확인됨

기술 효과 차별성 (15점)
- 15: 비교군 3개 특허 모두와 기술 효과 차이가 확인됨
- 10: 비교군 2개 특허와 기술 효과 차이가 확인됨
- 5: 비교군 1개 이하 특허와만 기술 효과 차이가 확인됨

`subscores.technical_differentiation.score`는 위 3개 세부점수의 합계로 0~60점 범위에서 산정한다.

`subscores.technical_differentiation.details`에는 아래 세부점수를 숫자 필드로 작성한다.
- `configuration_differentiation`: 7, 13, 20 중 하나
- `operation_differentiation`: 8, 17, 25 중 하나
- `effect_differentiation`: 5, 10, 15 중 하나


----------------------------------------
2. 구현 구체성 (40점)
----------------------------------------

목적:
특허 기술이 실제 구현 가능한 수준으로 구체적으로 설명되어 있는지를 평가한다.

사용 데이터:
- 대상 특허 PDF 원문

평가 방식:
- 대상 특허 PDF 원문을 바탕으로 청구항, 상세설명, 도면에서 기술 구성 요소, 처리 절차, 기술 동작 방식 및 구현 설명을 분석한다.

점수화 기준:
- 구성 요소 구체성: 기술 구성 요소 및 역할이 설명됨 → 15점, 아니면 0점
- 처리 절차 구체성: 단계별 처리 절차가 설명됨 → 15점, 아니면 0점
- 구현 설명 구체성: 기술 동작 방식 또는 구현 방식이 설명됨 → 10점, 아니면 0점

`subscores.implementation_specificity.score`는 위 3개 세부점수의 합계로 0~40점 범위에서 산정한다.

`subscores.implementation_specificity.details`에는 아래 세부점수를 숫자 필드로 작성한다.
- `component_specificity`: 0 또는 15
- `procedure_specificity`: 0 또는 15
- `implementation_specificity_detail`: 0 또는 10


----------------------------------------
종합 점수
----------------------------------------

score = 기술 차별성 + 구현 구체성

grade:
80 이상 → A
60 이상 → B
40 이상 → C
40 미만 → D

confidence:
0.0 ~ 1.0


출력 규칙:
- Return ONLY one JSON object.
- Markdown, 설명 문구, 코드블록을 출력하지 않는다.
- 실제 기술적 약점만 `risk_factors`에 작성한다.
- 자료 부족은 `missing_information`에 작성한다.
- "정보 없음 = 낮은 기술성"으로 해석하지 않는다.
- 권리범위, 무효 가능성, 회피설계 가능성을 기술성 점수에 반영하지 않는다.
- 시장 규모, 사업 적용 여부, 매출 가능성을 기술성 점수에 반영하지 않는다.
- `subscores.technical_differentiation.rationale`에는 기술 구성 차이, 기술 동작 방식 차이, 기술 효과 차이를 종합 요약하되 세부점수 항목명을 나열하지 않는다.
- `subscores.implementation_specificity.rationale`에는 구성 요소, 처리 절차, 구현 설명의 구체성을 종합 요약하되 세부점수 항목명을 나열하지 않는다.

Return ONLY JSON:
{
  "axis": "technology",
  "label": "기술성",
  "score": 0,
  "subscores": {
    "technical_differentiation": {
      "label": "기술 차별성",
      "score": 0,
      "max_score": 60,
      "details": {
        "configuration_differentiation": 4,
        "operation_differentiation": 5,
        "effect_differentiation": 3
      },
      "rationale": "..."
    },
    "implementation_specificity": {
      "label": "구현 구체성",
      "score": 0,
      "max_score": 40,
      "details": {
        "component_specificity": 0,
        "procedure_specificity": 0,
        "implementation_specificity_detail": 0
      },
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
