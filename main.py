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
# Gate Futures candlesticks expects interval STRINGS.
#
# DO NOT use:
#   900
#   3600
#   14400
#
# Use:
#   15m
#   1h
#   4h
#
# ============================================================

TIMEFRAMES = {
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
}


# ============================================================
# INDICATORS
# ============================================================

SMA_PERIOD = 50
EMA_FAST = 20
EMA_SLOW = 200


# ============================================================
# MINIMUM SMA50 / EMA200 GAP
# ============================================================

GAP_MINIMUM = {
    "15m": 10.0,
    "1h": 20.0,
    "4h": 35.0,
}


# ============================================================
# PRICE / GAP RATIO
# ============================================================
#
# Price must remain within 20% of the SMA50 -> EMA200 range.
#
# LONG:
#   SMA50 < PRICE < EMA200
#   distance from SMA50 <= 20% of total gap
#
# SHORT:
#   EMA200 < PRICE < SMA50
#   distance from SMA50 <= 20% of total gap
#
# ============================================================

PRICE_GAP_RATIO = 0.20


# ============================================================
# EMA20 TOLERANCE
# ============================================================

EMA20_TOLERANCE = 0.02


# ============================================================
# TP SETTINGS
# ============================================================

TP1_PERCENT = 0.05
TP2_PERCENT = 0.10


# ============================================================
# ALERT REPEAT
# ============================================================

REPEAT_INTERVAL = 60 * 60


# ============================================================
# FUTURES CANDLE SETTINGS
# ============================================================

CANDLE_LIMIT = 1000


# ============================================================
# CONCURRENCY
# ============================================================
#
# Keep this at 6.
#
# Higher concurrency can cause Gate HTTP 429 errors.
#
# ============================================================

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
# HTTP HEADERS
# ============================================================

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/4.0"
}


# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)

file_lock = threading.Lock()
diagnostic_lock = threading.Lock()


# ============================================================
# DIAGNOSTIC COUNTERS
# ============================================================

diagnostics = {
    # Overall
    "total_scans": 0,

    # Symbols / prices
    "symbols_loaded": 0,
    "prices_loaded": 0,
    "missing_live_price": 0,

    # HTTP
    "candle_requests": 0,
    "candle_success": 0,
    "candle_failures": 0,

    "http_400": 0,
    "http_404": 0,
    "http_429": 0,
    "http_other": 0,

    "rate_limit_retries": 0,
    "request_retries": 0,

    # Candle validation
    "invalid_candles": 0,
    "insufficient_candles": 0,
    "no_closed_candles": 0,

    # Indicators
    "invalid_indicator": 0,

    # Strategy rejection
    "gap_rejected": 0,
    "price_rejected": 0,
    "price_gap_rejected": 0,
    "ema20_rejected": 0,

    # TP validation
    "tp3_rejected": 0,

    # Valid setups
    "long_valid": 0,
    "short_valid": 0,

    # Signals
    "long_signals": 0,
    "short_signals": 0,

    # Alerts
    "fresh_signals": 0,
    "hourly_repeats": 0,
}


def increment_counter(name, amount=1):
    with diagnostic_lock:
        diagnostics[name] = diagnostics.get(name, 0) + amount


# ============================================================
# HEADER
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
    print("HTTP 400 IS NOT RETRIED")
    print("HTTP 429 USES EXPONENTIAL BACKOFF")
    print("=" * 72)
    print()


# ============================================================
# FILE HELPERS
# ============================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"Could not load {filename}: {e}"
        )

        return default


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
# GATE HTTP GET
# ============================================================

