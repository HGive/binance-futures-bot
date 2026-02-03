# config.py
from dotenv import load_dotenv
import os, ccxt.pro as ccxt, logging, sys
from pytz import timezone
from datetime import datetime

load_dotenv()

# === DEMO 모드 체크 ===
# TEST_NET 또는 DEMO_MODE가 TRUE면 데모 모드로 동작
IS_DEMO = os.environ.get("DEMO_MODE", os.environ.get("TEST_NET", "FALSE")).upper() == "TRUE"

# === 로깅 설정 ===
tz = timezone("Asia/Seoul")
def timetz(*args): return datetime.now(tz).timetuple()
logging.Formatter.converter = timetz

log_filename = os.environ.get("LOG_FILENAME", "default_strategy.log")
# 데모 모드일 때는 demo.log, 아니면 LOG_FILENAME 사용
log_path = os.path.join("logs", "demo.log" if IS_DEMO else log_filename)
os.makedirs("logs", exist_ok=True)

# 항상 파일 로깅 (도커 볼륨 ./logs:/app/logs 로 프로젝트 디렉터리에서 확인 가능)
# 테스트넷이면 콘솔에도 출력 (docker logs로도 확인 가능)
fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
fmt.converter = timetz

root = logging.getLogger()
root.setLevel(logging.INFO)
root.handlers.clear()

file_handler = logging.FileHandler(log_path, encoding="utf-8")
file_handler.setFormatter(fmt)
root.addHandler(file_handler)

if IS_DEMO:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

# === Exchange 설정 ===
# Binance Demo Trading: 별도의 Demo endpoint 사용
BINANCE_DEMO_BASE = "https://demo-fapi.binance.com"

if IS_DEMO:
    # Demo Trading: Demo URL + Demo API Key
    exchange = ccxt.binanceusdm({
        "apiKey": os.environ.get("BINANCE_DEMO_API_KEY", ""),
        "secret": os.environ.get("BINANCE_DEMO_API_SECRET", ""),
        "enableRateLimit": True,
        "options": {
            "adjustForTimeDifference": True,
            "fetchCurrencies": False,  # sapi 호출 방지
        },
    })
    # Demo Trading URL로 변경 (경로 포함)
    exchange.urls["api"]["fapiPublic"] = f"{BINANCE_DEMO_BASE}/fapi/v1"
    exchange.urls["api"]["fapiPrivate"] = f"{BINANCE_DEMO_BASE}/fapi/v1"
    exchange.urls["api"]["fapiPublicV2"] = f"{BINANCE_DEMO_BASE}/fapi/v2"
    exchange.urls["api"]["fapiPrivateV2"] = f"{BINANCE_DEMO_BASE}/fapi/v2"
    logging.info("[DEMO TRADING MODE]")
else:
    # Production: binanceusdm (USDⓈ-M Futures 전용) 사용
    exchange = ccxt.binanceusdm({
        "apiKey": os.environ["BINANCE_API_KEY"],
        "secret": os.environ["BINANCE_API_SECRET"],
        "enableRateLimit": True,
        "options": {
            "adjustForTimeDifference": True,
            "fetchCurrencies": False,  # sapi 호출 방지
        }
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
