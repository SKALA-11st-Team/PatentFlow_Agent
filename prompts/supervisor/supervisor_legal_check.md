# Legal Axis Quality Check Criteria

이 문서는 권리성 평가 결과를 검토하기 위한 축별 품질 기준입니다.
라우팅을 결정하는 Supervisor 프롬프트가 아니며, `next_action`을 출력하지 않습니다.
최종 라우팅은 4개 축의 status를 결정적으로 집계해 수행하며, 별도의 라우팅 LLM은 없습니다.

## 검토 대상
- valuation.axes.legal
- patent.claim_context
- citation_evidence
- legal_context
- portfolio_context
- patent.rights_scope_context
- evidence.samples

## 정상으로 볼 수 있는 상태
- `axis`가 `legal`이고 `label`이 `권리성`입니다.
- `score`, `grade`, `rationale`, `evidence_ids`, `risk_factors`, `missing_information`, `confidence`가 존재합니다.
- `subscores`가 권리안정성, 권리보호력, 포트폴리오·해외 권리 가치 흐름과 맞습니다.
- 총점은 권리안정성 35점, 권리보호력 40점, 포트폴리오·해외 권리 가치 25점 구조와 모순되지 않습니다.
- 권리성 판단은 제품 적용 여부, 시장 수요, SK AX 사업 적합성과 섞이지 않습니다.

## 중점 검토 기준
1. 권리안정성
   - 선행문헌 후보 존재만으로 무효 리스크를 단정하지 않았는지 확인합니다.
   - 청구항 핵심 구성과 선행문헌의 겹침 정도를 근거로 설명했는지 확인합니다.
   - 삭제 청구항, 보정 전 청구항, 심사기록 부재를 감점 근거로 쓰지 않았는지 확인합니다.

2. 권리보호력
   - 독립항의 필수 구성과 발명의 핵심 해결수단을 연결해 판단했는지 확인합니다.
   - 청구항 수만으로 보호력을 높거나 낮게 판단하지 않았는지 확인합니다.
   - 특정 구현 조건이 보호범위에 미치는 영향을 과장하지 않았는지 확인합니다.

3. 포트폴리오·해외 권리 가치
   - 관련 특허군 연계·커버리지, 해외 권리 확보 범위, 후속 권리화 신호를 구분했는지 확인합니다.
   - 해외 패밀리 또는 해외 등록 정보가 없다는 사실만으로 권리 약점처럼 쓰지 않았는지 확인합니다.
   - 해외 권리 정보를 글로벌 시장 수요나 해외 사업 확장성 근거처럼 쓰지 않았는지 확인합니다.

4. 권리범위 참고도 활용
   - 대표 도면이 있는 경우 권리범위 참고도는 청구항 이해를 돕는 보조 자료로만 사용했는지 확인합니다.
   - 도면상 구성/흐름과 청구항 필수 구성을 매칭해 권리범위를 설명했는지 확인합니다.
   - 도면에 보이는 모든 요소가 곧바로 권리범위에 포함된다고 단정하지 않았는지 확인합니다.

## 재평가가 필요한 신호
- 권리성 점수가 높은데 청구항 또는 선행문헌 관련 근거가 거의 없습니다.
- 제품 적용, 시장성, 사업 연계성을 이유로 권리성 점수를 설명합니다.
- 해외 패밀리 부재를 권리 약점 또는 포기 근거로 단정합니다.
- 선행문헌 존재만으로 무효 가능성을 법률 결론처럼 단정합니다.

## 평가 범위 주의
- 권리성은 청구항·선행문헌·등록상태·포트폴리오 등 특허 수집 단계에서 들어온 정보로만 판단합니다.
- 이 근거는 Naver/글로벌 뉴스/산업 RAG 외부 검색으로 보강되지 않습니다. 따라서 권리성은 외부 근거 재수집(query_rewriting)을 요청하지 않습니다.
- 청구항·등록상태·선행문헌 식별 정보 자체가 거의 없으면, 이는 특허 수집 단계(patent_check)의 문제이며 이 체크의 재수집 대상이 아닙니다. 주어진 정보로 평가 논리가 타당한지만 봅니다.

## 근거 존재·내용 판단 주의
- evidence.samples에는 이 평가가 인용한 근거(evidence_ids)가 우선 포함되며, 전체 근거의 일부 미리보기입니다.
- 근거의 존재 여부는 evidence.samples가 아니라 known_evidence_ids로 판단하세요. known_evidence_ids에 있으면 그 근거는 존재합니다.
- samples에 본문이 안 보인다는 이유만으로 "근거 누락"으로 단정하지 마세요. 실제로 known_evidence_ids에 없는 항목(unknown_evidence_ids)만 문제 삼습니다.
- 선행문헌·인용문헌·청구항 텍스트는 evidence_bundle(뉴스·산업 RAG 근거)이 아니라 특허 수집 데이터(claim_context, citation_evidence)에서 옵니다. 평가가 이를 인용했다고 해서 evidence_ids/samples에 없는 것을 "근거 누락"이나 재평가 사유로 삼지 마세요.
- 평가가 인용한 선행문헌은 prior_art_context.cited_in_evaluation에, 입력으로 제공된 선행문헌 식별값은 prior_art_context.available_in_input에 있습니다. cited_in_evaluation의 항목이 available_in_input에 있으면 그 선행문헌은 입력에 근거한 정상 인용입니다. available_in_input에도 없는 문헌을 인용한 경우(환각)에만 valuation_retry 사유로 삼으세요.

## evidence_ids는 비어 있는 게 정상 (매우 중요)
- 권리성의 근거(선행문헌·청구항·등록상태)는 evidence_bundle이 아니라 특허 수집 데이터에서 오며, prior_art_references / prior_art_context로 추적됩니다. 따라서 `evidence_ids`는 **비어 있는 것이 기본값이자 정상**입니다.
- `evidence_ids`가 비어 있다는 사실 자체는 **절대 valuation_retry 사유가 아닙니다.** 다음과 같은 사유로 valuation_retry를 내지 마세요:
  - "evidence_ids가 비어 있어 근거 식별자가 없다 / 포함되어야 한다"
  - "평가와 근거의 명시적 매핑이 부재하다 / 근거 추적성이 확보되지 않는다"
  - "인용한 선행문헌을 evidence_ids에 매핑해야 한다"
- 만약 선행문헌 번호를 evidence_ids에 넣으면 오히려 known_evidence_ids(=evidence_bundle)에 없는 unknown_evidence_id가 되어 잘못된 출력입니다. 즉 evidence_ids에 선행문헌을 채우라고 요구하는 것은 틀린 지시입니다.
- 권리성 평가 메타데이터(evidence_ids)의 형식·완성도·매핑 여부는 이 체크의 대상이 아닙니다. 오직 평가 논리·점수·표현의 타당성만 봅니다.

## 출력 형식
Return ONLY one JSON object.
`next_action`은 출력하지 마세요.

{
  "status": "passed" | "valuation_retry",
  "issues": [],
  "reason": ""
}

status 선택 기준:
- `passed`: 권리성 평가가 자기 기준에 맞고 평가 논리가 타당함 (evidence_ids가 비어 있어도 passed로 둡니다)
- `valuation_retry`: 권리성 평가 논리, 점수, 표현 자체에 오류가 있어 다시 써야 함
  - evidence_ids가 비어 있다 / 근거 매핑·추적성이 부족하다 / 식별자를 포함해야 한다는 이유로는 valuation_retry를 선택하지 마세요.
