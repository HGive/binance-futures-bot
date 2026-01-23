# 15분봉 3% 추세추종 단타 전략 (min15_3p_strategy)

## 📌 전략 개요

| 항목 | 값 |
|------|-----|
| 타임프레임 | 15분봉 |
| 전략 유형 | Trend Following Scalping |
| 목표 수익률 | +3% (고정 익절) |
| 레버리지 | 3x (권장) |
| 최대 추가매수 | 1회 |

---

## 🎯 전략의 핵심 컨셉

### 목표
- 15분봉 기준으로 **추세 방향으로만** 진입
- 한 번에 크게 먹지 않는다
- **3% 내외의 확정 수익**을 반복적으로 쌓는다

### 본질
> "승률 높은 구간만 골라서, 욕심 없이 잘라먹고 도망치는 구조"

### 핵심 사고 전환
- ❌ 바닥 맞추기
- ❌ 꼭지 맞추기  
- ✅ **이미 방향 나온 곳에서 안전하게 3%만 먹기**

---

## 📊 기술적 지표 설정

### 사용 지표
| 지표 | 파라미터 | 용도 |
|------|----------|------|
| EMA | 20, 120 | 추세 판단 |
| RSI | 14 (기본값) | 진입 타이밍 |

---

## 🔹 1단계: 추세 판단 (TREND DETECTION)

### 📈 상승 추세 조건 (UPTREND)
다음 조건을 **모두** 만족해야 상승 추세로 인정:

```python
# 가격 위치 조건
current_price > 20EMA
current_price > 120EMA

# 기울기 조건 (양수)
slope_20ema > 0
slope_120ema > 0
```

**기울기 계산 방법:**
```python
# 최근 N개 봉의 EMA 값 변화율로 기울기 판단
slope = (current_ema - previous_ema) / previous_ema * 100

# 예시: 최근 3개 봉 기준
slope_20ema = (ema20[0] - ema20[2]) / ema20[2] * 100
slope_120ema = (ema120[0] - ema120[2]) / ema120[2] * 100
```

### 📉 하락 추세 조건 (DOWNTREND)
다음 조건을 **모두** 만족해야 하락 추세로 인정:

```python
# 가격 위치 조건
current_price < 20EMA
current_price < 120EMA

# 기울기 조건 (음수)
slope_20ema < 0
slope_120ema < 0
```

### ⚠️ 비추세 구간 (NO TRADE ZONE)
위 조건을 만족하지 않으면 **거래하지 않음**
- 현재가가 EMA 사이에 있을 때
- 기울기가 모호할 때 (0 근처)

---

## 🔹 2단계: 진입 타이밍 (ENTRY TIMING)

### 📈 롱 진입 조건
```python
# 전제: 상승 추세 확인됨
if trend == "UPTREND":
    if RSI < 50:  # 눌림목 구간
        → LONG 진입
```

### 📉 숏 진입 조건
```python
# 전제: 하락 추세 확인됨
if trend == "DOWNTREND":
    if RSI > 50:  # 되돌림 구간
        → SHORT 진입
```

### 🚫 진입 금지 조건
- 추세가 확인되지 않은 상태
- 이미 포지션이 있는 상태 (동일 심볼)
- 가용 잔고 부족

---

## 🔹 3단계: 포지션 관리 (POSITION MANAGEMENT)

### 📦 포지션 사이징

#### `calc_buy_unit()` - 공통 모듈

> **위치:** `modules/module_common.py`

```python
from modules.module_common import calc_buy_unit
```

**함수 시그니처:**
```python
def calc_buy_unit(total_balance: float) -> int:
    """
    매수 단위를 계산하는 공통 함수
    - 잔고의 10%를 1회 진입 금액으로 계산
    - 최소 5 USDT 보장
    """
```

**사용 예시:**
```python
from modules.module_common import calc_buy_unit

# 잔고 1000 USDT
buy_unit = calc_buy_unit(1000)  # → 100 USDT

# 잔고 873 USDT
buy_unit = calc_buy_unit(873)   # → 87 USDT

# 추가매수 시: buy_unit * 2
```

---

### ✅ 익절 조건 (TAKE PROFIT)

| 구분 | 값 | 설명 |
|------|-----|------|
| 익절 기준 | +3% | 진입가 대비 |
| 방식 | 지정가 or 시장가 | TP 주문 |

```python
# 롱 포지션
take_profit_price = entry_price * 1.03

# 숏 포지션  
take_profit_price = entry_price * 0.97
```

**원칙:**
> "더 갈 수도 있음"은 전략 외 생각. 무조건 3%에서 청산.

---

### 📉 추가매수 조건 (AVERAGING DOWN)

| 구분 | 값 | 설명 |
|------|-----|------|
| 추가매수 횟수 | **1회 한정** | 무한 물타기 금지 |
| 추가매수 트리거 | 진입가 대비 -5% | 반대 방향 이동 시 |
| 추가매수 수량 | buy_unit × 2 | 기본 단위의 2배 |

```python
# 롱 포지션 추가매수 가격
avg_down_price = entry_price * 0.95

# 숏 포지션 추가매수 가격
avg_down_price = entry_price * 1.05
```

