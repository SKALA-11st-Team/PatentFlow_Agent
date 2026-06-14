# PatentFlow Agent Workflow Collaboration Guide

이 문서는 PatentFlow Agent 코드를 여러 명이 나누어 구현할 때의 작업 경계와, 현재 워크플로우 실행 흐름을 한눈에 보기 위한 운영 가이드입니다.

## 0. 로컬 실행 전 서버 켜기

PatentFlow Agent를 Swagger 또는 CLI로 전체 실행하려면 보통 아래 3가지를 준비합니다.

### 1단계: pgvector DB 켜기

산업보고서 RAG 검색을 쓰려면 Postgres/pgvector가 떠 있어야 합니다.

```bash
docker compose up -d postgres
```

컨테이너 상태 확인:

```bash
docker compose ps
```

`.env`의 DB URL은 로컬 실행 기준으로 보통 아래 값을 사용합니다.

```env
PGVECTOR_DATABASE_URL=postgresql://patentflow:patentflow@localhost:5432/patentflow
```

### 2단계: Open API proxy/mock 서버 켜기

뉴스, GNews, KIPRIS proxy 등 `open_api` 쪽 엔드포인트를 쓰려면 별도 서버를 켭니다.

```bash
venv/bin/python -m uvicorn open_api.api_server:app --reload --port 8080
```

Agent의 `.env`는 이 서버를 보도록 맞춥니다.

```env
UNIFIED_API_BASE_URL=http://localhost:8080
```

이 서버가 꺼져 있으면 workflow 중 뉴스 검색에서 `/api/news/search`, `/api/v4/search` 호출이 실패하거나 404/connection error가 날 수 있습니다.

### 3단계: Agent API 서버 켜기

Swagger로 보고서를 생성하려면 Agent API 서버를 켭니다.

