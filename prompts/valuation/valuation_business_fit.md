# Valuation Business Fit Axis Prompt

사업연계성 축을 평가한다.

사업연계성은 권리성, 기술성, 시장성, 포트폴리오 가치를 다시 평가하는 축이 아니다.

이 특허와 관련된 SK AX 공식 사업/서비스 근거가 확인되는지,
그리고 그 공식 근거가 특허 내용과 얼마나 직접적이고 구체적으로 연결되는지를 평가한다.

총점은 100점이며 반드시 아래 3개 하위 항목 점수를 합산한다.

1. 공식 사업 근거 발견도: 30점
2. 사업 맥락 직접성: 45점
3. 적용 시나리오 구체성: 25점

요약 기준:
- 공식 사업 근거 발견도 30점
- 사업 맥락 직접성 45점
- 적용 시나리오 구체성 25점


평가 목적:
- SK AX 공식 사이트에서 특허와 관련된 사업/서비스/오퍼링 근거가 확인되는지 평가한다.
- 특허의 관련제품, 사업 분야, 기술 분야, 요약, 문제, 해결수단이 공식 근거와 직접 연결되는지 평가한다.
- 공식 근거가 단순 홍보 문구를 넘어 적용 시나리오나 업무 맥락 수준으로 구체적인지 평가한다.
- SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다.
- 공식 사이트에서 관련 사업 근거가 확인된다/확인되지 않는다 수준으로 표현한다.


평가 원칙:
- 입력에 없는 사실을 추정하지 않는다.
- skax.co.kr 공식 evidence 중심으로 평가한다.
- 외부 뉴스, 블로그, SK그룹 다른 도메인, 미러링 사이트는 공식 사업 근거로 보지 않는다.
- 검색 결과 개수만으로 높은 점수를 주지 않는다.
- 공식 근거의 직접성, 구체성, 사업 맥락 일치도를 본다.
- 공식 근거가 없다고 특허 가치가 낮다고 단정하지 않는다.
- 자료 부족은 missing_information 또는 confidence 하락 요인으로 처리한다.
- risk_factors에는 공식 근거 기반으로 확인되는 실제 한계만 작성한다.
- 추정성 사업 리스크를 작성하지 않는다.
- 권리성, 기술성, 시장성, 포트폴리오 가치, 최종 유지/포기 의사결정을 판단하지 않는다.
- 기술성 축처럼 보이는 "기술적 적합성" 표현보다 "사업 맥락 직접성", "공식 사업 근거와의 연결성", "적용 시나리오 구체성" 표현을 우선한다.


사용 근거:
- business_fit_context.patent_description
- business_fit_context.skax_official_evidence
- evidence 목록의 보조 정보
- 특허 관리번호
- 발명의 명칭
- 관련제품
- 관련 사업 분야
- 관련 기술 분야
- 특허 요약
- key_points
- problem_or_purpose
- solution_or_core_technology
- effect_or_expected_benefit
- use_case_or_application
- SK AX 공식 evidence의 title
- SK AX 공식 evidence의 url
- SK AX 공식 evidence의 content_excerpt
- matched_keywords
- matched_terms
- relevance_score
- candidate_relevance_score

사용 금지 근거:
- parsed_pdf 전체
- cleaned_markdown 전체
- claims_text 전체
- Tavily raw_content 전체
- candidate_results 전체
- search_request_url
- API status
- API key
- 외부 뉴스, 블로그, SK그룹 다른 도메인, 미러링 사이트를 공식 근거로 사용하는 것


----------------------------------------
1. 공식 사업 근거 발견도 (30점)
----------------------------------------

목적:
SK AX 공식 사이트에서 이 특허와 관련된 사업/서비스 근거가 실제로 확인되는지 평가한다.

평가 요소:
- skax.co.kr 공식 URL 존재 여부
- 최종 선택된 sk_ax_official evidence 수
- 단순 메인 카테고리인지, 구체 서비스/오퍼링 페이지인지
- content_excerpt 존재 여부
- 공식 evidence가 유효한 사업 근거인지
- 파일/PDF/낮은 관련 후보가 적절히 제외되었는지

평가 규칙:
- 공식 페이지가 1개라도 특허와 직접 연결되면 의미 있는 근거로 본다.
- 공식 evidence가 여러 개 있어도 검색 결과 개수만으로 고득점을 주지 않는다.
- broad category 또는 플랫폼 소개 수준이면 고득점은 가능해도 100점 산정 근거로 쓰지 않는다.
- 공식 근거가 없으면 특허 가치가 낮다고 단정하지 말고 missing_information 또는 confidence에 반영한다.

점수 후보:
30:
관련 공식 페이지가 여러 개 있고, 유효한 사업 근거로 확인됨

24:
관련 공식 페이지가 1~2개 있고, 직접 근거로 사용 가능함

16:
공식 페이지는 있으나 broad category 수준임

8:
공식 페이지 후보는 있으나 관련성이 약함

0:
공식 사이트 근거 없음


----------------------------------------
2. 사업 맥락 직접성 (45점)
----------------------------------------

목적:
특허의 핵심 제품, 기술, 문제 해결 방향이 SK AX 공식 사업 설명과 얼마나 직접적으로 연결되는지 평가한다.

평가 요소:
- related_product와 공식 evidence 내용의 일치
- business_area와 공식 페이지 카테고리의 일치
- technology_area와 공식 페이지 설명의 연결성
- 특허명 핵심어와 공식 페이지 설명의 일치
- patent_description의 summary와 공식 evidence의 연결성
- problem_or_purpose와 공식 사업 문제/니즈의 연결성
- solution_or_core_technology와 공식 서비스/오퍼링 설명의 연결성
- use_case_or_application과 공식 적용 맥락의 연결성
- matched_keywords, matched_terms, relevance_score

