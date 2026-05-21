# 시장성 평가 변경 요약

이 문서는 시장성 평가 축을 기존 LLM 단일 점수 방식에서 `산업 시장성 + 시장 성장성 + 글로벌 사업성` 세부 점수 방식으로 변경한 내용을 정리한다.

## 변경 목표

시장성 점수는 100점 만점이며 아래 3개 항목의 합산으로 계산한다.

```text
시장성 점수(100)
= 산업 시장성(40)
+ 시장 성장성(40)
+ 글로벌 사업성(20)
```

## 평가 항목별 산정 방식

| 항목 | 배점 | 산정 방식 |
| --- | ---: | --- |
| 산업 시장성 | 40 | Vector DB 산업 리포트/기업 투자 동향 근거를 LLM이 0/20/40점으로 판단 |
| 시장 성장성 | 40 | 대표 CPC 기준 최근 3개 완료 연도 특허 수로 CAGR 25점 + 추세 15점 계산 |
| 글로벌 사업성 | 20 | KIPRIS Patent Family 국가 정보로 코드 계산 |

현재 연도가 2026년이면 시장 성장성 기준 연도는 현재 연도를 제외한 `2023, 2024, 2025`다.

## 실행 방법

### 1. 시장성만 빠르게 평가

시장성 변경 사항만 확인할 때는 아래 스크립트를 사용한다.

```bash
cd /Users/hangyu/workspace/PatentFlow_Agent
source .venv/bin/activate
python scripts/evaluate_market_only.py P201702001-KR0 --industry 반도체
```

`--industry 반도체`는 산업 리포트 Vector DB 검색을 반도체 산업 chunk로 제한하는 옵션이다. 산업을 확정하기 어렵거나 전체 리포트에서 검색하고 싶으면 옵션을 빼고 실행한다.

```bash
python scripts/evaluate_market_only.py P201702001-KR0
```

다른 관리번호로 실행하려면 관리번호만 바꾼다.

```bash
python scripts/evaluate_market_only.py P201704001-KR0
python scripts/evaluate_market_only.py P201809001-KR0
python scripts/evaluate_market_only.py P202405001-KR0
```

출력 위치:

```text
artifacts/runs/manual/market_only_<timestamp>_<management_number>/
├── market_eval_result.json   # 세부 원본 JSON
└── market_eval_report.md     # 점수 중심 Markdown 리포트
```

### 2. 전체 workflow 실행

전체 가치평가를 실행해도 시장성 변경 사항은 반영된다. 전체 workflow의 시장성 노드가 동일한 `agents/valuation_axes/market.py`를 사용하기 때문이다.

```bash
cd /Users/hangyu/workspace/PatentFlow_Agent
source .venv/bin/activate
python -m app.main P201702001-KR0
```

전체 workflow는 요약, 검색, evidence compression, 4개 평가축, supervisor, 최종 보고서 생성을 모두 수행하므로 시장성 단독 실행보다 오래 걸린다. 시장성 40/40/20 계산만 확인하려면 `scripts/evaluate_market_only.py`를 우선 사용한다.

### 3. 실행 전 확인

Java 기반 PDF 파싱이 필요하므로 로컬 JDK가 없으면 아래 환경변수를 설정한다. 현재 프로젝트에는 로컬 JDK를 `.jdk/`에 풀어둔 상태다.

```bash
export JAVA_HOME=/Users/hangyu/workspace/PatentFlow_Agent/.jdk/jdk-17.0.19+10/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
```

산업 RAG와 LLM 호출에는 `.env`의 OpenAI/KIPRIS 설정과 pgvector DB가 필요하다.

## 주요 코드 변경

### `agents/valuation_axes/market.py`

- 시장성 payload에 `marketability_metrics`를 추가한다.
- LLM 결과를 그대로 쓰지 않고 `apply_marketability_scores()`에서 세부 점수를 합산한다.
- 산업 시장성 evidence는 `industry_report`, `company_disclosure` 중심으로 제한한다.
- 대표 CPC 추출 우선순위:
  1. `state.preprocessed_patent.metadata.cpc`
  2. `state.kipris_api_data.metadata.cpc`
  3. `state.patent_structured.cpc`
- 시장 성장성은 `cpcSearchInfo` 기반 KIPRIS CPC 검색 결과를 사용한다.
- 글로벌 사업성은 `state.kipris_family_patents`의 국가 코드로 산정한다.

### `prompts/valuation/valuation_market.md`

- LLM 역할을 시장성 전체 판단에서 산업 시장성 판단으로 축소했다.
- LLM은 `industry_marketability_score`만 판단한다.
- `market_growth_score`, `global_business_score`는 코드 계산값을 그대로 사용한다.

### `open_api/kipris_client.py`

`cpcSearchInfo` 호출 경로와 인증 파라미터를 수정했다.

