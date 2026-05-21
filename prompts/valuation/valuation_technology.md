# Valuation Technology Axis Prompt

기술성 축을 평가한다.

모든 출력 값은 반드시 한국어로 작성한다. `rationale`, `risk_factors`, `missing_information`에 영어 문장을 쓰지 않는다.

평가 구조:
- 기술성 점수(100) = 기술 차별성(60) + 구현 구체성(40)
- 기술 차별성(60)은 대상 특허와 `technology_metrics.similar_patents`의 Top 유사 특허를 비교해 산정한다.
- 구현 구체성(40)은 대상 특허 PDF/청구항/초록/상세설명에 구현 요소와 로직이 얼마나 구체적으로 설명되어 있는지 산정한다.
- 구현 구체성(40)은 비교 문헌의 내용이나 유무에 의해 변하면 안 되며, 대상 특허 문헌만 기준으로 판단한다.

사용 근거:
- 대상 특허 metadata
- 대상 특허 초록
- 대상 특허 대표 청구항
- 대상 특허 sections
- `technology_metrics.representative_cpc`
- `technology_metrics.similar_patents`
  - 각 유사 특허는 제목, 초록, 유사도, 출원일, 출원인, 상태를 포함한다.
  - PDF 수집이 성공한 경우 `pdf_text`에 도면/이미지를 제거한 유사 특허 원문 텍스트가 포함된다.
- portfolio_context는 보조 근거로만 사용한다.

## 1. 기술 차별성 (60)

유사 특허 대비 새로운 기술 구조 차별성을 평가한다.

점수 항목:
- 신규 구성요소 존재: 유사 특허에 없는 새로운 구성요소가 확인됨 -> 0 / 7.5 / 15
- 기술 조합 차별성: 기존 구성요소를 결합하는 방식이 다름 -> 0 / 7.5 / 15
- 처리 구조 차별성: 데이터·신호·공정·제어 흐름이 유사 특허와 다름 -> 0 / 7.5 / 15
- 해결 방식 차별성: 같은 문제를 다른 방식으로 해결하거나 다른 기술적 효과를 냄 -> 0 / 5 / 10
- 차별 근거 명확성: 차별 요소가 청구항·상세설명 등 문서 근거로 확인됨 -> 0 / 2.5 / 5

유사 특허가 없거나 `technology_metrics.warnings`에 후보 수집 실패가 있으면 기술 차별성 점수를 보수적으로 산정하고, missing_information에 유사 특허 비교 근거 부족을 남긴다. 유사 특허 PDF 수집이 일부 실패했더라도 제목·초록 기반 비교는 가능하므로, 수집 성공한 PDF와 metadata를 구분해 평가한다.

## 2. 구현 구체성 (40)

구현 구체성 점수는 입력·출력 구성요소 구체성 15점과 구현 로직 구체성 25점으로 나눈다.

### 입력·출력 구성요소 구체성 (15)

- 입력 데이터 명시: 사용하는 입력값·데이터·조건이 명확히 제시됨 -> 4
- 처리 대상 명시: 어떤 객체·정보·상태를 처리하는지 제시됨 -> 3
- 핵심 변수 명시: 특징값·조건값·제어값 등 핵심 변수가 제시됨 -> 3
- 출력 결과 구조: 생성되는 결과·산출물·판단값이 제시됨 -> 3
- 구성요소 연결성: 입력→처리대상→출력 간 관계가 설명됨 -> 2

### 구현 로직 구체성 (25)

- 처리 절차 제시: 단계별 처리 흐름 또는 수행 절차가 설명됨 -> 6
- 처리 로직 설명: 실제 동작 방식과 내부 처리 구조가 설명됨 -> 6
- 조건·파라미터 존재: threshold·weighting·조건값 등이 제시됨 -> 5
- 계산·판단 구조 존재: 계산·비교·판단·제어 방식이 설명됨 -> 5
- 예외·반복·업데이트 구조: 예외 처리, 반복 수행, 갱신 방식 등이 설명됨 -> 3

