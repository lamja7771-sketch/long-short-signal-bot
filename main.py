import os
import time
import json
import threading
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GATE_URL = "https://api.gateio.ws/api/v4"


# ============================================================
# TIMEFRAMES
# ============================================================
#
# IMPORTANT:
# Gate Futures API expects interval strings:
# "15m", "1h", "4h"
#
# Do NOT send 900 / 3600 / 14400 to Gate.
#

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
# PRICE / GAP SETTINGS
# ============================================================

# Price must be within 20% of the SMA50 -> EMA200 range.
PRICE_GAP_RATIO = 0.20

# Live price must be within 2% of EMA20.
EMA20_TOLERANCE = 0.02


# ============================================================
# TAKE PROFIT
# ============================================================

TP1_PERCENT = 0.05
TP2_PERCENT = 0.10


# ============================================================
# ALERT SETTINGS
# ============================================================

# Same setup can repeat every 60 minutes.
REPEAT_INTERVAL = 60 * 60


# ============================================================
# FUTURES CANDLE SETTINGS
# ============================================================

CANDLE_LIMIT = 1000


# ============================================================
# CONCURRENCY
# ============================================================

MAX_WORKERS = 6


# ============================================================
# RETRY SETTINGS
# ============================================================

MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.5


# ============================================================
# FILES
# ============================================================

ALERTS_FILE = "alerts.json"
SIGNALS_FILE = "signals.json"


# ============================================================
# HTTP HEADERS
# ============================================================

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/4.0"
}


# ============================================================
# GLOBAL SESSION
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)

file_lock = threading.Lock()


# ============================================================
# DIAGNOSTICS
# ============================================================

diagnostics = {
    # Universe
    "symbols_loaded": 0,
    "symbols_with_live_price": 0,
    "symbols_without_live_price": 0,

    # Scanning
    "total_scans": 0,

    # HTTP
    "candle_requests": 0,
    "candle_success": 0,
    "candle_failures": 0,

    "http_400": 0,
    "http_429": 0,
    "http_500_plus": 0,
    "request_errors": 0,

    # HTTP 400 by timeframe
    "http_400_15m": 0,
    "http_400_1h": 0,
    "http_400_4h": 0,

    # Candle problems
    "invalid_candles": 0,
    "insufficient_candles": 0,
    "no_closed_candles": 0,

    # Indicator
    "invalid_indicator": 0,

    # Strategy rejection
    "gap_rejected": 0,
    "price_position_rejected": 0,
    "price_gap_ratio_rejected": 0,
    "ema20_rejected": 0,

    # Direction
    "long_position_found": 0,
    "short_position_found": 0,

    # Valid setup
    "long_valid": 0,
    "short_valid": 0,

    # TP3
    "tp3_rejected": 0,

    # Final signals
    "long_signals": 0,
    "short_signals": 0,

    # Alerts
    "fresh_signals": 0,
    "hourly_repeats": 0,
}


# ============================================================
# DETAILED HTTP 400 TRACKING
# ============================================================

http_400_symbols = {
    "15m": set(),
    "1h": set(),
    "4h": set(),
}


# ============================================================
# PRINT HEADER
# ============================================================

def print_header():

    print()
    print("=" * 72)

    print("LONG + SHORT SIGNAL BOT")
    print("15M / 1H / 4H")

    print()

    print("FUTURES ONLY")
    print("NO SPOT")
    print("NO BOS")

    print()

    print("FUTURES SMA50 / FUTURES EMA20 / FUTURES EMA200")
    print("LIVE FUTURES ENTRY PRICE")
    print("FUTURES CLOSED CANDLES FOR INDICATORS")
    print("FUTURES CLOSED CANDLE = SL")

    print()

    print("TIMEFRAME-SPECIFIC MINIMUM GAP")
    print("15M GAP > 10%")
    print("1H GAP > 20%")
    print("4H GAP > 35%")

    print()

    print("PRICE / GAP RATIO = 2:10")
    print("EMA20 TOLERANCE = 2%")

    print()

    print("LONG = SMA50 < PRICE < EMA200")
    print("SHORT = EMA200 < PRICE < SMA50")

    print()

    print("NO BOS FILTER")
    print("NO SPOT API")

    print()

    print("NEW SETUP = IMMEDIATE ALERT")
    print("SAME SETUP = REPEAT EVERY 60 MINUTES")
    print("FRESH SCAN = EVERY 5 MINUTES")

    print()

    print("TP1 5% / TP2 10% / TP3 EMA200")

    print()

    print(f"FUTURES CANDLE LIMIT = {CANDLE_LIMIT}")
    print(f"MAX WORKERS = {MAX_WORKERS}")

    print()

    print("GATE INTERVALS = 15m / 1h / 4h")

    print("=" * 72)
    print()


