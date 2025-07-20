FROM python:3.12-slim

WORKDIR /app

# poetry 설치
RUN pip install poetry

# poetry 가상환경 프로젝트 내부에 생성하도록 설정
RUN poetry config virtualenvs.in-project true

# 의존성 설치 위해 pyproject.toml, poetry.lock만 먼저 복사 (빌드 캐시 활용)
COPY pyproject.toml poetry.lock ./

RUN poetry install --no-interaction --no-root

# 전체 소스 복사
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python3", "main.py"]