```text
경로: /openapi/rest/patUtiModInfoSearchSevice
인증 파라미터: accessKey
operation: cpcSearchInfo
```

### `services/evidence/api_normalizers.py`

KIPRIS `cpcSearchInfo` 응답의 `PatentUtilityInfo` 배열을 읽도록 파서를 확장했다.

### `services/patent/markdown_preprocess_service.py`

PDF markdown에서 `CPC특허분류` 라벨 다음 줄에 있는 CPC도 추출하도록 수정했다.

예시:

```text
(52) CPC특허분류
- H01L 22/20 (2013.01) H01L 22/12 (2013.01)
```

### `agents/valuation.py`

시장성 LLM 결과에 포함되는 optional field를 보존한다.

- `industry_marketability_score`
- `sub_scores`
- `marketability_metrics`

### `scripts/evaluate_market_only.py`

시장성만 단독 실행하는 스크립트를 추가했다.

실행 예시:

```bash
cd /Users/hangyu/workspace/PatentFlow_Agent
source .venv/bin/activate
python scripts/evaluate_market_only.py P201702001-KR0 --industry 반도체
```

출력 파일:

```text
artifacts/runs/manual/market_only_<timestamp>_<management_number>/
├── market_eval_result.json
└── market_eval_report.md
```

`market_eval_report.md`는 사람이 바로 읽을 수 있도록 점수 요약을 먼저 보여준다.

## 시장 성장성 계산

대표 CPC 기준으로 KIPRIS `cpcSearchInfo`를 호출한다.

```text
patent=True
utility=False
docsCount=500
docsStart=page
```

응답에서 `RegistrationStatus`가 다음 중 하나인 항목만 사용한다.

- `공개`
- `등록`

연도 기준:

- 등록 문헌: `RegistrationDate` 우선, 없으면 `OpeningDate`
- 공개 문헌: `OpeningDate`

중복 제거 키 우선순위:

1. `ApplicationNumber`
2. `RegistrationNumber`
3. `OpeningNumber`
4. `PublicNumber`
5. `InventionName + ApplicationDate`

### CAGR 점수

| CAGR | 점수 |
| --- | ---: |
| 15% 이상 | 25 |
| 8% 이상 15% 미만 | 20 |
| 3% 이상 8% 미만 | 15 |
| 0% 이상 3% 미만 | 10 |
| 음수 | 0 |

### 추세 점수

| 상태 | 점수 |
| --- | ---: |
| 연속 증가 | 15 |
| 일부 증가 | 8 |
| 연속 감소 | 0 |

## 글로벌 사업성 계산

KIPRIS Patent Family 국가 코드 기준으로 산정한다.

| 상태 | 점수 |
| --- | ---: |
| 미국/중국/일본 중 하나 이상 포함 | 20 |
| 그 외 해외 출원 존재 | 10 |
| 국내 단독 출원 | 0 |

## Missing 처리

시장 성장성은 데이터가 없으면 기본 점수를 주지 않는다.

다음 경우 `market_growth_score`는 `null`이 된다.

- 대표 CPC를 추출하지 못함
- KIPRIS CPC 검색 실패
- 최근 3개 완료 연도 count가 불완전함
- 시작 연도 count가 0이라 CAGR 계산이 불가능함

이 경우 `missing_information`에 아래 문구를 추가한다.

```text
CPC 기준 최근 3년 연도별 특허 출원 수 확인 필요
```

## 검증한 예시

### `P201702001-KR0`

대표 CPC:

```text
G05B 19/4065
```

KIPRIS `cpcSearchInfo` 기준 출원 수:

| 연도 | 출원 수 |
| --- | ---: |
| 2023 | 18 |
| 2024 | 22 |
| 2025 | 54 |

세부 점수:

| 항목 | 점수 |
| --- | ---: |
| 산업 시장성 | 40 / 40 |
| 시장 성장성 | 40 / 40 |
| 글로벌 사업성 | 20 / 20 |

### `P201704001-KR0`

대표 CPC:

```text
H01L 22/20
```

KIPRIS `cpcSearchInfo` 기준 출원 수:

| 연도 | 출원 수 |
| --- | ---: |
| 2023 | 1 |
| 2024 | 0 |
| 2025 | 0 |

정확 CPC 기준 결과가 적게 나온 사례다. 상위 CPC(`H01L22` 등)로 확장하면 더 많은 결과가 나오지만, 현재 구현은 PDF에서 추출한 대표 CPC 정확 코드를 사용한다.

## 테스트

시장성 계산 helper 검증을 `tests/test_valuation.py`에 추가했다.

확인 항목:

- 현재 연도 제외 최근 3개 완료 연도 계산
- CAGR 점수 구간
- 최근 3년 추세 점수
- 40/40/20 세부 점수 합산
- 시장 성장성 missing 시 기본 점수를 주지 않는 동작

실행:

```bash
.venv/bin/python -m pytest tests/test_valuation.py -q
```