# ============================================================
# SAFE COUNTER
# ============================================================

def increment_counter(name, amount=1):

    with file_lock:
        diagnostics[name] = diagnostics.get(name, 0) + amount


# ============================================================
# JSON HELPERS
# ============================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"Could not load {filename}: {e}"
        )

        return default


# ============================================================

def save_json(filename, data):

    temp_file = filename + ".tmp"

    with file_lock:

        try:

            with open(
                temp_file,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    data,
                    f,
                    indent=2
                )

            os.replace(
                temp_file,
                filename
            )

        except Exception as e:

            print(
                f"Could not save {filename}: {e}"
            )


# ============================================================
# HTTP GET
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

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            if response.status_code == 200:

                return response.json()


            # ------------------------------------------------
            # HTTP 400
            # ------------------------------------------------

            if response.status_code == 400:

                increment_counter("http_400")

                if timeframe:
                    increment_counter(
                        f"http_400_{timeframe}"
                    )

                if (
                    timeframe
                    and symbol
                ):

                    with file_lock:

                        http_400_symbols[
                            timeframe
                        ].add(symbol)

                # ------------------------------------------------
                # IMPORTANT:
                # 400 is normally a permanent request problem.
                # Do NOT retry it.
                # ------------------------------------------------

                return None


            # ------------------------------------------------
            # RATE LIMIT 429
            # ------------------------------------------------

            if response.status_code == 429:

                increment_counter("http_429")

                if attempt >= MAX_RETRIES:

                    print(
                        f"RATE LIMITED permanently: "
                        f"{description}"
                    )

                    return None

                retry_after = (
                    response.headers.get(
                        "Retry-After"
                    )
                )

                if retry_after:

                    try:
                        delay = float(
                            retry_after
                        )

                    except Exception:

                        delay = (
                            BASE_RETRY_DELAY
                            * (2 ** attempt)
                        )

                else:

                    delay = (
                        BASE_RETRY_DELAY
                        * (2 ** attempt)
                    )

                print(
                    f"RATE LIMITED: "
                    f"{description} | "
                    f"retry {attempt + 1}/"
                    f"{MAX_RETRIES} | "
                    f"waiting {delay:.2f}s"
                )

                time.sleep(delay)

                continue


            # ------------------------------------------------
            # SERVER ERROR
            # ------------------------------------------------

            if response.status_code >= 500:

                increment_counter(
                    "http_500_plus"
                )

                if attempt >= MAX_RETRIES:

                    print(
                        f"SERVER ERROR: "
                        f"{description} | "
                        f"HTTP {response.status_code}"
                    )

                    return None

                delay = (
                    BASE_RETRY_DELAY
                    * (2 ** attempt)
                )

                print(
                    f"SERVER ERROR: "
                    f"{description} | "
                    f"HTTP {response.status_code} | "
                    f"retry {attempt + 1}/"
                    f"{MAX_RETRIES} | "
                    f"waiting {delay:.2f}s"
                )

                time.sleep(delay)

                continue


            # ------------------------------------------------
            # OTHER HTTP ERROR
            # ------------------------------------------------

            print(
                f"Gate HTTP {response.status_code}: "
                f"{description}"
            )

            return None


        except requests.RequestException as e:

            increment_counter(
                "request_errors"
            )

            if attempt >= MAX_RETRIES:

                print(
                    f"REQUEST FAILED: "
                    f"{description} | {e}"
                )

                return None

            delay = (
                BASE_RETRY_DELAY
                * (2 ** attempt)
            )

            print(
                f"REQUEST ERROR: "
                f"{description} | "
                f"retry {attempt + 1}/"
                f"{MAX_RETRIES} | "
                f"waiting {delay:.2f}s"
            )

            time.sleep(delay)


        except Exception as e:

            print(
                f"UNEXPECTED REQUEST ERROR: "
                f"{description} | {e}"
            )

            return None


    return None


# ============================================================
# GET FUTURES SYMBOLS
# ============================================================

