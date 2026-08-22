# 배포 가이드

## 사전 준비

1. 서버에 Docker, Docker Compose 설치
2. Binance USDⓈ-M Futures API Key 발급 (IP 제한 권장)

## 배포

```bash
# 1. 프로젝트 클론
git clone <repo-url>
cd binance-futures-bot

# 2. 환경변수 설정
cp .env.example .env
vi .env   # BINANCE_API_KEY, BINANCE_API_SECRET 입력

# 3. 배포 실행
chmod +x deploy.sh
./deploy.sh
```

## 운영 명령어

```bash
# 실시간 로그 확인 (둘 다 동일한 내용)
docker logs -f binance-strategy1
tail -f logs/strategy1.log

# 중지
docker compose down

# 재시작
docker compose restart

# 재빌드 후 배포 (코드 변경 시)
git pull && ./deploy.sh
```

## 디렉토리 구조

```
binance-futures-bot/
├── .env                 # API 키 (git 미추적)
├── main.py              # 진입점 (7개 심볼 × 60초 루프)
├── config.py            # Exchange 초기화, 로깅
├── strategies/
│   └── min15_3p_strategy.py   # 실전 전략
├── modules/
│   ├── module_ema.py
│   ├── module_rsi.py
│   └── module_atr.py
├── logs/                # 로그 (Docker volume 마운트)
├── Dockerfile
├── docker-compose.yml
└── deploy.sh            # 원클릭 배포 스크립트
```