def gate_get(
    path,
    params=None,
    description=""
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

                try:
                    return response.json()

                except Exception as e:

                    print(
                        f"INVALID JSON: "
                        f"{description} | {e}"
                    )

                    return None

            # ------------------------------------------------
            # HTTP 400
            #
            # IMPORTANT:
            # 400 is a bad request.
            # Retrying it does not help.
            # ------------------------------------------------

            if response.status_code == 400:

                increment_counter("http_400")

                print(
                    f"Gate HTTP 400: "
                    f"{description}"
                )

                return None

            # ------------------------------------------------
            # HTTP 404
            # ------------------------------------------------

            if response.status_code == 404:

                increment_counter("http_404")

                print(
                    f"Gate HTTP 404: "
                    f"{description}"
                )

                return None

            # ------------------------------------------------
            # HTTP 429
            #
            # Retry with exponential backoff.
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

                increment_counter(
                    "rate_limit_retries"
                )

                print(
                    f"RATE LIMITED: "
                    f"{description} | "
                    f"retry "
                    f"{attempt + 1}/{MAX_RETRIES} | "
                    f"waiting {delay:.2f}s"
                )

                time.sleep(delay)

                continue

            # ------------------------------------------------
            # OTHER HTTP ERROR
            # ------------------------------------------------

            increment_counter(
                "http_other"
            )

            print(
                f"Gate HTTP "
                f"{response.status_code}: "
                f"{description}"
            )

            return None

        # ----------------------------------------------------
        # REQUEST ERROR
        # ----------------------------------------------------

        except requests.RequestException as e:

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

            increment_counter(
                "request_retries"
            )

            print(
                f"REQUEST ERROR: "
                f"{description} | "
                f"retry "
                f"{attempt + 1}/{MAX_RETRIES} | "
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
# GET FUTURES CONTRACTS
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

            name = str(name).strip()

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

                status = str(
                    status
                ).lower()

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

    increment_counter(
        "symbols_loaded",
        len(symbols)
    )

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

    increment_counter(
        "prices_loaded",
        len(prices)
    )

    elapsed = (
        time.time() - start
    )

    print(
        f"Loaded {len(prices)} live Futures prices "
        f"in {elapsed:.2f}s"
    )

    return prices


# ============================================================
# FUTURES CANDLES
# ============================================================

def get_futures_candles(
    symbol,
    timeframe
):

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Gate requires:
    #
    # 15m
    # 1h
    # 4h
    #
    # NOT:
    #
    # 900
    # 3600
    # 14400
    # --------------------------------------------------------

    interval = TIMEFRAMES[
        timeframe
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
            f"Futures "
            f"{symbol} "
            f"{timeframe}"
        )
    )

    if not data:

        increment_counter(
            "candle_failures"
        )

        return None

    candles = []

    try:

        for row in data:

            # ------------------------------------------------
            # Gate Futures normally returns:
            #
            # [timestamp, volume, close, high, low, open]
            # ------------------------------------------------

            if isinstance(row, list):

                if len(row) < 6:
                    continue

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

            elif isinstance(row, dict):

                timestamp = float(
                    row.get(
                        "t",
                        row.get(
                            "timestamp"
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
                            "close"
                        )
                    )
                )

                high = float(
                    row.get(
                        "h",
                        row.get(
                            "high"
                        )
                    )
                )

                low = float(
                    row.get(
                        "l",
                        row.get(
                            "low"
                        )
                    )

                open_price = float(
                    row.get(
                        "o",
                        row.get(
                            "open"
                        )
                    )

                )

            else:
                continue

            # ------------------------------------------------
            # Validate OHLC
            # ------------------------------------------------

            if (
                close <= 0
                or high <= 0
                or low <= 0
                or open_price <= 0
            ):
                continue

            candles.append(
                {
                    "timestamp": timestamp,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )

        # ----------------------------------------------------
        # Sort oldest -> newest
        # ----------------------------------------------------

        candles.sort(
            key=lambda x:
            x["timestamp"]
        )

    except Exception as e:

        increment_counter(
            "invalid_candles"
        )

        print(
            f"Invalid candle data: "
            f"{symbol} "
            f"{timeframe} | "
            f"{e}"
        )

        return None

    # --------------------------------------------------------
    # Need enough raw candles
    # --------------------------------------------------------

    if len(candles) < (
        EMA_SLOW + 10
    ):

        increment_counter(
            "insufficient_candles"
        )

        return None

    # --------------------------------------------------------
    # REMOVE CURRENTLY FORMING CANDLE
    #
    # Indicators use CLOSED Futures candles only.
    # --------------------------------------------------------

    now = time.time()

    closed = []

    # Seconds per candle
    interval_seconds = {
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
    }[timeframe]

    for candle in candles:

        candle_close_time = (
            candle["timestamp"]
            + interval_seconds
        )

        if candle_close_time <= now:

            closed.append(
                candle
            )

    if len(closed) < (
        EMA_SLOW + 5
    ):

        increment_counter(
            "no_closed_candles"
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

    multiplier = (
        2.0
        / (period + 1.0)
    )

    # Proper SMA seed
    ema = (
        sum(
            values[:period]
        )
        / period
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
        sum(
            values[-period:]
        )
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

    if (
        sma50 <= 0
        or ema20 <= 0
        or ema200 <= 0
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
    # FUTURES CANDLES ONLY
    # --------------------------------------------------------

    candles = get_futures_candles(
        symbol,
        timeframe
    )

    if not candles:
        return None

    # --------------------------------------------------------
    # INDICATORS
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

        increment_counter(
            "missing_live_price"
        )

        return None

    if price <= 0:

        increment_counter(
            "missing_live_price"
        )

        return None

    # ========================================================
    # GAP FILTER
    # ========================================================

    gap = calculate_gap_percent(
        sma50,
        ema200
    )

    minimum_gap = GAP_MINIMUM[
        timeframe
    ]

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
    #   SMA50 < PRICE < EMA200
    #
    # SHORT:
    #   EMA200 < PRICE < SMA50
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

    if (
        not long_position
        and
        not short_position
    ):

        increment_counter(
            "price_rejected"
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

    if long_position:

        distance_from_sma = (
            price - sma50
        )

        if (
            distance_from_sma
            > allowed_price_distance
        ):

            increment_counter(
                "price_gap_rejected"
            )

            return None

    elif short_position:

        distance_from_sma = (
            sma50 - price
        )

        if (
            distance_from_sma
            > allowed_price_distance
        ):

            increment_counter(
                "price_gap_rejected"
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
    # DIRECTION
    # ========================================================

    if long_position:

        direction = "LONG"

    else:

        direction = "SHORT"

    # ========================================================
    # VALID SETUP COUNT
    # ========================================================

    if direction == "LONG":

        increment_counter(
            "long_valid"
        )

    else:

        increment_counter(
            "short_valid"
        )

    # ========================================================
    # STOP LOSS
    #
    # MOST RECENT CLOSED FUTURES CANDLE
    # ========================================================

    last_closed = candles[-1]

    if direction == "LONG":

        sl = float(
            last_closed["low"]
        )

    else:

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

        # TP3 must be above entry
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

        # TP3 must be below entry
        if tp3 >= price:

            increment_counter(
                "tp3_rejected"
            )

            return None

    # ========================================================
    # SIGNAL
    # ========================================================

    if direction == "LONG":

        increment_counter(
            "long_signals"
        )

    else:

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

def format_price(value):

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
# SIGNAL MESSAGE
# ============================================================

def format_signal(
    signal
):

    emoji = (
        "🟢"
        if signal["direction"]
        == "LONG"
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

    d = diagnostics

    print()
    print("=" * 72)
    print("DIAGNOSTIC REJECTION SUMMARY")
    print("=" * 72)

    print()
    print(
        f"SYMBOLS LOADED           : "
        f"{d['symbols_loaded']}"
    )

    print(
        f"LIVE PRICES LOADED       : "
        f"{d['prices_loaded']}"
    )

    print(
        f"TOTAL SCANS              : "
        f"{d['total_scans']}"
    )

    print()
    print("-" * 72)
    print("HTTP / API")
    print("-" * 72)

    print(
        f"CANDLE REQUESTS          : "
        f"{d['candle_requests']}"
    )

    print(
        f"CANDLE SUCCESS           : "
        f"{d['candle_success']}"
    )

    print(
        f"CANDLE FAILURES          : "
        f"{d['candle_failures']}"
    )

    print(
        f"HTTP 400 BAD REQUEST     : "
        f"{d['http_400']}"
    )

    print(
        f"HTTP 404 NOT FOUND       : "
        f"{d['http_404']}"
    )

    print(
        f"HTTP 429 RATE LIMITED    : "
        f"{d['http_429']}"
    )

    print(
        f"HTTP OTHER ERRORS        : "
        f"{d['http_other']}"
    )

    print(
        f"RATE-LIMIT RETRIES       : "
        f"{d['rate_limit_retries']}"
    )

    print(
        f"REQUEST RETRIES          : "
        f"{d['request_retries']}"
    )

    print()
    print("-" * 72)
    print("CANDLE VALIDATION")
    print("-" * 72)

    print(
        f"INVALID CANDLES          : "
        f"{d['invalid_candles']}"
    )

    print(
        f"INSUFFICIENT CANDLES     : "
        f"{d['insufficient_candles']}"
    )

    print(
        f"NO CLOSED CANDLES        : "
        f"{d['no_closed_candles']}"
    )

    print()
    print("-" * 72)
    print("STRATEGY REJECTIONS")
    print("-" * 72)

    print(
        f"REJECTED BY GAP          : "
        f"{d['gap_rejected']}"
    )

    print(
        f"REJECTED BY PRICE        : "
        f"{d['price_rejected']}"
    )

    print(
        f"REJECTED BY PRICE/GAP    : "
        f"{d['price_gap_rejected']}"
    )

    print(
        f"REJECTED BY EMA20        : "
        f"{d['ema20_rejected']}"
    )

    print(
        f"REJECTED BY TP3          : "
        f"{d['tp3_rejected']}"
    )

    print()
    print("-" * 72)
    print("VALID SETUPS")
    print("-" * 72)

    print(
        f"VALID LONG SETUPS        : "
        f"{d['long_valid']}"
    )

    print(
        f"VALID SHORT SETUPS       : "
        f"{d['short_valid']}"
    )

    print()
    print("-" * 72)
    print("SIGNALS")
    print("-" * 72)

    print(
        f"LONG SIGNALS             : "
        f"{d['long_signals']}"
    )

    print(
        f"SHORT SIGNALS            : "
        f"{d['short_signals']}"
    )

    print()
    print("-" * 72)
    print("ALERTS")
    print("-" * 72)

    print(
        f"FRESH SIGNALS            : "
        f"{d['fresh_signals']}"
    )

    print(
        f"HOURLY REPEATS           : "
        f"{d['hourly_repeats']}"
    )

    print(
        f"TOTAL SIGNAL ALERTS      : "
        f"{d['fresh_signals'] + d['hourly_repeats']}"
    )

    print("=" * 72)
    print()


# ============================================================
# TELEGRAM ZERO-SIGNAL REPORT
# ============================================================

def zero_signal_report():

    d = diagnostics

    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 LONG + SHORT SIGNAL BOT\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "15M / 1H / 4H\n"
        "FUTURES ONLY\n"
        "NO SPOT\n"
        "NO BOS\n\n"

        "No fresh signals found "
        "in this scan.\n\n"

        f"Futures symbols: "
        f"{d['symbols_loaded']}\n"

        f"Scans: "
        f"{d['total_scans']}\n\n"

        f"HTTP 400: "
        f"{d['http_400']}\n"

        f"HTTP 429: "
        f"{d['http_429']}\n"

        f"Candle failures: "
        f"{d['candle_failures']}\n"

        f"Insufficient candles: "
        f"{d['insufficient_candles']}\n\n"

        f"Gap rejected: "
        f"{d['gap_rejected']}\n"

        f"Price rejected: "
        f"{d['price_rejected']}\n"

        f"Price/Gap rejected: "
        f"{d['price_gap_rejected']}\n"

        f"EMA20 rejected: "
        f"{d['ema20_rejected']}\n"

        f"TP3 rejected: "
        f"{d['tp3_rejected']}\n\n"

        f"Valid LONG: "
        f"{d['long_valid']}\n"

        f"Valid SHORT: "
        f"{d['short_valid']}\n\n"

        f"Fresh signals: "
        f"{d['fresh_signals']}\n"

        f"Hourly repeats: "
        f"{d['hourly_repeats']}\n"

        "━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print_header()

    # --------------------------------------------------------
    # RESET DIAGNOSTICS
    # --------------------------------------------------------

    with diagnostic_lock:

        for key in diagnostics:
            diagnostics[key] = 0

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
    # GET FUTURES SYMBOLS
    # --------------------------------------------------------

    symbols = get_futures_symbols()

    if not symbols:

        print(
            "No Futures symbols found."
        )

        return

    print(
        f"Scanning "
        f"{len(symbols)} Futures symbols..."
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

    # --------------------------------------------------------
    # CREATE SCAN JOBS
    # --------------------------------------------------------

    jobs = []

    for symbol in symbols:

        live_price = (
            futures_prices.get(
                symbol
            )
        )

        if live_price is None:

            increment_counter(
                "missing_live_price"
            )

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
        f"Total scans: "
        f"{len(jobs)}"
    )

    # --------------------------------------------------------
    # PARALLEL SCAN
    # --------------------------------------------------------

    results = []

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

                result = (
                    future.result()
                )

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
                    f"{timeframe} | "
                    f"{e}"
                )

            # ------------------------------------------------
            # PROGRESS
            # ------------------------------------------------

            if (
                completed % 300 == 0
                or
                completed == len(jobs)
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
        key=lambda x:
        x["gap"],
        reverse=True
    )

    print()
    print(
        f"Valid strategy results: "
        f"{len(results)}"
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
                    signal[
                        "direction"
                    ],

                "timeframe":
                    signal[
                        "timeframe"
                    ],

                "entry":
                    signal[
                        "entry"
                    ],

                "gap":
                    signal[
                        "gap"
                    ],
            }

            signals_history.append(
                {
                    "timestamp": now,
                    "type": "fresh",
                    **signal,
                }
            )

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

        # ====================================================
        # HOURLY REPEAT
        # ====================================================

        if (
            same_setup
            and
            (
                now
                - last_alert
                >= REPEAT_INTERVAL
            )
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

            signals_history.append(
                {
                    "timestamp": now,
                    "type":
                        "hourly_repeat",
                    **signal,
                }
            )

    # --------------------------------------------------------
    # UPDATE DIAGNOSTICS
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

        time.sleep(0.2)

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

        time.sleep(0.2)

    # ========================================================
    # ALWAYS SEND ZERO-SIGNAL REPORT
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
    # PRINT FRESH SIGNALS
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

    # ========================================================
    # PRINT HOURLY REPEATS
    # ========================================================

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
