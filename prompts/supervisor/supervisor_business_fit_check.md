# Business Fit Axis Quality Check Criteria

이 문서는 사업 연계성 평가 결과를 검토하기 위한 축별 품질 기준입니다.
라우팅을 결정하는 Supervisor 프롬프트가 아니며, `next_action`을 출력하지 않습니다.
최종 라우팅은 4개 축의 status를 결정적으로 집계해 수행하며, 별도의 라우팅 LLM은 없습니다.

## 검토 대상
- valuation.axes.business_fit
- business_fit_context
- SK AX 공식 evidence
- SK 계열 운영 매체 evidence
- patent metadata
- summary_result
- evidence.samples

## 정상으로 볼 수 있는 상태
- `axis`가 `business_fit`이고 `label`이 `사업 연계성`입니다.
- `score`, `grade`, `rationale`, `evidence_ids`, `risk_factors`, `missing_information`, `confidence`가 존재합니다.
- 점수 구조는 공식 근거 존재성 30점 + 제품·기능 직접 매칭도 45점 + 사업 문맥 적합성 25점입니다.
- `subscores`의 각 항목은 `details`를 보존해 근거 수, 매칭 수준, 핵심 매칭어를 추적할 수 있습니다.
- SK AX가 실제로 특허를 사용 중이라고 단정하지 않습니다.
- 사업 연계성의 긍정 근거에는 SK AX 또는 SK C&C와 연결되는 공식 사이트/계열 운영 매체 근거가 최소 1건 포함됩니다.
- 계열 운영 매체는 SK Careers Journal(`skcareersjournal.com`), SK OpenAPI News(`openapi.sk.com`) 또는 `sk_group_owned_media`/`sk_related_owned_media`로 분류된 자료를 의미합니다.
- 일반 뉴스·산업 기사·블로그는 시장·기술 문맥의 보조 자료일 수 있으나 사업 연계성의 직접 근거로 사용하지 않습니다.

## 중점 검토 기준
1. 공식 근거 존재성
   - `business_fit_context.skax_official_evidence`, `sk_owned_media_evidence`, `cited_evidence`를 함께 확인합니다.
   - `skax.co.kr` 공식 evidence와 SK 계열 운영 매체 evidence를 구분했는지 확인합니다.
   - SK Careers Journal·SK OpenAPI News 자료는 본문에 SK AX 또는 SK C&C 언급이 확인된 경우에만 계열 운영 매체 보조 근거로 인정합니다.
   - 계열 매체만 있는 경우 이를 SK AX 공식 사이트 근거처럼 표현하지 않았는지 확인합니다.
   - 공식 evidence가 없다는 사실을 곧바로 사업 가치 없음으로 단정하지 않았는지 확인합니다.
   - 공식/계열 근거가 수집되어 있는데 `valuation_axis.evidence_ids`에서 사용하지 않았다면 `valuation_retry`입니다.
   - 공식/계열 근거가 모두 없다면 재검색으로 보완 가능한 경우 `query_rewriting`입니다.

2. 제품·기능 직접 매칭도
   - 관련제품명과 특허 핵심 기능이 evidence에서 직접 또는 부분적으로 확인되는지 확인합니다.
   - 제품명만 같고 핵심 기능 연결이 약한데 만점 또는 고득점으로 처리하지 않았는지 확인합니다.
   - 같은 산업·사업군 수준의 연결과 제품·기능 직접 매칭을 구분했는지 확인합니다.
   - 공식 페이지에 특허 핵심 용어가 없는데 제품·기능 직접 매칭을 높게 평가하지 않았는지 확인합니다.

3. 사업 문맥 적합성
   - 특허의 문제, 해결수단, 적용 대상이 SK AX 사업 문맥과 어떻게 맞물리는지 설명했는지 확인합니다.
   - 공개 자료에서 확인 가능한 사업 문맥과 실제 제품 탑재 여부를 구분했는지 확인합니다.
   - 미확인 정보를 낮은 사업 가치로 자동 해석하지 않았는지 확인합니다.

