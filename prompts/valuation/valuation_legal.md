# Valuation Legal Axis Prompt

권리성 축을 평가한다.

평가 목표:
- 대표 청구항, 청구항 통계, 초록, IPC/CPC, portfolio_context를 바탕으로 권리범위의 명확성, 구성의 구체성, 보호 범위의 실용성을 평가한다.
- 무효 가능성, 침해 가능성, 분쟁 가능성은 법적 결론으로 단정하지 않는다.

사용 근거:
- 대표 청구항
- 전체 청구항
- 독립항/종속항 수
- 전체 청구항 수
- claim_stats
- representative_claims
- prior_art_candidates
- citation/prior_art evidence
- IPC/CPC
- 초록
- 등록 상태
- portfolio_context

평가 기준:
- 높은 점수: 대표 청구항의 기술 구성이 비교적 명확하고, 보호하려는 기능/처리 흐름이 구체적이며, 청구항 구성이 포트폴리오와 보완 관계를 가지는 경우
- 중간 점수: 핵심 구성은 확인되지만 권리범위의 폭, 차별성, 청구항 구체성 판단에 추가 확인이 필요한 경우
- 낮은 점수: 대표 청구항 정보가 부족하거나, 구성 요소가 지나치게 추상적이거나, 권리범위 판단에 필요한 정보가 부족한 경우

주의:
- 무효, 침해, 분쟁 가능성을 단정하지 않는다.
- prior_art_candidates 또는 citation/prior_art evidence가 제공되면 권리안정성 판단에 참고하되, 후보 문헌만으로 무효 가능성을 단정하지 않는다.
- 선행기술/인용문헌 후보가 없으면 이를 임의로 만들지 말고 confidence와 missing_information에만 보수적으로 반영한다.
- “권리범위가 넓다/좁다”는 표현은 청구항 근거가 있을 때만 사용한다.
- representative_claims 또는 claim_stats가 제공된 경우, 이를 기준으로 권리 구조를 판단하고 “청구항 전문 부재”를 반복하지 않는다.
- representative_claims와 claim_stats가 모두 부족할 때만 청구항 정보 부족을 missing_information에 남긴다.
- 심사이력·보정이력·파일랩퍼 전문은 통상 사업부가 인터넷에서 쉽게 확인할 자료가 아니므로, 핵심 판단 근거가 아니라 법무/특허팀 세부 검토 항목으로만 간단히 언급한다.
- portfolio_context는 권리 포트폴리오의 보완성 판단에 사용한다.
- sibling 특허들이 전후 공정, 보완 기능, 제어 계층, 장치 구성요소를 나누어 보호하는 경우 대상 특허 단독의 권리 한계를 보완하는지 판단한다.
- sibling 특허 사이에 실질적 중복이 크면 유지 우선순위를 낮추는 리스크로, 서로 다른 구성요소를 보호하면 포트폴리오 시너지로 반영한다.

Return ONLY JSON:
{
  "axis": "legal",
  "label": "권리성",
  "score": 0,
  "grade": "A/B/C/D",
  "rationale": "...",
  "evidence_ids": [],
  "risk_factors": [],
  "missing_information": [],
  "confidence": 0.0
}
