import os
import time
import json
import threading
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# LONG + SHORT SIGNAL BOT
# REAL MARKET-STRUCTURE BOS
# NO OBV
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GATE_URL = "https://api.gateio.ws/api/v4"


# ============================================================
# TIMEFRAMES
# ============================================================

TIMEFRAMES = {
    "15m": {
        "interval": "15m",
        "seconds": 900,
        "gap_minimum": 10.0,
    },
    "1h": {
        "interval": "1h",
        "seconds": 3600,
        "gap_minimum": 20.0,
    },
    "4h": {
        "interval": "4h",
        "seconds": 14400,
        "gap_minimum": 35.0,
    },
}


# ============================================================
# INDICATORS
# ============================================================

SMA_PERIOD = 50
EMA_FAST = 20
EMA_SLOW = 200


# ============================================================
# MARKET STRUCTURE BOS
# ============================================================

SWING_LENGTH = 5


# ============================================================
# PRICE / GAP RULES
# ============================================================

PRICE_GAP_RATIO = 0.20
EMA20_TOLERANCE = 0.02


# ============================================================
# TAKE PROFITS
# ============================================================

TP1_PERCENT = 0.05
TP2_PERCENT = 0.10


# ============================================================
# ALERT SETTINGS
# ============================================================

REPEAT_INTERVAL = 60 * 60


# ============================================================
# CANDLE / CONCURRENCY SETTINGS
# ============================================================

CANDLE_LIMIT = 1000
MAX_WORKERS = 6


# ============================================================
# RETRY SETTINGS
# ============================================================

MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.5


# ============================================================
# HISTORY FILES
# ============================================================

ALERTS_FILE = "alerts.json"
SIGNALS_FILE = "signals.json"


# ============================================================
# SESSION
# ============================================================

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/5.0"
}

session = requests.Session()
session.headers.update(HEADERS)

file_lock = threading.Lock()


# ============================================================
# DIAGNOSTICS
# ============================================================

diagnostics = {
    "symbols_loaded": 0,
    "symbols_with_live_price": 0,
    "symbols_without_live_price": 0,

    "total_scans": 0,

    "candle_requests": 0,
    "candle_success": 0,
    "candle_failures": 0,

    "http_400": 0,
    "http_429": 0,
    "http_500_plus": 0,
    "request_errors": 0,

    "http_400_15m": 0,
    "http_400_1h": 0,
    "http_400_4h": 0,

    "invalid_candles": 0,
    "insufficient_candles": 0,
    "no_closed_candles": 0,

    "invalid_indicator": 0,

    "gap_rejected": 0,
    "price_position_rejected": 0,
    "price_gap_ratio_rejected": 0,
    "ema20_rejected": 0,

    "no_bos": 0,
    "bullish_bos_found": 0,
    "bearish_bos_found": 0,
    "bos_direction_rejected": 0,

    "long_position_found": 0,
    "short_position_found": 0,

    "long_valid": 0,
    "short_valid": 0,

    "tp3_rejected": 0,

    "long_signals": 0,
    "short_signals": 0,

    "fresh_signals": 0,
    "hourly_repeats": 0,
}


http_400_symbols = {
    "15m": set(),
    "1h": set(),
    "4h": set(),
}


# ============================================================
# HEADER
# ============================================================

def print_header():

    print()
    print("=" * 72)
    print("LONG + SHORT SIGNAL BOT")
    print("=" * 72)

    print("TIMEFRAMES: 15M / 1H / 4H")
    print("MARKET: GATE FUTURES ONLY")
    print("NO SPOT")
    print()

    print("REAL MARKET-STRUCTURE BOS")
    print(f"SWING CONFIRMATION = {SWING_LENGTH}")

    print("BULLISH BOS = CLOSE ABOVE STRUCTURAL SWING HIGH")
    print("BEARISH BOS = CLOSE BELOW STRUCTURAL SWING LOW")

    print()

    print("FUTURES SMA50")
    print("FUTURES EMA20")
    print("FUTURES EMA200")
    print("LIVE FUTURES ENTRY PRICE")
    print("FUTURES CLOSED CANDLES ONLY")

    print()

    print("GAP MINIMUMS:")
    print("15M > 10%")
    print("1H  > 20%")
    print("4H  > 35%")

    print()

    print("PRICE/GAP RATIO = 2:10")
    print("EMA20 TOLERANCE = 2%")

    print()

    print("LONG  = SMA50 < PRICE < EMA200")
    print("SHORT = EMA200 < PRICE < SMA50")

    print()

    print("BOS CANDLE LOW  = LONG SL")
    print("BOS CANDLE HIGH = SHORT SL")

    print()

    print("NEW BOS = IMMEDIATE ALERT")
    print("SAME BOS = REPEAT EVERY 60 MINUTES")

    print()

    print("SCAN = EVERY 5 MINUTES")

    print()

    print("TP1 = 5%")
    print("TP2 = 10%")
    print("TP3 = EMA200")

    print()

    print(f"CANDLE LIMIT = {CANDLE_LIMIT}")
    print(f"MAX WORKERS = {MAX_WORKERS}")

    print("=" * 72)
    print()


