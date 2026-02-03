FROM python:3.12-slim

WORKDIR /app

# C 확장 빌드용 (psutil 등)
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev && rm -rf /var/lib/apt/lists/*

# poetry 설치
RUN pip install poetry

# poetry 가상환경 프로젝트 내부에 생성하도록 설정
RUN poetry config virtualenvs.in-project true

# 의존성 설치 (poetry.lock 없으면 pyproject.toml 기준으로 해결)
COPY pyproject.toml ./
RUN poetry install --no-interaction --no-root

# 전체 소스 복사
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python3", "main.py"]