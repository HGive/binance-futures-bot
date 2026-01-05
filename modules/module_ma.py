# modules/module_ma.py
import pandas as pd
import numpy as np

def calc_ma(df, period):
    """이동평균 계산"""
    return df['close'].rolling(window=period).mean()

def calc_ma_slope(df, period, lookback=5):
    """MA 기울기 계산 (lookback 기간 동안의 기울기)"""
    ma = calc_ma(df, period)
    
    # 최근 lookback 기간의 MA 값들
    recent_ma = ma.tail(lookback).values
    
    # 선형 회귀로 기울기 계산
    x = np.arange(len(recent_ma))
    slope = np.polyfit(x, recent_ma, 1)[0]
    
    return slope

def get_ma_signals(df):
    """MA 기반 신호 생성"""
    # MA40, MA120 계산
    ma40 = calc_ma(df, 40)
    ma120 = calc_ma(df, 120)
    
    # 기울기 계산 (최근 5개 봉 기준)
    ma40_slope = calc_ma_slope(df, 40, 5)
    ma120_slope = calc_ma_slope(df, 120, 5)
    
    # 현재 가격
    current_price = df['close'].iloc[-1]
    
    # 신호 생성
    signals = {
        'ma40': ma40.iloc[-1],
        'ma120': ma120.iloc[-1],
        'ma40_slope': ma40_slope,
        'ma120_slope': ma120_slope,
        'current_price': current_price,
        'price_above_ma40': current_price > ma40.iloc[-1],
        'price_above_ma120': current_price > ma120.iloc[-1],
        'ma40_above_ma120': ma40.iloc[-1] > ma120.iloc[-1],
        'ma40_slope_positive': ma40_slope > 0,
        'ma120_slope_positive': ma120_slope > 0,
    }
    
    return signals

