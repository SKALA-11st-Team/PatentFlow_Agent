# 파이썬 3.11의 가벼운 버전 사용
FROM python:3.11-slim
WORKDIR /app

# (선택) pgvector 등 DB 연결이나 컴파일에 필요한 필수 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 패키지 캐시 방지 및 환경 변수 설정
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 요구사항 파일 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 전체 소스 복사
COPY . .

# FastAPI 포트 노출
EXPOSE 8000

# FastAPI serving entrypoint 실행
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
