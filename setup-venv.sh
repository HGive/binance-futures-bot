#!/bin/bash

echo "=== 가상환경 설정 스크립트 ==="

# Poetry가 설치되어 있는지 확인
if ! command -v poetry &> /dev/null; then
    echo "❌ Poetry가 설치되어 있지 않습니다. 설치를 시작합니다..."
    curl -sSL https://install.python-poetry.org | python3 -
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "✅ Poetry가 이미 설치되어 있습니다."
    poetry --version
fi

# Poetry 설정: 프로젝트 디렉토리에 .venv 생성
echo "Poetry 설정 중..."
poetry config virtualenvs.in-project true

# 가상환경 생성 (프로젝트 루트에)
echo "가상환경 생성 중..."
poetry install

echo "✅ 가상환경 설정 완료!"
echo "이제 docker-compose up -d로 컨테이너를 실행할 수 있습니다." 