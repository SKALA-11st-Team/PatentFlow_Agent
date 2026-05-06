# Architecture

## Purpose

이 프로젝트는 사용자가 선택한 특허를 기준으로 특허 원문, KIPRIS API, Web/API 검색 근거, 산업 리포트 RAG 근거를 결합해 사업부서용 요약 보고서와 특허 가치평가 보고서를 생성한다.

핵심 원칙은 단순하다.

- 확정적으로 처리할 수 있는 수집, 파싱, 전처리, 병합, 검증은 Node 또는 Service에서 처리한다.
- 자연어 판단이 필요한 요약, 검색계획, 가치평가, 의미 검증은 Agent 또는 LLM 기반 Node로 처리한다.
- Supervisor는 하나만 두고, 현재 단계에 맞는 검증 기준을 선택해 다음 액션을 결정한다.

## Folder Structure

```text
final_v1/
├── AGENTS.md
├── ARCHITECTURE.md
├── README.md
├── requirements.txt
├── .env.example
├── app/
├── workflow/
├── agents/
├── services/
├── rag/
├── schemas/
├── prompts/
├── open_api/
├── data/
├── artifacts/
└── tests/
```

## Runtime Flow

현재 구현은 Agent 전체 그래프의 앞부분을 먼저 안정화하는 단계다.

```text
사용자 특허 선택
→ patent_resolve_node
→ patent_fetch_node
→ common_preprocess_node
→ supervisor_node
```

목표 전체 흐름은 다음과 같다.

```text
특허 선택
→ 특허 식별
→ 특허 데이터 수집
→ 공통 전처리
→ 요약 Agent
→ 검색 계획 Node
→ 검색어 재작성 Node
→ Web/API 검색 Node
→ 산업 리포트 RAG 검색 Node
→ 근거 병합 Node
→ Supervisor 검증
→ 가치평가 Agent
→ Validation Node
→ 최종 병합 Node
→ 최종 보고서
```

## Responsibilities

### app/

Application entrypoints and runtime configuration.

- `main.py`: CLI에서 특허 ID, KIPRIS API 수집, PDF 수집 옵션을 받아 워크플로우를 실행한다.
- `config.py`: `.env`와 기본 경로를 로드한다. OpenAI 임베딩 모델 기본값은 `text-embedding-3-small`이다.

### workflow/

Agent orchestration layer.

- `state.py`: 모든 Node와 Agent가 공유하는 `PatentWorkflowState`를 정의한다.
- `graph.py`: 현재 실행 가능한 워크플로우 순서를 정의한다. 향후 LangGraph 기반 분기 그래프로 확장한다.
- `nodes.py`: 특허 식별, 특허 수집, 공통 전처리, 최종 병합처럼 결정적 단계의 wrapper를 둔다.
- `supervisor.py`: 단계별 rule-based 검증을 먼저 수행하고, 필요하면 LLM Supervisor 판단으로 확장한다.

`PatentWorkflowState`의 주요 필드는 다음과 같다.

- `user_input`: 사용자가 선택한 특허 ID, 출원번호, PDF/API 수집 옵션
- `patent_structured`: DB/API/PDF/전처리 결과가 합쳐진 특허 구조체
- `kipris_api_data`: KIPRIS API에서 받은 서지, 초록, 청구항 정보
- `parsed_pdf`: KIPRIS PDF 다운로드 및 OpenDataLoader 파싱 결과
- `preprocessed_patent`: Agent 입력용으로 정제/구조화된 특허 객체
- `summary_result`: 요약 Agent 결과
- `query_plan`, `search_queries`: 검색 계획 및 검색어
- `evidence_bundle`: Web/API/RAG 근거가 병합된 목록
- `valuation_result`: 가치평가 Agent 결과
- `validation_result`: 검증 결과
- `supervisor_decision`: Supervisor의 통과 여부와 다음 액션
- `final_report`: 최종 병합 결과

### agents/

LLM-based reasoning modules.

- `summary.py`: 비전문가와 사업부서가 이해할 수 있는 특허 요약을 생성한다.
- `valuation.py`: 권리성, 기술성, 시장성, 라이프사이클 경제성, 전략 적합성 평가를 수행한다.

