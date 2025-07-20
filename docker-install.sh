#!/bin/bash

echo "=== Docker 설치 스크립트 ==="

# 도커 설치 확인
if command -v docker &> /dev/null; then
    echo "✅ Docker가 이미 설치되어 있습니다."
    docker --version
else
    echo "❌ Docker가 설치되어 있지 않습니다. 설치를 시작합니다..."
    
    # 기존 도커 제거 (있다면)
    sudo apt-get remove docker docker-engine docker.io containerd runc
    
    # 필요한 패키지 설치
    sudo apt-get update
    sudo apt-get install -y \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        lsb-release
    
    # 도커 GPG 키 추가
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    
    # 도커 저장소 추가
    echo \
      "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # 도커 설치
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io
    
    # 현재 사용자를 docker 그룹에 추가
    sudo usermod -aG docker $USER
    
    echo "✅ Docker 설치 완료!"
    echo "⚠️  시스템을 재시작하거나 로그아웃 후 다시 로그인해주세요."
fi

# Docker Compose 설치 확인
if command -v docker-compose &> /dev/null; then
    echo "✅ Docker Compose가 이미 설치되어 있습니다."
    docker-compose --version
else
    echo "❌ Docker Compose가 설치되어 있지 않습니다. 설치를 시작합니다..."
    
    # Docker Compose 설치
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
    
    echo "✅ Docker Compose 설치 완료!"
fi

echo "=== 설치 완료 ===" 