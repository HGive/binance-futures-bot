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

## 전략 규칙

전략 개발·검증·배포 규칙은 [STRATEGY_RULES.md](STRATEGY_RULES.md) 참고.

## 브랜치 운영 방식

전략은 브랜치 단위로 관리한다.

- `main` — 공용 골격 + 기본 전략 하나(`strategies/trailing_atr.py`). 백테스트 코드는 두지 않는다.
- `dev1`, `dev2`, ... — 신규 전략 개발 브랜치. `main`에서 분기해 전략 파일과 백테스트를 추가한다.
- `testing` — 이전에 혼재돼 있던 전략/백테스트 스냅샷 보관용.

공용 모듈(`config.py`, `modules/`, `main.py`)만 `main`에서 관리하고,
전략별 코드는 각 브랜치 안에서만 유지한다.