앞으로 Agent 개발 시 각 Agent는 전체 state를 통째로 문자열로 받지 않는다. `preprocessed_patent["agent_inputs"]`, `evidence_bundle`, 필요한 metadata만 adapter/render 단계에서 프롬프트로 변환해 호출한다.

### services/

External integrations and reusable domain services.

- `patent/kipris_patent_service.py`: 로컬 `data/patents.sqlite3`에서 특허를 선택하고, KIPRIS API/PDF 데이터를 수집한다.
- `patent/markdown_preprocess_service.py`: OpenDataLoader markdown과 KIPRIS API 결과를 Agent 입력용 구조화 객체로 만든다.
- `evidence/external_search_service.py`: query rewriting 결과를 받아 Naver/GNews/DART/KIPRIS 검색을 실행하고 evidence로 병합한다.
- `evidence/api_normalizers.py`: Naver News, GNews, DART, KIPRIS 검색 결과처럼 서로 다른 API 응답을 공통 evidence shape로 변환한다.
- `evidence/news_article_extraction_service.py`: 뉴스 URL 본문을 가져와 snippet evidence를 가능한 경우 full text evidence로 보강한다.
- `evidence/news_filter_service.py`: 뉴스 evidence를 관련성, 최신성, 길이 기준으로 LLM 처리 전에 rule-based 필터링한다.
- `evidence/store_service.py`: evidence_id 부여, 중복 제거, API별 JSON 저장, filtered evidence 저장을 담당한다.
- `rag/industry_rag_service.py`: 산업 리포트 pgvector VectorDB 검색 결과를 workflow에서 쓰기 쉬운 evidence 형태로 변환한다.
- `llm/client_service.py`: LLM 호출 wrapper를 담당한다.
- `llm/prompt_service.py`: prompt markdown 로딩을 담당한다.
- `observability/langsmith_service.py`: 워크플로우, Node, Agent tracing decorator를 제공한다.

### rag/

Industry report RAG pipeline.

- `industry_report_chunker.py`: `data/industry_reports/`의 KIET 산업 리포트 PDF를 `pdfplumber`로 읽고, 표 영역과 노이즈를 제거한 뒤 산업별 chunk JSONL을 만든다.
- `industry_vector_store.py`: `industry_report_chunks.jsonl`을 OpenAI embedding으로 임베딩하고 pgvector 테이블에 저장/검색한다.

현재 저장물은 다음과 같다.

- `data/vector_db/industry_report_chunks.jsonl`: chunk 원본
- PostgreSQL `industry_report_chunks` table: chunk text, JSONB metadata, embedding vector

산업 리포트 chunk metadata는 최소한 다음 필드를 가진다.

```json
{
  "source_type": "industry_report",
  "source_name": "KIET_...pdf",
  "published_year": 2026,
  "industry": "조선",
  "chunk_id": "KIET_..._조선_p33_001",
  "heading": "2026년 전망",
  "page": 33
}
```

`industry`는 chunk 내용에서 추론하지 않고, 보고서의 장 제목과 page range를 기준으로 부여한다. 예를 들어 `제3장 일반기계산업`이 시작되면 다음 장 전까지 해당 page range의 chunk metadata는 `일반기계`로 기록한다.

### open_api/

External API clients and specs.

- `kipris_client.py`: KIPRIS API 호출 client.
- `*_open_api.yaml`: KIPRIS, Naver, GNews, DART 등 API 명세/참고 파일.

### schemas/

Typed data models shared across workflow, agents, and services.

- `patent.py`: 특허 입력/구조화 모델
- `evidence.py`: 검색/RAG 근거 모델
- `valuation.py`: 가치평가 결과 모델
- `supervisor.py`: Supervisor decision 모델
- `report.py`: 최종 보고서 모델

### prompts/

Prompt templates used by agents and LLM-based nodes.

Supervisor는 하나지만, 단계별 판단 기준이 다르므로 prompt는 분리한다.

- `supervisor_patent_check.md`: 특허 수집/전처리 결과 검증
- `supervisor_summary_check.md`: 요약 결과 검증
- `supervisor_evidence_check.md`: 검색/RAG 근거 충분성 검증
- `supervisor_valuation_check.md`: 가치평가 결과 검증
- `supervisor_final_check.md`: 최종 병합 전 검증

