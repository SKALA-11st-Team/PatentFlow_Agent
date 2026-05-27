# Valuation Legal Axis Prompt

권리성 축을 평가한다.

권리성은 단순 법적 유효성만 평가하는 축이 아니다.

이 특허가 유지할 가치가 있는 권리 자산인지,
즉 안정적으로 유지될 수 있고,
실질적인 보호력을 가지며,
사내 IP 포트폴리오에서 전략적 의미가 있는지를 평가한다.

총점은 100점이며 시스템 코드가 아래 3개 하위 항목 점수를 합산한다.

1. 권리안정성: 40점
2. 권리보호력: 40점
3. 포트폴리오·방어가치: 20점


평가 목적:
- 현재 등록된 최종 청구항과 등록상태를 기준으로 권리가 안정적으로 유지될 가능성을 평가한다.
- 최종 독립항과 종속항이 핵심 기술을 얼마나 적절히 보호하는지 평가한다.
- 패밀리, 해외 등록 B 문헌, 사내 관련 특허군이 전략적 방어 자산으로 의미가 있는지 평가한다.
- 법적 결론을 단정하지 않는다.


평가 원칙:
- 입력에 없는 사실을 추정하지 않는다.
- 제품/서비스 적용 여부는 권리성 평가에 반영하지 않는다.
- 경쟁사 침해 가능성을 단정하지 않는다.
- 선행문헌 후보 존재만으로 무효 리스크를 자동 판단하지 않는다.
- 관련 특허군 존재만으로 시너지를 자동 부여하지 않는다.
- 패밀리 특허 존재만으로 높은 점수를 자동 부여하지 않는다.
- 심사기록, Office Action, 보정 이력은 평가 대상이 아니다.
- "추가 검토 필요"만 쓰고 평가 회피를 금지한다.
- 심사기록 부재를 risk_factors 또는 missing_information에 작성하지 않는다.
- 자료 부족은 특허 약점이 아니라 confidence 하락 요인으로 처리한다.
- 삭제 청구항 번호, 보정 전 청구항, has_deleted_claims_gap 정보가 입력에 보이더라도 이를 권리 안정성 리스크나 권리범위 축소 근거로 사용하지 않는다.
- 권리성 평가는 현재 유효한 최종 청구항만 기준으로 한다.


사용 근거:
- 등록상태
- `claim_context.independent_claims`
- `claim_context.dependent_claims`
- 독립항/종속항 구조
- claim_stats
- 발명의 효과
- summary_result
- prior_art_candidates
- citation_evidence.kr_citation_documents
- citation_evidence.foreign_citation_documents
- citation_evidence.foreign_claim_lookup_candidates
- citation_evidence.citing_signal
- legal_scoring_context
- 패밀리 특허 정보
- 해외 등록 B 문헌 여부
- portfolio_context
- 관련 특허군
- 사내 유사 특허
- IPC/CPC
- 예상 소멸일 또는 잔여 존속기간


----------------------------------------
1. 권리안정성 (40점)
----------------------------------------

목적:
현재 등록된 권리가 안정적으로 유지될 가능성을 평가한다.

정의:
신규성 리스크:
출원 전 공개된 선행기술에 청구항 핵심 구성이 실질적으로 동일하게 존재할 가능성

진보성 리스크:
기존 기술 조합으로 쉽게 도출될 가능성

세부지표:
- 현재 권리상태: gate, 코드 산정
- 선행문헌 충돌도: 20점, LLM 라벨
- 유사 청구항 밀집도: 12점, LLM 라벨
- 등록/청구항 기본 안정성: 8점, 코드 산정

평가 규칙:
- prior_art_candidates는 선행문헌 후보 목록이다.
- citation_evidence는 제목, 초록, 대표 청구항이 확인된 선행문헌이다.
- citation_evidence.citing_signal은 피인용/후속 참조의 통계 신호이며, 선행문헌 비교 대상이나 청구항 유사성 판단 근거로 사용하지 않는다.
- `claim_context`의 독립항 및 종속항 구성과 선행문헌의 기술 구성을 직접 비교하여 판단한다.
- 해외 패밀리/주요국 등록 여부는 권리안정성 점수에 사용하지 않는다.
- 단순히 유사 문헌 존재 여부가 아니라 "어떤 구성요소가 얼마나 겹치는지"를 기준으로 점수화한다.
- "상세 1:1 비교가 추가로 필요하다"는 표현만으로 평가를 회피하지 않는다.
- 제공된 문헌 정보 범위 내에서 비교 판단을 수행한다.

LLM이 선택할 라벨:
- prior_art_collision: low | medium | high | critical | unknown
  - low = 20점: 선행문헌과 일부 유사하나 핵심 차별 구성이 명확함
  - medium = 13점: 일부 핵심 구성이 겹치지만 차별점도 존재함
  - high = 6점: 핵심 구성 상당 부분이 선행문헌과 유사함
  - critical = 0점: 독립항 핵심 구성이 선행문헌에 거의 그대로 존재함
  - unknown = 점수 미산정: 비교 정보 부족