# ============================================================
# DIAGNOSTIC COUNTER
# ============================================================

def increment_counter(name, amount=1):

    with file_lock:
        if name in diagnostics:
            diagnostics[name] += amount


# ============================================================
# JSON HELPERS
# ============================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:

        print(f"JSON LOAD ERROR [{filename}]: {e}")
        return default


def save_json(filename, data):

    temp_file = filename + ".tmp"

    try:

        with file_lock:

            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(
                    data,
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            os.replace(temp_file, filename)

    except Exception as e:

        print(f"JSON SAVE ERROR [{filename}]: {e}")


# ============================================================
# GATE API REQUEST
# ============================================================

def gate_get(
    path,
    params=None,
    description="",
    timeframe=None,
    symbol=None
):

    url = GATE_URL + path

    for attempt in range(MAX_RETRIES + 1):

        try:

            response = session.get(
                url,
                params=params,
                timeout=15
            )

            status = response.status_code

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            if status == 200:

                try:
                    return response.json()

                except Exception as e:

                    print(
                        f"JSON ERROR [{description}]: {e}"
                    )

                    increment_counter("request_errors")

                    return None

            # ------------------------------------------------
            # HTTP 400
            # ------------------------------------------------

            if status == 400:

                increment_counter("http_400")

                if timeframe:
                    increment_counter(
                        f"http_400_{timeframe}"
                    )

                if timeframe and symbol:
                    http_400_symbols[timeframe].add(symbol)

                return None

            # ------------------------------------------------
            # HTTP 429
            # ------------------------------------------------

            if status == 429:

                increment_counter("http_429")

                if attempt >= MAX_RETRIES:
                    return None

                retry_after = response.headers.get(
                    "Retry-After"
                )

                if retry_after:

                    try:
                        delay = float(retry_after)

                    except Exception:
                        delay = BASE_RETRY_DELAY * (
                            2 ** attempt
                        )

                else:

                    delay = BASE_RETRY_DELAY * (
                        2 ** attempt
                    )

                time.sleep(delay)

                continue

            # ------------------------------------------------
            # HTTP 500+
            # ------------------------------------------------

            if status >= 500:

                increment_counter("http_500_plus")

                if attempt >= MAX_RETRIES:
                    return None

                delay = BASE_RETRY_DELAY * (
                    2 ** attempt
                )

                time.sleep(delay)

                continue

            # ------------------------------------------------
            # OTHER HTTP ERROR
            # ------------------------------------------------

            print(
                f"HTTP {status} [{description}]"
            )

            increment_counter("request_errors")

            return None

        except requests.RequestException as e:

            increment_counter("request_errors")

            if attempt >= MAX_RETRIES:

                print(
                    f"REQUEST FAILED [{description}]: {e}"
                )

                return None

            delay = BASE_RETRY_DELAY * (
                2 ** attempt
            )

            time.sleep(delay)

    return None


# ============================================================
# GET FUTURES SYMBOLS
# ============================================================

def get_futures_symbols():

    data = gate_get(
        "/futures/usdt/contracts",
        description="futures contracts"
    )

    if not data:
        return []

    symbols = set()

    for item in data:

        try:

            name = item.get("name")

            if not name:
                continue

            if not name.endswith("_USDT"):
                continue

            status = str(
                item.get("status", "")
            ).lower()

            if status and status not in {
                "trading",
                "open"
            }:
                continue

            symbols.add(name)

        except Exception:
            continue

    symbols = sorted(symbols)

    diagnostics["symbols_loaded"] = len(symbols)

    print(
        f"USDT FUTURES CONTRACTS: {len(symbols)}"
    )

    return symbols


# ============================================================
# GET FUTURES PRICES
# ============================================================

def get_futures_prices():

    data = gate_get(
        "/futures/usdt/tickers",
        description="futures tickers"
    )

    if not data:
        return {}

    prices = {}

    for item in data:

        try:

            contract = item.get("contract")
            last = float(item.get("last", 0))

            if (
                contract
                and last > 0
            ):
                prices[contract] = last

        except Exception:
            continue

    return prices


# ============================================================
# PARSE CANDLE
# ============================================================

def parse_candle(row):

    try:

        # ----------------------------------------------------
        # Gate list format:
        #
        # [timestamp, volume, close, high, low, open, ...]
        # ----------------------------------------------------

        if isinstance(row, list):

            if len(row) < 6:
                increment_counter("invalid_candles")
                return None

            timestamp = int(float(row[0]))
            volume = float(row[1])
            close = float(row[2])
            high = float(row[3])
            low = float(row[4])
            open_price = float(row[5])

        # ----------------------------------------------------
        # Dict format
        # ----------------------------------------------------

        elif isinstance(row, dict):

            timestamp = int(
                float(
                    row.get(
                        "t",
                        row.get(
                            "timestamp",
                            0
                        )
                    )
                )
            )

            volume = float(
                row.get(
                    "v",
                    row.get(
                        "volume",
                        0
                    )
                )
            )

            close = float(
                row.get(
                    "c",
                    row.get(
                        "close",
                        0
                    )
                )
            )

            high = float(
                row.get(
                    "h",
                    row.get(
                        "high",
                        0
                    )
                )
            )

            low = float(
                row.get(
                    "l",
                    row.get(
                        "low",
                        0
                    )
                )
            )

            open_price = float(
                row.get(
                    "o",
                    row.get(
                        "open",
                        0
                    )
                )
            )

        else:

            increment_counter("invalid_candles")
            return None

        if (
            timestamp <= 0
            or open_price <= 0
            or high <= 0
            or low <= 0
            or close <= 0
        ):

            increment_counter("invalid_candles")
            return None

        return {
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    except Exception:

        increment_counter("invalid_candles")
        return None


# ============================================================
# GET FUTURES CANDLES
# ============================================================

def get_futures_candles(symbol, timeframe):

    increment_counter("candle_requests")

    config = TIMEFRAMES[timeframe]

    data = gate_get(
        "/futures/usdt/candlesticks",
        params={
            "contract": symbol,
            "interval": config["interval"],
            "limit": CANDLE_LIMIT,
        },
        description=f"{symbol} {timeframe} candles",
        timeframe=timeframe,
        symbol=symbol
    )

    if not data:

        increment_counter("candle_failures")
        return []

    candles = []

    for row in data:

        candle = parse_candle(row)

        if candle:
            candles.append(candle)

    if not candles:

        increment_counter("candle_failures")
        return []

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    # --------------------------------------------------------
    # REMOVE DUPLICATES
    # --------------------------------------------------------

    unique = {}

    for candle in candles:
        unique[candle["timestamp"]] = candle

    candles = list(unique.values())

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    # --------------------------------------------------------
    # REMOVE CURRENTLY FORMING CANDLE
    # --------------------------------------------------------

    now = int(time.time())

    closed_candles = []

    for candle in candles:

        if (
            candle["timestamp"]
            + config["seconds"]
            <= now
        ):
            closed_candles.append(candle)

    candles = closed_candles

    if not candles:

        increment_counter("no_closed_candles")
        return []

    if len(candles) < 200:

        increment_counter("insufficient_candles")
        return []

    increment_counter("candle_success")

    return candles


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    ema = sum(
        values[:period]
    ) / period

    multiplier = 2.0 / (
        period + 1.0
    )

    for price in values[period:]:

        ema = (
            (price - ema)
            * multiplier
        ) + ema

    return ema


# ============================================================
# SMA
# ============================================================

def calculate_sma(values, period):

    if len(values) < period:
        return None

    return sum(
        values[-period:]
    ) / period


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    sma50 = calculate_sma(
        closes,
        SMA_PERIOD
    )

    ema20 = calculate_ema(
        closes,
        EMA_FAST
    )

    ema200 = calculate_ema(
        closes,
        EMA_SLOW
    )

    if (
        sma50 is None
        or ema20 is None
        or ema200 is None
        or sma50 <= 0
        or ema20 <= 0
        or ema200 <= 0
    ):

        increment_counter(
            "invalid_indicator"
        )

        return None

    return {
        "sma50": sma50,
        "ema20": ema20,
        "ema200": ema200,
    }


# ============================================================
# GAP %
# ============================================================

def calculate_gap_percent(
    sma50,
    ema200
):

    minimum = min(
        sma50,
        ema200
    )

    if minimum <= 0:
        return 0.0

    return (
        abs(ema200 - sma50)
        / minimum
    ) * 100.0


# ============================================================
# SWING HIGH
# ============================================================

def is_swing_high(
    candles,
    index
):

    left = SWING_LENGTH
    right = SWING_LENGTH

    if (
        index < left
        or index + right >= len(candles)
    ):
        return False

    candidate = candles[index]["high"]

    for i in range(
        index - left,
        index
    ):

        if candidate <= candles[i]["high"]:
            return False

    for i in range(
        index + 1,
        index + right + 1
    ):

        if candidate <= candles[i]["high"]:
            return False

    return True


# ============================================================
# SWING LOW
# ============================================================

def is_swing_low(
    candles,
    index
):

    left = SWING_LENGTH
    right = SWING_LENGTH

    if (
        index < left
        or index + right >= len(candles)
    ):
        return False

    candidate = candles[index]["low"]

    for i in range(
        index - left,
        index
    ):

        if candidate >= candles[i]["low"]:
            return False

    for i in range(
        index + 1,
        index + right + 1
    ):

        if candidate >= candles[i]["low"]:
            return False

    return True


# ============================================================
# BULLISH BOS
# ============================================================

def find_bullish_bos(candles):

    if len(candles) < (
        SWING_LENGTH * 2 + 2
    ):
        return None

    # Search newest confirmed swing high first
    for swing_index in range(
        len(candles) - SWING_LENGTH - 1,
        SWING_LENGTH - 1,
        -1
    ):

        if not is_swing_high(
            candles,
            swing_index
        ):
            continue

        swing_price = candles[
            swing_index
        ]["high"]

        # Search break after swing confirmation
        start_index = (
            swing_index
            + SWING_LENGTH
            + 1
        )

        for bos_index in range(
            start_index,
            len(candles)
        ):

            candle = candles[bos_index]

            if candle["close"] > swing_price:

                return {
                    "type": "BULLISH BOS",
                    "direction": "LONG",

                    "swing_index": swing_index,

                    "swing_timestamp":
                        candles[
                            swing_index
                        ]["timestamp"],

                    "swing_price":
                        swing_price,

                    "bos_index": bos_index,

                    "bos_timestamp":
                        candle["timestamp"],

                    "bos_open":
                        candle["open"],

                    "bos_high":
                        candle["high"],

                    "bos_low":
                        candle["low"],

                    "bos_close":
                        candle["close"],
                }

    return None


# ============================================================
# BEARISH BOS
# ============================================================

def find_bearish_bos(candles):

    if len(candles) < (
        SWING_LENGTH * 2 + 2
    ):
        return None

    # Search newest confirmed swing low first
    for swing_index in range(
        len(candles) - SWING_LENGTH - 1,
        SWING_LENGTH - 1,
        -1
    ):

        if not is_swing_low(
            candles,
            swing_index
        ):
            continue

        swing_price = candles[
            swing_index
        ]["low"]

        start_index = (
            swing_index
            + SWING_LENGTH
            + 1
        )

        for bos_index in range(
            start_index,
            len(candles)
        ):

            candle = candles[bos_index]

            if candle["close"] < swing_price:

                return {
                    "type": "BEARISH BOS",
                    "direction": "SHORT",

                    "swing_index": swing_index,

                    "swing_timestamp":
                        candles[
                            swing_index
                        ]["timestamp"],

                    "swing_price":
                        swing_price,

                    "bos_index": bos_index,

                    "bos_timestamp":
                        candle["timestamp"],

                    "bos_open":
                        candle["open"],

                    "bos_high":
                        candle["high"],

                    "bos_low":
                        candle["low"],

                    "bos_close":
                        candle["close"],
                }

    return None


# ============================================================
# FIND LATEST BOS
# ============================================================

def find_latest_bos(candles):

    bullish = find_bullish_bos(
        candles
    )

    bearish = find_bearish_bos(
        candles
    )

    if (
        bullish is None
        and bearish is None
    ):

        increment_counter("no_bos")

        return None

    if bullish is not None:
        increment_counter(
            "bullish_bos_found"
        )

    if bearish is not None:
        increment_counter(
            "bearish_bos_found"
        )

    if (
        bullish is not None
        and bearish is not None
    ):

        if (
            bullish["bos_timestamp"]
            >= bearish["bos_timestamp"]
        ):
            return bullish

        return bearish

    if bullish is not None:
        return bullish

    return bearish


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    timeframe,
    live_price
):

    increment_counter("total_scans")

    candles = get_futures_candles(
        symbol,
        timeframe
    )

    if not candles:
        return None

    indicators = calculate_indicators(
        candles
    )

    if not indicators:
        return None

    sma50 = indicators["sma50"]
    ema20 = indicators["ema20"]
    ema200 = indicators["ema200"]

    try:
        price = float(live_price)
    except Exception:
        return None

    if price <= 0:
        return None

    # ========================================================
    # GAP FILTER
    # ========================================================

    gap_percent = calculate_gap_percent(
        sma50,
        ema200
    )

    minimum_gap = TIMEFRAMES[
        timeframe
    ]["gap_minimum"]

    if gap_percent <= minimum_gap:

        increment_counter(
            "gap_rejected"
        )

        return None

    # ========================================================
    # PRICE POSITION
    # ========================================================

    direction = None

    # LONG
    if (
        sma50 < price
        < ema200
    ):

        direction = "LONG"

        increment_counter(
            "long_position_found"
        )

    # SHORT
    elif (
        ema200 < price
        < sma50
    ):

        direction = "SHORT"

        increment_counter(
            "short_position_found"
        )

    else:

        increment_counter(
            "price_position_rejected"
        )

        return None

    # ========================================================
    # PRICE / GAP RATIO
    # ========================================================

    total_gap = abs(
        ema200 - sma50
    )

    if total_gap <= 0:
        return None

    if direction == "LONG":

        distance_from_sma = (
            price - sma50
        )

        ratio = (
            distance_from_sma
            / total_gap
        )

    else:

        distance_from_ema = (
            sma50 - price
        )

        ratio = (
            distance_from_ema
            / total_gap
        )

    if ratio < PRICE_GAP_RATIO:

        increment_counter(
            "price_gap_ratio_rejected"
        )

        return None

    # ========================================================
    # EMA20 TOLERANCE
    # ========================================================

    ema20_distance = abs(
        price - ema20
    ) / ema20

    if (
        ema20_distance
        > EMA20_TOLERANCE
    ):

        increment_counter(
            "ema20_rejected"
        )

        return None

    # ========================================================
    # MARKET STRUCTURE BOS
    # ========================================================

    bos = find_latest_bos(
        candles
    )

    if not bos:
        return None

    # ========================================================
    # BOS MUST MATCH DIRECTION
    # ========================================================

    if bos["direction"] != direction:

        increment_counter(
            "bos_direction_rejected"
        )

        return None

    # ========================================================
    # BOS PRICE
    # ========================================================

    bos_price = bos[
        "swing_price"
    ]

    # ========================================================
    # STOP LOSS
    # ========================================================

    if direction == "LONG":

        stop_loss = bos[
            "bos_low"
        ]

        # SL must be below entry
        if stop_loss >= price:
            return None

    else:

        stop_loss = bos[
            "bos_high"
        ]

        # SL must be above entry
        if stop_loss <= price:
            return None

    # ========================================================
    # TAKE PROFITS
    # ========================================================

    if direction == "LONG":

        tp1 = price * (
            1 + TP1_PERCENT
        )

        tp2 = price * (
            1 + TP2_PERCENT
        )

        tp3 = ema200

        # TP3 must be above entry
        if tp3 <= price:

            increment_counter(
                "tp3_rejected"
            )

            return None

    else:

        tp1 = price * (
            1 - TP1_PERCENT
        )

        tp2 = price * (
            1 - TP2_PERCENT
        )

        tp3 = ema200

        # TP3 must be below entry
        if tp3 >= price:

            increment_counter(
                "tp3_rejected"
            )

            return None

    # ========================================================
    # VALID SIGNAL
    # ========================================================

    if direction == "LONG":

        increment_counter(
            "long_valid"
        )

        increment_counter(
            "long_signals"
        )

    else:

        increment_counter(
            "short_valid"
        )

        increment_counter(
            "short_signals"
        )

    last_candle = candles[-1]

    return {

        "symbol": symbol,

        "timeframe": timeframe,

        "direction": direction,

        "entry": price,

        "stop_loss": stop_loss,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,

        "sma50": sma50,

        "ema20": ema20,

        "ema200": ema200,

        "gap_percent": gap_percent,

        "price_gap_ratio": ratio,

        "bos_type": bos["type"],

        "bos_price": bos_price,

        "bos_timestamp":
            bos["bos_timestamp"],

        "bos_candle_timestamp":
            bos["bos_timestamp"],

        "bos_open":
            bos["bos_open"],

        "bos_high":
            bos["bos_high"],

        "bos_low":
            bos["bos_low"],

        "bos_close":
            bos["bos_close"],

        "swing_timestamp":
            bos["swing_timestamp"],

        "last_candle_timestamp":
            last_candle["timestamp"],

        "last_candle_close":
            last_candle["close"],
    }


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(value):

    try:
        value = float(value)
    except Exception:
        return "N/A"

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.6f}"

    if value >= 0.01:
        return f"{value:.8f}"

    return f"{value:.10f}"


