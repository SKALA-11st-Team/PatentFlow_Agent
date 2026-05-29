# Patent Agent Valuation

사용자가 선택한 특허를 기반으로 특허 요약, 외부 근거 수집, 산업 RAG 검색, 가치평가, 검증, 최종 보고서를 생성하는 Agent 오케스트레이션 프로젝트입니다.


## 실행 방식

- `app.api`: FastAPI 서버용입니다. Spring Boot나 브라우저가 HTTP로 Agent를 호출할 때 사용합니다.
- `app.main`: CLI 워크플로우 실행용입니다. 터미널에서 특허 ID를 넣고 AI 평가 workflow를 직접 돌릴 때 사용합니다.

두 실행 방식 모두 같은 가상환경(`venv`)과 같은 환경변수를 사용합니다. `app.api`를 띄운다고 `app.main`이 같이 실행되는 것은 아니며, `app.main`은 별도 명령으로 한 번 실행하고 종료되는 CLI입니다.

## 1. 공통 로컬 환경 설정
`PatentFlow_Agent` 폴더에서 아래 명령을 실행합니다.

### Python 가상환경
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### DB 실행
pgvector/RAG 기능을 테스트할 때만 DB가 필요합니다. `/health`, `/docs`, 기본 평가 API 확인만 할 때는 DB 없이도 서버 기동을 확인할 수 있습니다.

```bash
docker compose up -d postgres
```

### 환경변수
로컬 Python 실행에서는 DB와 BE 주소를 `localhost` 기준으로 둡니다.

```bash
cp .env.example .env
```

복사한 `.env`에서 필요한 API key를 채웁니다. 기본 로컬 DB/BE 주소는 아래 값입니다.

```bash
PGVECTOR_DATABASE_URL=postgresql://patentflow:patentflow@localhost:5432/patentflow
UNIFIED_API_BASE_URL=http://localhost:8080
```

`docker compose`로 Agent를 실행할 때는 compose가 컨테이너 내부 주소(`postgres`, `patentflow-api`)를 주입합니다. 로컬에서 Python으로 직접 실행할 때는 `.env`의 `localhost` 값을 사용합니다.

## 2. FastAPI 서버 실행: `app.api`
Spring Boot가 Agent를 HTTP로 호출하거나 Swagger에서 API를 확인할 때 사용하는 실행 방식입니다.

```bash
venv/bin/uvicorn app.api:app --reload --port 8000
```

실행 후 다음 주소로 접속 가능합니다.

- API 서버: http://localhost:8000
- Swagger Docs: http://localhost:8000/docs
- Health Check: http://localhost:8000/health

현재 기본 평가 API는 BE 연동 확인용 응답을 반환합니다. 실제 workflow 연결 시 이 엔드포인트에서 `run_workflow(...)`를 호출하도록 확장합니다.

```bash
curl -X POST http://localhost:8000/api/v1/ai/patents/PAT-TEST/evaluate \
  -H "Content-Type: application/json" \
  -d '{"managementNumber":"P202405001-KR0","title":"테스트 특허"}'
```

## 3. CLI workflow 실행: `app.main`
기존 Agent workflow를 터미널에서 직접 실행하는 방식입니다. 서버처럼 계속 떠 있지 않고, 실행 후 종료됩니다.

```bash
venv/bin/python -m app.main --patent-id 1
```

관리번호로도 실행할 수 있습니다.

```bash
venv/bin/python -m app.main P202405001-KR0
```

전처리에서는 KIPRIS API 값을 우선 사용하고, PDF markdown은 명세서 본문 섹션 보강에 사용합니다.

## External API Evidence

Unified API 서버를 먼저 실행합니다.

```bash
venv/bin/python -m uvicorn open_api.api_server:app --reload --port 8080
```

워크플로우 내부에서 Query Rewriting, Naver/GNews/KIPRIS evidence 수집, 뉴스 필터링, 산업 RAG, evidence compression이 순서대로 실행됩니다.

```bash
venv/bin/python -m app.main --patent-id 1
```

`--collect-api-evidence`는 이전 CLI 호환용 옵션입니다. 현재 workflow evidence가 이미 생성된 경우 중복 수집을 건너뜁니다.

DART 공시까지 별도로 수집하려면 `corp_code`를 추가합니다.

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

동일 제품군 sibling 특허는 PDF 파싱 없이 KIPRIS API만으로 보강해 별도 portfolio evidence로 저장합니다.

```text
artifacts/runs/<run_id>/portfolio_evidence/
└── <patent_id>_portfolio_evidence.json
```

LLM으로 압축한 valuation 입력용 evidence 저장 위치:

```text
artifacts/runs/<run_id>/compressed_evidence/
└── <patent_id>_compressed_evidence.json
```

가치평가 Agent별 입력 확인용 JSON 저장 위치:

```text
artifacts/runs/<run_id>/valuation_inputs/
├── legal_input.json
├── technology_input.json
├── market_input.json
├── business_fit_input.json
└── final_report_input.json
```

요약 보고서와 최종 가치평가 보고서는 Markdown으로도 저장됩니다.

```text
artifacts/runs/<run_id>/summary/
└── <patent_id>_summary.md

artifacts/runs/<run_id>/final/
└── <patent_id>_final_report.md
```

요약 보고서, query rewriting, portfolio sibling 요약, 가치평가 4개 축, 최종 가치평가 보고서는 LLM 응답이 필수입니다. LLM 호출이 실패하거나 관련 LLM 옵션이 비활성화되면 deterministic fallback 결과를 생성하지 않고 실행을 중단합니다.

## Query Rewriting Output

Query Rewriting은 `prompts/evidence/query_rewriting.md`를 사용하며, 출력 형식은 아래와 같습니다.

```json
{
  "ko": ["..."],
  "en": ["..."]
}
```

- `ko`: Naver News 검색어 리스트
- `en`: GNews 검색어 리스트 (영어만 사용)
- 각 리스트는 최대 3개를 사용합니다.
- 관련제품, 권리자, 공동출원인 정보가 있으면 검색어 후보에 반영합니다.
- 이전 검색어는 `previous_queries`로 전달되어 supervisor 재검색 loop에서 중복 생성을 줄입니다.

워크플로우 state에는 아래처럼 저장됩니다.

```text
query_plan.ko_queries
query_plan.en_queries
query_plan.rewrite_meta
```

## Batch Report Generation

출원국이 KR인 특허 중 랜덤으로 성공한 20건의 요약/가치평가 Markdown을 별도 batch 폴더에 모을 수 있습니다.

```bash
venv/bin/python scripts/generate_kr20_reports.py --count 20
```

파일명 규칙:

```text
{순번}_{관리번호}_{등록번호}_{리포트종류}.md
```

예:

```text
01_P201510001-KR0_KR10-1795556_summary.md
01_P201510001-KR0_KR10-1795556_final_report.md
```

Batch 산출물은 아래에 저장되며, `artifacts/` 전체는 `.gitignore` 대상입니다.

```text
artifacts/batches/<batch_name>/
├── summary_reports/
├── valuation_reports/
├── logs/
├── manifest.json
└── manifest.txt
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

산업 리포트 RAG 저장소는 PostgreSQL pgvector를 사용합니다. 로컬 통합 DB를 사용할 때는 다음 URL을 사용합니다.

```bash
export PGVECTOR_DATABASE_URL="postgresql://patentflow:patentflow@localhost:5432/patentflow"
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
venv/bin/python -m app.main P202405001-KR0
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