- similar_claim_density: low | medium | high | critical | unknown
  - low = 12점: 출원 전 유사 청구항이 없거나 1건 수준이며 핵심 구성 반복이 약함
  - medium = 8점: 출원 전 유사 청구항이 2~4건 수준이거나 일부 구성 반복은 있으나 핵심 차별점 유지
  - high = 4점: 출원 전 유사 청구항이 5건 이상이거나 복수 문헌에서 핵심 구성 일부가 반복됨
  - critical = 0점: 핵심 구성이 다수 선행 청구항과 강하게 중복
  - unknown = 점수 미산정: 비교 불가


----------------------------------------
2. 권리보호력 (40점)
----------------------------------------

목적:
청구항이 핵심 기술을 충분히 보호하고,
경쟁사가 쉽게 회피설계하기 어려운지 평가한다.

세부지표:
- 독립항 확보 여부: 6점, 코드 산정
- 종속항 보완성: 6점, LLM 라벨
- 핵심 해결수단 반영 여부: 12점, LLM 라벨
- 구현 한정 과다 여부: 10점, LLM 라벨
- 회피설계 대응 가능성: 6점, LLM 라벨

평가 규칙:
- 청구항 수만으로 보호력을 판단하지 않는다.
- "권리범위가 넓다"는 표현은 청구항 근거가 있을 때만 사용한다.
- 권리보호력은 대상 특허의 청구항 구조만 기준으로 판단한다.
- 관련 특허군은 포트폴리오·방어가치에서만 반영한다.
- 아래 정량 보조 기준은 절대 규칙이 아니라 라벨 선택 보조 기준이다.
- 단순 개수보다 핵심 구성의 중요도와 청구항 내 역할을 우선한다.
- 근거가 부족하면 유리하거나 불리하게 추정하지 말고 unknown을 선택한다.

LLM이 선택할 라벨:
- dependent_claim_support: strong | moderate | weak | none | unknown
  - strong = 6점: 종속항 5개 이상이고, 핵심 구현 변형·조건·대체 실시형태·fallback position 중 2개 이상을 실질적으로 보완함
  - moderate = 4점: 종속항 2~4개이고, 일부 보완 구성을 제공하지만 보호 범위 확장 또는 방어 논리가 부분적임
  - weak = 2점: 종속항 1개 수준이거나, 종속항 다수가 있어도 독립항을 단순 구체화하거나 반복하는 수준에 가까움
  - none = 0점: 종속항이 없거나 실질적인 보완 기능이 확인되지 않음
  - unknown = 점수 미산정: 종속항 내용 또는 의존관계 판단 정보 부족
- core_feature_covered: clear | partial | weak | unknown
  - clear = 12점: 발명의 핵심 해결수단 대부분, 대략 80% 이상이 독립항에 반영되어 청구항과 발명의 효과가 직접 연결됨
  - partial = 7점: 핵심 해결수단 일부, 대략 40~80%가 독립항에 반영되나 중요한 구성·처리 단계·조건 중 일부가 약하게 표현됨
  - weak = 2점: 핵심 해결수단 반영이 대략 40% 미만이거나, 발명의 효과를 만드는 핵심 수단보다 주변 구성 중심으로 보호됨
  - unknown = 점수 미산정: 발명의 핵심 해결수단 또는 독립항 내용 판단 정보 부족
- claim_scope_limitation: broad | moderate | narrow | overly_narrow | unknown
  - broad = 10점: 독립항이 핵심 기능 중심이며 특정 구현 방식, 데이터 구조, 순서, 임계값 등 세부 구현 한정이 0~1개 수준임
  - moderate = 7점: 세부 구현 한정이 2~3개 있으나 핵심 보호 범위가 비교적 유지됨
  - narrow = 3점: 특정 모델, 절차, 장치 구성, 데이터 조건 등 세부 구현 한정이 4개 이상이거나 보호 범위가 좁게 형성됨
  - overly_narrow = 0점: 특정 실시예 또는 세부 구현 조건에 가까워 동일 효과를 다른 방식으로 피하기 쉬움
  - unknown = 점수 미산정: 청구항 한정 요소를 판단할 정보 부족
- design_around_difficulty: hard | moderate | easy | unknown
  - hard = 6점: 동일 효과를 내려면 독립항의 핵심 구성 대부분을 사용할 수밖에 없어 회피 경로가 뚜렷하지 않음
  - moderate = 4점: 대체 구현 경로가 1~2개 가능하지만 핵심 구성 일부는 여전히 필요함
  - easy = 1점: 대체 구현 경로가 3개 이상이거나 핵심 구성을 우회해도 동일 효과 달성이 비교적 쉬움
  - unknown = 점수 미산정: 회피설계 가능성을 판단할 청구항 또는 구현 정보 부족


