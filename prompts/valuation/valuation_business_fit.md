# Valuation Business Fit Axis Prompt

사업 연계성 축을 평가한다.

사업 연계성은 단순히 관련 사업 분야 이름이 비슷한지 보는 축이 아니다.

이 특허가 SK AX의 공식 사업/서비스/제품 문맥과 얼마나 연결되는지,
관련제품 또는 핵심 기능이 공식 근거에서 확인되는지,
그리고 특허의 문제·해결수단·적용 대상이 사업 문맥과 얼마나 직접 맞물리는지를 평가한다.

총점은 100점이며 아래 3개 하위 항목 점수를 합산한다.

1. 공식 근거 존재성: 30점
2. 제품·기능 직접 매칭도: 45점
3. 사업 문맥 적합성: 25점


평가 목적:
- SK AX 공식 evidence에서 대상 특허와 연결되는 사업/서비스/제품 근거가 확인되는지 평가한다.
- 관련제품명과 특허 핵심 기능이 공식 evidence에서 직접 또는 부분적으로 확인되는지 평가한다.
- 특허의 문제, 해결수단, 적용 대상이 공식 evidence의 사업 문맥과 직접 연결되는지 평가한다.
- AI 평가 레포트의 사업 연계성 근거를 제공하되, 최종 판단을 단정하지 않는다.


평가 원칙:
- Return ONLY one JSON object.
- Markdown, 설명 문구, 코드블록을 출력하지 않는다.
- 모든 출력 값은 반드시 한국어로 작성한다.
- 입력에 없는 SK AX 적용 사례, 실제 사용 여부, 매출, 고객, 도입 계획을 만들지 않는다.
- SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다.
- 공식 사이트에서 관련 사업 근거가 확인된다/확인되지 않는다 수준으로 표현한다.
- 외부 뉴스, 블로그, SK그룹 다른 도메인, 미러링 사이트만으로 사업 연계성을 단정하지 않는다.
- 권리성, 기술성, 시장성, 포트폴리오 가치는 평가하지 않는다.
- 자료 부족은 `missing_information`과 `confidence`에 반영하고, 낮은 사업 가치로 자동 해석하지 않는다.
- 실제 확인된 약점만 `risk_factors`에 작성한다.
- `score`는 반드시 세 하위 항목 점수 합계와 일치해야 한다.
- `subscores`의 각 detail score는 아래 점수 후보 중 하나만 사용한다.


사용 근거:
- `business_fit_context.patent_description`
  - 특허명
  - 관련제품
  - 관련 사업 분야
  - 관련 기술 분야
  - 특허 요약
  - key_points
  - problem_or_purpose
  - solution_or_core_technology
  - use_case_or_application
  - key_terms
- `business_fit_context.skax_official_evidence`
  - evidence_id
  - title
  - url
  - source
  - source_type
  - relevance_score
  - matched_keywords
  - matched_terms
  - score_reasons
  - content_excerpt
  - business_context_hint
- `business_fit_context.sk_owned_media_evidence`
  - SK Careers Journal, SK OpenAPI News 등 SK 계열 운영 매체에서 수집된 보조 evidence
  - 수집 시 본문에 SK AX 또는 SK C&C 언급이 확인된 항목만 포함된다.
  - 이 evidence는 `skax.co.kr` 공식 사이트 근거보다 낮은 신뢰도 tier로 취급한다.
- `business_fit_context.quantitative_metrics`
  - official_evidence_count
  - official_site_evidence_count
  - sk_owned_media_evidence_count
  - business_evidence_count
  - best_relevance_score
  - official_business_evidence
  - product_function_direct_match
- evidence 목록 중 `sk_ax_official`, `company_disclosure`, `portfolio_context`, 관련 뉴스


사용 금지 정보:
- parsed_pdf 전체
- cleaned_markdown 전체
- claims_text 전체
- Tavily raw_content 전체
- candidate_results 전체
- search diagnostics 전체
- API key 또는 환경변수


----------------------------------------
1. 공식 근거 존재성 (30점)
----------------------------------------

목적:
SK AX 공식 evidence가 대상 특허의 사업 연계성 판단에 사용할 수 있을 만큼 존재하는지 평가한다.

평가 규칙:
- `skax_official_evidence`와 `quantitative_metrics.official_business_evidence`를 우선 참고한다.
- `sk_owned_media_evidence`는 SK 계열 운영 매체의 보조 근거로만 사용한다.
- 계열 매체 evidence만 있는 경우 `SK AX 공식 사이트에서 확인`이라고 쓰지 말고 `SK 계열 매체에서 SK AX/SK C&C 관련 언급이 확인`된다고 표현한다.
- `skax.co.kr` 공식 evidence와 SK 계열 운영 매체 evidence가 모두 없는 경우 이 항목은 0점이다.
- 공식 evidence가 있더라도 일반 인사이트/트렌드 페이지에 가깝고 구체 사업 페이지가 아니면 낮게 평가한다.
- 단순 건수만 보지 말고 title, url, content_excerpt가 대상 특허의 제품/사업/기술 문맥과 연결되는지 함께 본다.

점수 후보:
- 30점: 구체적인 SK AX 공식 evidence가 3건 이상 확인되고 대상 특허의 사업/제품/기술 문맥과 연결됨
- 24점: 구체적인 SK AX 공식 evidence가 2건 확인되고 대상 특허의 사업/제품/기술 문맥과 연결됨
- 16점: 구체적인 SK AX 공식 evidence가 1건 확인되어 일부 공식 근거가 있음
- 16점: SK AX 공식 사이트 근거는 없지만 SK 계열 운영 매체 evidence가 2건 이상 있고, 본문에서 SK AX 또는 SK C&C 관련 사업/서비스 문맥이 확인됨
- 8점: 공식 evidence는 있으나 일반 소개, 인사이트, 트렌드 성격이 강하거나 대상 특허와의 연결이 넓음
- 8점: SK AX 공식 사이트 근거는 없지만 SK 계열 운영 매체 evidence가 1건 있고, 본문에서 SK AX 또는 SK C&C 관련 사업/서비스 문맥이 확인됨
- 0점: SK AX 공식 사이트 및 SK 계열 운영 매체 evidence가 확인되지 않음


----------------------------------------
2. 제품·기능 직접 매칭도 (45점)
----------------------------------------

목적:
대상 특허의 관련제품과 핵심 기능이 SK AX 공식 evidence에서 얼마나 직접 확인되는지 평가한다.

평가 규칙:
- `patent_description.related_product`, `key_terms`, `solution_or_core_technology`, `use_case_or_application`을 기준으로 핵심 제품/기능을 잡는다.
- `quantitative_metrics.product_function_direct_match`는 참고값이다. 최종 출력은 아래 점수 후보 중 하나를 선택한다.
- 관련제품명이 직접 확인되어도 특허 핵심 기능이 확인되지 않으면 만점으로 평가하지 않는다.
- 같은 산업 또는 사업군 수준의 연결만 있으면 낮은 점수를 선택한다.
- 공식 evidence에서 확인되지 않는 핵심 기능은 `missing_information` 또는 `risk_factors`에 구분해 작성한다.

점수 후보:
- 45점: 관련제품과 특허 핵심 기능 대부분이 SK AX 공식 evidence에서 직접 확인됨
- 36점: 관련제품은 직접 확인되고 특허 핵심 기능 일부도 확인됨
- 24점: 관련제품 또는 유사 제품/서비스 문맥은 확인되지만 특허 핵심 기능 직접 매칭은 약함
- 12점: 같은 산업 또는 사업군 수준의 연결은 있으나 제품명과 핵심 기능 직접 연결은 확인되지 않음
- 0점: 제품/서비스 및 핵심 기능 연결이 확인되지 않음


----------------------------------------
3. 사업 문맥 적합성 (25점)
----------------------------------------

목적:
특허의 문제, 해결수단, 적용 대상이 SK AX 공식 evidence의 서비스/업무 문맥과 얼마나 자연스럽게 연결되는지 평가한다.

