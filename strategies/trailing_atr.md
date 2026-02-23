# Trailing ATR Strategy

## 개요
15분봉 추세추종 전략 (물타기 없음, 손익비 최적화)

## 핵심 특징
- **물타기 없음**: 추가매수 로직 완전 제거
- **손익비 개선**: 익절 늦춤(5%), 손절 타이트(ATR*2.0), 트레일링 넓음(ATR*2.5)
- **Stoch RSI 필터**: 확실한 과매도/과매수 구간에서만 진입 (K < 15 / K > 85)
- **EMA 20/60**: 명확한 추세 방향 확인 후 진입

## 상수값

| 항목 | 값 | 설명 |
|------|-----|------|
| TIMEFRAME | 15m | 캔들 시간프레임 |
| LEVERAGE | 3 | 레버리지 배수 |
| EMA_MEDIUM | 20 | 단기 EMA |
| EMA_SLOW | 60 | 장기 EMA |
| SLOPE_PERIOD | 3 | 기울기 판정 봉 수 |
| STOCH_RSI_PERIOD | 14 | Stoch RSI 기간 |
| STOCH_RSI_K_PERIOD | 3 | K 라인 기간 |
| STOCH_RSI_D_PERIOD | 3 | D 라인 기간 |
| STOCH_RSI_LONG_THRESHOLD | 15 | 롱 진입 조건 (K < 15, 과매도) |
| STOCH_RSI_SHORT_THRESHOLD | 85 | 숏 진입 조건 (K > 85, 과매수) |
| PARTIAL_TP_PCT | 5% | 부분 익절 레벨 |
| INITIAL_SL_ATR_MULT | 2.0 | 초기 손절 (ATR 배수) |
| TRAILING_STOP_ATR_MULT | 2.5 | 트레일링 스탑 (ATR 배수) |
| POSITION_SIZE_PCT | 10% | 포지션 사이징 |

## 진입 조건

### Long 진입
1. 현재가 > EMA20 AND 현재가 > EMA60
2. EMA20 기울기 > 0 (3봉 기준)
3. EMA60 기울기 > 0 (3봉 기준)
4. Stoch RSI K < 15 (과매도 구간)

### Short 진입
1. 현재가 < EMA20 AND 현재가 < EMA60
2. EMA20 기울기 < 0 (3봉 기준)
3. EMA60 기울기 < 0 (3봉 기준)
4. Stoch RSI K > 85 (과매수 구간)

## 포지션 관리 (4단계)

### 1. 트레일링 갱신
- Long: `best_price = max(best_price, high)`
- Short: `best_price = min(best_price, low)`

### 2. 손절 체크
- Long: `low <= sl_price` → 청산
- Short: `high >= sl_price` → 청산

### 3. 부분 익절 (+5%)
- 조건 도달 시 50% 청산
- SL을 진입가로 이동 (본전 보장)
- 트레일링 모드 활성화

### 4. 트레일링 스탑
- Long: `low <= best_price - ATR * 2.5` → 청산
- Short: `high >= best_price + ATR * 2.5` → 청산

## 백테스트 결과 (CRV/USDT, 1000봉)

| 지표 | 값 |
|------|-----|
| 수익률 | +11.06% |
| 승률 | 73.7% |
| 손익비 | 0.79 |
| MDD | -3.66% |

## 파일
- 전략: `strategies/trailing_atr.py`
- 백테스트 v2 (RSI, EMA120): `backtest/backtest_v2_no_avgdown.py`
- 백테스트 v3 (Stoch RSI, EMA60): `backtest/backtest_v3_stochrsi.py`
