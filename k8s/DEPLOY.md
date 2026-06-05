# PatentFlow Agent — EKS 배포 가이드

BE(`PatentFlow_BE`)와 동일한 Harbor → EKS 파이프라인이다. 에이전트는 클러스터 내부
`ClusterIP` 서비스(`team11-patentflow-agent-svc:8000`)로, BE가 `PATENTFLOW_AGENT_URL=
http://team11-patentflow-agent-svc:8000` 으로 호출한다. (에이전트 미배포 시 BE는 안전하게
in-memory 폴백으로 동작한다.)

## 트리거
- `Seeun` 브랜치 push 또는 GitHub Actions `Deploy Agent to EKS` 수동 실행(workflow_dispatch).
- 즉 분야 추천 기능을 라이브로 올리려면 feature PR을 **Seeun에 머지**한 뒤 워크플로가 돌면 된다.

## 사전 등록 필요 — GitHub Actions Secrets (Repo Settings → Secrets and variables → Actions)
BE 레포에 이미 있는 값들을 **에이전트 레포에도** 등록해야 한다(레포별로 분리 저장됨).

| Secret | 설명 | BE와 동일 값? |
|---|---|---|
| `HARBOR_REGISTRY` | Harbor 레지스트리 호스트 | 동일 |
| `HARBOR_PROJECT` | Harbor 프로젝트명 | 동일 |
| `HARBOR_USERNAME` | Harbor 사용자 | 동일 |
| `HARBOR_PASSWORD` | Harbor 비밀번호 | 동일 |
| `KUBE_CONFIG` | base64 인코딩 kubeconfig | 동일 |
| `AWS_ACCESS_KEY_ID` | EKS get-token용 | 동일 |
| `AWS_SECRET_ACCESS_KEY` | EKS get-token용 | 동일 |
| `AWS_SESSION_TOKEN` | (선택) 임시 자격증명 시 | 동일 |
| `POSTGRES_PASSWORD` | 공유 PG 비밀번호 (= BE `SPRING_DATASOURCE_PASSWORD`) | 동일 값 |
| **`OPENAI_API_KEY`** | **LLM 호출용 — 신규 등록 필요** | ⚠️ 신규 |
| `KIPRIS_SERVICE_KEYS` | 초록/평가용 KIPRIS **다중 키**(콤마 구분, 한도 분산 권장) = BE `PATENTFLOW_KIPRIS_SERVICE_KEYS` 값 | 값 동일, 이름 다름 |
| `KIPRIS_SERVICE_KEY` | (대안) 단일 KIPRIS 키. KEYS가 있으면 불필요 | 값 동일, 이름 다름 |
| `OPENAI_SUPERVISOR_MODEL` | (선택) supervisor 모델 | 신규(선택) |
| `LANGSMITH_API_KEY` | (선택) 트레이싱 | 신규(선택) |

### 평가(evaluate) 워크플로 전용 — 분류(recommend-fields)에는 불필요
근거수집(시장성/뉴스/재무) 단계에서 쓰인다. 미등록 시 빈 값으로 들어가 **해당 근거만 degrade**되고
부팅·분류에는 지장이 없다. evaluate까지 제대로 쓰려면 등록한다.

| Secret | 서비스 |
|---|---|
| `DART_KEY` | DART 재무공시 |
| `GNEWS_API_KEY` | GNews 뉴스 검색 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | Naver 검색 |
| `GOOGLE_CUSTOM_SEARCH_API_KEY`, `GOOGLE_CUSTOM_SEARCH_CX` | Google CSE (SK AX 사이트 검색) |
| `TAVILY_API_KEY` | Tavily 검색(대체) |

> BigQuery(해외특허, `BIGQUERY_PROJECT` + GCP 서비스계정)는 별도 인증이 필요해 이 워크플로에 포함하지
> 않았다. 필요 시 GOOGLE_APPLICATION_CREDENTIALS 마운트를 추가로 구성해야 한다.

## 선택 — GitHub Actions Variables (없으면 기본값 사용)
| Variable | 기본값 |
|---|---|
| `KUBE_NAMESPACE` | `patentflow` → 실제는 `skala3-finalproj-class3-team11` 로 설정 권장 |
| `OPENAI_CHAT_MODEL` | `gpt-5-mini` |
| `ENABLE_SHARED_DB_FALLBACK` | `true` (evaluate가 patent_id로 공유 DB 식별자 조회) |
| `UNIFIED_API_BASE_URL` | `http://team11-patentflow-be-svc:80` |
| `PGVECTOR_HOST/DB/USER` | `team11-patentflow-postgres-svc` / `patentflow` / `patentflow` |
| `AWS_REGION` / `AWS_PROFILE_NAME` | `ap-northeast-2` / `skala-student` |

> **중요**: 실제 네임스페이스가 `patentflow`가 아니라면(현재 `skala3-finalproj-class3-team11`)
> 반드시 repo Variable `KUBE_NAMESPACE`를 그 값으로 설정해야 한다. BE 레포와 동일.

## 알려진 한계
- 현재 `Dockerfile`은 Java를 설치하지 않는다. **분야 추천(recommend-fields)** 경로는 Java가
  필요 없어 정상 동작하지만, **평가(evaluate)의 PDF 파싱**은 Java가 필요할 수 있다. 평가까지
  라이브로 쓰려면 Dockerfile에 JRE 설치를 추가해야 한다(별도 작업).
