# TREND_VT 자동 실행 (macOS launchd)

## 왜 이렇게 되어 있나

프로젝트가 `~/Desktop` 아래에 있다. macOS 는 Desktop/Documents/Downloads 를
TCC 로 보호해서 **launchd 에이전트가 그 안에 파일을 쓰지 못한다**
(`Operation not permitted`). 코드와 `.env` 읽기는 된다.

그래서 로그와 상태 파일만 보호되지 않는 곳으로 보낸다:

    ~/Library/Application Support/trendvt/logs/

`live/trend_bot.py` 의 `TREND_LOG_DIR` 환경변수가 그 경로를 받는다.
**킬스위치용 자본 고점과 정지 플래그가 거기 있으므로 지우면 안 된다.**

프로젝트를 `~/Desktop` 밖으로 옮기면 이 우회는 필요 없어진다.

## 구성

| 파일 | 위치 |
|---|---|
| 진입 스크립트 | `~/Library/Application Support/trendvt/run.sh` (사본: `scripts/run_trend_vt.sh`) |
| launchd 설정 | `~/Library/LaunchAgents/com.algorijun.trendvt.plist` |
| 로그·상태 | `~/Library/Application Support/trendvt/logs/` |

실행 시각: 매일 **09:05 / 09:40 / 11:00 / 13:30 (KST)**.
일봉 마감이 UTC 00:00 = KST 09:00 이고, 봇의 `CATCHUP_HOURS=6` 안에서만 동작한다.
여러 번 불러도 안전하다 — 목표와 보유가 같으면 밴드 규칙이 거래를 막는다.
맥이 자고 있었거나 꺼져 있었어도 깨어난 뒤 한 번 돌면 따라잡는다.

## 자주 쓰는 명령

```bash
launchctl list | grep trendvt                      # 등록 확인
launchctl start com.algorijun.trendvt              # 지금 한 번 실행
tail -f ~/Library/Application\ Support/trendvt/logs/cron.log   # 로그 보기
launchctl unload ~/Library/LaunchAgents/com.algorijun.trendvt.plist   # 중지
launchctl load   ~/Library/LaunchAgents/com.algorijun.trendvt.plist   # 재개
```

## 실전 전환

`plist` 의 `TREND_MODE` 를 `testnet` → `live` 로 바꾸고 재로드한다.
**전용 서브계정**이어야 한다 (봇이 다른 포지션을 발견하면 시작을 거부한다).
규칙상 데모 1주 → 소액 실전 2주를 거친다 (STRATEGY_RULES 6).

---

# SWING_DCA 자동 실행

같은 TCC 우회를 쓴다. 로그·상태는 `~/Library/Application Support/swingdca/logs/`,
환경변수는 `SWING_LOG_DIR`.

실행 시각: 매일 **09:10 / 13:10 / 18:10 / 23:10 (KST)**.
익절·물타기·손절 주문은 거래소에 걸려 있으므로, 봇은 체결 확인과 신규 진입만 한다.
그래서 자주 돌 필요가 없다.

```bash
launchctl list | grep swingdca
tail -f ~/Library/Application\ Support/swingdca/logs/cron.log
```

## 지금 paper 모드인 이유

데모 계좌는 TREND_VT 가 쓰고 있다(BTC·ETH 롱). SWING_DCA 는 계정 전체를
자기 자본으로 보고 보유분에 익절·손절 주문을 걸기 때문에, 같은 계정에서 돌리면
**서로의 주문을 지운다.** 봇의 가드가 이를 감지하고 시작을 거부한다.

데모 실주문으로 넘기려면 **바이낸스 테스트넷 계정을 하나 더** 만들고
`.env` 에 별도 키를 넣은 뒤 plist 의 `SWING_MODE` 를 `testnet` 으로 바꾼다.
