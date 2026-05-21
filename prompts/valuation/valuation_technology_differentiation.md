# Valuation Technology Differentiation Prompt

기술성 축 중 기술 차별성만 평가한다.

모든 출력 값은 반드시 한국어로 작성한다. `rationale`, `risk_factors`, `missing_information`에 영어 문장을 쓰지 않는다.

평가 구조:
- 기술 차별성(60)은 대상 특허와 `technology_metrics.similar_patents`의 비교를 통해 산정한다.
- 구현 구체성은 이 프롬프트에서 평가하지 않는다. 구현 구체성 관련 점수는 모두 0으로 출력한다.

사용 근거:
- 대상 특허 metadata
- 대상 특허 초록
- 대상 특허 대표 청구항
- 대상 특허 sections
- `technology_metrics.similar_patents`
  - 각 비교 문헌은 제목, 초록, 유사도, 출원일, 출원인, 상태를 포함한다.
  - PDF 수집이 성공한 경우 `pdf_text`에 원문 텍스트가 포함된다.

## 기술 차별성 (60)

비교 문헌 대비 새로운 기술 구조 차별성을 평가한다.

점수 항목:
- 신규 구성요소 존재: 비교 문헌에 없는 새로운 구성요소가 확인됨 -> 0 / 7.5 / 15
- 기술 조합 차별성: 기존 구성요소를 결합하는 방식이 다름 -> 0 / 7.5 / 15
- 처리 구조 차별성: 데이터·신호·공정·제어 흐름이 비교 문헌과 다름 -> 0 / 7.5 / 15
- 해결 방식 차별성: 같은 문제를 다른 방식으로 해결하거나 다른 기술적 효과를 냄 -> 0 / 5 / 10
- 차별 근거 명확성: 차별 요소가 청구항·상세설명 등 문서 근거로 확인됨 -> 0 / 2.5 / 5

주의:
- 구현 구체성 감점/가점 사유를 이 프롬프트의 점수에 반영하지 않는다.
- 비교 문헌 PDF가 있으면 원문 수준으로 비교한다.
- 차별성은 "다르다"라고만 쓰지 말고, 어떤 구성/흐름/해결 방식이 다른지 설명한다.
- 점수는 3단계만 사용한다: `0`, `절반 점수`, `만점`.
- 일부 차별점은 있으나 강하지 않거나 비교 근거가 제한적이면 `절반 점수`를 부여한다.
- 비교 문헌과 대부분 중복되거나 차별 근거가 매우 약하면 `0점`으로 둔다.

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
