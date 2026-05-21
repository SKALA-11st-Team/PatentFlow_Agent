# Valuation Technology Implementation Prompt

기술성 축 중 구현 구체성만 평가한다.

모든 출력 값은 반드시 한국어로 작성한다. `rationale`, `risk_factors`, `missing_information`에 영어 문장을 쓰지 않는다.

평가 구조:
- 구현 구체성(40)은 대상 특허 PDF/청구항/초록/상세설명만 기준으로 산정한다.
- 비교 문헌의 내용, 차별성, 중복성, 신규성 리스크는 이 프롬프트에서 반영하지 않는다.
- 기술 차별성은 이 프롬프트에서 평가하지 않는다. 기술 차별성 관련 점수는 모두 0으로 출력한다.

사용 근거:
- 대상 특허 metadata
- 대상 특허 초록
- 대상 특허 대표 청구항
- 대상 특허 sections

## 구현 구체성 (40)

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
- 구현 구체성은 대상 특허 문헌 내 입력, 처리 대상, 변수, 계산/판단 로직, 출력 구조에 근거해 평가한다.
- 비교 문헌과의 중복성, 차별성 부족, 신규성 리스크는 구현 구체성 감점 근거로 사용하지 않는다.
- 대상 특허에 수식, 임계값, 가중치 산정식, 파라미터, 갱신/반복/예외 처리 로직이 명시되지 않았으면 해당 항목은 0점이다.

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
