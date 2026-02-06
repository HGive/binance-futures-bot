#!/bin/bash

echo "=== 바이낸스 봇 도커 배포 스크립트 ==="

# 로그 디렉토리 생성
mkdir -p logs

# .env에서 로그 파일명 읽기
LOG_FILENAME=$(grep "^LOG_FILENAME=" .env | cut -d'=' -f2 || echo "strategy1.log")

echo "로그 파일명: $LOG_FILENAME"

# 로그 파일 생성 및 권한 설정
touch "logs/$LOG_FILENAME"
chmod 775 logs/
chmod 775 "logs/$LOG_FILENAME"

echo "로그 파일 권한을 775로 설정했습니다"

# 기존 컨테이너 중지 및 제거
echo "기존 컨테이너 정리 중..."
docker compose down

# 이미지 빌드
echo "도커 이미지 빌드 중..."
docker compose build --no-cache

# 컨테이너 실행
echo "컨테이너 실행 중..."
docker compose up -d

# 상태 확인
echo "컨테이너 상태 확인 중..."
docker compose ps

echo "=== 배포 완료 ==="
echo "로그 확인: docker compose logs -f"
echo "중지: docker compose down"
echo "재시작: docker compose restart" 