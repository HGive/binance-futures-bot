# Python 3.12 베이스 이미지 사용
FROM python:3.12-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 업데이트 및 필요한 패키지 설치
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 포트 노출 (Flask 앱이 있다면)
EXPOSE 5000

# 기본 실행 명령어 (docker-compose에서 오버라이드됨)
CMD ["python", "main.py"] 