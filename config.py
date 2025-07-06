# config.py
from dotenv import load_dotenv
import os, ccxt.pro as ccxt, logging
from pytz import timezone
from datetime import datetime

tz = timezone("Asia/Seoul")
def timetz(*args): return datetime.now(tz).timetuple()
logging.Formatter.converter = timetz
logging.basicConfig(filename="hour_3p_strategy.log", format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")

load_dotenv()
# exchange = None
exchange = ccxt.binance({"apiKey": os.environ["BINANCE_API_KEY"], "secret": os.environ["BINANCE_API_SECRET"], "enableRateLimit": True, "options": {"defaultType": "future", "adjustForTimeDifference": True}})
