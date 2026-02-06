# config.py
from dotenv import load_dotenv
import os, ccxt.pro as ccxt, logging, sys
from pytz import timezone
from datetime import datetime

load_dotenv()

# === 로깅 설정 ===
tz = timezone("Asia/Seoul")
def timetz(*args): return datetime.now(tz).timetuple()
logging.Formatter.converter = timetz

log_filename = os.environ.get("LOG_FILENAME", "strategy1.log")
log_path = os.path.join("logs", log_filename)
os.makedirs("logs", exist_ok=True)

fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
fmt.converter = timetz

root = logging.getLogger()
root.setLevel(logging.INFO)
root.handlers.clear()

file_handler = logging.FileHandler(log_path, encoding="utf-8")
file_handler.setFormatter(fmt)
root.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(fmt)
root.addHandler(stream_handler)

# === Exchange 설정 (Binance USDⓈ-M Futures) ===
exchange = ccxt.binanceusdm({
    "apiKey": os.environ["BINANCE_API_KEY"],
    "secret": os.environ["BINANCE_API_SECRET"],
    "enableRateLimit": True,
    "options": {
        "adjustForTimeDifference": True,
        "fetchCurrencies": False,
    }
})
logging.info("[PRODUCTION MODE]")
