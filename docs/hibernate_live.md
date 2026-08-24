# HIBERNATE 실전 봇 — 운영 안내

```bash
poetry run python live/hibernate_bot.py --mode paper     # 주문 없이 신호만 (실서버 데이터)
poetry run python live/hibernate_bot.py --mode testnet   # 테스트넷에 실제 주문
poetry run python live/hibernate_bot.py --mode live --i-know
```

## 어떻게 도는가

| | |
|---|---|
| 데이터 | **항상 실서버 공개 API** (키 불필요). 주문만 testnet/live 로 갈린다 |
| 하루 1회 (UTC 00:02) | 일봉 갱신 → 보유 점검 → BTC 100일선 확인 → 스캔 → 시장가 진입 |
| 5분마다 | 손절 감시 + 걸어둔 익절 체결 확인 |
| 익절·손절 | 진입 직후 **OCO 두 쌍을 거래소에 건다** (1차 70% / 2배 30%, 손절가는 동일 -20%) |
| 코드 손절 | OCO 등록에 실패했을 때만 폴백. 그 사실을 로그와 상태파일(`exchange_stop`)에 남긴다 |
| 상태 | `logs/hibernate_<mode>.json` — 재시작해도 이어서 돈다 |

**익절을 지정가로 미리 거는 이유**: 백테스트는 "그날 고가가 목표를 넘으면 목표가에 체결"로 계산한다.
이건 지정가 주문을 걸어둔 것과 정확히 같은 뜻이다. 봇이 하루 한 번만 본다면 목표를 지나쳤다가
되돌아온 날을 놓치게 되어 백테스트보다 나빠진다.

**OCO 를 쓰는 이유**: 현물은 같은 수량을 익절·손절 두 주문에 따로 묶을 수 없지만,
OCO(One-Cancels-Other)는 예외로 한 쌍을 묶어준다. 이러면 **봇이 죽어도 손절이 산다**
(STRATEGY_RULES 3.5). 손절 스탑이 발동하면 그보다 2% 아래 지정가로 나가서 체결을 보장한다.
OCO 등록이 실패한 종목은 `exchange_stop: false` 로 남고, 그때만 봇이 5분마다 가격을 본다.

## 테스트넷 키 만들기

현물 테스트넷은 선물 테스트넷과 **키가 다르다**.

1. https://testnet.binance.vision 접속 → GitHub 계정으로 로그인
2. `Generate HMAC_SHA256 Key` → API Key / Secret 복사
3. `.env` 에 추가:

```
BINANCE_SPOT_TESTNET_KEY=...
BINANCE_SPOT_TESTNET_SECRET=...
```

(선물 테스트넷을 쓸 일이 생기면 https://testnet.binancefuture.com 에서 따로 받아
`BINANCE_TESTNET_KEY` / `BINANCE_TESTNET_SECRET` 로 넣는다. 예전 키의 `-2015` 오류는
키가 만료됐거나 실서버 키를 테스트넷에 쓴 경우다.)

## 테스트넷으로 확인할 것

현물 테스트넷에는 BTC·ETH·BNB·LTC·TRX·XRP 정도만 있다.
**HIBERNATE 신호가 뜰 종목이 없으므로 전략 검증은 못 하고, 주문 배관만 점검한다.**

```bash
# 신호를 무시하고 강제로 한 종목 사서 주문 경로 전체를 태운다
poetry run python live/hibernate_bot.py --mode testnet --force-entry BTC/USDT --force-usdt 50

# 확인할 것:
#   시장가 매수 체결가가 상태파일에 제대로 들어갔는가
#   수수료가 코인으로 빠진 뒤 실제 보유 수량으로 매도 주문이 나갔는가
#   OCO 두 쌍(1차 70% / 2배 30%)이 실제로 걸렸는가 — 상태파일 exchange_stop 이 true 인가
#   최소 주문금액·수량 단위에 안 걸리는가

# 5분 감시 루프를 붙여서 재시작 복구까지 본다
poetry run python live/hibernate_bot.py --mode testnet --fast 60

# 정리
poetry run python live/hibernate_bot.py --mode testnet --close-all
```

## 실전 전 관문 (STRATEGY_RULES.md 6절)

1. [ ] 페이퍼 모드 2주 — 신호가 백테스트와 같은 빈도로 뜨는가
2. [ ] 테스트넷 주문 배관 점검 — 위 항목 전부
3. [ ] 소액 실전 2주 (시드 5~10만원) — **실제 체결가가 백테스트 가정과 맞는가**
4. [ ] 맞으면 규모 확대. 안 맞으면 차이의 원인부터 규명

## 자동 정지 조건

- 실전 MDD 가 백테스트 MDD(-23.6%)의 1.5배(-35%)를 넘으면 정지
- 연속 손절 5회면 정지
- 정지는 상태파일의 `"halted": true` 로 건다 (신규 진입만 멈추고 보유는 계속 관리)

## 백테스트와 어긋날 수 있는 지점 (실전에서 확인할 것)

| 항목 | 백테스트 가정 | 실전 |
|---|---|---|
| 진입가 | 다음날 시가 | UTC 00:02 시장가 — 슬리피지 0.05% 로 잡아둠 |
| 익절 체결 | 목표가 정확히 | 지정가라 목표가 이상 (유리) |
| 손절 체결 | 손절가 정확히 | 5분마다 확인 → 급락 시 더 나쁘게 체결 (불리) |
| 유동성 | 무제한 | 소형 종목은 호가가 얇다. 시드가 커지면 문제가 된다 |
| 상장폐지 | 마지막 종가로 정리 | 거래정지되면 못 팔 수 있다 |
