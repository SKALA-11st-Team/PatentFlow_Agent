# Unified API Backend (KIPRIS + DART + NAVER + GNews)

하나의 `main.py`에서 아래 4개 API를 통합 제공하는 FastAPI 백엔드입니다.

- KIPRISPlus
- OpenDART
- NAVER News Search
- GNews Search

OpenAPI 문서도 아래 4개 YAML을 합쳐 `/docs`에서 한 번에 확인할 수 있습니다.

- `kipris_all_open_api.yaml`
- `dart_open_api.yaml`
- `naver_open_api.yaml`
- `gnews_open_api.yaml`

## 1) 설치

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2) 환경변수 설정

프로젝트 루트에 `.env` 파일을 만들고 키를 설정하세요.

```env
KIPRIS_SERVICE_KEY=YOUR_KIPRIS_KEY
DART_KEY=YOUR_DART_KEY
NAVER_CLIENT_ID=YOUR_NAVER_CLIENT_ID
NAVER_CLIENT_SECRET=YOUR_NAVER_CLIENT_SECRET
GNEWS_API_KEY=YOUR_GNEWS_API_KEY
```

## 3) 실행

```bash
source venv/bin/activate
python -m uvicorn main:app --reload
```

실행 후:

- API 문서: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
- 헬스체크: `http://127.0.0.1:8000/`

## 4) 주요 엔드포인트

### DART

- `GET /dart/company`
- `GET /dart/financial-account`

### NAVER News

- `GET /api/news/search`
- `GET /api/news/naver/search` (alias)

### GNews

- `GET /api/v4/search`
- `GET /api/news/gnews/search` (alias)

### KIPRIS

- `GET /kipris/patent-utility/search/advanced`
- `GET /kipris/patent-utility/search/application-number`
- `GET /kipris/patent-utility/detail/bibliography`
- `GET /kipris/patent-utility/search/ipc`
- `GET /kipris/patent-utility/search/cpc`
- `GET /kipris/patent-utility/search/applicant`
- `GET /kipris/patent-utility/search/right-holder`
- `GET /kipris/patent-utility/admin-history/transfers`
- `GET /kipris/overseas-patent/bibliography/claims`
- `GET /kipris/overseas-patent/citations/domestic-documents`
- `GET /kipris/overseas-patent/citations/foreign-documents`
- `GET /kipris/patent-utility/documents/publication-fulltext-pdf`
- `GET /kipo-api/kipi/patUtiModInfoSearchSevice/getPubFullTextInfoSearch`
- `GET /kipris/evaluation-data/{applicationNumber}/snapshot`

## 5) 참고 사항

- KIPRIS 호출 시 `ServiceKey`는 요청 쿼리에 넣지 않아도 됩니다. 서버가 `.env` 키를 사용합니다.
- KIPRIS의 실제 경로명에는 `patUtiModInfoSearchSevice`처럼 원문 기준 표기가 포함됩니다.
- OpenAPI 서버 URL을 바꾸고 싶으면 환경변수 `UNIFIED_OPENAPI_SERVER_URL`을 지정하세요.
