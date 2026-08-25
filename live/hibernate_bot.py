#!/usr/bin/env python3
"""
[폐기됨] live/runner.py 로 통합됐다.

같은 규칙을 두 군데에 구현하면 반드시 갈라진다 (STRATEGY_RULES 3.7).
실제로 이 파일과 백테스트가 갈려서 BTC 를 매수 후보로 올린 적이 있다.
엔진은 live/engine.py 하나, 전략별 차이는 live/adapters.py 에만 둔다.

  poetry run python live/runner.py --mode paper --strategies hibernate
"""
import sys

print(__doc__)
sys.exit(1)