# ============================================================
# FORMAT TIMESTAMP
# ============================================================

def format_timestamp(timestamp):

    try:

        return time.strftime(
            "%Y-%m-%d %H:%M UTC",
            time.gmtime(
                int(timestamp)
            )
        )

    except Exception:

        return "N/A"


# ============================================================
# SIGNAL KEY
# ============================================================

def signal_key(signal):

    symbol = signal[
        "symbol"
    ]

    timeframe = signal[
        "timeframe"
    ]

    direction = signal[
        "direction"
    ]

    bos_timestamp = signal.get(
        "bos_timestamp",
        signal.get(
            "last_candle_timestamp",
            0
        )
    )

    return (
        f"{symbol}_"
        f"{timeframe}_"
        f"{direction}_"
        f"{bos_timestamp}"
    )


# ============================================================
# SEND TELEGRAM
# ============================================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        print(
            "TELEGRAM ERROR: "
            "BOT TOKEN OR CHAT ID MISSING"
        )

        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code == 200:
            return True

        print(
            "TELEGRAM ERROR:",
            response.status_code,
            response.text
        )

        return False

    except Exception as e:

        print(
            f"TELEGRAM REQUEST ERROR: {e}"
        )

        return False


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    direction = signal[
        "direction"
    ]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    symbol = signal[
        "symbol"
    ]

    timeframe = signal[
        "timeframe"
    ]

    message = (
        f"{emoji} #{symbol} "
        f"{direction} {timeframe}\n\n"

        f"Entry: "
        f"{format_price(signal['entry'])}\n"

        f"SL: "
        f"{format_price(signal['stop_loss'])}\n"

        f"TP1: "
        f"{format_price(signal['tp1'])}\n"

        f"TP2: "
        f"{format_price(signal['tp2'])}\n"

        f"TP3: "
        f"{format_price(signal['tp3'])}\n\n"

        f"BOS: "
        f"{signal['bos_type']}\n"

        f"BOS Level: "
        f"{format_price(signal['bos_price'])}\n"

        f"BOS Candle: "
        f"{format_timestamp(signal['bos_timestamp'])}\n\n"

        f"SMA50: "
        f"{format_price(signal['sma50'])}\n"

        f"EMA20: "
        f"{format_price(signal['ema20'])}\n"

        f"EMA200: "
        f"{format_price(signal['ema200'])}\n"

        f"Gap: "
        f"{signal['gap_percent']:.2f}%"
    )

    return message


