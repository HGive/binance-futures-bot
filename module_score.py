import numpy as np

def calculate_score(df, rsi, stoch_rsi, ema_10, ema_20, ema_50):
    long_score = 0
    short_score = 0
    
    # RSI 점수 계산
    if rsi >= 90:
        long_score += 30
    elif rsi >= 80 :
        long_score += 25
    elif rsi >= 70 : 
        long_score += 20
    elif rsi <= 10 :
        short_score += 30
    elif rsi <= 20 :
        short_score += 25
    elif rsi <= 30 :
        short_score += 20

    
    # Stoch RSI 점수 계산
    k, d = stoch_rsi['K'].iloc[-1], stoch_rsi['D'].iloc[-1]
    if k <= 30 and d <= 30:
        long_score += 10
        if k <= 20 and d <= 20:
            long_score += 10
    elif k >= 70 and d >= 70:
        short_score += 10
        if k >= 80 and d >= 80:
            short_score += 10
    
    # EMA 점수 계산
    current_price = df['close'].iloc[-1]
    if ema_50 < ema_20 < ema_10 < current_price:
        long_score += 10
    elif ema_50 > ema_20 > ema_10 > current_price:
        short_score += 10
    
    # 최근 40개 봉 기준 점수 계산
    last_40_high = df['high'].iloc[-40:].max()
    last_40_close = df['close'].iloc[-40:].max()
    middle_price = (last_40_high + last_40_close) / 2
    price_diff_percent = (current_price - middle_price) / middle_price * 100
    
    if price_diff_percent <= -5:
        long_score += abs(int(price_diff_percent)) - 4
    elif price_diff_percent >= 5:
        short_score += int(price_diff_percent) - 4
    
    return long_score, short_score