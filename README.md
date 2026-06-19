# Patent Agent Valuation

사용자가 선택한 특허를 기반으로 특허 요약, 외부 근거 수집, 산업 RAG 검색, 가치평가, 검증, 최종 보고서를 생성하는 Agent 오케스트레이션 프로젝트입니다. PatentFlow 백엔드가 AI 평가 레포트를 생성할 때 호출하는 AI 서비스입니다.

## 전체 흐름

```text
특허 식별 → 요약 → 외부 근거 수집 → 산업 RAG → 4축 가치평가 → 검증 → 최종 보고서
```

가치평가 4축은 **권리성 · 기술성 · 시장성 · 사업 연계성**입니다. 요약·query rewriting·4축 평가·최종 보고서는 LLM 응답이 필수이며, LLM 호출이 실패하거나 관련 옵션이 비활성화되면 deterministic fallback을 만들지 않고 실행을 중단합니다.

## 시스템 속 위치

- **BE → Agent**: Spring Boot 백엔드가 `app.api`(FastAPI)를 HTTP로 호출해 평가 레포트를 생성합니다. FE는 정상 흐름에서 Agent를 직접 호출하지 않습니다.
- 응답의 `degraded`·`failureReason`·`warnings`·`evidenceConfidence`는 **조용한 실패를 숨기지 않기 위한** 운영/화면 표시용 계약 신호입니다. `degraded=true`이면 근거 부족·외부 수집 실패·low confidence 등으로 제한된 평가입니다.

## 빠른 시작

`PatentFlow_Agent` 폴더에서 실행합니다.

```bash
# 1. Python 가상환경
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 환경변수
cp .env.example .env          # 필요한 API key 채우기
```

```bash
# 로컬 Python 실행 기본 주소
PGVECTOR_DATABASE_URL=postgresql://patentflow:patentflow@localhost:5432/patentflow
UNIFIED_API_BASE_URL=http://localhost:8080
```

> ⚠️ **`UNIFIED_API_BASE_URL` 주의**: 이 값은 외부 근거 수집용 **Unified API 게이트웨이** 주소입니다. 로컬에서 BE API 포트(`8080`)와 우연히 같을 수 있으니, BE 주소로 잘못 가리키지 않도록 주의하세요. 게이트웨이가 아닌 BE로 라우팅되면 KIPRIS/CSE 근거 수집이 조용히 0건이 됩니다.

시스템 의존성(선택): 해외특허(CN/JP/TW)가 이미지 전용 PDF로만 제공될 때 OCR 폴백을 쓰려면 `tesseract`(언어팩 `chi_sim`/`chi_tra`/`jpn`)와 `poppler`가 필요합니다. 미설치 시 OCR 단계는 에러 없이 건너뜁니다(`ocr_warning="tesseract_not_installed"`).

```bash
# macOS
brew install tesseract tesseract-lang poppler
# Debian/Ubuntu
sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra tesseract-ocr-jpn poppler-utils
```

pgvector/RAG 기능을 테스트할 때만 DB가 필요합니다(`/health`·`/docs`·기본 평가 API 확인은 DB 없이 가능).

```bash
docker compose up -d postgres
```

## 실행 방식: `app.api` vs `app.main`

두 실행 방식은 같은 `venv`와 같은 환경변수를 사용합니다. `app.api`를 띄운다고 `app.main`이 같이 도는 것은 아닙니다.

### `app.api` — FastAPI 서버

Spring Boot가 Agent를 HTTP로 호출하거나 Swagger에서 API를 확인할 때 사용합니다.

```bash
venv/bin/uvicorn app.api:app --reload --port 8000
```

- API 서버: `http://localhost:8000`
- Swagger Docs: `http://localhost:8000/docs`
- Health Check: `http://localhost:8000/health`

평가 API는 실제 LangGraph workflow를 실행합니다.

```bash
curl -X POST http://localhost:8000/api/v1/ai/patents/PAT-TEST/evaluate \
  -H "Content-Type: application/json" \
  -d '{"managementNumber":"P202405001-KR0","title":"테스트 특허"}'
```

### `app.main` — CLI 워크플로우

터미널에서 특허 ID를 넣고 평가 workflow를 한 번 실행하고 종료하는 방식입니다.

```bash
venv/bin/python -m app.main --patent-id 1
venv/bin/python -m app.main P202405001-KR0          # 관리번호로도 실행
```

`managementNumber`·`applicationNumber`·`registrationNumber` 중 하나로 평가 대상 특허를 식별합니다.
