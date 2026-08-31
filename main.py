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

TIMEFRAMES = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

SMA_PERIOD = 50
EMA_FAST = 20
EMA_SLOW = 200


# ============================================================
# TIMEFRAME GAP REQUIREMENTS
# ============================================================

GAP_MINIMUM = {
    "15m": 10.0,
    "1h": 20.0,
    "4h": 35.0,
}


# ============================================================
# PRICE / GAP FILTER
# ============================================================

PRICE_GAP_RATIO = 0.20


# ============================================================
# EMA20 FILTER
# ============================================================

EMA20_TOLERANCE = 0.02


# ============================================================
# TP SETTINGS
# ============================================================

TP1_PERCENT = 0.05
TP2_PERCENT = 0.10


# ============================================================
# REPEAT SETTINGS
# ============================================================

REPEAT_INTERVAL = 60 * 60


# ============================================================
# CANDLE SETTINGS
# ============================================================

CANDLE_LIMIT = 1000


# ============================================================
# WORKERS
# ============================================================

MAX_WORKERS = 6


# ============================================================
# HTTP RETRIES
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
# GLOBAL HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)

file_lock = threading.Lock()


# ============================================================
# DIAGNOSTIC COUNTERS
# ============================================================

diagnostics = {

    # -------------------------------
    # GENERAL
    # -------------------------------

    "symbols_loaded": 0,
    "prices_loaded": 0,
    "total_scans": 0,

    # -------------------------------
    # CANDLE REQUESTS
    # -------------------------------

    "candle_requests": 0,
    "candle_success": 0,
    "candle_failures": 0,

    # -------------------------------
    # HTTP ERRORS
    # -------------------------------

    "http_400": 0,
    "http_401": 0,
    "http_403": 0,
    "http_404": 0,
    "http_408": 0,
    "http_429": 0,
    "http_500": 0,
    "http_other": 0,

    # -------------------------------
    # CANDLE VALIDATION
    # -------------------------------

    "invalid_candles": 0,
    "empty_candles": 0,
    "insufficient_candles": 0,
    "forming_candle_removed": 0,

    # -------------------------------
    # INDICATORS
    # -------------------------------

    "invalid_indicator": 0,

    # -------------------------------
    # STRATEGY REJECTIONS
    # -------------------------------

    "gap_rejected": 0,
    "price_position_rejected": 0,
    "price_gap_ratio_rejected": 0,
    "ema20_rejected": 0,

    # -------------------------------
    # VALID SETUPS
    # -------------------------------

    "long_valid": 0,
    "short_valid": 0,

    # -------------------------------
    # TP VALIDATION
    # -------------------------------

    "long_tp3_invalid": 0,
    "short_tp3_invalid": 0,

    # -------------------------------
    # FINAL SIGNALS
    # -------------------------------

    "long_signals": 0,
    "short_signals": 0,

    "fresh_signals": 0,
    "hourly_repeats": 0,
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
    print("=" * 72)
    print()


# ============================================================
# FILE HELPERS
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
# GATE HTTP REQUEST
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

            status = response.status_code

            # =================================================
            # SUCCESS
            # =================================================

            if status == 200:

                return response.json()


            # =================================================
            # HTTP 400
            #
            # IMPORTANT:
            # 400 is normally a bad/unsupported contract or
            # invalid candle request.
            #
            # DO NOT RETRY 400.
            # =================================================

            if status == 400:

                diagnostics["http_400"] += 1
                diagnostics["candle_failures"] += 1

                print(
                    f"Gate HTTP 400: {description}"
                )

                return None


            # =================================================
            # AUTH
            # =================================================

            if status == 401:

                diagnostics["http_401"] += 1

                print(
                    f"Gate HTTP 401: {description}"
                )

                return None


            # =================================================
            # FORBIDDEN
            # =================================================

            if status == 403:

                diagnostics["http_403"] += 1

                print(
                    f"Gate HTTP 403: {description}"
                )

                return None


            # =================================================
            # NOT FOUND
            # =================================================

            if status == 404:

                diagnostics["http_404"] += 1

                print(
                    f"Gate HTTP 404: {description}"
                )

                return None


            # =================================================
            # TIMEOUT
            # =================================================

            if status == 408:

                diagnostics["http_408"] += 1


            # =================================================
            # RATE LIMIT
            # =================================================

            if status == 429:

                diagnostics["http_429"] += 1

                if attempt >= MAX_RETRIES:

                    print(
                        f"RATE LIMITED permanently: "
                        f"{description}"
                    )

                    diagnostics["candle_failures"] += 1

                    return None

                retry_after = response.headers.get(
                    "Retry-After"
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


            # =================================================
            # SERVER ERRORS
            # =================================================

            if status >= 500:

                diagnostics["http_500"] += 1

                if attempt >= MAX_RETRIES:

                    print(
                        f"Gate HTTP {status}: "
                        f"{description}"
                    )

                    diagnostics["candle_failures"] += 1

                    return None

                delay = (
                    BASE_RETRY_DELAY
                    * (2 ** attempt)
                )

                print(
                    f"Gate HTTP {status}: "
                    f"{description} | "
                    f"retry {attempt + 1}/"
                    f"{MAX_RETRIES} | "
                    f"waiting {delay:.2f}s"
                )

                time.sleep(delay)

                continue


            # =================================================
            # OTHER HTTP
            # =================================================

            diagnostics["http_other"] += 1

            print(
                f"Gate HTTP {status}: "
                f"{description}"
            )

            return None


        except requests.RequestException as e:

            if attempt >= MAX_RETRIES:

                print(
                    f"REQUEST FAILED: "
                    f"{description} | {e}"
                )

                diagnostics["candle_failures"] += 1

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


        except ValueError as e:

            print(
                f"INVALID JSON: "
                f"{description} | {e}"
            )

            diagnostics["candle_failures"] += 1

            return None


        except Exception as e:

            print(
                f"UNEXPECTED REQUEST ERROR: "
                f"{description} | {e}"
            )

            diagnostics["candle_failures"] += 1

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

            # ------------------------------------------------
            # Only USDT futures
            # ------------------------------------------------

            if not name.endswith("_USDT"):
                continue


            # ------------------------------------------------
            # Contract status
            # ------------------------------------------------

            status = item.get("status")

            if status:

                status_lower = str(
                    status
                ).lower()

                if status_lower not in (
                    "trading",
                    "open"
                ):

                    continue


            # ------------------------------------------------
            # Basic sanity check
            # ------------------------------------------------

            if len(name) < 6:
                continue

            symbols.append(name)

        except Exception:

            continue


    symbols = sorted(
        set(symbols)
    )

    diagnostics["symbols_loaded"] = len(
        symbols
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


    diagnostics["prices_loaded"] = len(
        prices
    )

    elapsed = time.time() - start

    print(
        f"Loaded {len(prices)} live Futures prices "
        f"in {elapsed:.2f}s"
    )

    return prices


# ============================================================
# PARSE FUTURES CANDLE
# ============================================================

def parse_futures_candle(row):

    try:

        # ----------------------------------------------------
        # Gate list format:
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

            timestamp_raw = row.get(
                "t",
                row.get("timestamp")
            )

            volume_raw = row.get(
                "v",
                row.get("volume", 0)
            )

            close_raw = row.get(
                "c",
                row.get("close")
            )

            high_raw = row.get(
                "h",
                row.get("high")
            )

            low_raw = row.get(
                "l",
                row.get("low")
            )

            open_raw = row.get(
                "o",
                row.get("open")
            )

            if (
                timestamp_raw is None
                or close_raw is None
                or high_raw is None
                or low_raw is None
                or open_raw is None
            ):

                return None

            timestamp = float(
                timestamp_raw
            )

            volume = float(
                volume_raw
            )

            close = float(
                close_raw
            )

            high = float(
                high_raw
            )

            low = float(
                low_raw
            )

            open_price = float(
                open_raw
            )


        else:

            return None


        # ----------------------------------------------------
        # Validate values
        # ----------------------------------------------------

        if timestamp <= 0:
            return None

        if close <= 0:
            return None

        if high <= 0:
            return None

        if low <= 0:
            return None

        if open_price <= 0:
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
        KeyError,
        IndexError
    ):

        return None

    except Exception:

        return None


# ============================================================
# GET FUTURES CANDLES
# ============================================================

def get_futures_candles(
    symbol,
    timeframe
):

    interval = TIMEFRAMES[
        timeframe
    ]

    diagnostics[
        "candle_requests"
    ] += 1


    data = gate_get(
        "/futures/usdt/candlesticks",
        params={
            "contract": symbol,
            "interval": interval,
            "limit": CANDLE_LIMIT,
        },
        description=(
            f"Futures {symbol} "
            f"{timeframe}"
        )
    )


    if not data:

        diagnostics[
            "empty_candles"
        ] += 1

        return None


    candles = []


    # ========================================================
    # PARSE CANDLES
    # ========================================================

    for row in data:

        candle = parse_futures_candle(
            row
        )

        if candle is None:

            diagnostics[
                "invalid_candles"
            ] += 1

            continue

        candles.append(
            candle
        )


    if not candles:

        diagnostics[
            "empty_candles"
        ] += 1

        return None


    # ========================================================
    # SORT OLDEST -> NEWEST
    # ========================================================

    candles.sort(
        key=lambda x: x["timestamp"]
    )


    # ========================================================
    # REMOVE DUPLICATES
    # ========================================================

    unique = []

    seen_timestamps = set()

    for candle in candles:

        ts = candle[
            "timestamp"
        ]

        if ts in seen_timestamps:
            continue

        seen_timestamps.add(ts)

        unique.append(
            candle
        )

    candles = unique


    # ========================================================
    # REMOVE CURRENTLY FORMING CANDLE
    #
    # Indicators MUST use CLOSED candles only.
    # ========================================================

    now = time.time()

    closed = []

    for candle in candles:

        candle_close_time = (
            candle["timestamp"]
            + interval
        )

        if candle_close_time <= now:

            closed.append(
                candle
            )

        else:

            diagnostics[
                "forming_candle_removed"
            ] += 1


    # ========================================================
    # MINIMUM HISTORY
    # ========================================================

    minimum_required = (
        EMA_SLOW + 5
    )

    if len(closed) < minimum_required:

        diagnostics[
            "insufficient_candles"
        ] += 1

        return None


    diagnostics[
        "candle_success"
    ] += 1

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
        2.0 / (period + 1.0)
    )


    # Proper SMA seed
    ema = sum(
        values[:period]
    ) / period


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
        float(candle["close"])
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


    smaller = min(
        sma50,
        ema200
    )


    return (
        abs(
            ema200 - sma50
        )
        / smaller
    ) * 100.0


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    timeframe,
    live_price
):

    diagnostics[
        "total_scans"
    ] += 1


    # ========================================================
    # GET FUTURES CANDLES
    # ========================================================

    candles = get_futures_candles(
        symbol,
        timeframe
    )

    if not candles:
        return None


    # ========================================================
    # INDICATORS
    # ========================================================

    indicators = calculate_indicators(
        candles
    )

    if not indicators:

        diagnostics[
            "invalid_indicator"
        ] += 1

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


    # ========================================================
    # LIVE FUTURES PRICE
    # ========================================================

    try:

        price = float(
            live_price
        )

    except Exception:

        diagnostics[
            "price_position_rejected"
        ] += 1

        return None


    if price <= 0:

        diagnostics[
            "price_position_rejected"
        ] += 1

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

        diagnostics[
            "gap_rejected"
        ] += 1

        return None


    # ========================================================
    # PRICE POSITION
    #
    # LONG:
    # SMA50 < PRICE < EMA200
    #
    # SHORT:
    # EMA200 < PRICE < SMA50
    # ========================================================

    long_position = (
        sma50 < price < ema200
    )

    short_position = (
        ema200 < price < sma50
    )


    if (
        not long_position
        and not short_position
    ):

        diagnostics[
            "price_position_rejected"
        ] += 1

        return None


    # ========================================================
    # PRICE / GAP RATIO
    #
    # Allowed distance from SMA50:
    # 20% of total SMA50 -> EMA200 gap
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

            diagnostics[
                "price_gap_ratio_rejected"
            ] += 1

            return None


    else:

        distance_from_sma = (
            sma50 - price
        )

        if (
            distance_from_sma
            > allowed_price_distance
        ):

            diagnostics[
                "price_gap_ratio_rejected"
            ] += 1

            return None


    # ========================================================
    # EMA20 TOLERANCE
    #
    # Live Futures price must be within 2%
    # of Futures EMA20.
    # ========================================================

    ema20_distance = (
        abs(price - ema20)
        / ema20
    )


    if (
        ema20_distance
        > EMA20_TOLERANCE
    ):

        diagnostics[
            "ema20_rejected"
        ] += 1

        return None


    # ========================================================
    # LAST CLOSED FUTURES CANDLE
    # ========================================================

    last_closed = candles[-1]


    # ========================================================
    # DIRECTION + SL
    # ========================================================

    if long_position:

        direction = "LONG"

        sl = float(
            last_closed["low"]
        )

        diagnostics[
            "long_valid"
        ] += 1

    else:

        direction = "SHORT"

        sl = float(
            last_closed["high"]
        )

        diagnostics[
            "short_valid"
        ] += 1


    # ========================================================
    # TP1 / TP2 / TP3
    # ========================================================

    if direction == "LONG":

        tp1 = price * (
            1 + TP1_PERCENT
        )

        tp2 = price * (
            1 + TP2_PERCENT
        )

        tp3 = ema200


        if tp3 <= price:

            diagnostics[
                "long_tp3_invalid"
            ] += 1

            return None


    else:

        tp1 = price * (
            1 - TP1_PERCENT
        )

        tp2 = price * (
            1 - TP2_PERCENT
        )

        tp3 = ema200


        if tp3 >= price:

            diagnostics[
                "short_tp3_invalid"
            ] += 1

            return None


    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    if direction == "LONG":

        diagnostics[
            "long_signals"
        ] += 1

    else:

        diagnostics[
            "short_signals"
        ] += 1


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

