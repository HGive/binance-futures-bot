# Development Guidelines

## 1. 프로젝트 개요

| 항목 | 값 |
|------|-----|
| 프로젝트 | Binance Futures 자동매매 봇 |
| 언어 | Python 3.11+ |
| 핵심 라이브러리 | ccxt.pro (async), pandas, asyncio |
| 거래소 | Binance Futures (USDT-M) |
| 환경 | Testnet / Production (`.env`로 분리) |

---

## 2. 프로젝트 아키텍처

```
binance-futures-bot/
├── main.py              # 엔트리포인트 - 전략 루프 실행
├── config.py            # 환경설정, exchange 인스턴스 생성
├── strategies/          # 매매 전략 클래스들
│   ├── hour_3p_strategy.py
│   └── min15_3p_strategy.py
├── modules/             # 기술지표 계산 모듈
│   ├── module_common.py  # 공통 유틸 (calc_buy_unit 등)
│   ├── module_ema.py
│   ├── module_ma.py
│   ├── module_rsi.py
│   └── module_stochrsi.py
├── .cursor/rules/       # AI Agent 룰 파일
│   ├── positions-example.mdc    # 포지션 데이터 구조
│   └── ccxt-binance-functions.mdc  # CCXT 함수 사용법
└── .env                 # API 키 (gitignore)
```

### 모듈 역할

| 모듈 | 역할 |
|------|------|
| `config.py` | **읽기 전용** - exchange 인스턴스 import용 |
| `strategies/*.py` | 매매 전략 로직 구현 |
| `modules/*.py` | 기술지표 계산 함수 |
| `main.py` | 전략 실행 및 루프 관리 |

---

## 3. 코드 표준

### 비동기 패턴 (필수)

```python
# ✅ 올바른 사용
async def run_once(self):
    balance = await self.exchange.fetch_balance()
    positions = await self.exchange.fetch_positions(symbols=[self.symbol])

# ❌ 금지 - 동기 호출
def run_once(self):
    balance = self.exchange.fetch_balance()  # 에러 발생
```

### 명명 규칙

| 대상 | 규칙 | 예시 |
|------|------|------|
| 전략 클래스 | PascalCase + Strategy | `Min15Strategy3p` |
| 전략 파일 | snake_case + _strategy.py | `min15_3p_strategy.py` |
| 모듈 파일 | module_ + 지표명.py | `module_rsi.py` |
| 상수 | UPPER_SNAKE_CASE | `TAKE_PROFIT_PCT` |

### 로깅 패턴

```python
# 표준 로깅 형식
logging.info(f"[{self.symbol}] 메시지")
logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")
```

---

## 4. 전략 구현 표준

### 필수 클래스 구조

```python
class NewStrategy:
    def __init__(self, exchange, symbol, leverage=3, timeframe="15m"):
        self.exchange = exchange
        self.symbol = symbol
        self.leverage = leverage
        self.timeframe = timeframe
        # 상태 변수 초기화

    async def setup(self):
        """초기화: 레버리지, 마진모드 설정"""
        await self.exchange.cancel_all_orders(symbol=self.symbol)
        await self.exchange.set_leverage(self.leverage, self.symbol)
        await self.exchange.set_margin_mode("isolated", self.symbol)

    async def run_once(self):
        """메인 로직 - 1회 실행"""
        try:
            # 1. 데이터 수집
            # 2. 포지션 관리 또는 진입 판단
        except Exception as e:
            logging.error(f"[{self.symbol}] Error: {type(e).__name__}: {e}")
```

### 필수 상수 정의

```python
# 전략 파일 상단에 상수 정의
TIMEFRAME = "15m"
LEVERAGE = 3
TAKE_PROFIT_PCT = 0.03      # +3%
STOP_LOSS_PCT = -0.05       # -5%
```

---

## 5. CCXT/외부 라이브러리 사용 표준

### 필수 참조 문서

- **CCXT 함수 사용법**: `.cursor/rules/ccxt-binance-functions.mdc` 참조
- **포지션 데이터 구조**: `.cursor/rules/positions-example.mdc` 참조