def get_futures_symbols():

    data = gate_get(
        "/futures/usdt/contracts",
        description="Futures contracts"
    )

    if not data:

        return []


    symbols = []

    for item in data:

        try:

            name = item.get("name")

            if not name:
                continue

            # ------------------------------------------------
            # Only USDT Futures
            # ------------------------------------------------

            if not name.endswith("_USDT"):
                continue


            # ------------------------------------------------
            # Trading status
            # ------------------------------------------------

            status = item.get("status")

            if status:

                if status not in (
                    "trading",
                    "open"
                ):

                    continue


            symbols.append(name)

        except Exception:

            continue


    symbols = sorted(
        set(symbols)
    )

    diagnostics[
        "symbols_loaded"
    ] = len(symbols)

    return symbols


# ============================================================
# GET LIVE FUTURES PRICES
# ============================================================

def get_futures_prices():

    start = time.time()

    data = gate_get(
        "/futures/usdt/tickers",
        description="Futures live prices"
    )

    prices = {}

    if not data:

        return prices


    for item in data:

        try:

            contract = item.get(
                "contract"
            )

            last = item.get(
                "last"
            )

            if not contract:
                continue

            if last is None:
                continue

            price = float(last)

            if price <= 0:
                continue

            prices[contract] = price

        except Exception:

            continue


    elapsed = (
        time.time()
        - start
    )

    print(
        f"Loaded {len(prices)} live Futures prices "
        f"in {elapsed:.2f}s"
    )

    return prices


# ============================================================
# PARSE FUTURES CANDLE
# ============================================================

def parse_candle(row):

    try:

        # ----------------------------------------------------
        # Gate list format
        #
        # [timestamp, volume, close, high, low, open, ...]
        # ----------------------------------------------------

        if isinstance(row, list):

            if len(row) < 6:

                return None

            timestamp = float(
                row[0]
            )

            volume = float(
                row[1]
            )

            close = float(
                row[2]
            )

            high = float(
                row[3]
            )

            low = float(
                row[4]
            )

            open_price = float(
                row[5]
            )


        # ----------------------------------------------------
        # Defensive dictionary support
        # ----------------------------------------------------

        elif isinstance(row, dict):

            timestamp_value = (
                row.get("t")
                if row.get("t") is not None
                else row.get("timestamp")
            )

            volume_value = (
                row.get("v")
                if row.get("v") is not None
                else row.get("volume", 0)
            )

            close_value = (
                row.get("c")
                if row.get("c") is not None
                else row.get("close")
            )

            high_value = (
                row.get("h")
                if row.get("h") is not None
                else row.get("high")
            )

            low_value = (
                row.get("l")
                if row.get("l") is not None
                else row.get("low")
            )

            open_value = (
                row.get("o")
                if row.get("o") is not None
                else row.get("open")
            )

            if (
                timestamp_value is None
                or close_value is None
                or high_value is None
                or low_value is None
                or open_value is None
            ):

                return None


            timestamp = float(
                timestamp_value
            )

            volume = float(
                volume_value
            )

            close = float(
                close_value
            )

            high = float(
                high_value
            )

            low = float(
                low_value
            )

            open_price = float(
                open_value
            )


        else:

            return None


        # ----------------------------------------------------
        # Validate values
        # ----------------------------------------------------

        if (
            timestamp <= 0
            or close <= 0
            or high <= 0
            or low <= 0
            or open_price <= 0
        ):

            return None


        return {
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }


    except (
        TypeError,
        ValueError,
        KeyError
    ):

        return None


# ============================================================
# GET FUTURES CANDLES
# ============================================================