----------------------------------------
3. 포트폴리오·방어가치 (20점)
----------------------------------------

목적:
이 특허가 단독 권리를 넘어 전략적 IP 자산으로 의미가 있는지 평가한다.

세부지표:
- 관련 특허군 연결성: 5점, LLM 라벨
- 포트폴리오 커버리지 확장성: 6점, LLM 라벨
- 해외 패밀리/주요국 등록: 5점, 코드 산정
- 피인용/후속 참조 신호: 4점, 코드 산정

평가 규칙:
- 패밀리, 해외 등록, 관련 특허군 정보가 존재하면 반영한다.
- 관련 정보가 없다고 자동 감점하지 않는다.
- 실제 중복 관계가 확인될 때만 감점한다.
- "정보가 제공되지 않아 제한적" 같은 표현 사용 금지.
- 정보 부족은 confidence에만 반영한다.
- citation_evidence.citing_signal은 이 항목의 피인용/후속 참조 신호에만 사용한다.
- 해외 패밀리/주요국 등록과 피인용/후속 참조 신호는 코드가 산정하므로 LLM은 관련 특허군의 연결성과 커버리지 확장성만 라벨링한다.
- 관련 특허군 개수 기준은 판단 보조 기준이며, 단순히 같은 제품군에 속한다는 이유만으로 strong을 선택하지 않는다.

코드 산정 참고:
- 해외 패밀리/주요국 등록: US/EP/CN/JP 중 등록 패밀리 존재 = 5점, 해외 패밀리 존재 = 3점, 국내 패밀리만 존재 = 1점, 패밀리 정보 없음 = unknown
- 피인용/후속 참조 신호: 피인용 3건 이상 = 4점, 1~2건 = 2점, 0건 = 0점, 피인용 정보 없음 = unknown

LLM이 선택할 라벨:
- portfolio_connection: strong | moderate | weak | unknown
  - strong = 5점: 동일 제품, 공정, 서비스 또는 기술 로드맵 안에서 관련 특허군 3건 이상과 자연스럽게 묶이며 구체적 연결 관계가 확인됨
  - moderate = 3점: 같은 기술분야 또는 인접 기능의 관련 특허 1~2건이 확인되지만 연결 구조가 부분적임
  - weak = 1점: 넓은 기술분야 또는 제품군 수준의 유사성만 확인됨
  - unknown = 점수 미산정: portfolio_context 또는 관련 특허군 정보 부족
- portfolio_coverage_extension: strong | moderate | weak | none | unknown
  - strong = 6점: 관련 특허군이 다루지 않는 별도 기술 요소, 처리 단계, 시스템 구성요소를 2개 이상 명확히 커버함
  - moderate = 4점: 관련 특허군 안에서 새로운 보호 포인트 1개 이상을 추가함
  - weak = 2점: 관련성은 있으나 기존 관련 특허군 대비 추가 커버리지가 작거나 역할이 좁음
  - none = 0점: 독립적인 추가 보호 포인트가 확인되지 않거나 기존 관련 특허군과 실질적으로 중복됨
  - unknown = 점수 미산정: 관련 특허군의 보호 범위 또는 대상 특허의 역할 판단 정보 부족


----------------------------------------
종합 점수
----------------------------------------

score = 코드에서 재계산한다. LLM은 scoring_labels와 rationale을 제공한다.

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
- "방어력이 제한적" 같은 단정 표현 금지.
- scoring_labels의 라벨은 반드시 위에서 허용한 값 중 하나만 사용한다.
- LLM이 출력한 score, grade, subscores.score는 코드에서 재계산되므로 임의 점수 판단보다 라벨 선택의 일관성을 우선한다.


Return ONLY JSON:

{
  "axis": "legal",
  "label": "권리성",
  "score": 0,
  "scoring_labels": {
    "prior_art_collision": "low/medium/high/critical/unknown",
    "similar_claim_density": "low/medium/high/critical/unknown",
    "dependent_claim_support": "strong/moderate/weak/none/unknown",
    "core_feature_covered": "clear/partial/weak/unknown",
    "claim_scope_limitation": "broad/moderate/narrow/overly_narrow/unknown",
    "design_around_difficulty": "hard/moderate/easy/unknown",
    "portfolio_connection": "strong/moderate/weak/unknown",
    "portfolio_coverage_extension": "strong/moderate/weak/none/unknown"
  },
  "subscores": {
    "right_stability": {
      "label": "권리안정성",
      "score": 0,
      "max_score": 40,
      "rationale": "..."
    },
    "claim_protection": {
      "label": "권리보호력",
      "score": 0,
      "max_score": 40,
      "rationale": "..."
    },
    "portfolio_defensive_value": {
      "label": "포트폴리오·방어가치",
      "score": 0,
      "max_score": 20,
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