def signal_key(signal):

    return (
        f"{signal['symbol']}_"
        f"{signal['timeframe']}_"
        f"{signal['direction']}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

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

        "disable_web_page_preview":
            True,
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

def format_signal(signal):

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
        f"FUTURES SYMBOLS LOADED   : "
        f"{diagnostics['symbols_loaded']}"
    )

    print(
        f"LIVE FUTURES PRICES      : "
        f"{diagnostics['prices_loaded']}"
    )

    print(
        f"TOTAL SCANS              : "
        f"{diagnostics['total_scans']}"
    )


    print()
    print("CANDLE REQUESTS")
    print("-" * 72)

    print(
        f"CANDLE REQUESTS          : "
        f"{diagnostics['candle_requests']}"
    )

    print(
        f"CANDLE SUCCESS           : "
        f"{diagnostics['candle_success']}"
    )

    print(
        f"CANDLE FAILURES          : "
        f"{diagnostics['candle_failures']}"
    )

    print(
        f"EMPTY CANDLE RESPONSES   : "
        f"{diagnostics['empty_candles']}"
    )

    print(
        f"INVALID CANDLES          : "
        f"{diagnostics['invalid_candles']}"
    )

    print(
        f"INSUFFICIENT CANDLES     : "
        f"{diagnostics['insufficient_candles']}"
    )

    print(
        f"FORMING CANDLES REMOVED  : "
        f"{diagnostics['forming_candle_removed']}"
    )


    print()
    print("HTTP ERRORS")
    print("-" * 72)

    print(
        f"HTTP 400                 : "
        f"{diagnostics['http_400']}"
    )

    print(
        f"HTTP 401                 : "
        f"{diagnostics['http_401']}"
    )

    print(
        f"HTTP 403                 : "
        f"{diagnostics['http_403']}"
    )

    print(
        f"HTTP 404                 : "
        f"{diagnostics['http_404']}"
    )

    print(
        f"HTTP 408                 : "
        f"{diagnostics['http_408']}"
    )

    print(
        f"HTTP 429 RATE LIMITED    : "
        f"{diagnostics['http_429']}"
    )

    print(
        f"HTTP 5XX                 : "
        f"{diagnostics['http_500']}"
    )

    print(
        f"OTHER HTTP ERRORS        : "
        f"{diagnostics['http_other']}"
    )


    print()
    print("INDICATOR REJECTIONS")
    print("-" * 72)

    print(
        f"INVALID INDICATORS       : "
        f"{diagnostics['invalid_indicator']}"
    )


    print()
    print("STRATEGY REJECTIONS")
    print("-" * 72)

    print(
        f"REJECTED BY GAP          : "
        f"{diagnostics['gap_rejected']}"
    )

    print(
        f"REJECTED BY PRICE        : "
        f"{diagnostics['price_position_rejected']}"
    )

    print(
        f"REJECTED BY PRICE/GAP    : "
        f"{diagnostics['price_gap_ratio_rejected']}"
    )

    print(
        f"REJECTED BY EMA20        : "
        f"{diagnostics['ema20_rejected']}"
    )


    print()
    print("VALID SETUPS")
    print("-" * 72)

    print(
        f"VALID LONG SETUPS        : "
        f"{diagnostics['long_valid']}"
    )

    print(
        f"VALID SHORT SETUPS       : "
        f"{diagnostics['short_valid']}"
    )


    print()
    print("TP VALIDATION")
    print("-" * 72)

    print(
        f"LONG TP3 INVALID         : "
        f"{diagnostics['long_tp3_invalid']}"
    )

    print(
        f"SHORT TP3 INVALID        : "
        f"{diagnostics['short_tp3_invalid']}"
    )


    print()
    print("FINAL SIGNALS")
    print("-" * 72)

    print(
        f"LONG SIGNALS             : "
        f"{diagnostics['long_signals']}"
    )

    print(
        f"SHORT SIGNALS            : "
        f"{diagnostics['short_signals']}"
    )

    print(
        f"FRESH SIGNALS            : "
        f"{diagnostics['fresh_signals']}"
    )

    print(
        f"HOURLY REPEATS           : "
        f"{diagnostics['hourly_repeats']}"
    )

    print(
        f"TOTAL SIGNAL ALERTS      : "
        f"{diagnostics['fresh_signals'] + diagnostics['hourly_repeats']}"
    )


    print()
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

        "No fresh signals found "
        "in this scan.\n\n"

        f"Futures symbols: "
        f"{diagnostics['symbols_loaded']}\n"

        f"Scans: "
        f"{diagnostics['total_scans']}\n\n"

        f"Gap rejected: "
        f"{diagnostics['gap_rejected']}\n"

        f"Price rejected: "
        f"{diagnostics['price_position_rejected']}\n"

        f"Price/Gap rejected: "
        f"{diagnostics['price_gap_ratio_rejected']}\n"

        f"EMA20 rejected: "
        f"{diagnostics['ema20_rejected']}\n\n"

        f"HTTP 400: "
        f"{diagnostics['http_400']}\n"

        f"HTTP 429: "
        f"{diagnostics['http_429']}\n"

        f"Candle failures: "
        f"{diagnostics['candle_failures']}\n"

        f"Insufficient candles: "
        f"{diagnostics['insufficient_candles']}\n\n"

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


    # ========================================================
    # RESET DIAGNOSTICS
    # ========================================================

    for key in diagnostics:

        diagnostics[key] = 0


    # ========================================================
    # LOAD HISTORY
    # ========================================================

    alerts = load_json(
        ALERTS_FILE,
        {}
    )


    signals_history = load_json(
        SIGNALS_FILE,
        []
    )


    # ========================================================
    # GET FUTURES UNIVERSE
    # ========================================================

    symbols = get_futures_symbols()


    if not symbols:

        print(
            "No Futures symbols found."
        )

        return


    print(
        f"Scanning {len(symbols)} "
        f"Futures symbols..."
    )


    # ========================================================
    # GET LIVE FUTURES PRICES
    # ========================================================

    futures_prices = (
        get_futures_prices()
    )


    if not futures_prices:

        print(
            "No live Futures prices loaded."
        )

        return


    # ========================================================
    # CREATE SCAN JOBS
    # ========================================================

    jobs = []


    for symbol in symbols:

        live_price = (
            futures_prices.get(
                symbol
            )
        )


        # ----------------------------------------------------
        # If ticker does not exist, don't request candles.
        # ----------------------------------------------------

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


    # ========================================================
    # SCAN
    # ========================================================

    results = []

    completed = 0

    start_time = time.time()


    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        future_map = {}


        for (
            symbol,
            timeframe,
            price
        ) in jobs:

            future = executor.submit(
                analyze_symbol,
                symbol,
                timeframe,
                price
            )

            future_map[
                future
            ] = (
                symbol,
                timeframe
            )


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
                    future_map[future]
                )


                print(
                    f"ERROR: "
                    f"{symbol} "
                    f"{timeframe} | "
                    f"{e}"
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


    # ========================================================
    # SORT BY GAP
    # ========================================================

    results.sort(
        key=lambda x: x["gap"],
        reverse=True
    )


    # ========================================================
    # PROCESS ALERT HISTORY
    # ========================================================

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


    # ========================================================
    # FINAL COUNTERS
    # ========================================================

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


    # ========================================================
    # SAVE ALERT HISTORY
    # ========================================================

    save_json(
        ALERTS_FILE,
        alerts
    )


    # ========================================================
    # LIMIT SIGNAL HISTORY
    # ========================================================

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
    # PRINT DIAGNOSTICS
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