Rule-based check를 먼저 실행하고, 의미 판단이 필요한 부분만 LLM prompt로 넘긴다.

### data/

Local source data and Vector DB inputs.

- `patents.sqlite3`: 사용자가 선택할 수 있는 특허 metadata DB
- `patent_pdf/`: KIPRIS에서 다운로드한 특허 PDF
- `industry_reports/`: KIET 등 산업 리포트 PDF 원본
- `vector_db/`: 산업 리포트 chunk JSONL

### artifacts/

Workflow run artifacts.

```text
artifacts/runs/<run_id>/
├── patent_markdown/
├── preprocessed_patents/
├── api_evidence/
├── filtered_evidence/
├── industry_rag/
├── compressed_evidence/
└── final/
```

- `patent_markdown/`: OpenDataLoader로 변환한 특허 markdown
- `preprocessed_patents/`: Agent 입력용 특허 JSON
- `api_evidence/`: API별 정규화 evidence JSON
- `filtered_evidence/`: rule-based 필터를 통과한 뉴스와 RAG evidence JSON
- `industry_rag/`: pgvector 검색 결과 JSON
- `compressed_evidence/`: valuation 입력용 LLM 압축 evidence JSON
- `final/`: 최종 보고서 JSON

### tests/

Focused regression tests.

- `test_graph.py`: 현재 워크플로우 실행 경로
- `test_nodes.py`: 주요 Node 동작
- `test_patent_markdown_preprocess.py`: 특허 markdown/API 전처리
- `test_industry_vector_store.py`: pgvector VectorDB build/search
- `test_valuation.py`: 가치평가 구조

## Patent Data Strategy

특허 데이터는 API를 우선 사용하고, PDF는 API에서 부족한 본문 정보를 보강하는 용도로 사용한다.

- 로컬 DB: 사용자가 선택할 특허 목록과 내부 관리 metadata 제공
- KIPRIS API: 서지, 출원인, 발명자, IPC, 초록, 청구항처럼 안정적으로 구조화된 정보 제공
- KIPRIS PDF + OpenDataLoader: 명세서, 배경기술, 해결하려는 과제, 효과, 구체적 실시내용 등 API보다 풍부한 본문 제공

따라서 `common_preprocess_node`는 API와 PDF가 모두 있으면 둘을 합쳐 `preprocessed_patent`를 만든다. Agent는 이 구조화 객체를 기준 입력으로 사용한다.

## Preprocessed Patent Shape

`preprocessed_patent`는 다음 구조를 목표로 한다.

```json
{
  "patent_id": "KR10-2932891",
  "source": {},
  "metadata": {},
  "sections": {},
  "claims": [],
  "claim_stats": {},
  "agent_inputs": {
    "summary": {},
    "valuation": {},
    "search_planning": {}
  },
  "validation": {}
}
```

`agent_inputs`는 긴 prompt 문자열을 미리 저장하지 않는다. 각 Agent 호출 직전에 adapter/render 함수가 dict를 prompt text로 변환한다.

## Evidence Strategy

모든 근거는 최종 보고서에서 추적 가능해야 한다.

외부 API는 응답 구조가 서로 다르기 때문에 raw response를 Agent에 바로 넘기지 않는다.

```text
API raw response
→ evidence_api_normalizers.py
→ 공통 evidence shape
→ artifacts/runs/<run_id>/api_evidence/에 source별 JSON 저장
→ rule-based news filter와 RAG score threshold 적용
→ LLM evidence compression 단계에서 valuation 입력용 key facts로 압축
→ Agent 입력
```

근거 객체는 최소한 다음 필드를 가진다.

```json
{
  "evidence_id": "industry_001",
  "source": "KIET_...pdf",
  "source_type": "industry_report",
  "industry": "조선",
  "page": 33,
  "heading": "2026년 전망",
  "published_at": "2026",
  "collected_at": "2026-05-05T10:30:00+09:00",
  "content": "...",
  "confidence": null,
  "score": 0.82,
  "metadata": {}
}
```

`related_axis`는 검색 API 수집 단계에서 채우지 않는다. 어떤 근거가 권리성, 기술성, 시장성, 전략성 중 어디에 연결되는지는 valuation 단계에서 evidence 내용을 보고 판단한다.

날짜는 두 종류를 구분한다.

