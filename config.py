# config.py
"""
거래소 인스턴스 생성.

  - 데이터(과거 봉)는 항상 실서버 공개 API 에서 받는다. 키가 필요 없다.
  - 주문만 testnet / production 으로 갈린다.
  - 테스트넷 키는 실서버 키와 다르다.
      선물  testnet.binancefuture.com   → BINANCE_TESTNET_KEY / _SECRET
      현물  testnet.binance.vision      → BINANCE_SPOT_TESTNET_KEY / _SECRET
"""
from dotenv import load_dotenv
import os, logging, sys
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


_KEYS = {
    ("futures", False): ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
    ("futures", True): ("BINANCE_TESTNET_KEY", "BINANCE_TESTNET_SECRET"),
    ("spot", False): ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
    ("spot", True): ("BINANCE_SPOT_TESTNET_KEY", "BINANCE_SPOT_TESTNET_SECRET"),
}


def make_exchange(market="futures", testnet=False, use_pro=True, need_keys=True):
    """market: 'spot' | 'futures'.  testnet=True 면 주문이 테스트넷으로 간다."""
    if use_pro:
        import ccxt.pro as ccxt
    else:
        import ccxt
    cls = ccxt.binance if market == "spot" else ccxt.binanceusdm

    cfg = {
        "enableRateLimit": True,
        "options": {"adjustForTimeDifference": True, "fetchCurrencies": False},
    }
    if need_keys:
        k, s = _KEYS[(market, testnet)]
        if k not in os.environ or not os.environ[k]:
            raise RuntimeError(
                f"환경변수 {k} / {s} 가 없다. .env 에 넣어라.\n"
                + ("  선물 테스트넷 키: https://testnet.binancefuture.com\n"
                   "  현물 테스트넷 키: https://testnet.binance.vision" if testnet else "")
            )
        cfg["apiKey"] = os.environ[k]
        cfg["secret"] = os.environ[s]

    ex = cls(cfg)
    if testnet:
        ex.set_sandbox_mode(True)
    logging.info(f"[{'TESTNET' if testnet else 'PRODUCTION'}] {market}")
    return ex


def public_exchange(market="spot"):
    """과거 봉 수집 전용 — 항상 실서버, 키 없음."""
    import ccxt
    cls = ccxt.binance if market == "spot" else ccxt.binanceusdm
    ex = cls({"enableRateLimit": True, "options": {"fetchCurrencies": False}})
    ex.rateLimit = 300
    return ex


# main.py 하위호환 (선물 실서버)
def _legacy():
    try:
        return make_exchange("futures", testnet=False)
    except Exception as e:                      # 키가 없으면 import 만으로 죽지 않게
        logging.warning(f"기본 exchange 생성 실패: {e}")
        return None


exchange = _legacy()
