# modules/module_common.py
import math


def calc_buy_unit(total_balance: float) -> int:
    """
    매수 단위를 계산하는 공통 함수
    
    Args:
        total_balance: 사용 가능한 잔고 (USDT)
    
    Returns:
        buy_unit: 1회 진입 금액 (USDT, 정수)
    """
    base_amount = total_balance / 10  # 잔고의 10%
    buy_unit = math.floor(base_amount)
    return max(buy_unit, 5)  # 최소 5 USDT 보장
