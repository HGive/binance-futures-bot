# Python 3.12 베이스 이미지 사용
FROM python:3.12-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 업데이트 및 필요한 패키지 설치
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Poetry 설치
RUN pip install poetry

# Poetry 설정 (가상환경 생성하지 않음)
RUN poetry config virtualenvs.create false

# pyproject.toml과 poetry.lock 복사
COPY pyproject.toml poetry.lock ./

# 의존성 설치
RUN poetry install --no-dev --no-interaction --no-ansi

# 애플리케이션 코드 복사
COPY . .

# 환경변수 파일이 있다면 복사 (선택사항)
COPY .env* ./

# 포트 노출 (Flask 앱이 있다면)
EXPOSE 5000

# 실행 명령어
CMD ["python", "main.py"] 