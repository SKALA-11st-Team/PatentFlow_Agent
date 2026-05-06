# Patent Agent Valuation

사용자가 선택한 특허를 기반으로 특허 요약, 외부 근거 수집, 산업 RAG 검색, 가치평가, 검증, 최종 보고서를 생성하는 Agent 오케스트레이션 프로젝트입니다.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
venv/bin/python -m app.main --patent-id 1
```


전처리에서는 KIPRIS API 값을 우선 사용하고, PDF markdown은 명세서 본문 섹션 보강에 사용합니다.

## External API Evidence Save

Unified API 서버를 먼저 실행합니다.

```bash
venv/bin/python -m uvicorn open_api.api_server:app --reload
```

그 다음 워크플로우를 실행하면서 API evidence 저장을 켭니다.

```bash
venv/bin/python -m app.main --patent-id 1 --collect-api-evidence
```

DART 공시까지 같이 수집하려면 `corp_code`를 추가합니다.

```bash
venv/bin/python -m app.main --patent-id 1 --collect-api-evidence --dart-corp-code 00126380
```

API evidence 저장 위치:

```text
artifacts/runs/<run_id>/api_evidence/
├── news/
├── company_disclosure/
└── competitor_patent/
```

뉴스는 raw API 결과를 위 경로에 그대로 저장한 뒤, LLM/가치평가 전에 룰 기반 필터를 통과한 결과를 별도로 저장합니다.

```text
artifacts/runs/<run_id>/filtered_evidence/news/
└── <patent_id>_filtered_news.json
```

가치평가에 들어가는 선별 완료 evidence는 필터 통과 뉴스와 산업 RAG 청크를 함께 저장합니다.

```text
artifacts/runs/<run_id>/filtered_evidence/
└── <patent_id>_filtered_evidence.json
```

LLM으로 압축한 valuation 입력용 evidence 저장 위치:

```text
artifacts/runs/<run_id>/compressed_evidence/
└── <patent_id>_compressed_evidence.json
```

## Query Rewriting Output

Query Rewriting은 `prompts/query_rewriting.md`를 사용하며, 출력 형식은 아래와 같습니다.

```json
{
  "ko": ["..."],
  "en": ["..."]
}
```

- `ko`: Naver News 검색어 리스트
- `en`: GNews 검색어 리스트 (영어만 사용)
- 각 리스트는 최대 3개를 사용합니다.

워크플로우 state에는 아래처럼 저장됩니다.

```text
query_plan.ko_queries
query_plan.en_queries
query_plan.rewrite_meta
```

## Service Structure

`services/`는 역할별 하위 패키지로 나눕니다.

```text
services/
├── evidence/       # 외부 API evidence 수집, 정규화, 저장, 뉴스 필터링
├── patent/         # KIPRIS/PDF 수집과 특허 markdown 전처리
├── rag/            # 산업 리포트 pgvector 검색 orchestration
├── llm/            # LLM 호출과 prompt 로딩
└── observability/  # LangSmith tracing
```

## Industry Report Chunking

산업 리포트 PDF를 markdown으로 변환한 뒤, 소제목 기준으로 먼저 나누고 긴 섹션만 token 기준으로 분할합니다.

```bash
venv/bin/python -m rag.industry_report_chunker
```

chunk 원본은 실행 산출물이 아니라 Vector DB 입력 자산이므로 아래 JSONL에 저장됩니다.

```text
data/vector_db/industry_report_chunks.jsonl
```

산업 리포트 RAG 저장소는 PostgreSQL pgvector를 사용합니다.

```bash
export PGVECTOR_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/patent_rag"
venv/bin/python -m rag.industry_vector_store
```

검색 확인:

```bash
venv/bin/python -m rag.industry_vector_store --query "조선 LNG 운반선 수요 전망" --industry 조선 --top-k 3
```

전처리 결과는 기본적으로 아래 위치에 저장됩니다.

```text
artifacts/runs/<run_id>/preprocessed_patents/
└── KR10-2932891.json
```

정제 markdown 디버그 파일까지 저장하려면:

```bash
venv/bin/python -m app.main --patent-id 1 --collect-pdf --save-cleaned-markdown
```

다른 식별자로도 실행할 수 있습니다.

```bash
venv/bin/python -m app.main --application-number 10-2024-0115774
venv/bin/python -m app.main --registration-number 10-2932891
venv/bin/python -m app.main --management-number P202405001-KR0
```

## LangSmith

`.env`에 아래 값을 설정하면 workflow, node, agent 함수 실행이 LangSmith trace로 기록됩니다.

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your-langsmith-api-key>
LANGSMITH_PROJECT=patent-agent-valuation
```

## Key Documents

- `AGENTS.md`: Agent/Node 설계 원칙과 검증 규칙
- `ARCHITECTURE.md`: 폴더 구조와 모듈 책임
