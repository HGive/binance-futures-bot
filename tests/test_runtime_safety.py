import asyncio
from unittest.mock import AsyncMock
import pytest
from live.swing_bot import SwingBot


def bot(tmp_path):
    b = SwingBot('testnet', capital=1000, reserved={'BTC/USDT:USDT'})
    b.st = {'owned': [], 'owned_ever': ['AAA/USDT:USDT', 'BTC/USDT:USDT']}
    b.state_path = str(tmp_path / 'state.json')
    b.ex = type('Fake', (), {})()
    b.ex.markets = {'AAA/USDT:USDT': {}}
    b.ex.market = lambda sym: {'id': sym.split('/')[0] + 'USDT'}
    b.ex.fetch_open_orders = AsyncMock(return_value=[{'id': 'buy', 'symbol': 'AAA/USDT:USDT'}])
    b.ex.fetch_positions = AsyncMock(return_value=[])
    b.ex.cancel_order = AsyncMock()
    b.ex.fapiPrivateGetOpenAlgoOrders = AsyncMock(return_value=[{'symbol': 'AAAUSDT', 'algoId': 5}])
    b.ex.fapiPrivateDeleteAlgoOrder = AsyncMock()
    b.positions = AsyncMock(return_value={})
    b.save = lambda: None
    return b


def test_closed_position_cancels_orders_before_forgetting(tmp_path):
    b = bot(tmp_path)
    asyncio.run(b.cleanup_closed({}))
    b.ex.cancel_order.assert_awaited_once_with('buy', 'AAA/USDT:USDT')
    b.ex.fapiPrivateDeleteAlgoOrder.assert_awaited_once_with({'algoId': '5'})


def test_cleanup_failure_prevents_forgetting(tmp_path):
    b = bot(tmp_path)
    b.st['owned'] = ['AAA/USDT:USDT']
    b.ex.cancel_order.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError):
        asyncio.run(b.cleanup_closed({}))
    assert 'AAA/USDT:USDT' in b.st['owned']


def test_cleanup_preserves_open_position_and_foreign_symbols(tmp_path):
    b = bot(tmp_path)
    asyncio.run(b.cleanup_closed({'AAA/USDT:USDT': {'qty': 1}}))
    b.ex.cancel_order.assert_not_awaited()


def test_expired_add_is_not_rearmed(tmp_path):
    b = bot(tmp_path)
    b.ex.milliseconds = lambda: 1000000
    b.st['add_deadlines'] = {'AAA/USDT:USDT': 999999}
    assert b.add_deadline('AAA/USDT:USDT') == 999999
    assert b.add_deadline('AAA/USDT:USDT') < b.ex.milliseconds()


def test_cancel_fill_race_keeps_stop_and_restores_owner(tmp_path):
    b = bot(tmp_path)
    b.ex.fetch_positions.return_value = [{'symbol': 'AAA/USDT:USDT', 'contracts': 2}]
    with pytest.raises(RuntimeError, match='체결'):
        asyncio.run(b.cleanup_closed({}))
    assert 'AAA/USDT:USDT' in b.st['owned']
    b.ex.fapiPrivateDeleteAlgoOrder.assert_not_awaited()


def order_bot(tmp_path):
    b = bot(tmp_path)
    b.ex.milliseconds = lambda: 1000000
    b.ex.amount_to_precision = lambda sym, amount: str(round(amount, 5))
    b.ex.price_to_precision = lambda sym, price: str(round(price, 5))
    b.ex.create_order = AsyncMock(return_value={'id': 'new'})
    b.ex.fetch_open_orders.return_value = []
    b.ex.fapiPrivateGetOpenAlgoOrders.return_value = []
    return b


def test_expired_add_cancelled_and_not_recreated(tmp_path):
    b = order_bot(tmp_path)
    b.st['add_deadlines'] = {'AAA/USDT:USDT': 999999}
    b.ex.fetch_open_orders.return_value = [{'id': 'old', 'side': 'buy', 'type': 'limit',
                                          'price': 85, 'remaining': 1}]
    asyncio.run(b.ensure_orders('AAA/USDT:USDT', {'avg': 100, 'qty': 0.5}, 100))
    b.ex.cancel_order.assert_awaited_once()
    assert all(c.args[2] != 'buy' for c in b.ex.create_order.await_args_list)


def test_add_uses_exchange_expiry_and_protection_survives(tmp_path):
    b = order_bot(tmp_path)
    asyncio.run(b.ensure_orders('AAA/USDT:USDT', {'avg': 100, 'qty': 0.5}, 100))
    calls = b.ex.create_order.await_args_list
    buy = next(c for c in calls if c.args[2] == 'buy')
    assert buy.args[-1]['timeInForce'] == 'GTD'
    assert buy.args[-1]['goodTillDate'] == 1000000 + 86400000
    stop = next(c for c in calls if c.args[1] == 'STOP_MARKET')
    assert stop.args[-1]['reduceOnly'] is True
    assert 'goodTillDate' not in stop.args[-1]