### 주요 CCXT 함수

| 함수 | 용도 |
|------|------|
| `fetch_balance()` | 잔고 조회 |
| `fetch_positions(symbols=[symbol])` | 포지션 조회 |
| `fetch_ohlcv(symbol, timeframe, limit)` | 캔들 데이터 |
| `create_order(symbol, type, side, amount, price, params)` | 주문 생성 |
| `cancel_all_orders(symbol=symbol)` | 주문 취소 |

### 포지션 데이터 접근

```python
positions = await self.exchange.fetch_positions(symbols=[self.symbol])
position = positions[0] if positions and positions[0]["contracts"] > 0 else None

if position:
    side = position["side"]           # "long" or "short"
    entry = position["entryPrice"]    # 진입가
    pnl_pct = position["percentage"]  # 손익률 (%)
    contracts = position["contracts"] # 포지션 크기
```

---

## 6. 워크플로우 표준

### 새 전략 개발 플로우

1. `strategies/` 폴더에 새 전략 파일 생성
2. 전략 클래스 구현 (`setup()` + `run_once()`)
3. `main.py`에 전략 import 및 SYMBOLS 설정
4. **Testnet에서 테스트** (`TEST_NET=TRUE`)
5. Production 배포

### 테스트 플로우

```bash
# 1. .env에서 TEST_NET=TRUE 설정
# 2. 실행
python main.py
# 3. 로그 확인 (콘솔 출력)
```

---

## 7. 핵심 파일 상호작용 표준

### 전략 추가 시 수정 필요 파일

| 작업 | 수정 파일 |
|------|----------|
| 새 전략 추가 | `strategies/new_strategy.py` 생성 → `main.py` import 추가 |
| 새 지표 모듈 추가 | `modules/module_xxx.py` 생성 → 전략에서 import |
| 심볼 추가/변경 | `main.py`의 `SYMBOLS` 리스트 수정 |

### main.py 수정 패턴

```python
# 1. import 추가
from strategies.new_strategy import NewStrategy

# 2. SYMBOLS 설정
SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

# 3. 전략 인스턴스 생성
strategies = [NewStrategy(exchange, symbol) for symbol in SYMBOLS]
```

---

## 8. AI 의사결정 표준

### 전략 수정 vs 새 전략 생성

| 상황 | 결정 |
|------|------|
| 기존 전략의 파라미터 변경 | 기존 전략 수정 |
| 진입/청산 로직 변경 | 기존 전략 수정 |
| 완전히 다른 타임프레임/로직 | **새 전략 파일 생성** |
| 기존 전략 A/B 테스트 | **새 전략 파일 생성** |

### 테스트 우선 원칙

1. **항상 Testnet 먼저** - Production 직접 배포 금지
2. 로그로 동작 확인 후 Production 배포

---

## 9. 금지 사항

### ❌ 절대 금지

| 금지 사항 | 이유 |
|----------|------|
| `ccxt` 동기 버전 사용 | 이 프로젝트는 `ccxt.pro` (async) 전용 |
| `config.py`의 exchange 직접 수정 | 환경변수로만 제어 |
| API 키 하드코딩 | `.env` 파일만 사용 |
| `main.py`에서 직접 거래 로직 구현 | 전략 클래스에서만 구현 |
| `time.sleep()` 사용 | `asyncio.sleep()` 사용 |

### ❌ 전략 구현 시 금지

| 금지 사항 | 대안 |
|----------|------|
| 무한 while 루프 in run_once() | main.py의 루프가 run_once() 호출 |
| 하드코딩된 심볼 | `self.symbol` 사용 |
| print() 사용 | `logging.info()` 사용 |

---

## 10. 환경 변수 (.env)

```bash
# 필수
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret

# 테스트넷 (옵션)
BINANCE_TESTNET_API_KEY=testnet_key
BINANCE_TESTNET_API_SECRET=testnet_secret

# 모드 전환
TEST_NET=TRUE   # 테스트넷
TEST_NET=FALSE  # 프로덕션

# 로그
LOG_FILENAME=strategy.log
```