def get_futures_candles(
    symbol,
    timeframe
):

    timeframe_config = TIMEFRAMES[
        timeframe
    ]

    interval = timeframe_config[
        "interval"
    ]

    interval_seconds = timeframe_config[
        "seconds"
    ]

    increment_counter(
        "candle_requests"
    )


    data = gate_get(
        "/futures/usdt/candlesticks",

        params={
            "contract": symbol,
            "interval": interval,
            "limit": CANDLE_LIMIT,
        },

        description=(
            f"Futures {symbol} {timeframe}"
        ),

        timeframe=timeframe,
        symbol=symbol
    )


    # --------------------------------------------------------
    # Failed HTTP request
    # --------------------------------------------------------

    if not data:

        increment_counter(
            "candle_failures"
        )

        return None


    candles = []


    # --------------------------------------------------------
    # Parse
    # --------------------------------------------------------

    try:

        for row in data:

            candle = parse_candle(
                row
            )

            if candle is not None:

                candles.append(
                    candle
                )


    except Exception as e:

        print(
            f"Invalid candle data: "
            f"{symbol} {timeframe} | {e}"
        )

        increment_counter(
            "invalid_candles"
        )

        return None


    # --------------------------------------------------------
    # Sort oldest -> newest
    # --------------------------------------------------------

    candles.sort(
        key=lambda x: x["timestamp"]
    )


    # --------------------------------------------------------
    # Remove duplicate timestamps
    # --------------------------------------------------------

    unique = {}

    for candle in candles:

        unique[
            candle["timestamp"]
        ] = candle


    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x: x["timestamp"]
    )


    # --------------------------------------------------------
    # Basic candle count
    # --------------------------------------------------------

    if len(candles) < EMA_SLOW:

        increment_counter(
            "insufficient_candles"
        )

        return None


    # --------------------------------------------------------
    # Remove currently-forming candle
    #
    # We only use CLOSED Futures candles for:
    #
    # SMA50
    # EMA20
    # EMA200
    # SL
    # --------------------------------------------------------

    now = time.time()

    closed = []

    for candle in candles:

        candle_close_time = (
            candle["timestamp"]
            + interval_seconds
        )

        if candle_close_time <= now:

            closed.append(
                candle
            )


    if not closed:

        increment_counter(
            "no_closed_candles"
        )

        return None


    # --------------------------------------------------------
    # Proper EMA warm-up
    # --------------------------------------------------------

    if len(closed) < EMA_SLOW:

        increment_counter(
            "insufficient_candles"
        )

        return None


    increment_counter(
        "candle_success"
    )

    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    values,
    period
):

    if len(values) < period:

        return None


    # --------------------------------------------------------
    # Proper SMA seed
    # --------------------------------------------------------

    ema = sum(
        values[:period]
    ) / period


    multiplier = (
        2.0
        / (period + 1.0)
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

def calculate_sma(
    values,
    period
):

    if len(values) < period:

        return None


    return (
        sum(values[-period:])
        / period
    )


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(
    candles
):

    if not candles:

        return None


    closes = [
        float(
            candle["close"]
        )
        for candle in candles
    ]


    if len(closes) < EMA_SLOW:

        return None


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
    ):

        return None


    return {
        "sma50": sma50,
        "ema20": ema20,
        "ema200": ema200,
    }


# ============================================================
# GAP
# ============================================================

def calculate_gap_percent(
    sma50,
    ema200
):

    if (
        sma50 <= 0
        or ema200 <= 0
    ):

        return 0.0


    return (
        abs(
            ema200 - sma50
        )
        / min(
            sma50,
            ema200
        )
    ) * 100.0


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    timeframe,
    live_price
):

    increment_counter(
        "total_scans"
    )


    # --------------------------------------------------------
    # Futures candles
    # --------------------------------------------------------

    candles = get_futures_candles(
        symbol,
        timeframe
    )


    if not candles:

        return None


    # --------------------------------------------------------
    # Indicators
    # --------------------------------------------------------

    indicators = calculate_indicators(
        candles
    )


    if not indicators:

        increment_counter(
            "invalid_indicator"
        )

        return None


    sma50 = indicators[
        "sma50"
    ]

    ema20 = indicators[
        "ema20"
    ]

    ema200 = indicators[
        "ema200"
    ]


    # --------------------------------------------------------
    # LIVE FUTURES PRICE
    # --------------------------------------------------------

    try:

        price = float(
            live_price
        )

    except Exception:

        return None


    if price <= 0:

        return None


    # ========================================================
    # GAP FILTER
    # ========================================================

    gap = calculate_gap_percent(
        sma50,
        ema200
    )


    minimum_gap = TIMEFRAMES[
        timeframe
    ]["gap_minimum"]


    if gap < minimum_gap:

        increment_counter(
            "gap_rejected"
        )

        return None


    # ========================================================
    # PRICE POSITION
    # ========================================================
    #
    # LONG:
    # SMA50 < PRICE < EMA200
    #
    # SHORT:
    # EMA200 < PRICE < SMA50
    #
    # ========================================================

    long_position = (
        sma50
        < price
        < ema200
    )

    short_position = (
        ema200
        < price
        < sma50
    )


    if long_position:

        increment_counter(
            "long_position_found"
        )

    elif short_position:

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


    allowed_price_distance = (
        total_gap
        * PRICE_GAP_RATIO
    )


    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if long_position:

        distance_from_sma = (
            price - sma50
        )

        if (
            distance_from_sma
            > allowed_price_distance
        ):

            increment_counter(
                "price_gap_ratio_rejected"
            )

            return None


    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        distance_from_sma = (
            sma50 - price
        )

        if (
            distance_from_sma
            > allowed_price_distance
        ):

            increment_counter(
                "price_gap_ratio_rejected"
            )

            return None


    # ========================================================
    # EMA20 FILTER
    # ========================================================

    ema20_distance = (
        abs(
            price - ema20
        )
        / ema20
    )


    if (
        ema20_distance
        > EMA20_TOLERANCE
    ):

        increment_counter(
            "ema20_rejected"
        )

        return None


    # ========================================================
    # MOST RECENT CLOSED FUTURES CANDLE
    # ========================================================

    last_closed = candles[-1]


    if long_position:

        direction = "LONG"

        sl = float(
            last_closed["low"]
        )


    else:

        direction = "SHORT"

        sl = float(
            last_closed["high"]
        )


    # ========================================================
    # TAKE PROFITS
    # ========================================================

    if direction == "LONG":

        tp1 = (
            price
            * (1 + TP1_PERCENT)
        )

        tp2 = (
            price
            * (1 + TP2_PERCENT)
        )

        tp3 = ema200


        if tp3 <= price:

            increment_counter(
                "tp3_rejected"
            )

            return None


    else:

        tp1 = (
            price
            * (1 - TP1_PERCENT)
        )

        tp2 = (
            price
            * (1 - TP2_PERCENT)
        )

        tp3 = ema200


        if tp3 >= price:

            increment_counter(
                "tp3_rejected"
            )

            return None


    # ========================================================
    # VALID SETUP
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


    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,

        "entry": price,

        "sma50": sma50,
        "ema20": ema20,
        "ema200": ema200,

        "gap": gap,

        "sl": sl,

        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,

        "last_candle_timestamp":
            last_closed[
                "timestamp"
            ],
    }


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(
    value
):

    value = float(value)


    if value >= 1000:

        return f"{value:.2f}"


    if value >= 1:

        return f"{value:.6f}"


    if value >= 0.01:

        return f"{value:.8f}"


    return f"{value:.10f}"


