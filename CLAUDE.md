# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Binance 선물 거래 자동매매 봇. `ccxt` 라이브러리를 통해 Binance Futures API와 통신하며, 기술적 지표(RSI, StochRSI, EMA, MA) 기반 스코어링으로 롱/숏 진입 신호를 판단한다.

## 개발 환경 설정

패키지 관리는 **Poetry** 사용:

```bash
# 의존성 설치
poetry install

# 가상환경 활성화
poetry shell

# 스크립트 실행
poetry run python <script>.py
```

`.env` 파일에 API 키 필요:

```
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

서버 배포는 AWS Lightsail 기준 (README.md 참고).

## 코드 아키텍처

### 디렉토리 구조

- **`modules/`** — 재사용 가능한 지표 계산 모듈
- **`prac/`** — 실험/연습용 스크립트 (프로덕션 코드 아님)
- **`data_structure/`** — API 응답 데이터 구조 예시 JSON
- **`.cursor/rules/`** — Cursor 규칙 (positions 데이터 구조 참조)
- **`backtest/`** — 백테스트 관련 코드
- **`strategies/`** — 실제 전략 관련 코드

### 핵심 모듈 (`modules/`)

| 파일                 | 역할                                                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `module_rsi.py`      | RSI 계산 — `calc_rsi(ohlc_df, period=14)` → 마지막 값(float) 반환                                                     |
| `module_stochrsi.py` | Stochastic RSI 계산 — `calc_stoch_rsi(close_prices)` → `{StochRSI, K, D}` DataFrame 반환                              |
| `module_ema.py`      | EMA 계산 — `calc_ema(data, window)` → pandas Series 반환                                                              |
| `module_ma.py`       | MA40/MA120 계산 및 기울기(선형회귀) — `get_ma_signals(df)` → 신호 dict 반환                                           |
| `module_score.py`    | 지표 통합 스코어링 — `calculate_score(df, rsi, stoch_rsi, ema_10, ema_20, ema_50)` → `(long_score, short_score)` 반환 |
| `module_common.py`   | 공통 유틸 — `calc_buy_unit(total_balance)` → 잔고의 10%, 최소 5 USDT                                                  |

### 스코어링 로직 (`module_score.py`)

- **RSI**: 극단값(90↑/10↓)에서 최대 30점, 중간값(80↑/20↓)에서 25점
- **StochRSI K/D**: 과매도(≤30)면 롱 +10~20점, 과매수(≥70)면 숏 +10~20점
- **EMA 정렬**: `ema_50 < ema_20 < ema_10 < price` → 롱 +10점 (역순이면 숏)
- **최근 40봉 가격 위치**: 중간가격 대비 ±5% 이상 이탈 시 추가 점수

### Exchange 초기화 패턴

```python
import ccxt
exchange = ccxt.binance(config={
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'adjustForTimeDifference': True
    }
})
```

실시간 WebSocket이 필요한 경우 `ccxt.pro` (`ccxtpro`) 사용.

### Positions 데이터 구조

포지션 처리 시 핵심 필드:

- `contracts`: 포지션 수량 (항상 양수)
- `side`: `"long"` 또는 `"short"`
- `entryPrice`: 진입가
- `markPrice`: 현재 시장가
- `unrealizedPnl`: 미실현 손익 (USDT)
- `percentage`: 미실현 손익률 (%)
- `marginMode`: `"isolated"` (격리 마진 사용)

전체 예시는 [data_structure/positions.json](data_structure/positions.json) 참조.
