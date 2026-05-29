# Valuation Business Fit Context Label Prompt

사업연계성의 세 번째 지표인 사업 문맥 적합성만 판단한다.

중요:
- 전체 business_fit score를 산정하지 않는다.
- grade를 산정하지 않는다.
- subscores를 산정하지 않는다.
- 공식 근거 존재성 점수와 제품·기능 직접 매칭도 점수를 변경하지 않는다.
- direct/plausible/broad/weak/none 중 하나의 label만 선택한다.
- SK AX가 해당 특허를 실제 사용 중이라고 단정하지 않는다.
- 공식 사이트에서 관련 사업 근거가 확인된다/확인되지 않는다 수준으로 표현한다.
- 입력에 없는 사실을 추정하지 않는다.
- 권리성, 기술성, 시장성, 포트폴리오 가치를 평가하지 않는다.
- 내부 진단 필드명을 출력하지 않는다.

평가 대상:
- patent_description
- skax_official_evidence
- rule_based_summary

사용 가능 정보:
- 특허명
- 관련제품
- 관련 사업 분야
- 관련 기술 분야
- 특허 요약
- key_points
- problem_or_purpose
- solution_or_core_technology
- use_case_or_application
- SK AX 공식 evidence의 title
- SK AX 공식 evidence의 url
- SK AX 공식 evidence의 content_excerpt
- matched_keywords
- matched_terms

사용 금지 정보:
- parsed_pdf 전체
- cleaned_markdown 전체
- claims_text 전체
- Tavily raw_content 전체
- candidate_results 전체
- search diagnostics 전체
- API key 또는 환경변수
- 외부 뉴스, 블로그, SK그룹 다른 도메인, 미러링 사이트

Label 기준:

direct:
특허의 문제, 해결수단, 적용 대상이 SK AX 공식 evidence의 서비스/업무 문맥과 직접 연결된다.

plausible:
사업 영역과 적용 방향은 자연스럽게 연결되지만, 핵심 구현의 1:1 매핑은 부족하다.

broad:
같은 산업 또는 기술군 수준의 연결은 있으나, 적용 문맥은 넓거나 간접적이다.

weak:
공식 evidence와 특허 문맥의 연결이 약하거나 추정에 가깝다.

none:
문맥상 연결 근거가 확인되지 않는다.

출력 규칙:
- Return ONLY one JSON object.
- context_fit_label은 direct/plausible/broad/weak/none 중 하나만 사용한다.
- rationale은 1~3문장으로 작성한다.
- confirmed_contexts에는 공식 evidence에서 확인되는 사업 문맥만 작성한다.
- unconfirmed_contexts에는 확인되지 않는 사업 문맥만 작성한다.
- score, grade, subscores, confidence, risk_factors, missing_information은 출력하지 않는다.

Return ONLY JSON:

{
  "context_fit_label": "direct/plausible/broad/weak/none",
  "rationale": "...",
  "confirmed_contexts": [],
  "unconfirmed_contexts": []
}