4. 출처 및 표현 경계
   - `cited_evidence` 중 `is_sk_ax_official` 또는 `is_sk_owned_media`가 아닌 일반 뉴스·외부 기사를 사업 연계성 직접 근거로 사용하면 `valuation_retry`입니다.
   - `portfolio_context`는 관련 특허군의 내부 문맥을 설명하는 보조 근거로만 허용하며, SK AX 공식 사업 근거를 대신할 수 없습니다.
   - 청구항·초록·명세서·원문 PDF 제공 여부를 사업 연계성의 감점, risk_factors, missing_information으로 사용하면 `valuation_retry`입니다.
   - 입력 없이 “사용 중”, “도입 완료”, “매출 발생”, “고객 적용”, “제품에 탑재”라고 단정하면 `valuation_retry`입니다.
   - SK AX가 아닌 다른 SK 계열사·외부 회사 사례를 SK AX 사업 근거처럼 표현하면 `valuation_retry`입니다.

5. 점수 구조
   - 공식 근거 존재성은 0/8/16/24/30점 중 하나인지 확인합니다.
   - 제품·기능 직접 매칭도는 0/24/36/45점 중 하나인지 확인합니다.
   - 사업 문맥 적합성은 0/4/10/18/25점 중 하나인지 확인합니다.
   - 세 하위 점수 합계가 `score`와 다르면 `valuation_retry`입니다.

## 재평가가 필요한 신호
- 사업 연계성 고득점인데 SK AX 공식 또는 계열 매체 근거가 거의 없습니다.
- 공식 사이트 근거와 일반 뉴스, 블로그, SK그룹 다른 도메인 근거를 같은 수준으로 취급합니다.
- 실제 적용, 매출, 고객, 도입 계획을 입력 없이 단정합니다.
- 관련제품 메타데이터만으로 제품·기능 직접 매칭을 높게 평가합니다.
- 일반 외부 기사를 SK AX 공식 제품·서비스 근거처럼 인용합니다.
- 청구항·초록·명세서 제공 여부를 사업 연계성 위험 또는 부족 정보로 작성합니다.

## 근거 재수집이 필요한 신호
- SK AX 공식 evidence와 계열 매체 evidence가 모두 부족합니다.
- 관련제품 또는 핵심 기능을 확인할 수 있는 evidence가 없습니다.
- evidence_id가 실제 evidence_bundle에 존재하지 않습니다.

## query_rewriting은 "채울 수 있는 부족"에만
- query_rewriting은 재검색으로 **채워질 수 있는** 근거(SK AX 공식 `site:skax.co.kr`·SK 계열 매체, 제품·기능 확인 근거)가 빈약할 때만 선택합니다.
- 단, 대상 기술에 해당하는 SK AX 공식 페이지가 애초에 존재하지 않으면 재검색해도 채워지지 않습니다. 이런 구조적 부재는 query_rewriting 사유가 아니라 missing_information/confidence로 처리하세요.
- 재수집 **횟수 제한은 시스템(코드)이 관리**합니다. 같은 부족이 반복되어 보이더라도 retry 횟수는 신경 쓰지 말고, 지금 주어진 근거의 품질만 보고 판정하세요. 한도를 넘으면 시스템이 알아서 진행시킵니다.

## 근거 존재·내용 판단 주의
- evidence.samples에는 이 평가가 인용한 근거(evidence_ids)가 우선 포함되며, 전체 근거의 일부 미리보기입니다.
- 근거의 존재 여부는 evidence.samples가 아니라 known_evidence_ids로 판단하세요. known_evidence_ids에 있으면 그 근거는 존재합니다.
- samples에 본문이 안 보인다는 이유만으로 "근거 누락"으로 단정하거나 근거 재수집(query_rewriting)을 요청하지 마세요. 실제로 known_evidence_ids에 없는 항목(unknown_evidence_ids)만 문제 삼습니다.

## 출력 형식
Return ONLY one JSON object.
`next_action`은 출력하지 마세요.

{
  "status": "passed" | "valuation_retry" | "query_rewriting",
  "issues": [],
  "reason": ""
}

status 선택 기준:
- `passed`: 사업 연계성 평가가 자기 기준에 맞고, 공식/계열 근거와 제품·기능 매칭 판단이 확인됨
- `valuation_retry`: 근거는 있으나 사업 연계성 평가 논리, 점수, 표현을 다시 써야 함
- `query_rewriting`: 사업 연계성 판단에 필요한 SK AX 공식/계열 매체 또는 제품·기능 근거가 부족함
