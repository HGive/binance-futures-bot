# config.py
from dotenv import load_dotenv
import os, ccxt.pro as ccxt, logging
from pytz import timezone
from datetime import datetime

tz = timezone("Asia/Seoul")
def timetz(*args): return datetime.now(tz).timetuple()
logging.Formatter.converter = timetz

# 로그 디렉토리는 deploy.sh에서 생성됨

load_dotenv()

# 환경변수에서 로그 파일명 가져오기 (기본값: hour_3p_strategy.log)
log_filename = os.environ.get("LOG_FILENAME", "hour_3p_strategy.log")
log_path = os.path.join("logs", log_filename)

logging.basicConfig(
    filename=log_path,
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8"  # 한글 로그 깨짐 방지
)

# exchange = None
binance_exchange = ccxt.binance({"apiKey": os.environ["BINANCE_API_KEY"], "secret": os.environ["BINANCE_API_SECRET"], "enableRateLimit": True, "options": {"defaultType": "future", "adjustForTimeDifference": True}})
bybit_exchange = ccxt.bybit({"apiKey": os.environ["BYBIT_API_KEY"], "secret": os.environ["BYBIT_API_SECRET"], "enableRateLimit": True, "options": {"defaultType": "linear", "adjustForTimeDifference": True}})