# ============================================================
# SIGNAL KEY
# ============================================================

def signal_key(
    signal
):

    return (
        f"{signal['symbol']}_"
        f"{signal['timeframe']}_"
        f"{signal['direction']}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if (
        not BOT_TOKEN
        or not CHAT_ID
    ):

        print(
            "Telegram credentials missing."
        )

        return False


    url = (
        "https://api.telegram.org/"
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
            f"Telegram error "
            f"{response.status_code}: "
            f"{response.text}"
        )


    except Exception as e:

        print(
            f"Telegram error: {e}"
        )


    return False


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    signal
):

    emoji = (
        "🟢"
        if signal["direction"] == "LONG"
        else "🔴"
    )


    return (

        f"{emoji} "
        f"#{signal['symbol'].replace('_USDT', '')} "
        f"{signal['direction']} "
        f"{signal['timeframe']}\n\n"

        f"Entry: "
        f"${format_price(signal['entry'])}\n"

        f"SL: "
        f"${format_price(signal['sl'])}\n\n"

        f"TP1: "
        f"${format_price(signal['tp1'])}\n"

        f"TP2: "
        f"${format_price(signal['tp2'])}\n"

        f"TP3: "
        f"${format_price(signal['tp3'])}\n\n"

        f"SMA50: "
        f"${format_price(signal['sma50'])}\n"

        f"EMA20: "
        f"${format_price(signal['ema20'])}\n"

        f"EMA200: "
        f"${format_price(signal['ema200'])}\n\n"

        f"Gap: "
        f"{signal['gap']:.2f}%"
    )


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def print_diagnostics():

    print()

    print("=" * 72)
    print("DETAILED DIAGNOSTIC REJECTION SUMMARY")
    print("=" * 72)


    print()
    print("UNIVERSE")
    print("-" * 72)

    print(
        f"FUTURES SYMBOLS LOADED       : "
        f"{diagnostics['symbols_loaded']}"
    )

    print(
        f"LIVE FUTURES PRICES          : "
        f"{diagnostics['symbols_with_live_price']}"
    )

    print(
        f"WITHOUT LIVE PRICE           : "
        f"{diagnostics['symbols_without_live_price']}"
    )


    print()
    print("SCAN")
    print("-" * 72)

    print(
        f"TOTAL SCANS                  : "
        f"{diagnostics['total_scans']}"
    )


    print()
    print("CANDLE REQUESTS")
    print("-" * 72)

    print(
        f"CANDLE REQUESTS              : "
        f"{diagnostics['candle_requests']}"
    )

    print(
        f"CANDLE SUCCESS               : "
        f"{diagnostics['candle_success']}"
    )

    print(
        f"CANDLE FAILURES              : "
        f"{diagnostics['candle_failures']}"
    )


    print()
    print("HTTP ERRORS")
    print("-" * 72)

    print(
        f"HTTP 400                     : "
        f"{diagnostics['http_400']}"
    )

    print(
        f"HTTP 400 - 15M               : "
        f"{diagnostics['http_400_15m']}"
    )

    print(
        f"HTTP 400 - 1H                : "
        f"{diagnostics['http_400_1h']}"
    )

    print(
        f"HTTP 400 - 4H                : "
        f"{diagnostics['http_400_4h']}"
    )

    print(
        f"HTTP 429 RATE LIMIT          : "
        f"{diagnostics['http_429']}"
    )

    print(
        f"HTTP 500+                    : "
        f"{diagnostics['http_500_plus']}"
    )

    print(
        f"REQUEST ERRORS               : "
        f"{diagnostics['request_errors']}"
    )


    print()
    print("CANDLE DATA")
    print("-" * 72)

    print(
        f"INVALID CANDLES              : "
        f"{diagnostics['invalid_candles']}"
    )

    print(
        f"INSUFFICIENT CANDLES         : "
        f"{diagnostics['insufficient_candles']}"
    )

    print(
        f"NO CLOSED CANDLES            : "
        f"{diagnostics['no_closed_candles']}"
    )

    print(
        f"INVALID INDICATORS           : "
        f"{diagnostics['invalid_indicator']}"
    )


    print()
    print("STRATEGY REJECTIONS")
    print("-" * 72)

    print(
        f"REJECTED BY GAP              : "
        f"{diagnostics['gap_rejected']}"
    )

    print(
        f"REJECTED BY PRICE POSITION   : "
        f"{diagnostics['price_position_rejected']}"
    )

    print(
        f"REJECTED BY PRICE/GAP RATIO  : "
        f"{diagnostics['price_gap_ratio_rejected']}"
    )

    print(
        f"REJECTED BY EMA20            : "
        f"{diagnostics['ema20_rejected']}"
    )

    print(
        f"REJECTED BY TP3              : "
        f"{diagnostics['tp3_rejected']}"
    )


    print()
    print("PRICE POSITIONS")
    print("-" * 72)

    print(
        f"LONG POSITION FOUND          : "
        f"{diagnostics['long_position_found']}"
    )

    print(
        f"SHORT POSITION FOUND         : "
        f"{diagnostics['short_position_found']}"
    )


    print()
    print("VALID SETUPS")
    print("-" * 72)

    print(
        f"VALID LONG SETUPS            : "
        f"{diagnostics['long_valid']}"
    )

    print(
        f"VALID SHORT SETUPS           : "
        f"{diagnostics['short_valid']}"
    )


    print()
    print("SIGNALS")
    print("-" * 72)

    print(
        f"LONG SIGNALS                 : "
        f"{diagnostics['long_signals']}"
    )

    print(
        f"SHORT SIGNALS                : "
        f"{diagnostics['short_signals']}"
    )


    print()
    print("ALERTS")
    print("-" * 72)

    print(
        f"FRESH SIGNALS                : "
        f"{diagnostics['fresh_signals']}"
    )

    print(
        f"HOURLY REPEATS               : "
        f"{diagnostics['hourly_repeats']}"
    )

    print(
        f"TOTAL SIGNAL ALERTS          : "
        f"{diagnostics['fresh_signals'] + diagnostics['hourly_repeats']}"
    )


    # --------------------------------------------------------
    # HTTP 400 SYMBOLS
    # --------------------------------------------------------

    print()
    print("HTTP 400 SYMBOL DETAILS")
    print("-" * 72)

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
            f"{timeframe.upper()} "
            f"HTTP 400 SYMBOLS          : "
            f"{len(symbols)}"
        )

        if symbols:

            # Don't flood GitHub Actions.
            # Show maximum 30.
            shown = symbols[:30]

            print(
                "  "
                + ", ".join(shown)
            )

            if len(symbols) > 30:

                print(
                    f"  ... and "
                    f"{len(symbols) - 30} more"
                )


    print("=" * 72)
    print()


