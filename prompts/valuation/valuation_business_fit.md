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
- 청구항 또는 초록의 제공 여부는 사업 연계성 점수의 평가 요소가 아니며 `risk_factors`나 `missing_information`에 작성하지 않는다.
- 대상 특허의 문서 제공 상태를 언급해야 하는 경우 `business_fit_context.target_source_status`만 최신 기준으로 사용한다.
- `portfolio_context`에 포함된 대상 특허의 청구항·초록 제공 여부 설명은 수집 시점이 다를 수 있으므로 사용하지 않는다.
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
- `business_fit_context.target_source_status`
  - 최신 전처리 결과의 청구항·초록·상세설명 제공 상태
  - 사업 연계성 점수 산정용 정보가 아니라 오래된 포트폴리오 문서 상태 설명을 배제하기 위한 기준
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
- `business_fit_context.sk_ax_relevant_news_evidence`
  - 압축 단계에서 SK AX(또는 SK C&C)의 사업/제품/서비스와 직접 관련 있다고 판단된 뉴스 등 보조 근거
  - `sk_ax_official`/`sk_group_owned_media` 공식 근거보다 낮은 tier의 참고 자료다.
  - 사업 문맥·제품 매칭 판단의 보조 근거로만 활용하고, **공식 근거 존재성(30점) 산정에는 포함하지 않는다**(이 항목만으로 공식 근거가 있다고 보지 않는다).
- `business_fit_context.quantitative_metrics`
  - official_evidence_count
  - official_site_evidence_count
  - sk_owned_media_evidence_count
  - business_evidence_count
  - best_relevance_score
  - official_business_evidence
  - product_function_direct_match
- evidence 목록 중 `sk_ax_official`, `sk_group_owned_media` (SK AX 공식 사이트/계열 매체 콘텐츠), 그리고 sk_ax_relevant 판단된 뉴스 보조 근거


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
대상 특허의 관련제품이 SK AX에서 실제로 상용화·운영되고 있다는 근거가 공식/계열 evidence에 있는지 평가한다. 정확한 제품명/브랜드가 evidence에서 확인되는지가 핵심이며, 그 위에서 특허의 핵심 기능(구성요소·흐름)까지 확인되는지로 점수를 가른다.

판단 입력:
- `patent_description.related_product`: 관련 제품명/브랜드.
- `patent_structure.key_elements`: 발명의 주요 구성요소(핵심/보조)와 역할.
- `patent_structure.key_flow`: 구성요소 간 흐름(입력→처리→출력) 관계.
- SK AX 공식/계열 evidence 본문.

평가 규칙:
- 제품 식별이 게이트다: 관련제품명 또는 그 핵심 브랜드(부가어를 뺀 형태, 예: `AccuInsight+ Runtime`→`AccuInsight+`)가 evidence 본문에서 직접 확인되지 않으면 0점이다. 회사명·사업영역('AI', 'Data', 'AIOps' 등) 언급만으로는 제품 식별로 보지 않는다. 사업 방향만 맞는 경우는 '사업 문맥 적합성'에서 평가하며, 이 항목에서는 0점이다.
- 제품이 확인된 위에서 evidence가 key_elements를 (다른 표현으로라도) 설명하는지, 나아가 key_flow의 흐름(구성요소 간 관계)까지 설명하는지 의미적으로 판단해 점수를 올린다.
- 인용 필수: 제품·구성요소·흐름 확인을 주장하면 evidence의 어느 문장이 어느 제품명/key_element_id/흐름과 연결되는지 rationale에 구체적으로 인용한다. 인용할 수 없으면 인정하지 않는다.
- 확인되지 않는 구성요소·기능은 `missing_information`에 적는다.

점수 후보:
- 45점: 정확한 제품명/브랜드가 직접 확인되고, 핵심 구성요소 대부분과 그 흐름(관계)까지 evidence에서 확인됨
- 36점: 정확한 제품명/브랜드가 직접 확인되고, 핵심 구성요소 일부가 확인됨(흐름은 부분적이거나 불명확함)
- 24점: 정확한 제품명/브랜드는 확인되나 특허 핵심 기능과의 직접 연결은 약함
- 0점: 정확한 제품명/브랜드가 evidence에서 확인되지 않음


----------------------------------------
3. 사업 문맥 적합성 (25점)
----------------------------------------

목적:
특허의 문제·해결수단·적용 대상이 SK AX 공식/계열 evidence의 서비스·업무 문맥과 얼마나 자연스럽게 연결되는지(사업 방향 정합)를 평가한다. 제품이 evidence에 직접 등장하지 않아도 SK AX가 하는 일과 이 특허의 쓰임이 맞물리면 평가한다.

평가 규칙:
- 매칭도와 분리한다: "발명이 자료에 직접 묘사됐는지"(제품·기능 직접 매칭도 영역)를 근거로 이 점수를 올리지 않는다. 오직 특허의 문제·적용대상과 SK AX 사업 방향의 정합만 본다.
- 인용 강제: 18점 이상은 evidence에서 SK AX의 특정 서비스·업무가 확인되고 그것이 특허의 적용대상·문제와 맞물린다는 근거를 rationale에 인용한다.
- 부풀림 방지: 'AI', 'Data', '클라우드'처럼 같은 산업·기술군 수준의 일반적 연결만 있으면 10점 이하로 평가한다.

점수 후보:
- 25점: SK AX의 구체적 서비스·업무가 특허의 적용대상·문제를 직접 다룸
- 18점: 사업 영역·적용 방향은 자연스럽게 연결되나 핵심 구현의 정합성은 부족함
- 10점: 같은 산업 또는 기술군 수준의 연결만 있음(적용 문맥은 넓거나 간접적)
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
- `subscores.product_function_direct_match.score`는 0, 24, 36, 45 중 하나만 사용한다.
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