```bash
venv/bin/uvicorn app.api:app --reload --port 8000
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

CLI만 실행할 때는 Agent API 서버가 꼭 필요하지 않습니다. CLI는 아래처럼 바로 실행할 수 있습니다.

```bash
venv/bin/python -m app.main P202405001-KR0
```

정리하면:

| 목적 | 필요한 것 |
| --- | --- |
| CLI로 전체 workflow 실행 | pgvector DB, open_api 서버 |
| Swagger에서 평가 실행 | pgvector DB, open_api 서버, Agent API 서버 |
| RAG 없이 일부 코드 테스트 | 서버 없이 pytest 가능 |

## 1. 3명 협업 구조 제안

현재 가치평가는 4개 축으로 나뉩니다.

- `legal`: 권리성
- `technology`: 기술성
- `market`: 시장성
- `business_fit`: 사업 연계성

3명이 나눠 작업한다면 아래처럼 나누는 것이 충돌이 가장 적습니다.

| 담당 | 주 책임 | 주 수정 파일 | 보조 확인 파일 |
| --- | --- | --- | --- |
| Agent A | 권리성 | `agents/valuation_axes/legal.py`, `prompts/valuation/valuation_legal.md` | `tests/test_valuation.py` |
| Agent B | 기술성 | `agents/valuation_axes/technology.py`, `prompts/valuation/valuation_technology.md` | `tests/test_valuation.py` |
| Agent C | 시장성 + 사업 연계성 | `agents/valuation_axes/market.py`, `agents/valuation_axes/business_fit.py`, `prompts/valuation/valuation_market.md`, `prompts/valuation/valuation_business_fit.md` | `prompts/evidence/query_rewriting.md`, `services/evidence/*`, `tests/test_evidence_service.py` |
| Agent D | 최종 가치평가 보고서 | `agents/writing/final_report.py`, `prompts/writing/final_report.md` | `workflow/graph.py`, `tests/test_valuation.py`, `tests/test_graph.py` |

`agents/valuation.py`는 4개 평가축 실행 순서, 공통 input helper, LLM JSON 정규화, 축별 점수 종합만 담당합니다. 최종 Markdown 보고서는 Writing Team의 `agents/writing/final_report.py`가 담당합니다. 각 담당자는 자기 축의 `agents/valuation_axes/{axis}.py`와 해당 prompt를 먼저 수정하고, 공통 helper가 필요할 때만 한 명이 `agents/valuation.py`를 맡아 반영하는 방식이 좋습니다.

### 파일별 책임

| 파일 | 책임 | 보통 수정하는 사람 |
| --- | --- | --- |
| `agents/valuation.py` | 축 실행 순서, 공통 input payload, 공통 prompt builder, LLM JSON normalize, 점수/지표/권고 종합 | 공통 담당 1명 |
| `agents/valuation_axes/legal.py` | 권리성 축 실행 흐름과 권리성 근거 선택 | 권리성 담당 |
| `agents/valuation_axes/technology.py` | 기술성 축 실행 흐름과 기술성 근거 선택 | 기술성 담당 |
| `agents/valuation_axes/market.py` | 시장성 축 실행 흐름과 시장성 근거 선택 | 시장성 담당 |
| `agents/valuation_axes/business_fit.py` | 사업 연계성 축 실행 흐름, 회사/제품 키워드 기반 근거 선택 | 사업 연계성 담당 |
| `agents/valuation_axes/common.py` | 여러 축이 같이 쓰는 작은 helper | 공통 담당 또는 합의 후 수정 |
| `agents/writing/final_report.py` | 종합 가치평가 결과를 Markdown 보고서로 작성 | Writing 담당 |

`common.py`는 “축별 agent”가 아니라 중복 제거용 helper 파일입니다. 현재는 `source_type`/`related_axes` 기반 근거 선택 함수와 간단한 문자열 정규화 함수만 들어 있습니다. 특정 축에만 필요한 판단 로직은 `common.py`로 빼지 말고 각 축 파일에 두는 편이 협업할 때 더 안전합니다.

## 2. 축별 코드 구조

가치평가의 공통 조립 파일은 `agents/valuation.py`이고, 실제 축별 작업 파일은 `agents/valuation_axes/` 아래에 있습니다.

```text
workflow.graph의 valuation_axes_analyze
→ legal / technology / market / business_fit 축 노드 fan-out 실행
→ agents/valuation_axes/{axis}.py의 run()
  → select_evidence()
  → runtime.build_input_payload()
  → runtime.build_prompt()로 common_valuation.md + valuation_{axis}.md 로드
  → runtime.run_llm_required()로 LLM JSON 결과 normalize
→ valuation_axes_merge에서 축별 결과 fan-in
→ build_final_valuation_result
→ 점수/지표/권고 종합
```

`run_valuation_agent()`는 단독 호출용 호환 함수이고, 실제 workflow graph에서는 `valuation_axes_analyze`에서 4개 축을 분기한 뒤 `valuation_axes_merge`에서 합치는 fan-out/fan-in 구조를 사용합니다.

축별 입력 근거 선택은 각 축 파일의 `select_evidence()`에서 결정됩니다.

- 권리성: `agents/valuation_axes/legal.py`
- 기술성: `agents/valuation_axes/technology.py`
- 시장성: `agents/valuation_axes/market.py`
- 사업 연계성: `agents/valuation_axes/business_fit.py`

### 각 축 파일에서 보이는 실행 흐름

각 축 파일의 `run()`만 보면 해당 agent가 어떤 순서로 동작하는지 볼 수 있습니다.

```python
def run(state, runtime):
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = runtime.build_input_payload(axis=AXIS, state=state, evidence=evidence)
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    return runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
```

따라서 축 담당자가 주로 보는 것은 아래 3가지입니다.

- `AXIS`, `LABEL`, `PROMPT_PATH`: 축 이름, 화면/보고서 라벨, 사용할 md prompt 경로
- `run()`: input 생성, md prompt 로드, LLM 호출, output 정규화로 이어지는 실행 흐름
- `select_evidence()`: 해당 축에 넣을 근거를 고르는 기준

`runtime`은 `valuation.py`에서 넘겨주는 공통 기능 묶음입니다. 축 파일이 공통 기능을 직접 import하지 않아도 되도록 만들어 둔 연결부입니다.

### 왜 `business_fit.py`만 긴가

권리성/기술성/시장성은 대부분 `source_type`이나 `related_axes`만 보고 근거를 고를 수 있습니다.

예를 들어 시장성은 `news`, `industry_report`를 고르면 됩니다.

사업 연계성은 조금 다릅니다. 단순히 뉴스 전체를 넣으면 “시장 뉴스”와 “우리 회사/제품과 연결되는 뉴스”가 섞이기 쉽습니다. 그래서 `business_fit.py`는 아래 로직을 추가로 가집니다.

- 특허명, 제품명, 사업 분야, 기술 분야, 공동출원인, 출원인 이름을 keyword로 만든다.
- 뉴스 제목/요약/본문/context/key facts에 keyword가 들어가는지 본다.
- 회사/제품과 직접 연결되는 뉴스는 우선순위로 넣는다.
- `company_disclosure`, `portfolio_context`는 사업 연계성 보조 근거로 넣는다.

그래서 다른 축보다 길어 보이는 것이 정상입니다. 다만 사업 연계성 담당자가 판단 기준을 바꿔야 할 때는 `business_fit.py`만 고치면 됩니다.

축별 prompt를 수정할 때는 결과 JSON 형식을 유지해야 합니다.

```json
{
  "score": 70,
  "grade": "B",
  "rationale": "...",
  "evidence_ids": [],
  "risk_factors": [],
  "missing_information": [],
  "confidence": 0.7
}
```

즉 각 담당자는 자기 축에 필요한 입력을 `state`에서 골라 prompt input으로 구성하고, 출력은 위 JSON 계약을 유지하면 됩니다. 새로운 필드를 추가하고 싶다면 먼저 downstream에서 쓰는지 확인해야 합니다. 현재 최종 보고서와 supervisor는 최소한 `score`, `grade`, `rationale`, `evidence_ids`, `risk_factors`, `missing_information`, `confidence`를 기대합니다.

### State에서 입력을 고르는 방법

`PatentWorkflowState`는 단계별 산출물을 누적해서 들고 다니는 공유 상태입니다. 축별 agent가 주로 참고하는 값은 아래입니다.

| State 필드 | 내용 | 주로 쓰는 축 |
| --- | --- | --- |
| `patent_structured` | SQLite/KIPRIS 기반 특허 메타데이터 | 전체 |
| `kipris_api_data` | KIPRIS 서지, 청구항 통계, 패밀리 정보 | 권리성, 기술성 |
| `preprocessed_patent` | PDF/Markdown 전처리 결과, 초록/청구항/섹션 | 전체 |
| `summary_result` | 특허 요약 결과 | 전체 |
| `portfolio_evidence` | 같은 제품군/관리번호 기반 유사·보완 특허 근거 | 권리성, 기술성, 사업 연계성 |
| `evidence_bundle` | 뉴스, 산업보고서, 공시, 포트폴리오 등 압축 근거 | 전체 |
| `query_plan` | query rewriting, 검색, 필터링, compression 통계 | 디버깅, 근거 품질 확인 |
| `valuation_result` | 4개 평가축 결과, 종합 점수, Writing Team이 추가한 최종 보고서 | supervisor, final |

축별 입력은 `agents/valuation.py`의 `build_axis_input_payload()`에서 구성됩니다. 특정 축에만 더 필요한 정보가 있으면 이 함수에서 `axis == "legal"` 같은 조건으로 추가하는 방식이 안전합니다.

예시:

```python
full_claims = valuation_claims(state) if axis == "legal" else []
```

권리성처럼 전체 청구항이 필요한 축만 full claims를 받고, 다른 축은 대표 청구항만 받게 하는 식입니다.

### Output 계약

각 축의 output은 LLM이 JSON으로 반환하고, `normalize_axis_llm_result()`가 검증합니다.

| 필드 | 의미 |
| --- | --- |
| `score` | 0~100 점수 |
| `grade` | A/B/C 등 등급 |
| `rationale` | 평가 이유 |
| `evidence_ids` | 입력 evidence 중 실제 사용한 근거 ID |
| `risk_factors` | 리스크 |
| `missing_information` | 추가 확인 필요 정보 |
| `confidence` | 0~1 신뢰도 |

`evidence_ids`는 반드시 해당 축 input에 들어온 evidence의 `evidence_id` 중에서 골라야 합니다. 없는 ID를 쓰면 supervisor에서 문제가 됩니다.

최종 종합 결과는 `build_final_valuation_result()`가 4개 축 결과를 합쳐 만듭니다.

- `total_score`: 권리성·기술성·시장성 3개 핵심 축 합산 점수(사업 연계성은 합산 제외)
- `average_score`: 3개 핵심 축의 (가중) 평균 점수
- `recommendation`: AI 권고 라벨(유지 권고 / 포기 검토 / 추가 정보 필요)

최종 Markdown 보고서는 `agents/writing/final_report.py`가 `prompts/writing/final_report.md`를 사용해 생성하고, 기존 API/저장 로직과 호환되도록 `valuation_result["final_report_markdown"]`에 추가합니다.

## 3. 전체 워크플로우 흐름

현재 LangGraph 흐름은 `workflow/graph.py`에 정의되어 있습니다.

```mermaid
flowchart TD
    START --> TOP["top_supervisor"]
    TOP -->|"research_team"| COLLECT["patent_context_collect"]
    COLLECT --> PORTFOLIO["portfolio_sibling"]
    PORTFOLIO --> PRE["common_preprocess"]
    PRE --> RS["research_supervisor"]

    RS -->|"query_rewriting"| QR["query_rewriting"]
    QR --> SEARCH["evidence_search"]
    SEARCH --> COMPRESS["evidence_compression"]
    COMPRESS --> RS

    RS -->|"valuation_team"| VA["valuation_axes_analyze"]
    VA --> VL["valuation_legal"]
    VA --> VT["valuation_technology"]
    VA --> VM["valuation_market"]
    VA --> VB["valuation_business_fit"]
    VL --> VAM["valuation_axes_merge"]
    VT --> VAM
    VM --> VAM
    VB --> VAM
    VAM --> VS["valuation_supervisor"]
    VS -->|"research_team"| COLLECT
    VS -->|"valuation_team"| VA
    VS -->|"writing_team"| SUMMARY["summary"]

    SUMMARY --> REPORT["final_report"]
    REPORT --> VALIDATION["validation"]
    VALIDATION --> WS["writing_supervisor"]
    WS -->|"writing_team"| SUMMARY
    WS -->|"final_merge"| FINAL["final_merge"]
    FINAL --> END
```

### 주요 노드 역할

| 노드 | 역할 |
| --- | --- |
| `top_supervisor` | 현재 상태를 보고 Research / Valuation / Writing 팀으로 라우팅 |
| `patent_context_collect` | SQLite 특허 메타데이터, KIPRIS API, PDF/Markdown 수집 |
| `portfolio_sibling` | 같은 제품군/관리번호 기반 유사·보완 특허 근거 생성 |
| `common_preprocess` | PDF/KIPRIS 데이터를 공통 특허 구조로 전처리 |
| `summary` | Writing Team에서 특허 요약문 생성 |
| `query_rewriting` | 뉴스 검색용 `ko/en` 쿼리와 산업 RAG용 `industry_rag` 쿼리 생성 |
| `evidence_search` | Naver/GNews/industry RAG 검색 및 필터링 |
| `evidence_compression` | 뉴스/산업보고서 근거를 LLM으로 압축하고 관련성 낮은 근거 제거 |
| `valuation_axes_analyze` | 이전 평가/검증 상태를 초기화하고 4개 평가축 분석을 시작 |
| `valuation_legal` / `valuation_technology` / `valuation_market` / `valuation_business_fit` | 축별 가치평가 수행 |
| `valuation_axes_merge` | 축별 평가 결과를 종합해 총점, 평균, 최종 지표, 권고를 생성 |
| `valuation_supervisor` | 평가 결과 구조와 근거 연결을 검증 |
| `final_report` | Writing Team에서 가치평가 종합 결과를 최종 Markdown 보고서로 작성 |
| `validation` | 최종 보고 전 평가축 구조를 검증 |
| `writing_supervisor` | 요약문과 최종 보고서 Markdown 존재 여부 및 품질 검증 |
| `final_merge` | summary, valuation, evidence를 최종 state로 병합 |

## 4. 실행 방법과 LLM 옵션

기본 실행:

```bash
venv/bin/python -m app.main P202405001-KR0
```

관리번호 대신 다른 식별자도 가능합니다.

```bash
venv/bin/python -m app.main --patent-id 5
venv/bin/python -m app.main --application-number 10-2023-0089519
venv/bin/python -m app.main --registration-number 10-2881532
venv/bin/python -m app.main --management-number P202301007-KR0
```

Supervisor LLM judge만 끄기:

```bash
venv/bin/python -m app.main P202405001-KR0 --no-llm-supervisor
```

이 옵션은 supervisor의 LLM 품질 판단만 끕니다. rule-based supervisor routing은 계속 동작합니다.

주의: `--no-llm-summary`, `--no-llm-valuation`, `--no-llm-final-report`는 현재 deterministic fallback이 없어서 해당 단계에서 실패하도록 설계되어 있습니다. 디버깅 목적 외에는 기본값 그대로 두는 것이 좋습니다.

## 5. 루프와 재시도 제한

Evidence 검색 루프 제한:

- 설정 위치: `.env`, `.env.example`, `app/config.py`
- 변수: `MAX_EVIDENCE_SEARCH_ROUNDS`
- 현재 예시값: `4`

`research_supervisor`가 근거 부족으로 `query_rewriting`을 반복시키더라도, `retry_count >= MAX_EVIDENCE_SEARCH_ROUNDS`가 되면 valuation 단계로 넘어갑니다.

Supervisor retry 제한:

- 위치: `workflow/supervisor.py`
- `VALUATION_SUPERVISOR_RETRY_LIMIT`
- `WRITING_SUPERVISOR_RETRY_LIMIT`

Supervisor LLM이 valuation 또는 writing 재시도를 요구해도, 구조적으로 통과 가능한 결과가 있으면 설정된 횟수 이후 다음 단계로 진행합니다.

## 6. 뉴스 검색과 RAG 검색 설정

검색 관련 설정은 `.env` 또는 `.env.example`에서 조정합니다.

```env
SEARCH_QUERY_COUNT=4
INDUSTRY_RAG_QUERY_COUNT=1
INDUSTRY_RAG_TOP_K=3
MAX_EVIDENCE_SEARCH_ROUNDS=3
FETCH_NEWS_FULL_TEXT=true
```

### Query rewriting 결과

`query_rewriting` 노드는 LLM으로 아래 구조를 만듭니다.

```json
{
  "ko": ["Naver News용 한국어 검색어"],
  "en": ["GNews용 영어 검색어"],
  "industry_rag": ["산업보고서 벡터DB 검색어"]
}
```

### 뉴스 검색 개수

워크플로우에서는 `SEARCH_QUERY_COUNT`만큼 Naver/GNews 쿼리를 선택합니다.

- Naver News: 쿼리 1개당 `display=5`
- GNews: 쿼리 1개당 `max=5`

예를 들어 `SEARCH_QUERY_COUNT=4`이면 최대:

- Naver News 4개 쿼리 x 5건 = 20건
- GNews 4개 쿼리 x 5건 = 20건

이후 뉴스 필터와 compression을 거치며 최종 평가 입력에 들어가는 수는 줄어듭니다.

### 뉴스 필터 기준

뉴스 필터 위치:

- `services/evidence/news_filter_service.py`

현재 기준:

- 중복 뉴스 제거
- 발행일 없는 뉴스 제거
- 최근성 기준: `DEFAULT_MAX_AGE_DAYS = 365 * 5`
- 본문 길이 기준: `DEFAULT_MAX_CONTENT_CHARS = 5000`
- 5000자를 넘으면 탈락시키지 않고 앞 5000자만 사용
- 키워드 매칭은 기록용으로만 남기고 탈락 조건으로 쓰지 않음

### 산업보고서 RAG 검색

RAG 검색 위치:

- `services/rag/industry_rag_service.py`
- `rag/industry_vector_store.py`

현재 방식:

- `industry_rag` 쿼리로 pgvector 검색
- 쿼리당 `INDUSTRY_RAG_TOP_K`개 검색
- 검색 시 hard metadata filter는 기본으로 사용하지 않음
- embedding text에는 `industry`, `heading`, `text`가 함께 들어가므로 산업명은 유사도에 soft하게 반영됨

RAG 결과는 `evidence_search`에서 일단 evidence bundle에 들어간 뒤, `evidence_compression`에서 점수 기준으로 후보가 됩니다.

RAG compression threshold:

- 위치: `services/evidence/compression_service.py`
- 변수: `DEFAULT_RAG_SCORE_THRESHOLD = 0.5`
- `industry_report` 근거는 `score >= 0.5`일 때 compression 후보가 됩니다.

## 7. 새 크롤링/외부 수집을 붙이는 방법

누군가 새 크롤러나 외부 API 수집기를 붙여야 한다면, 바로 valuation prompt에 넣기보다 evidence pipeline에 맞춰 넣는 것이 좋습니다.

권장 흐름:

```text
외부 수집기
→ 공통 evidence dict로 normalize
→ evidence_bundle에 합류
→ 필요하면 filter/compression
→ valuation axis input에서 선택
→ final report에서 evidence_id로 인용
```

### 1단계: 수집기는 services 아래에 둡니다

예시 위치:

```text
services/evidence/new_source_service.py
```

수집기는 원천 API나 크롤링 결과를 가져오되, workflow가 이해할 수 있는 공통 evidence 형태로 변환해야 합니다.

최소 권장 필드:

```json
{
  "evidence_id": "new_source_001",
  "source_type": "news",
  "source": "new_source",
  "title": "근거 제목",
  "url": "https://...",
  "published_at": "2026-05-19",
  "collected_at": "2026-05-19T00:00:00+09:00",
  "content": "본문 또는 요약",
  "related_axes": ["market"]
}
```

`source_type`은 downstream 선택 로직에 중요합니다.

- 뉴스/기사: `news`
- 산업보고서 RAG: `industry_report`
- 기업 공시: `company_disclosure`
- 사내/특허 포트폴리오: `portfolio_context`
- 특허 API/청구항 계열: `patent_api`
- 경쟁 특허: `competitor_patent`

새 source_type을 만들 수도 있지만, 그 경우 사용하는 축 파일의 `select_evidence()`와 final report evidence reference 로직도 같이 수정해야 합니다. 가능하면 기존 source_type 중 하나에 매핑하는 것이 안전합니다.

### 2단계: workflow 노드에 연결합니다

외부 시장 근거라면 보통 `workflow/nodes.py`의 `evidence_search_node()`에 연결합니다.

```text
evidence_search_node
→ collect_external_evidence
→ filter_news_safely
→ search_industry_rag_safely
→ 새 수집기 호출 위치
→ evidence_items에 합치기
```

새 수집 결과가 기사라면 `filter_news_safely()`를 재사용할 수 있고, 긴 본문은 현재 기준상 5000자까지 잘라 사용됩니다. 기사 성격이 아니면 별도 filter를 만들거나 바로 compression 후보로 넘깁니다.

### 3단계: compression 대상인지 결정합니다

현재 compression 대상은 `services/evidence/compression_service.py`에서 정합니다.

- `news`: compression 후보
- `industry_report`: `score >= DEFAULT_RAG_SCORE_THRESHOLD`이면 후보
- 그 외 source_type: 기본적으로 제외

새 수집기가 `company_disclosure` 같은 타입이면 compression 대상에 넣을지 별도로 결정해야 합니다. 평가 prompt에 바로 넣고 싶다면 해당 축 파일의 `select_evidence()`에서 선택되도록 해야 하고, LLM 입력 길이를 고려해 요약 필드를 만들어야 합니다.

### 4단계: 축별 선택 로직을 확인합니다

예를 들어 새 크롤러가 시장 기사라면:

```text
source_type = "news"
related_axes = ["market"]
```

로 두면 시장성 평가에 들어가기 쉽습니다.

사업 연계성까지 쓰고 싶으면 회사명/제품명이 content나 title에 포함되도록 하거나, `related_axes`에 `business_fit`을 넣는 방식을 고려할 수 있습니다.

### 5단계: artifact를 저장합니다

디버깅을 위해 원천 수집 결과와 필터 결과를 저장하는 것이 좋습니다.

기존 저장 위치 예:

```text
artifacts/runs/{run_id}/api_evidence/
artifacts/runs/{run_id}/filtered_evidence/
artifacts/runs/{run_id}/compressed_evidence/
```

새 수집기도 같은 run directory 아래에 저장하면, 나중에 “검색은 됐는데 보고서에 왜 안 나왔는지” 추적하기 쉽습니다.

## 8. 산출물 위치

실행 결과는 기본적으로 아래에 저장됩니다.

```text
artifacts/runs/{timestamp}_{management_number}/
```

주요 산출물:

| 경로 | 내용 |
| --- | --- |
| `preprocessed_patents/*.json` | 전처리된 특허 구조 |
| `summary/*_summary.md` | 특허 요약문 |
| `api_evidence/news/*.json` | Naver/GNews 원천 검색 결과 |
| `filtered_evidence/news/*_filtered_news.json` | 뉴스 필터 결과 |
| `industry_rag/*_industry_rag_top3.json` | 산업보고서 RAG 검색 결과 |
| `compressed_evidence/*_compressed_evidence.json` | 평가에 들어가는 압축 근거 |
| `valuation_inputs/*_input.json` | 각 평가축 LLM 입력 |
| `final/*_final_report.md` | 최종 가치평가 보고서 |

보고서에 특정 뉴스가 안 쓰였는지 확인하려면 아래 순서로 보면 됩니다.

```text
api_evidence/news
→ filtered_evidence/news
→ compressed_evidence
→ valuation_inputs/market_input.json
→ final/*_final_report.md
```

## 9. 협업 시 충돌을 줄이는 규칙

1. 축별 담당자는 자기 prompt부터 수정합니다.
2. `agents/valuation.py`, `workflow/graph.py`, `workflow/supervisor.py`, `workflow/state.py`는 공용 파일로 보고 한 명만 수정합니다.
3. prompt를 바꾸면 관련 테스트를 최소 1개 추가하거나 기존 테스트를 업데이트합니다.
4. artifact 파일은 결과 확인용이며, 일반적으로 커밋하지 않습니다.
5. 실행 전후로 아래 명령을 사용합니다.

```bash
git status --short
venv/bin/python -m pytest -v
```

## 10. 팀원에게 설명할 때 쓰는 짧은 버전

PatentFlow Agent는 먼저 특허 정보를 가져오고, 특허 내용을 요약한 뒤, 뉴스와 산업보고서 RAG로 외부 근거를 모읍니다. Research supervisor가 근거가 충분하다고 판단하면 Valuation team으로 넘기고, Valuation team은 권리성·기술성·시장성·사업 연계성 4개 축을 각각 LLM으로 평가합니다. 이후 Valuation supervisor와 Writing supervisor가 구조와 문서 품질을 확인하고, 최종적으로 요약문과 가치평가 보고서를 저장합니다.

3명이 나눠 개발할 때는 권리성, 기술성, 시장성/사업연계성 단위로 prompt와 테스트를 나누고, 공용 orchestration 파일은 한 명이 관리하는 방식이 가장 안전합니다.