평가 규칙:
- 특허의 문제·해결수단·적용 대상과 공식 evidence의 서비스/업무 문맥을 비교한다.
- 적용 문맥이 직접 연결되면 높게 평가한다.
- 사업 영역과 적용 방향은 자연스럽지만 1:1 구현 매핑이 부족하면 중간 점수를 선택한다.
- 같은 산업 또는 기술군 수준의 연결만 있으면 낮은 점수 후보를 선택한다.
- 추정에 가까운 연결이거나 문맥상 연결 근거가 없으면 4점 또는 0점을 선택한다.

점수 후보:
- 25점: 특허의 문제, 해결수단, 적용 대상이 SK AX 공식 evidence의 서비스/업무 문맥과 직접 연결됨
- 18점: 사업 영역과 적용 방향은 자연스럽게 연결되지만 핵심 구현의 1:1 매핑은 부족함
- 10점: 같은 산업 또는 기술군 수준의 연결은 있으나 적용 문맥은 넓거나 간접적임
- 4점: 공식 evidence와 특허 문맥의 연결이 약하거나 추정에 가까움
- 0점: 문맥상 연결 근거가 확인되지 않음


----------------------------------------
종합 점수
----------------------------------------

score = 공식 근거 존재성 + 제품·기능 직접 매칭도 + 사업 문맥 적합성

grade:
80 이상 -> A
60 이상 -> B
40 이상 -> C
미만 -> D

confidence:
0.0 ~ 1.0

0.8~1.0:
공식 evidence, 제품/기능 직접 매칭, 사업 문맥 연결이 충분하고 판단이 명확함

0.5~0.79:
일부 공식 evidence 또는 간접 연결은 있으나 제품/기능 직접 매칭이나 적용 문맥 확인이 더 필요함

0.0~0.49:
공식 evidence가 부족하거나 대상 특허와 사업 문맥의 연결이 약함


출력 규칙:
- `axis`는 반드시 `business_fit`으로 작성한다.
- `label`은 반드시 `사업 연계성`으로 작성한다.
- `subscores.official_business_evidence.score`는 0, 8, 16, 24, 30 중 하나만 사용한다.
- `subscores.product_function_direct_match.score`는 0, 12, 24, 36, 45 중 하나만 사용한다.
- `subscores.business_context_fit.score`는 0, 4, 10, 18, 25 중 하나만 사용한다.
- 각 subscore에는 판단을 추적할 수 있도록 `details` 객체를 포함한다.
- `details`에는 점수 산정에 사용한 근거 수, 매칭 수준, 핵심 매칭어, score_reasons 등 입력에서 확인 가능한 값만 작성한다.
- `evidence_ids`에는 출력 근거로 실제 사용한 evidence_id만 작성한다.
- 점수 감점 사유와 자료 부족 사유를 구분한다.
- 관련제품 또는 핵심 기능이 공식 evidence에서 확인되지 않으면 그 한계를 구체적으로 작성한다.
- 계열 매체 근거만 있는 경우 공식 사이트 근거 부재를 `missing_information`에 포함한다.
- `정보 부족 있음`, `추가 확인 필요`, `N/A`는 누락, 불충분, 미적용 자료에만 사용한다.
- legacy 사업 연계성 필드는 출력하지 않는다.


Return ONLY JSON:
{
  "axis": "business_fit",
  "label": "사업 연계성",
  "score": 0,
  "subscores": {
    "official_business_evidence": {
      "label": "공식 근거 존재성",
      "score": 0,
      "max_score": 30,
      "details": {
        "official_site_evidence_count": 0,
        "sk_owned_media_evidence_count": 0,
        "score_reasons": []
      },
      "rationale": "..."
    },
    "product_function_direct_match": {
      "label": "제품·기능 직접 매칭도",
      "score": 0,
      "max_score": 45,
      "details": {
        "product_match_level": "direct/partial/broad/none",
        "matched_core_terms": [],
        "missing_core_terms": [],
        "core_match_ratio": 0.0
      },
      "rationale": "..."
    },
    "business_context_fit": {
      "label": "사업 문맥 적합성",
      "score": 0,
      "max_score": 25,
      "details": {
        "context_match_level": "direct/natural/broad/weak/none",
        "matched_business_context": [],
        "score_reasons": []
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
