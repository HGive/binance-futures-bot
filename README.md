# binance-futures-bot
binance futures trading bot

# 1. 서버에 접속
ssh -i your-key.pem ubuntu@your-lightsail-ip

# 2. 프로젝트 클론
git clone your-repo-url
cd binance-futures-bot

# 3. Poetry 설치
curl -sSL https://install.python-poetry.org | python3 -

# 4. 의존성 설치
poetry install

# 5. .env 파일 생성
nano .env
# API 키 입력

# 6. 웹서버 실행 
(실행 전 가상환경 진입입)
poetry run python web_controller.py