# ============================================================
# ZERO SIGNAL TELEGRAM REPORT
# ============================================================

def zero_signal_report():

    return (

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 LONG + SHORT SIGNAL BOT\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "15M / 1H / 4H\n"
        "FUTURES ONLY\n"
        "NO SPOT\n"
        "NO BOS\n\n"

        "No fresh signals found in this scan.\n\n"

        f"Scans: "
        f"{diagnostics['total_scans']}\n"

        f"Candle requests: "
        f"{diagnostics['candle_requests']}\n"

        f"Candle failures: "
        f"{diagnostics['candle_failures']}\n"

        f"HTTP 400: "
        f"{diagnostics['http_400']}\n"

        f"HTTP 429: "
        f"{diagnostics['http_429']}\n\n"

        f"Gap rejected: "
        f"{diagnostics['gap_rejected']}\n"

        f"Price rejected: "
        f"{diagnostics['price_position_rejected']}\n"

        f"Price/Gap rejected: "
        f"{diagnostics['price_gap_ratio_rejected']}\n"

        f"EMA20 rejected: "
        f"{diagnostics['ema20_rejected']}\n\n"

        f"Fresh signals: "
        f"{diagnostics['fresh_signals']}\n"

        f"Hourly repeats: "
        f"{diagnostics['hourly_repeats']}\n"

        "━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print_header()


    # --------------------------------------------------------
    # RESET HTTP 400 SYMBOL TRACKING
    # --------------------------------------------------------

    for timeframe in (
        "15m",
        "1h",
        "4h"
    ):

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

    signals_history = load_json(
        SIGNALS_FILE,
        []
    )


    # --------------------------------------------------------
    # GET FUTURES UNIVERSE
    # --------------------------------------------------------

    symbols = get_futures_symbols()


    if not symbols:

        print(
            "No Futures symbols found."
        )

        return


    print(
        f"Scanning {len(symbols)} Futures symbols..."
    )


    # --------------------------------------------------------
    # GET LIVE FUTURES PRICES
    # --------------------------------------------------------

    futures_prices = (
        get_futures_prices()
    )


    if not futures_prices:

        print(
            "No live Futures prices loaded."
        )

        return


    diagnostics[
        "symbols_with_live_price"
    ] = sum(
        1
        for symbol in symbols
        if symbol in futures_prices
    )


    diagnostics[
        "symbols_without_live_price"
    ] = (
        len(symbols)
        - diagnostics[
            "symbols_with_live_price"
        ]
    )


    # --------------------------------------------------------
    # CREATE JOBS
    # --------------------------------------------------------

    jobs = []


    for symbol in symbols:

        live_price = (
            futures_prices.get(
                symbol
            )
        )


        if live_price is None:

            continue


        for timeframe in TIMEFRAMES:

            jobs.append(
                (
                    symbol,
                    timeframe,
                    live_price
                )
            )


    print(
        f"Total scans: {len(jobs)}"
    )


    results = []


    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    completed = 0

    start_time = time.time()


    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        future_map = {

            executor.submit(
                analyze_symbol,
                symbol,
                timeframe,
                price
            ): (
                symbol,
                timeframe
            )

            for (
                symbol,
                timeframe,
                price
            ) in jobs
        }


        for future in as_completed(
            future_map
        ):

            completed += 1


            try:

                result = future.result()


                if result:

                    results.append(
                        result
                    )


            except Exception as e:

                symbol, timeframe = (
                    future_map[
                        future
                    ]
                )


                print(
                    f"ERROR: "
                    f"{symbol} "
                    f"{timeframe} | {e}"
                )


            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            if (
                completed % 300 == 0
                or completed == len(jobs)
            ):

                elapsed = (
                    time.time()
                    - start_time
                )


                print(
                    f"Progress: "
                    f"{completed}/"
                    f"{len(jobs)} | "
                    f"{elapsed:.1f}s"
                )


    # --------------------------------------------------------
    # SORT BY GAP
    # --------------------------------------------------------

    results.sort(
        key=lambda x: x["gap"],
        reverse=True
    )


    # --------------------------------------------------------
    # PROCESS ALERT HISTORY
    # --------------------------------------------------------

    now = time.time()


    fresh_signals = []
    hourly_repeats = []


    for signal in results:

        key = signal_key(
            signal
        )


        previous = alerts.get(
            key
        )


        # ====================================================
        # NEW SETUP
        # ====================================================

        if previous is None:

            fresh_signals.append(
                signal
            )


            alerts[key] = {

                "last_alert": now,

                "direction":
                    signal["direction"],

                "timeframe":
                    signal["timeframe"],

                "entry":
                    signal["entry"],

                "gap":
                    signal["gap"],

            }


            signals_history.append({

                "timestamp": now,

                "type": "fresh",

                **signal,

            })


            continue


        # ====================================================
        # SAME SETUP
        # ====================================================

        last_alert = float(
            previous.get(
                "last_alert",
                0
            )
        )


        previous_direction = (
            previous.get(
                "direction"
            )
        )


        previous_timeframe = (
            previous.get(
                "timeframe"
            )
        )


        same_setup = (

            previous_direction
            == signal["direction"]

            and

            previous_timeframe
            == signal["timeframe"]

        )


        if (

            same_setup

            and

            now - last_alert
            >= REPEAT_INTERVAL

        ):

            hourly_repeats.append(
                signal
            )


            alerts[key][
                "last_alert"
            ] = now


            alerts[key][
                "entry"
            ] = signal[
                "entry"
            ]


            alerts[key][
                "gap"
            ] = signal[
                "gap"
            ]


            signals_history.append({

                "timestamp": now,

                "type":
                    "hourly_repeat",

                **signal,

            })


    # --------------------------------------------------------
    # COUNTERS
    # --------------------------------------------------------

    diagnostics[
        "fresh_signals"
    ] = len(
        fresh_signals
    )


    diagnostics[
        "hourly_repeats"
    ] = len(
        hourly_repeats
    )


    # --------------------------------------------------------
    # SAVE ALERT HISTORY
    # --------------------------------------------------------

    save_json(
        ALERTS_FILE,
        alerts
    )


    # --------------------------------------------------------
    # LIMIT SIGNAL HISTORY
    # --------------------------------------------------------

    if len(
        signals_history
    ) > 5000:

        signals_history = (
            signals_history[-5000:]
        )


    save_json(
        SIGNALS_FILE,
        signals_history
    )


    # ========================================================
    # SEND FRESH SIGNALS
    # ========================================================

    for signal in fresh_signals:

        message = (
            "🚨 NEW SIGNAL\n\n"
            + format_signal(
                signal
            )
        )


        send_telegram(
            message
        )


        time.sleep(
            0.2
        )


    # ========================================================
    # SEND HOURLY REPEATS
    # ========================================================

    for signal in hourly_repeats:

        message = (
            "🔄 HOURLY REPEAT\n\n"
            + format_signal(
                signal
            )
        )


        send_telegram(
            message
        )


        time.sleep(
            0.2
        )


    # ========================================================
    # ZERO SIGNAL REPORT
    # ========================================================

    if (

        len(fresh_signals) == 0

        and

        len(hourly_repeats) == 0

    ):

        send_telegram(
            zero_signal_report()
        )


    # ========================================================
    # PRINT RESULTS
    # ========================================================

    print()

    print(
        f"FRESH SIGNALS: "
        f"{len(fresh_signals)}"
    )


    print(
        f"HOURLY REPEATS: "
        f"{len(hourly_repeats)}"
    )


    print(
        f"TOTAL SIGNAL ALERTS: "
        f"{len(fresh_signals) + len(hourly_repeats)}"
    )


    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    print_diagnostics()


    # ========================================================
    # PRINT ACTUAL SIGNALS
    # ========================================================

    if fresh_signals:

        print()
        print("=" * 72)
        print("FRESH SIGNALS")
        print("=" * 72)


        for signal in fresh_signals:

            print(

                f"{signal['direction']} "

                f"{signal['symbol']} "

                f"{signal['timeframe']} "

                f"Gap="
                f"{signal['gap']:.2f}% "

                f"Entry="
                f"{format_price(signal['entry'])}"

            )


    if hourly_repeats:

        print()
        print("=" * 72)
        print("HOURLY REPEATS")
        print("=" * 72)


        for signal in hourly_repeats:

            print(

                f"{signal['direction']} "

                f"{signal['symbol']} "

                f"{signal['timeframe']} "

                f"Gap="
                f"{signal['gap']:.2f}% "

                f"Entry="
                f"{format_price(signal['entry'])}"

            )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        run_scan()


    except KeyboardInterrupt:

        print(
            "Stopped."
        )


    except Exception as e:

        print()
        print("=" * 72)
        print("FATAL ERROR")
        print("=" * 72)
        print(e)
        print("=" * 72)

        raise
