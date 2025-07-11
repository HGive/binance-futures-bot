from flask import Flask, render_template, jsonify, request
import subprocess
import threading
import time
import json
import os
import psutil
from datetime import datetime
import logging
from config import exchange
import asyncio
from strategies.hour_3p_strategy import Hour3PStrategy

app = Flask(__name__)

# 전역 변수
bot_process = None
bot_running = False
bot_thread = None
trading_config = {
    "rsi_threshold_30": 0.9,
    "rsi_threshold_40": 0.5,
    "buy_amount_percent": 0.1,
    "take_profit_percent": 0.03,
    "leverage": 3
}

# 로그 파일 경로
LOG_FILE = "hour_3p_strategy.log"

class TradingBot:
    def __init__(self):
        self.running = False
        self.strategies = []
        
    async def run_bot(self):
        try:
            await exchange.load_markets()
            symbols = ["CHR/USDT:USDT", "CRV/USDT:USDT", "AR/USDT:USDT"]
            self.strategies = [Hour3PStrategy(exchange, symbol, trading_config["leverage"]) for symbol in symbols]
            
            for s in self.strategies:
                await s.setup()
            
            logging.info("=== Bot started via web interface ===")
            
            while self.running:
                for s in self.strategies:
                    await s.run_once()
                await asyncio.sleep(3600)  # 1시간 대기
                
        except Exception as e:
            logging.error(f"Bot error: {e}")
            self.running = False

bot_instance = TradingBot()

def run_bot_async():
    """비동기 봇을 별도 스레드에서 실행"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_instance.run_bot())

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/status')
def get_status():
    """봇 상태 조회"""
    return jsonify({
        'bot_running': bot_instance.running,
        'config': trading_config
    })

@app.route('/api/start', methods=['POST'])
def start_bot():
    """봇 시작"""
    global bot_thread
    
    if not bot_instance.running:
        bot_instance.running = True
        bot_thread = threading.Thread(target=run_bot_async)
        bot_thread.daemon = True
        bot_thread.start()
        return jsonify({'status': 'success', 'message': 'Bot started'})
    else:
        return jsonify({'status': 'error', 'message': 'Bot already running'})

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    """봇 중지"""
    if bot_instance.running:
        bot_instance.running = False
        return jsonify({'status': 'success', 'message': 'Bot stopped'})
    else:
        return jsonify({'status': 'error', 'message': 'Bot not running'})

@app.route('/api/logs')
def get_logs():
    """최근 로그 조회"""
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            recent_logs = lines[-50:] if len(lines) > 50 else lines
            return jsonify({'logs': recent_logs})
    except FileNotFoundError:
        return jsonify({'logs': []})

@app.route('/api/config', methods=['GET', 'POST'])
def config():
    """설정 조회/수정"""
    global trading_config
    
    if request.method == 'POST':
        data = request.json
        trading_config.update(data)
        return jsonify({'status': 'success', 'config': trading_config})
    else:
        return jsonify(trading_config)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 