# 파이썬 3.11의 가벼운 버전 사용
FROM python:3.11-slim
WORKDIR /app

# pgvector 등 DB 연결/컴파일용 패키지와, 전문 PDF 파싱(opendataloader_pdf)이 호출하는
# JRE를 설치한다. opendataloader_pdf는 번들 JAR을 `java -jar`로 실행하므로(EXT-05) JRE가
# 없으면 컨테이너에서 PDF→Markdown 파싱이 실패해 기술성/권리성 입력이 로컬과 달라진다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 패키지 캐시 방지 및 환경 변수 설정
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 요구사항 파일 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 전체 소스 복사
COPY . .

# SEC-09: 비특권 사용자로 실행한다(컨테이너 root 실행 방지). 런타임 산출물(/app/artifacts) 쓰기를 위해
# 작업 디렉터리 소유권을 appuser에게 넘긴다.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/artifacts \
    && chown -R appuser:appuser /app
USER appuser

# FastAPI 포트 노출
EXPOSE 8000

# FastAPI serving entrypoint 실행
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