주의:
- 입력에 없는 성능 수치, 제품 구현, 실제 도입 사례를 만들지 않는다.
- 유사 특허가 metadata 수준으로만 제공되면 PDF 전문 비교가 제한적이라고 명시한다.
- `pdf_text` 또는 `pdf_text_excerpt`가 제공된 유사 특허는 대상 특허 문헌과 원문 수준으로 비교한다.
- 차별성은 "다르다"라고만 쓰지 말고, 어떤 구성/흐름/해결 방식이 다른지 설명한다.
- 구현 구체성은 특허 문헌 내 입력, 처리 대상, 변수, 계산/판단 로직, 출력 구조에 근거해 평가한다.
- 비교 문헌과의 중복성, 차별성 부족, 신규성 리스크는 기술 차별성에만 반영하고 구현 구체성 감점 근거로 사용하지 않는다.
- 점수 합계는 반드시 `technical_differentiation_score + implementation_specificity_score`와 일치해야 한다.
- `technical_differentiation_breakdown` 합계는 `technical_differentiation_score`와 일치해야 한다.
- `implementation_specificity_breakdown` 중 입력·출력 5개 항목 합계는 `input_output_specificity_score`, 구현 로직 5개 항목 합계는 `implementation_logic_score`와 일치해야 한다.
- `input_output_specificity_score + implementation_logic_score`는 `implementation_specificity_score`와 일치해야 한다.
- 기술 차별성 항목은 3단계만 사용한다: `0`, `절반 점수`, `만점`.
- 구현 구체성 항목은 부분점수를 주지 않는다. 각 항목은 해당 배점 그대로 또는 0점만 부여한다.
- 예: 구현 구체성의 6점 항목은 6 또는 0, 5점 항목은 5 또는 0, 4점 항목은 4 또는 0만 가능하다.
- 점수는 보수적으로 부여한다. "관련성이 있다", "가능성이 있다", "추정된다" 수준이면 0점이다.
- 유사 특허 원문과 대상 특허 원문을 비교해 명확한 차이가 확인되는 경우에만 기술 차별성 항목을 부여한다.
- 일부 차별점은 있으나 강하지 않거나 비교 근거가 제한적이면 기술 차별성 항목에 절반 점수를 부여한다.
- 대상 특허에 수식, 임계값, 가중치 산정식, 파라미터, 갱신/반복/예외 처리 로직이 명시되지 않았으면 해당 구현 로직 항목은 0점이다.
- `missing_information`에 어떤 정보가 부족하다고 적었다면, 그 부족 정보와 직접 관련된 세부 항목은 만점으로 주면 안 된다.
- `risk_factors`에 기술 범위 중복, 구현 불명확성, 성능 검증 부족, 파라미터 불명확성을 적었다면 관련 세부 항목은 0점으로 둔다.
- 최종 90점 이상은 차별성 근거와 구현 로직이 모두 원문에서 명확하고 `missing_information`이 거의 없는 경우에만 가능하다.

Return ONLY JSON:
{
  "axis": "technology",
  "label": "기술성",
  "score": 0,
  "grade": "A/B/C/D",
  "technical_differentiation_score": 0,
  "implementation_specificity_score": 0,
  "sub_scores": {
    "technical_differentiation_score": 0,
    "implementation_specificity_score": 0,
    "input_output_specificity_score": 0,
    "implementation_logic_score": 0
  },
  "technical_differentiation_breakdown": {
    "new_component_score": 0,
    "combination_difference_score": 0,
    "processing_structure_difference_score": 0,
    "solution_approach_difference_score": 0,
    "evidence_clarity_score": 0
  },
  "implementation_specificity_breakdown": {
    "input_data_score": 0,
    "processing_target_score": 0,
    "core_variable_score": 0,
    "output_structure_score": 0,
    "component_linkage_score": 0,
    "procedure_score": 0,
    "logic_score": 0,
    "condition_parameter_score": 0,
    "calculation_decision_score": 0,
    "exception_iteration_update_score": 0
  },
  "rationale": "...",
  "evidence_ids": [],
  "risk_factors": [],
  "missing_information": [],
  "confidence": 0.0
}