평가 규칙:
- 단순히 같은 산업군이라는 이유만으로 높은 점수를 주지 않는다.
- 단순 AI, Data, Cloud 같은 넓은 표현만 있으면 낮게 반영한다.
- 특허 핵심어 일부가 공식 evidence에 없으면 사업 맥락 직접성을 만점으로 보지 않는다.
- 특허와 공식 사업 근거의 1:1 매핑이 확인되지 않으면 100점 산정 근거로 쓰지 않는다.
- SK AX가 실제로 특허를 사용한다고 단정하지 않는다.

점수 후보:
45:
특허 핵심 제품/기술과 공식 사업 페이지가 직접 연결됨

36:
직접 제품명 일부 또는 강한 사업 맥락이 확인됨

27:
같은 산업/기술군이나 구체적 연결은 약함

12:
넓은 사업 분야만 같고 직접성 낮음

0:
연결 근거 없음


----------------------------------------
3. 적용 시나리오 구체성 (25점)
----------------------------------------

목적:
공식 근거가 단순 홍보 문구를 넘어 실제 서비스/오퍼링/유스케이스 수준으로 구체적인지 평가한다.

평가 요소:
- 구체적인 서비스 또는 오퍼링 존재
- 적용 대상 산업/고객군
- 적용 방식
- 업무 프로세스
- 특허 기술과 연결 가능한 사용 시나리오
- 공식 페이지의 구체적 문구와 특허 요약 간 연결성

평가 규칙:
- 추상적인 "AI 활용", "데이터 분석" 수준이면 낮게 평가한다.
- 구체적 서비스명, 업무 시나리오, 적용 방식이 있으면 높게 평가한다.
- 적용 시나리오가 공식 페이지에 있으나 특허 구현과 직접 매핑되지 않으면 적용 시나리오 구체성을 만점으로 보지 않는다.
- 특허의 실제 적용 여부를 단정하지 않는다.

점수 후보:
25:
실제 오퍼링/유스케이스/적용 방식이 구체적임

20:
서비스 방향과 적용 시나리오가 비교적 명확함

14:
적용 가능성은 있으나 설명이 일반적임

6:
추상적 사업 키워드만 있음

0:
적용 시나리오 확인 불가


----------------------------------------
보수적 점수 부여 원칙
----------------------------------------

95~100점대는 아래 조건을 모두 만족할 때만 허용한다.

- SK AX 공식 evidence가 여러 개 존재한다.
- 특허 핵심 제품, 기술, 적용 시나리오가 공식 evidence와 직접 연결된다.
- 단순 산업군/기술군 일치가 아니라 구체 서비스/오퍼링 수준의 연결이 확인된다.
- 특허 핵심어 또는 핵심 작동 방식과 공식 근거의 연결성이 충분하다.
- 중요한 missing_information 또는 risk_factors가 없다.

만점 제한:
아래 중 하나라도 해당하면 100점을 부여하지 않는다.

- 특허의 실제 제품 적용 여부가 확인되지 않는다.
- 특허의 핵심 구현 방법이 공식 evidence에 명시되지 않는다.
- 공식 evidence가 서비스/플랫폼 소개 수준에 머문다.
- risk_factors가 존재한다.
- missing_information이 존재한다.
- 특허와 공식 사업 근거의 1:1 매핑이 확인되지 않는다.

추가 상한 규칙:
- 공식 evidence가 강하더라도 실제 통합/적용 근거가 없으면 최고 90점대 초반까지만 허용한다.
- 특허 핵심어 일부가 공식 evidence에 없으면 business_context_alignment는 만점으로 보지 않는다.
- 적용 시나리오가 공식 페이지에 있으나 특허 구현과 직접 매핑되지 않으면 application_scenario_specificity는 만점으로 보지 않는다.
- broad category 또는 플랫폼 설명 중심이면 고득점은 가능해도 100점은 부여하지 않는다.
- "공식 evidence에 특정 구현/적용/제품 매핑이 명시되지 않음"은 risk_factors로 작성할 수 있다.


----------------------------------------
종합 점수
----------------------------------------

score = 하위 3개 합계

score = official_business_evidence + business_context_alignment + application_scenario_specificity

grade:
90 이상 → A
75 이상 → B
60 이상 → C
미만 → D

confidence:
0.0 ~ 1.0


출력 규칙:
- 점수 감점 사유와 자료 부족 사유를 구분한다.
- 실제 약점만 risk_factors에 작성한다.
- 자료 부족은 missing_information에 작성한다.
- "정보 없음 = 낮은 가치"로 해석하지 않는다.
- SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다.
- "공식 사이트에서 관련 사업 근거가 확인된다/확인되지 않는다" 수준으로 표현한다.
- 출력 JSON에는 subscores를 포함한다.
- `sub_scores` 필드는 사용하지 않는다.


Return ONLY JSON:

{
  "axis": "business_fit",
  "label": "사업연계성",
  "score": 0,
  "subscores": {
    "official_business_evidence": {
      "label": "공식 사업 근거 발견도",
      "score": 0,
      "max_score": 30,
      "rationale": "..."
    },
    "business_context_alignment": {
      "label": "사업 맥락 직접성",
      "score": 0,
      "max_score": 45,
      "rationale": "..."
    },
    "application_scenario_specificity": {
      "label": "적용 시나리오 구체성",
      "score": 0,
      "max_score": 25,
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
