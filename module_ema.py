import pandas as pd

def calc_ema(data, window):
    """ 지수 이동평균(EMA)을 계산하는 함수 """
    return data.ewm(span=window, adjust=False).mean()

