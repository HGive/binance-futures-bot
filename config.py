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
        "timeout": 15000,
        "options": {"adjustForTimeDifference": True, "fetchCurrencies": False, "recvWindow": 10000},
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
        _use_testnet(ex, market)
    logging.info(f"[{'DEMO(테스트넷)' if testnet else 'PRODUCTION'}] {market}")
    if use_pro:
        resilient_reads(ex)
    return ex


def resilient_reads(ex):
    """읽기만 재시도한다. 주문 타임아웃은 중복 체결 위험 때문에 재전송하지 않는다."""
    import asyncio
    import ccxt
    import time
    original = ex.request
    sync_at = 0.0
    syncing = False

    async def request(path, api="public", method="GET", params=None, headers=None, body=None, config=None):
        nonlocal sync_at, syncing
        if "private" in str(api).lower() and not syncing and time.monotonic() - sync_at > 60:
            syncing = True
            try:
                await ex.load_time_difference()
                sync_at = time.monotonic()
            finally:
                syncing = False
        for attempt in range(3):
            try:
                return await original(path, api, method, params or {}, headers, body, config or {})
            except ccxt.InvalidNonce:
                if method != "GET" or attempt == 2 or syncing:
                    raise
                syncing = True
                try:
                    await ex.load_time_difference()
                    sync_at = time.monotonic()
                finally:
                    syncing = False
                logging.warning("거래소 시간 재동기화 후 읽기 재시도")
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable):
                if method != "GET" or attempt == 2:
                    raise
                logging.warning("거래소 읽기 실패 — 재시도 %s/2", attempt + 1)
                await asyncio.sleep(2 ** attempt)
    ex.request = request


def _use_testnet(ex, market):
    """데모 서버로 주소를 바꾼다.

    ccxt 가 선물 sandbox 모드를 없앴다(deprecation). 그래서 직접 갈아끼운다.
      현물 데모  https://testnet.binance.vision
      선물 데모  https://testnet.binancefuture.com  ← 바이낸스가 'Demo Trading' 으로 부르는 그것
    두 서버는 **키가 다르다.**
    """
    test = ex.urls.get("test") or {}
    if market == "spot":
        try:
            ex.set_sandbox_mode(True)
            return
        except Exception:
            pass
    api = dict(ex.urls["api"])
    for k, v in test.items():
        if isinstance(v, str) and "binancefuture" in v if market != "spot" else True:
            api[k] = v
    if market != "spot":
        # 선물 데모는 fapi/dapi 만 갈아끼운다. sapi 는 데모에 없다.
        for k in list(api):
            if k.startswith("fapi") or k.startswith("dapi"):
                if k in test:
                    api[k] = test[k]
    # 웹소켓 주소도 갈아끼운다. ccxt 의 sandbox 제거 이후 자동으로 안 된다.
    # 주소는 urls["api"]["ws"] 안에 있다 (urls["ws"] 가 아니다 — 여기서 한 번 틀렸다).
    # REBOUND 는 1분봉 스트림이 생명줄이라 빼먹으면 조용히 아무것도 안 받는다.
    tws = test.get("ws")
    if isinstance(tws, dict):
        ws = dict(api.get("ws") or {})
        ws.update({k: v for k, v in tws.items() if k != "ws-api"})
        if isinstance(tws.get("ws-api"), dict):
            wa = dict(ws.get("ws-api") or {})
            wa.update(tws["ws-api"])
            ws["ws-api"] = wa
        api["ws"] = ws
    ex.urls["api"] = api
    ex.options["defaultType"] = "future" if market != "spot" else "spot"


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


def acquire_run_lock(path):
    """프로세스 종료 시 OS가 해제하는 비차단 잠금."""
    import fcntl
    handle = open(path, "a", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle
