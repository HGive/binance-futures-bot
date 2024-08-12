import ccxt
import os

from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
import datetime
import pprint

load_dotenv()

app = Flask(__name__)

api_key = os.environ['BINANCE_API_KEY']
secret_key = os.environ['BINANCE_API_SECRET']
symbol = 'CHRUSDT'

@app.route('/data', methods=['POST'])
def future_trades():

    exchange = ccxt.binanceusdm({
        'apiKey': api_key,
        'secret': secret_key,
        'enableRateLimit': True,  # API 요청 속도 제한을 활성화
        'options': {
            'defaultType': 'future'  # 선물 거래로 설정
        }
    })

    try:

        start_time = '2023-08-06'
        end_time = '2099-12-31'

        data = request.get_json()
        if data['start_time']:
            start_time = data['start_time']

        if data['end_time']:
            end_time = data['end_time']

        #한국시간
        #kst_offset = datetime.timedelta(hours=9)

        # 시작 시간 밀리초로 변환
        start_datetime = datetime.datetime.strptime(start_time, "%Y-%m-%d")
        start_timestamp = int(start_datetime.timestamp() * 1000)

        # 종료 시간 밀리초로 변환 (하루의 끝을 포함하기 위해 하루의 마지막 순간으로 설정)
        end_datetime = datetime.datetime.strptime(end_time, "%Y-%m-%d")
        end_datetime = end_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
        end_timestamp = int(end_datetime.timestamp() * 1000)

        # 손익 내역 가져오기
        income_history = exchange.fapiPrivateGetIncome({
            'symbol': symbol,
            'incomeType': 'REALIZED_PNL',
            'startTime': start_timestamp,
            'endTime': end_timestamp,
            'limit': 1000  # 최대 1000개 항목 가져오기
        })

        #total_pnl = sum(float(income['income']) for income in income_history)

        # 해당 기간의 첫 번째 거래에서의 포지션 가치 계산
        #first_trade = exchange.fapiPrivateGetUserTrades({
        #    'symbol': 'ETHUSDT',
        #    'startTime': start_timestamp,
        #    'limit': 1
        #})

        return jsonify(income_history)
        #if first_trade:
        #    initial_position_value = float(first_trade[0]['price']) * float(first_trade[0]['qty'])
        #else:
        #    print("해당 기간에 거래 내역이 없습니다.")

        # 손익률 계산
        #pnl_percentage = (total_pnl / initial_position_value) * 100
#
        #print(f"시작 날짜: {start_time}")
        #print(f"종료 날짜: {end_time}")
        #print(f"총 손익: {total_pnl:.2f} USDT")
        #print(f"손익률: {pnl_percentage:.2f}%")

    except ccxt.NetworkError as e:
        print(f"네트워크 오류 발생: {str(e)}")
    except ccxt.ExchangeError as e:
        print(f"거래소 오류 발생: {str(e)}")
    except Exception as e:
        print(f"예상치 못한 오류 발생: {str(e)}")

@app.route('/fee', methods=['POST'])
def future_fee():
    exchange = ccxt.binanceusdm({
        'apiKey': api_key,
        'secret': secret_key,
        'enableRateLimit': True,  # API 요청 속도 제한을 활성화
        'options': {
            'defaultType': 'future'  # 선물 거래로 설정
        }
    })

    try:

        start_time = '2023-08-06'
        end_time = '2099-12-31'

        data = request.get_json()
        if data['start_time']:
            start_time = data['start_time']

        if data['end_time']:
            end_time = data['end_time']

        #한국시간
        #kst_offset = datetime.timedelta(hours=9)

        # 시작 시간 밀리초로 변환
        start_datetime = datetime.datetime.strptime(start_time, "%Y-%m-%d")
        start_timestamp = int(start_datetime.timestamp() * 1000)

        # 종료 시간 밀리초로 변환 (하루의 끝을 포함하기 위해 하루의 마지막 순간으로 설정)
        end_datetime = datetime.datetime.strptime(end_time, "%Y-%m-%d")
        end_datetime = end_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
        end_timestamp = int(end_datetime.timestamp() * 1000)

        # 손익 내역 가져오기
        income_history = exchange.fapiPrivateGetIncome({
            'symbol': symbol,
            'incomeType': 'COMMISSION',
            'startTime': start_timestamp,
            'endTime': end_timestamp,
            'limit': 1000  # 최대 1000개 항목 가져오기
        })

        return jsonify(income_history)

    except ccxt.NetworkError as e:
        print(f"네트워크 오류 발생: {str(e)}")
    except ccxt.ExchangeError as e:
        print(f"거래소 오류 발생: {str(e)}")
    except Exception as e:
        print(f"예상치 못한 오류 발생: {str(e)}")
@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)