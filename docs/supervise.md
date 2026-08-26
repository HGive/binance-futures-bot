# 한 시간에 한 번, Claude 가 감독한다

## 무엇인가

`live/supervise.sh` 를 OS 크론에 걸어두면, 매 시각 Claude 가
시장 브리핑 + 봇 상태 + 현재 게이트를 읽고 **브레이크를 밟을지만** 판단한다.

세션 안의 크론이 아니라 **맥의 crontab** 이다. 대화창을 닫아도 계속 돈다.

## 왜 브레이크만인가

진입은 백테스트로 검증한 규칙이 정한다. Claude 는 그걸 못 이긴다 —
검증받지 않은 판단이기 때문이다. 대신 규칙이 **볼 수 없는 것**은 있다:

  거래소 출금 중단 / 스테이블 디페그 / 보유 종목 상장폐지 예고 /
  봇 자체의 이상 (주문 반복 실패, 포지션 불일치, 데이터 정체)

이럴 때만 조인다. 그 외에는 아무것도 하지 않는 것이 정답이다.
"BTC 가 빠졌다", "변동성이 크다" — 이건 이미 규칙 안에 들어있다.

허용된 동작은 두 가지뿐이다.

    신규 진입 금지 (--block)
    비중 축소     (--size 0.5)

풀거나, 더 사거나, 손절을 늦추는 것은 코드 수준에서 막혀 있다
(`ai_gate.write` 가 `size_mult` 를 0~1.0 으로 자른다).

## 설치

    crontab -e

한 줄 추가:

    7 * * * * /Users/gimhyeongjun/Desktop/DevProjects/binance-futures-bot/live/supervise.sh

`:00` 이 아니라 `:07` 인 이유는 봇의 정각 작업과 겹치지 않게 하려는 것이다.

모드를 바꾸려면:

    7 * * * * MODE=live /Users/.../live/supervise.sh

## 확인

    tail -50 logs/supervise.log        # 매 시각 판단 기록
    cat logs/ai_gate.json              # 지금 걸린 게이트
    tail logs/ai_gate_audit.jsonl      # 게이트 이력 (나중에 이 판단이 옳았는지 잰다)

게이트는 최대 7일이면 자동으로 풀린다. 잊고 방치되는 브레이크는 없다.

## 손으로 밟기

    .venv/bin/python live/ai_gate.py set --strategy surfer --block \
        --reason "바이낸스 출금 중단 공지" --days 2
    .venv/bin/python live/ai_gate.py clear --strategy surfer