# ============================================================
# PRINT DIAGNOSTICS
# ============================================================

def print_diagnostics():

    print()
    print("=" * 72)
    print("DIAGNOSTICS")
    print("=" * 72)

    for key, value in diagnostics.items():

        print(
            f"{key.upper():35} {value}"
        )

    print()

    print("HTTP 400 SYMBOLS")

    for timeframe in (
        "15m",
        "1h",
        "4h"
    ):

        symbols = sorted(
            http_400_symbols[
                timeframe
            ]
        )

        print(
            f"{timeframe}: "
            f"{len(symbols)}"
        )

        if symbols:

            print(
                "  "
                + ", ".join(
                    symbols[:50]
                )
            )

    print("=" * 72)
    print()


# ============================================================
# ZERO SIGNAL REPORT
# ============================================================

def zero_signal_report():

    return (
        "⚪ LONG + SHORT BOT\n\n"

        "No fresh BOS signals found.\n\n"

        f"Scans: "
        f"{diagnostics['total_scans']}\n"

        f"Gap rejected: "
        f"{diagnostics['gap_rejected']}\n"

        f"Price position rejected: "
        f"{diagnostics['price_position_rejected']}\n"

        f"Price/gap ratio rejected: "
        f"{diagnostics['price_gap_ratio_rejected']}\n"

        f"EMA20 rejected: "
        f"{diagnostics['ema20_rejected']}\n\n"

        f"BOS not found: "
        f"{diagnostics['no_bos']}\n"

        f"Bullish BOS found: "
        f"{diagnostics['bullish_bos_found']}\n"

        f"Bearish BOS found: "
        f"{diagnostics['bearish_bos_found']}\n"

        f"BOS direction rejected: "
        f"{diagnostics['bos_direction_rejected']}"
    )


