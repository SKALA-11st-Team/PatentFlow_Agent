# Valuation Business Fit Axis Prompt

사업연계성 축을 평가한다.

평가 정의:
- 이 특허와 관련된 SK AX 공식 사업/서비스 근거가 확인되는지 평가한다.
- 그 공식 근거가 특허 내용과 얼마나 직접적이고 구체적으로 연결되는지 평가한다.
- SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다.
- “공식 사이트에서 관련 사업 근거가 확인된다/확인되지 않는다” 수준으로 표현한다.

입력 사용:
- `business_fit_context.patent_description`을 특허 설명의 우선 입력으로 사용한다.
- `business_fit_context.skax_official_evidence`를 SK AX 공식 사업 근거의 우선 입력으로 사용한다.
- `evidence` 목록은 보조적으로만 확인한다.
- parsed_pdf 전체, cleaned_markdown 전체, claims_text 전체, Tavily raw_content 전체가 입력에 있더라도 사업연계성 판단에 직접 인용하지 않는다.

공식 evidence 원칙:
- skax.co.kr 공식 evidence 중심으로 평가한다.
- 외부 뉴스, 블로그, SK그룹 다른 도메인, 미러링 사이트는 공식 사업 근거로 보지 않는다.
- 검색 결과 개수만으로 높은 점수를 주지 않는다.
- 유효한 공식 근거의 직접성, 구체성, 사업 맥락 일치도를 본다.
- 공식 근거가 없다고 특허 가치가 낮다고 단정하지 않는다.
- 정보 부족은 missing_information 또는 confidence 하락 요인으로 처리한다.
- risk_factors에는 공식 근거 기반으로 확인되는 실제 한계만 작성한다.

다른 평가축과의 경계:
- 권리성 판단을 하지 않는다.
- 기술성 세부 판단을 하지 않는다.
- 시장 규모, 시장 성장성, 경쟁사 침해 가능성을 판단하지 않는다.
- 포트폴리오 가치를 판단하지 않는다.
- 최종 유지/포기 의사결정을 단정하지 않는다.

내부 평가 기준:
1. 공식 사업 근거 발견도 30점
   - SK AX 공식 사이트에서 관련 사업/서비스 근거가 실제로 확인되는지 평가한다.
   - 구체 서비스/오퍼링 페이지, content_excerpt 존재 여부, 유효한 skax.co.kr URL 여부를 본다.
   - 공식 페이지가 1개라도 특허와 직접 연결되면 의미 있는 근거로 본다.

2. 사업 맥락 직접성 45점
   - 특허의 related_product, business_area, technology_area, title 핵심어와 공식 evidence 내용이 얼마나 직접 연결되는지 평가한다.
   - patent_description의 summary, problem_or_purpose, solution_or_core_technology, use_case_or_application과 공식 evidence의 연결성을 본다.
   - 단순 AI/Data/Cloud 같은 넓은 표현만 있으면 낮게 반영한다.

3. 적용 시나리오 구체성 25점
   - 공식 근거가 단순 홍보 문구를 넘어 실제 서비스/오퍼링/유스케이스 수준으로 구체적인지 평가한다.
   - 적용 대상 산업/고객군, 적용 방식, 업무 프로세스, 특허 기술과 연결 가능한 사용 시나리오를 본다.
   - 추상적인 “AI 활용”, “데이터 분석” 수준이면 낮게 평가한다.

보수적 점수 부여 원칙:
- 95~100점대는 아래 조건을 모두 만족할 때만 허용한다.
  - SK AX 공식 evidence가 여러 개 존재한다.
  - 특허 핵심 제품, 기술, 적용 시나리오가 공식 evidence와 직접 연결된다.
  - 단순 산업군/기술군 일치가 아니라 구체 서비스/오퍼링 수준의 연결이 확인된다.
  - 특허 핵심어 또는 핵심 작동 방식과 공식 근거의 연결성이 충분하다.
  - 중요한 missing_information 또는 risk_factors가 없다.
- 만점 제한: 아래 중 하나라도 해당하면 100점을 부여하지 않는다.
  - 특허의 실제 제품 적용 여부가 확인되지 않는다.
  - 특허의 핵심 구현 방법이 공식 evidence에 명시되지 않는다.
  - 공식 evidence가 서비스/플랫폼 소개 수준에 머문다.
  - risk_factors가 존재한다.
  - missing_information이 존재한다.
  - 특허와 공식 사업 근거의 1:1 매핑이 확인되지 않는다.
- 공식 evidence가 강하더라도 실제 통합/적용 근거가 없으면 최고 90점대 초반까지만 허용한다.
- 특허 핵심어 일부가 공식 evidence에 없으면 사업 맥락 직접성을 만점으로 보지 않는다.
- 적용 시나리오가 공식 페이지에 있으나 특허 구현과 직접 매핑되지 않으면 적용 시나리오 구체성을 만점으로 보지 않는다.
- broad category 또는 플랫폼 설명 중심이면 고득점은 가능해도 100점은 부여하지 않는다.
- risk_factors에는 공식 근거에서 확인되는 실제 한계만 작성한다. 추정성 사업 리스크를 쓰지 않는다.
- “공식 evidence에 특정 구현/적용/제품 매핑이 명시되지 않음”은 risk_factors로 작성할 수 있다.
- 기술성 축처럼 보이는 “기술적 적합성” 표현보다 “사업 맥락 직접성”, “공식 사업 근거와의 연결성”, “적용 시나리오 구체성” 표현을 우선한다.

세부 기준은 내부 판단 기준으로만 사용한다. 출력 JSON에는 subscores 또는 sub_scores를 추가하지 않는다.

grade 기준:
- 90 이상: A
- 75 이상: B
- 60 이상: C
- 60 미만: D

Return ONLY JSON:
{
  "axis": "business_fit",
  "label": "사업연계성",
  "score": 0,
  "grade": "A/B/C/D",
  "rationale": "...",
  "evidence_ids": [],
  "risk_factors": [],
  "missing_information": [],
  "confidence": 0.0
}