**추가매수 실행 전 체크:**
```python
def can_average_down(position, available_balance, buy_unit):
    """
    추가매수 가능 여부 체크
    """
    required_margin = buy_unit * 2  # 추가매수는 2배 물량
    
    if available_balance < required_margin:
        # 잔고 부족 → 즉시 시장가 손절
        return False, "FORCE_STOP_LOSS"
    
    if position.avg_down_count >= 1:
        # 이미 1회 추가매수 완료 → 손절 대기
        return False, "MAX_AVG_DOWN_REACHED"
    
    return True, "OK"
```

---

### ❌ 손절 조건 (STOP LOSS)

#### Case 1: 추가매수 불가 시 (잔고 부족)
```python
if available_balance < required_margin:
    → 즉시 시장가 손절 (MARKET SELL)
```

#### Case 2: 추가매수 후 추가 하락 시
```python
# 추가매수 완료 후
if position.unrealizedPnl <= -5%:  # percentage 기준
    → 시장가 손절 (MARKET SELL)
```

**손절 판단 기준:**
```python
def should_stop_loss(position) -> bool:
    """
    손절 조건 체크
    
    position.percentage: unrealizedPnl 퍼센트 (-17.79 같은 형태)
    """
    if position.avg_down_count >= 1:  # 추가매수 완료된 상태
        if position.percentage <= -5:  # -5% 이하
            return True
    return False
```

---

## 📋 전체 플로우 차트

```
┌─────────────────────────────────────────────────────────┐
│                    15분봉 신호 체크                      │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
                ┌───────────────────────┐
                │     추세 판단         │
                │ 가격 vs EMA + 기울기  │
                └───────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
       [UPTREND]       [DOWNTREND]     [NO TREND]
            │               │               │
            ▼               ▼               ▼
     RSI < 50 ?        RSI > 50 ?      ─────────
            │               │               │
            ▼               ▼               ▼
       LONG 진입        SHORT 진입      거래 안함
            │               │
            └───────┬───────┘
                    ▼
        ┌───────────────────────┐
        │   포지션 모니터링      │
        │   (익절/추가매수/손절) │
        └───────────────────────┘
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
+3% 도달        -5% 도달        손절 조건
    │               │               │
    ▼               ▼               ▼
 익절 청산      추가매수 실행    시장가 손절
                    │
                    ▼
            ┌─────────────────┐
            │ 잔고 충분?       │
            └─────────────────┘
                    │
            ┌───────┴───────┐
            ▼               ▼
          [YES]           [NO]
            │               │
            ▼               ▼
    추가매수 (2x)     즉시 손절
            │
            ▼
    ┌─────────────────┐
    │ PnL <= -5% ?    │
    └─────────────────┘
            │
    ┌───────┴───────┐
    ▼               ▼
  [YES]           [NO]
    │               │
    ▼               ▼
 손절 청산      모니터링 계속
```

---

## ⚙️ 상수 설정 (CONFIG)

```python
# === 전략 설정 ===
TIMEFRAME = "15m"
LEVERAGE = 3

# === EMA 설정 ===
EMA_MEDIUM = 20
EMA_SLOW = 120
SLOPE_PERIOD = 3  # 기울기 계산 기간

# === RSI 설정 ===
RSI_PERIOD = 14
RSI_LONG_THRESHOLD = 50   # 롱 진입: RSI < 50
RSI_SHORT_THRESHOLD = 50  # 숏 진입: RSI > 50

# === 익절/손절 설정 ===
TAKE_PROFIT_PERCENT = 3.0   # +3%
AVG_DOWN_TRIGGER = 5.0      # -5%에서 추가매수
STOP_LOSS_PERCENT = -5.0    # 추가매수 후 -5%에서 손절
MAX_AVG_DOWN_COUNT = 1      # 최대 추가매수 횟수

# === 포지션 사이징 ===
RISK_PERCENT = 0.1          # 1회 진입 시 잔고의 10%
AVG_DOWN_MULTIPLIER = 2     # 추가매수 시 기본 단위의 2배
```

---

## 📝 주요 원칙 요약

### 1. 욕심 제거 = 자동화
- 자동매매의 목적은 실력 대신이 아니라 **감정 차단**
- 익절 구간이 와도 "조금만 더..." 하지 않음

### 2. 손절 없는 전략은 필패
- ❌ 무한 물타기 금지
- ✅ 1회만 추가매수
- ✅ 이후 반드시 손절

### 3. RSI 단독 사용 금지
- RSI 30/80 꺾임 매매의 문제: 추세장에서 계속 안 꺾임
- RSI는 **추세 판단용 보조 지표**로만 사용

### 4. 추세 추종의 핵심
- 바닥/꼭지 맞추기 ❌
- **이미 방향 나온 곳에서 3%만 먹기** ✅

---

## 🚀 구현 체크리스트

- [ ] `calc_buy_unit()` 공통 함수 생성 (`modules/` 폴더)
- [ ] EMA 계산 모듈 확인/수정
- [ ] RSI 계산 모듈 확인/수정
- [ ] 추세 판단 로직 구현
- [ ] 진입 로직 구현
- [ ] 익절 로직 구현
- [ ] 추가매수 로직 구현
- [ ] 손절 로직 구현
- [ ] 포지션 상태 관리 (avg_down_count 추적)
- [ ] 백테스트
- [ ] 페이퍼 트레이딩

---

## 📅 버전 히스토리

| 버전 | 날짜 | 변경사항 |
|------|------|----------|
| v1.0 | 2026-01-20 | 초안 작성 |