# ============================================================
# RUN SCAN
# ============================================================

def run_scan():

    print_header()

    # Reset HTTP 400 symbol tracking
    for timeframe in http_400_symbols:
        http_400_symbols[
            timeframe
        ].clear()

    # --------------------------------------------------------
    # LOAD HISTORY
    # --------------------------------------------------------

    alerts = load_json(
        ALERTS_FILE,
        {}
    )

    signal_history = load_json(
        SIGNALS_FILE,
        []
    )

    # --------------------------------------------------------
    # GET SYMBOLS
    # --------------------------------------------------------

    symbols = get_futures_symbols()

    if not symbols:

        print(
            "NO FUTURES SYMBOLS FOUND."
        )

        return

    # --------------------------------------------------------
    # GET LIVE FUTURES PRICES
    # --------------------------------------------------------

    prices = get_futures_prices()

    symbols_with_price = [
        symbol
        for symbol in symbols
        if symbol in prices
        and prices[symbol] > 0
    ]

    symbols_without_price = [
        symbol
        for symbol in symbols
        if symbol not in prices
    ]

    diagnostics[
        "symbols_with_live_price"
    ] = len(symbols_with_price)

    diagnostics[
        "symbols_without_live_price"
    ] = len(symbols_without_price)

    print(
        f"LIVE FUTURES PRICES: "
        f"{len(symbols_with_price)}"
    )

    print(
        f"WITHOUT LIVE PRICE: "
        f"{len(symbols_without_price)}"
    )

    # --------------------------------------------------------
    # CREATE JOBS
    # --------------------------------------------------------

    jobs = []

    for symbol in symbols_with_price:

        live_price = prices[symbol]

        for timeframe in TIMEFRAMES:

            jobs.append(
                (
                    symbol,
                    timeframe,
                    live_price
                )
            )

    print(
        f"TOTAL JOBS: {len(jobs)}"
    )

    # --------------------------------------------------------
    # RUN SCANS
    # --------------------------------------------------------

    signals = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_symbol,
                symbol,
                timeframe,
                live_price
            ): (
                symbol,
                timeframe
            )

            for (
                symbol,
                timeframe,
                live_price
            ) in jobs
        }

        for future in as_completed(
            futures
        ):

            symbol, timeframe = futures[
                future
            ]

            try:

                result = future.result()

                if result:

                    signals.append(
                        result
                    )

            except Exception as e:

                print(
                    f"ANALYSIS ERROR "
                    f"[{symbol} {timeframe}]: "
                    f"{e}"
                )

    # --------------------------------------------------------
    # SORT BY GAP
    # --------------------------------------------------------

    signals.sort(
        key=lambda x:
        x["gap_percent"],
        reverse=True
    )

    print()
    print(
        f"VALID SIGNALS FOUND: "
        f"{len(signals)}"
    )

    # --------------------------------------------------------
    # PROCESS ALERTS
    # --------------------------------------------------------

    fresh_signals = []
    hourly_repeats = []

    now = int(time.time())

    for signal in signals:

        key = signal_key(
            signal
        )

        existing = alerts.get(
            key
        )

        # ----------------------------------------------------
        # NEW BOS
        # ----------------------------------------------------

        if existing is None:

            fresh_signals.append(
                signal
            )

            alerts[key] = {
                "symbol":
                    signal["symbol"],

                "timeframe":
                    signal["timeframe"],

                "direction":
                    signal["direction"],

                "bos_timestamp":
                    signal["bos_timestamp"],

                "last_sent":
                    now,
            }

            signal_history.append(
                {
                    **signal,
                    "alert_type":
                        "NEW BOS",
                    "sent_at":
                        now,
                }
            )

        # ----------------------------------------------------
        # SAME BOS
        # ----------------------------------------------------

        else:

            last_sent = int(
                existing.get(
                    "last_sent",
                    0
                )
            )

            if (
                now - last_sent
                >= REPEAT_INTERVAL
            ):

                hourly_repeats.append(
                    signal
                )

                existing[
                    "last_sent"
                ] = now

                signal_history.append(
                    {
                        **signal,
                        "alert_type":
                            "HOURLY BOS REPEAT",
                        "sent_at":
                            now,
                    }
                )

    # --------------------------------------------------------
    # UPDATE DIAGNOSTICS
    # --------------------------------------------------------

    diagnostics[
        "fresh_signals"
    ] = len(fresh_signals)

    diagnostics[
        "hourly_repeats"
    ] = len(hourly_repeats)

    # --------------------------------------------------------
    # SAVE HISTORY
    # --------------------------------------------------------

    if len(signal_history) > 5000:

        signal_history = (
            signal_history[-5000:]
        )

    save_json(
        ALERTS_FILE,
        alerts
    )

    save_json(
        SIGNALS_FILE,
        signal_history
    )

    # --------------------------------------------------------
    # TELEGRAM NEW BOS
    # --------------------------------------------------------

    for signal in fresh_signals:

        message = (
            "🚨 NEW BOS SIGNAL\n\n"
            + format_signal(signal)
        )

        send_telegram(
            message
        )

        time.sleep(0.2)

    # --------------------------------------------------------
    # TELEGRAM HOURLY REPEAT
    # --------------------------------------------------------

    for signal in hourly_repeats:

        message = (
            "🔄 HOURLY BOS REPEAT\n\n"
            + format_signal(signal)
        )

        send_telegram(
            message
        )

        time.sleep(0.2)

    # --------------------------------------------------------
    # NO ALERTS
    # --------------------------------------------------------

    if (
        not fresh_signals
        and not hourly_repeats
    ):

        send_telegram(
            zero_signal_report()
        )

    # --------------------------------------------------------
    # CONSOLE SUMMARY
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("SCAN COMPLETE")
    print("=" * 72)

    print(
        f"FRESH BOS SIGNALS: "
        f"{len(fresh_signals)}"
    )

    print(
        f"HOURLY BOS REPEATS: "
        f"{len(hourly_repeats)}"
    )

    print(
        f"LONG SIGNALS: "
        f"{diagnostics['long_signals']}"
    )

    print(
        f"SHORT SIGNALS: "
        f"{diagnostics['short_signals']}"
    )

    print()

    if fresh_signals:

        print("NEW SIGNALS:")

        for signal in fresh_signals:

            print(
                f"{signal['symbol']} | "
                f"{signal['direction']} | "
                f"{signal['timeframe']} | "
                f"BOS {format_timestamp(signal['bos_timestamp'])}"
            )

    if hourly_repeats:

        print()
        print("HOURLY REPEATS:")

        for signal in hourly_repeats:

            print(
                f"{signal['symbol']} | "
                f"{signal['direction']} | "
                f"{signal['timeframe']}"
            )

    print()

    print_diagnostics()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    try:

        run_scan()

    except KeyboardInterrupt:

        print(
            "BOT STOPPED BY USER."
        )

    except Exception as e:

        print(
            f"FATAL ERROR: {e}"
        )
