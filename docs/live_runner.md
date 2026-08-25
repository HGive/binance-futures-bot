# 실전 러너 — 세 전략 돌리기

```bash
poetry run python live/runner.py --mode paper                # 주문 없이 신호만
poetry run python live/runner.py --mode testnet              # 테스트넷 실주문
poetry run python live/runner.py --mode testnet --once       # 1회 점검 후 종료
poetry run python live/runner.py --mode live --i-know        # 실전
```

## 구조

| 파일 | 역할 |
|---|---|
| `live/runner.py` | 여러 전략을 한 프로세스에서. 일봉을 공유한다 |
| `live/engine.py` | 범용 엔진 — 진입/청산/주문/상태복구/AI게이트 |
| `live/adapters.py` | **전략별 차이만** — 신호, 청산 계획, 매일 관리 |
| `live/sizing.py` | 최소 주문금액 검사 |
| `live/briefing.py` | 아침 브리핑 |
| `live/ai_gate.py` | AI 개입 (브레이크 전용) |
| `live/scalp_collector.py` | 스캘핑용 실시간 호가·체결 수집 |

엔진은 하나다. 전략마다 봇을 따로 만들면 백테스트와 갈린다 (STRATEGY_RULES 3.7).
실제로 그렇게 갈려서 HIBERNATE 봇이 BTC 를 매수 후보로 올린 적이 있다.

## 전략별 동작 차이

| | HIBERNATE | SURFER |
|---|---|---|
| 시장 | 현물 1배 롱 | 현물 1배 롱 |
| 진입 | 슈팅 이력 + 90일+ 조용 + 바닥권 | 상승추세 + StochRSI K<25 |
| 시장 필터 | BTC 100일선 위 | 알트 60일 중앙값>0 **&** BTC 추세성>0.2 |
| 비중 | 10% × 8칸 | 3% × 15칸 |
| 익절 | 종목별 목표 70% + 2배 30% | +20% 에서 50% |
| 손절 | -20% 고정 | ATR×4 (최대 -20%), 부분익절 후 트레일링 |
| 거래소 주문 | **OCO** (익절+손절 한 쌍) | 손절만 걸고 **매일 갱신** |

SURFER 는 트레일링이 매일 움직이므로 OCO 로 미리 못 건다.
대신 손절 주문을 매일 취소·재등록한다. 봇이 죽어도 마지막 손절은 살아 있다.

## 시드 — 최소 주문금액이 먼저 막는다

바이낸스 현물 USDT 페어 484종 중 **455종이 최소 5 USDT**.
쪼개서 파는 전략은 **파는 쪽이 먼저 막힌다.**

| | 계산 | 필요 시드 |
|---|---|---|
| HIBERNATE 매수 | 10% × E ≥ 5.75 | 58 USDT |
| **HIBERNATE 2차 익절** (포지션의 30%) | 30% × 10% × E ≥ 5.75 | **192 USDT** |
| SURFER 매수 | 3% × E ≥ 5.75 | 192 USDT |
| **SURFER 부분익절** (포지션의 50%) | 50% × 3% × E ≥ 5.75 | **383 USDT** |

```bash
poetry run python live/sizing.py --equity 44
```

**시드가 모자라면 엔진이 신규 진입을 거부한다.** 조용히 다르게 돌리지 않는다 —
칸 수나 비중을 임의로 바꾸면 그건 검증한 전략이 아니다.

## 테스트넷

테스트넷은 가짜 돈이라 시드 제약이 없다. 여기서 **주문 배관**만 점검한다.
(현물 테스트넷에는 BTC·ETH·BNB·LTC·TRX·XRP 정도만 있어서 전략 신호는 거의 안 뜬다.)

### 키 만들기

1. https://testnet.binance.vision → GitHub 로그인
2. `Generate HMAC_SHA256 Key`
3. `.env` 에 추가:
```
BINANCE_SPOT_TESTNET_KEY=...
BINANCE_SPOT_TESTNET_SECRET=...
```

### 점검 순서

```bash
# 1) 신호 무시하고 강제 매수 → 주문 경로 전체를 태운다
poetry run python live/runner.py --mode testnet --force-entry BTC/USDT \
    --force-strategy hibernate --force-usdt 50
poetry run python live/runner.py --mode testnet --force-entry ETH/USDT \
    --force-strategy surfer --force-usdt 50

# 2) 확인할 것 (logs/hibernate_testnet.json, logs/surfer_testnet.json)
#    - 시장가 체결가가 상태파일에 제대로 들어갔는가
#    - 수수료가 코인으로 빠진 뒤 **실제 보유 수량**으로 매도 주문이 나갔는가
#    - HIBERNATE: OCO 두 쌍이 걸렸는가 (exchange_stop == true)
#    - SURFER: 손절 주문이 걸렸는가, 다음날 트레일링으로 갱신되는가
#    - 최소 주문금액·수량 단위에 안 걸리는가

# 3) 루프를 돌려 재시작 복구까지 본다
poetry run python live/runner.py --mode testnet --fast 60
#    도중에 Ctrl+C → 다시 실행 → 보유가 그대로 복원되는가

# 4) 정리
poetry run python live/runner.py --mode testnet --close-all
```

## 실전 전 관문 (STRATEGY_RULES.md 6절)

1. [ ] 페이퍼 2주 — 신호 빈도가 백테스트와 비슷한가
2. [ ] 테스트넷 배관 점검 — 위 전부
3. [ ] 시드 확보 — HIBERNATE 최소 192 USDT (권장 300+)
4. [ ] 소액 실전 2주 — **실제 체결가가 백테스트 가정과 맞는가**
5. [ ] SURFER 는 아직 배포 기준 미달 (최근 4년 PF 0.92) — **페이퍼만**

## 맥북에서 돌리기

측정치: **하루 1회 66초 + 5분마다 1초 미만, RAM 350MB.** 평균 CPU 0.03%.

```bash
# 백그라운드로
nohup poetry run python live/runner.py --mode testnet > logs/runner.out 2>&1 &

# 확인
tail -f logs/runner_testnet.log
```

맥북이 잠들면 멈춘다. 계속 돌리려면 `caffeinate` 를 쓴다:
```bash
nohup caffeinate -i poetry run python live/runner.py --mode testnet > logs/runner.out 2>&1 &
```

## 백테스트와 어긋날 수 있는 지점

| 항목 | 백테스트 | 실전 |
|---|---|---|
| 진입가 | 다음날 시가 | UTC 00:02 시장가 (슬리피지 0.05% 로 잡아둠) |
| 익절 | 목표가 정확히 | 지정가라 목표가 이상 (유리) |
| 손절 | 손절가 정확히 | 스탑 발동 후 2% 아래 지정가 (불리) |
| 유동성 | 무제한 | 소형 종목은 호가가 얇다 |
| 상장폐지 | 마지막 종가로 정리 | 거래정지되면 못 팔 수 있다 |
