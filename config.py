# config.py
from dotenv import load_dotenv
import os, ccxt.pro as ccxt, logging, sys
from pytz import timezone
from datetime import datetime

load_dotenv()

# === TEST_NET 모드 체크 ===
IS_TESTNET = os.environ.get("TEST_NET", "FALSE").upper() == "TRUE"

# === 로깅 설정 ===
tz = timezone("Asia/Seoul")
def timetz(*args): return datetime.now(tz).timetuple()
logging.Formatter.converter = timetz

log_filename = os.environ.get("LOG_FILENAME", "default_strategy.log")
log_path = os.path.join("logs", log_filename)

# 로컬(테스트넷)은 콘솔 출력, 서버는 파일 로깅
if IS_TESTNET:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
else:
    logging.basicConfig(
        filename=log_path,
        format="%(asctime)s %(levelname)s: %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
        encoding="utf-8"
    )

# === Exchange 설정 ===
if IS_TESTNET:
    # Testnet
    exchange = ccxt.binance({
        "apiKey": os.environ.get("BINANCE_TESTNET_API_KEY", ""),
        "secret": os.environ.get("BINANCE_TESTNET_API_SECRET", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "future", "adjustForTimeDifference": True}
    })
    exchange.set_sandbox_mode(True)
    logging.info("[TESTNET MODE]")
else:
    # Production
    exchange = ccxt.binance({
        "apiKey": os.environ["BINANCE_API_KEY"],
        "secret": os.environ["BINANCE_API_SECRET"],
        "enableRateLimit": True,
        "options": {"defaultType": "future", "adjustForTimeDifference": True}
    })
    logging.info("[PRODUCTION MODE]")

# === 기타 Exchange (필요시) ===
binance_spot = ccxt.binance({
    "apiKey": os.environ.get("BINANCE_API_KEY", ""),
    "secret": os.environ.get("BINANCE_API_SECRET", ""),
    "enableRateLimit": True,
    "options": {"defaultType": "spot", "adjustForTimeDifference": True}
})

bybit_exchange = ccxt.bybit({
    "apiKey": os.environ.get("BYBIT_API_KEY", ""),
    "secret": os.environ.get("BYBIT_API_SECRET", ""),
    "enableRateLimit": True,
    "options": {"defaultType": "linear", "adjustForTimeDifference": True}
})