def test_post_timeout_is_never_retried():
    import ccxt
    import config
    e = type('Fake', (), {})()
    e.request = AsyncMock(side_effect=ccxt.RequestTimeout('offline'))
    original = e.request
    config.resilient_reads(e)
    with pytest.raises(ccxt.RequestTimeout):
        asyncio.run(e.request('order', 'public', 'POST'))
    assert original.await_count == 1


def test_nonce_read_resyncs_and_recovers():
    import ccxt
    import config
    e = type('Fake', (), {})()
    e.request = AsyncMock(side_effect=[ccxt.InvalidNonce('clock'), {'ok': True}])
    e.load_time_difference = AsyncMock()
    original = e.request
    config.resilient_reads(e)
    assert asyncio.run(e.request('account', 'fapiPrivate', 'GET')) == {'ok': True}
    assert original.await_count == 2
    assert e.load_time_difference.await_count == 2


def test_partial_fill_refreshes_full_stop_quantity(tmp_path):
    b = order_bot(tmp_path)
    b.ex.fapiPrivateGetOpenAlgoOrders.return_value = [
        {'symbol': 'AAAUSDT', 'algoId': 'old', 'orderType': 'STOP_MARKET',
         'side': 'SELL', 'reduceOnly': True, 'quantity': '0.5', 'triggerPrice': '50'}]
    b.st['add_deadlines'] = {'AAA/USDT:USDT': 999999}
    asyncio.run(b.ensure_orders('AAA/USDT:USDT', {'avg': 100, 'qty': 0.8}, 100))
    stop = next(c for c in b.ex.create_order.await_args_list if c.args[1] == 'STOP_MARKET')
    assert stop.args[3] == 0.8
    b.ex.fapiPrivateDeleteAlgoOrder.assert_awaited_once_with({'algoId': 'old'})


def test_run_lock_prevents_overlap_and_releases(tmp_path):
    import config
    path = str(tmp_path / 'run.lock')
    first = config.acquire_run_lock(path)
    assert config.acquire_run_lock(path) is None
    first.close()
    second = config.acquire_run_lock(path)
    assert second is not None
    second.close()


def test_trend_maintenance_keeps_existing_full_stop(tmp_path):
    import json
    from live.trend_bot import TrendBot
    from strategies import trend_vt as S
    b = TrendBot('testnet')
    b.journal = str(tmp_path / 'trend.jsonl')
    with open(b.journal, 'w') as f:
        f.write(json.dumps({'orders': {s: {'close': 100, 'vol_d': 0.02} for s in S.SYMBOLS}}))
    b.account = AsyncMock(return_value=(1000, {s: 1 for s in S.SYMBOLS}))
    b.ex = type('Fake', (), {})()
    b.ex.market = lambda s: {'id': s}
    b.ex.fapiPrivateGetOpenAlgoOrders = AsyncMock(return_value=[
        {'symbol': s, 'orderType': 'STOP_MARKET', 'side': 'SELL', 'quantity': '1', 'reduceOnly': True}
        for s in S.SYMBOLS])
    b.refresh_stop = AsyncMock(return_value=True)
    asyncio.run(b.maintenance())
    b.refresh_stop.assert_not_awaited()
    b.account.assert_awaited_once_with({})
    b.ex.fapiPrivateGetOpenAlgoOrders.return_value = []
    asyncio.run(b.maintenance())
    assert b.refresh_stop.await_count == len(S.SYMBOLS)


@pytest.mark.parametrize('script', ['run_swing_dca.sh', 'run_trend_vt.sh'])
def test_wrapper_propagates_python_failure(tmp_path, script):
    import os
    import subprocess
    from pathlib import Path
    python = tmp_path / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/bash\nexit 7\n')
    python.chmod(0o755)
    source = (Path(__file__).resolve().parents[1] / 'scripts' / script).read_text()
    source = source.replace('P=/Users/gimhyeongjun/Desktop/DevProjects/binance-futures-bot',
                            'P="' + str(tmp_path) + '"')
    wrapper = tmp_path / 'wrapper.sh'
    wrapper.write_text(source)
    result = subprocess.run(['bash', str(wrapper)], env={**os.environ, 'HOME': str(tmp_path)})
    assert result.returncode == 7


def test_stop_budget_holds_before_and_after_planned_add():
    from live.swing_bot import capped_stop, KILL_LIMIT
    for avg in (0.005, 1, 100, 5000):
        for budget in (10, 100, 1000):
            qty = budget / 2 / avg
            add_price = avg * 0.85
            add_qty = budget / 2 / add_price
            for planned in (0, add_qty):
                stop = capped_stop(avg, qty, budget, add_price, planned)
                assert (avg - stop) * qty <= budget * KILL_LIMIT + 1e-9
                if planned:
                    cost = avg * qty + add_price * planned
                    assert cost - stop * (qty + planned) <= budget * KILL_LIMIT + 1e-9
                assert stop < avg