- `published_at`: 뉴스 발행일, 공시일, 특허 공개/출원일, 리포트 발행연도처럼 source 자체의 생성 날짜
- `collected_at`: 우리 시스템이 API 또는 RAG에서 근거를 수집한 날짜

`published_at`이 없는 일반 웹페이지는 `null`로 둔다. 대신 `collected_at`은 반드시 기록한다. Supervisor는 `published_at`이 없거나 오래된 근거를 최신성 판단에서 감점하거나 추가 검색 대상으로 삼을 수 있다.

API별 특수 필드는 공통 필드로 억지로 끌어올리지 않고 `metadata`에 보관한다.

Web/API 검색 근거와 산업 RAG 근거는 `EvidenceMerge` 단계에서 하나의 `evidence_bundle`로 합쳐지고, 가치평가 Agent는 평가축별로 `evidence_ids`를 연결해야 한다.

## Search And Competitor Strategy

검색어 재작성 Agent와 경쟁사 후보 추론 Agent는 아직 확정하지 않는다. 대신 downstream 구조가 흔들리지 않도록 아래 interface를 먼저 고정한다.

검색어는 QueryRewriting Agent가 `prompts/query_rewriting.md`를 기반으로 생성한다.

출력 구조는 다음과 같다.

```json
{
  "ko": ["..."],
  "en": ["..."]
}
```

`ko`는 Naver News, `en`은 GNews 검색에 사용한다.

경쟁사는 처음부터 확정값으로 다루지 않는다. 초기에는 `competitor_candidates`로만 수집한다.

```json
{
  "company_name": "예시회사",
  "reason": "동일 시장에서 유사 문제를 해결하는 제품 근거 발견",
  "source_evidence_ids": ["news_001", "web_003"],
  "confidence": 0.62,
  "status": "candidate"
}
```

경쟁사 후보의 기준은 “동일/인접 기술을 제품화했거나 동일 시장에서 유사 문제를 해결하는 기업”이다. 후보 확정 여부는 근거 병합 후 Supervisor 또는 별도 competitor inference Agent가 판단한다.

## Supervisor Strategy

Supervisor Agent는 하나만 둔다. 다만 검증 시점마다 보는 기준이 다르므로 `current_stage`에 따라 다른 check를 사용한다.

```text
patent_check
summary_check
evidence_check
valuation_check
final_check
```

검증 원칙:

- 필수 필드 누락, 존재하지 않는 `evidence_id`, 청구항 누락 같은 확정적 문제는 rule-based로 실패 처리한다.
- 근거의 의미적 충분성, 점수와 rationale의 일관성, 사업부서용 요약 품질 같은 판단은 LLM Supervisor prompt로 검토한다.
- Supervisor는 결과를 직접 고치지 않고 `next_action`을 결정한다.

## Implementation Roadmap

다음 Agent 개발 순서는 아래가 적절하다.

1. `summary.py`: `preprocessed_patent["agent_inputs"]["summary"]` 기반 요약 생성
2. `search_planning` Node: 특허 metadata, sections, claims 기반 검색 계획 생성
3. `query_rewriting` Node: Web/API 검색어 생성
4. `industry_rag_query` Node: pgvector VectorDB에서 산업 근거 검색
5. `evidence_merge` Node: Web/API/RAG 근거 통합 및 `evidence_id` 부여
6. `valuation.py`: 5개 평가축별 점수, 근거, 리스크, 추천 생성
7. `validation` Node: valuation 결과와 evidence 연결 검증
8. `final_merge_node`: 요약과 검증된 가치평가를 최종 보고서로 병합

## Run Commands

특허 워크플로우 실행:

```bash
python -m app.main --patent-id 1 --collect-kipris-api
```

KIPRIS PDF까지 수집:

```bash
python -m app.main --patent-id 1 --collect-kipris-api --collect-pdf
```

산업 리포트 chunk 생성:

```bash
python -m rag.industry_report_chunker
```

산업 리포트 pgvector table 생성:

```bash
export PGVECTOR_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/patent_rag"
python -m rag.industry_vector_store
```

pgvector 검색 테스트:

```bash
python -m rag.industry_vector_store --query "조선 LNG 운반선 수요 전망" --industry 조선 --top-k 3
```

테스트:

```bash
PYTHONPATH=. pytest tests
```
