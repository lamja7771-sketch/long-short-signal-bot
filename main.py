import os
import time
import json
import threading
import requests

from datetime import datetime, timezone
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

# Minimum SMA50 / EMA200 gap
GAP_MINIMUM = {
    "15m": 10.0,
    "1h": 20.0,
    "4h": 35.0,
}

# Price must be within this percentage of the
# total SMA50 -> EMA200 gap, measured from SMA50.
PRICE_GAP_RATIO = 0.20

# Price must be within 2% of EMA20.
EMA20_TOLERANCE = 0.02

# TP settings
TP1_PERCENT = 0.05
TP2_PERCENT = 0.10

# Same setup can repeat after this many seconds
REPEAT_INTERVAL = 60 * 60

# Futures candles
CANDLE_LIMIT = 1000

# Concurrent workers.
# Keep this moderate because Gate can return HTTP 429.
MAX_WORKERS = 6

# Retry settings
MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.5

# History files
ALERTS_FILE = "alerts.json"
SIGNALS_FILE = "signals.json"

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/3.0"
}


# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)

file_lock = threading.Lock()


# ============================================================
# DIAGNOSTIC COUNTERS
# ============================================================

diagnostics = {
    "total_scans": 0,
    "candle_requests": 0,
    "candle_failures": 0,

    "invalid_candles": 0,
    "insufficient_candles": 0,

    "invalid_indicator": 0,

    "gap_rejected": 0,
    "price_rejected": 0,
    "ema20_rejected": 0,

    "long_valid": 0,
    "short_valid": 0,

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
    print("=" * 68)
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
    print("=" * 68)
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
        print(f"Could not load {filename}: {e}")
        return default


def save_json(filename, data):
    temp_file = filename + ".tmp"

    with file_lock:
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            os.replace(temp_file, filename)

        except Exception as e:
            print(f"Could not save {filename}: {e}")


# ============================================================
# HTTP REQUEST
# ============================================================

def gate_get(path, params=None, description=""):
    url = GATE_URL + path

    for attempt in range(MAX_RETRIES + 1):

        try:
            response = session.get(
                url,
                params=params,
                timeout=15
            )

            if response.status_code == 200:
                return response.json()

            # ------------------------------------------------
            # RATE LIMIT
            # ------------------------------------------------
            if response.status_code == 429:

                if attempt >= MAX_RETRIES:
                    print(
                        f"RATE LIMITED permanently: "
                        f"{description}"
                    )
                    return None

                retry_after = response.headers.get("Retry-After")

                if retry_after:
                    try:
                        delay = float(retry_after)
                    except Exception:
                        delay = BASE_RETRY_DELAY * (2 ** attempt)
                else:
                    delay = BASE_RETRY_DELAY * (2 ** attempt)

                print(
                    f"RATE LIMITED: {description} | "
                    f"retry {attempt + 1}/{MAX_RETRIES} | "
                    f"waiting {delay:.2f}s"
                )

                time.sleep(delay)
                continue

            print(
                f"Gate HTTP {response.status_code}: "
                f"{description}"
            )

            return None

        except requests.RequestException as e:

            if attempt >= MAX_RETRIES:
                print(
                    f"REQUEST FAILED: {description} | {e}"
                )
                return None

            delay = BASE_RETRY_DELAY * (2 ** attempt)

            print(
                f"REQUEST ERROR: {description} | "
                f"retry {attempt + 1}/{MAX_RETRIES} | "
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

            # Only USDT futures contracts
            if not name.endswith("_USDT"):
                continue

            # Skip contracts that are not trading
            status = item.get("status")

            if status and status not in ("trading", "open"):
                continue

            symbols.append(name)

        except Exception:
            continue

    return sorted(set(symbols))


# ============================================================
# GET ALL LIVE FUTURES PRICES
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
            contract = item.get("contract")
            last = item.get("last")

            if not contract or last is None:
                continue

            price = float(last)

            if price <= 0:
                continue

            prices[contract] = price

        except Exception:
            continue

    elapsed = time.time() - start

    print(
        f"Loaded {len(prices)} live Futures prices "
        f"in {elapsed:.2f}s"
    )

    return prices


# ============================================================
# FUTURES CANDLES
# ============================================================

def get_futures_candles(symbol, timeframe):
    interval = TIMEFRAMES[timeframe]

    diagnostics["candle_requests"] += 1

    data = gate_get(
        "/futures/usdt/candlesticks",
        params={
            "contract": symbol,
            "interval": interval,
            "limit": CANDLE_LIMIT,
        },
        description=f"Futures {symbol} {timeframe}"
    )

    if not data:
        diagnostics["candle_failures"] += 1
        return None

    candles = []

    try:
        for row in data:

            # Gate normally returns:
            # [timestamp, volume, close, high, low, open, ...]
            if isinstance(row, list):

                if len(row) < 6:
                    continue

                timestamp = float(row[0])
                volume = float(row[1])
                close = float(row[2])
                high = float(row[3])
                low = float(row[4])
                open_price = float(row[5])

            # Defensive support for dictionary format
            elif isinstance(row, dict):

                timestamp = float(
                    row.get("t", row.get("timestamp"))
                )

                volume = float(
                    row.get("v", row.get("volume", 0))
                )

                close = float(
                    row.get("c", row.get("close"))
                )

                high = float(
                    row.get("h", row.get("high"))
                )

                low = float(
                    row.get("l", row.get("low"))
                )

                open_price = float(
                    row.get("o", row.get("open"))
                )

            else:
                continue

            if (
                close <= 0
                or high <= 0
                or low <= 0
                or open_price <= 0
            ):
                continue

            candles.append({
                "timestamp": timestamp,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            })

        candles.sort(
            key=lambda x: x["timestamp"]
        )

    except Exception as e:
        print(
            f"Invalid candle data: "
            f"{symbol} {timeframe} | {e}"
        )

        diagnostics["invalid_candles"] += 1
        return None

    if len(candles) < EMA_SLOW + 10:
        diagnostics["insufficient_candles"] += 1
        return None

    # --------------------------------------------------------
    # IMPORTANT:
    # Remove the currently forming candle.
    #
    # We calculate SMA/EMA only from CLOSED futures candles.
    # --------------------------------------------------------

    now = time.time()

    closed = []

    for candle in candles:

        candle_close_time = (
            candle["timestamp"] + interval
        )

        if candle_close_time <= now:
            closed.append(candle)

    if len(closed) < EMA_SLOW + 5:
        diagnostics["insufficient_candles"] += 1
        return None

    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1.0)

    # Proper initial SMA seed
    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (
            (price - ema) * multiplier
        ) + ema

    return ema


# ============================================================
# SMA
# ============================================================

def calculate_sma(values, period):
    if len(values) < period:
        return None

    return sum(values[-period:]) / period


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(candles):
    if not candles:
        return None

    closes = [
        float(c["close"])
        for c in candles
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

def calculate_gap_percent(sma50, ema200):
    if sma50 <= 0 or ema200 <= 0:
        return 0.0

    return (
        abs(ema200 - sma50)
        / min(sma50, ema200)
    ) * 100.0


# ============================================================
# SIGNAL ANALYSIS
# ============================================================

def analyze_symbol(
    symbol,
    timeframe,
    live_price
):
    diagnostics["total_scans"] += 1

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
        diagnostics["invalid_indicator"] += 1
        return None

    sma50 = indicators["sma50"]
    ema20 = indicators["ema20"]
    ema200 = indicators["ema200"]

    price = float(live_price)

    if price <= 0:
        return None

    # --------------------------------------------------------
    # GAP
    # --------------------------------------------------------

    gap = calculate_gap_percent(
        sma50,
        ema200
    )

    minimum_gap = GAP_MINIMUM[timeframe]

    if gap < minimum_gap:
        diagnostics["gap_rejected"] += 1
        return None

    # --------------------------------------------------------
    # PRICE POSITION
    #
    # LONG:
    # SMA50 < PRICE < EMA200
    #
    # SHORT:
    # EMA200 < PRICE < SMA50
    # --------------------------------------------------------

    long_position = (
        sma50 < price < ema200
    )

    short_position = (
        ema200 < price < sma50
    )

    if not long_position and not short_position:
        diagnostics["price_rejected"] += 1
        return None

    # --------------------------------------------------------
    # PRICE / GAP RATIO
    #
    # Price should remain in the lower/upper 20%
    # of the SMA50 -> EMA200 range.
    #
    # LONG:
    # distance above SMA50 <= 20% of gap
    #
    # SHORT:
    # distance below SMA50 <= 20% of gap
    # --------------------------------------------------------

    total_gap = abs(
        ema200 - sma50
    )

    allowed_price_distance = (
        total_gap * PRICE_GAP_RATIO
    )

    if long_position:

        distance_from_sma = (
            price - sma50
        )

        if distance_from_sma > allowed_price_distance:
            diagnostics["price_rejected"] += 1
            return None

    elif short_position:

        distance_from_sma = (
            sma50 - price
        )

        if distance_from_sma > allowed_price_distance:
            diagnostics["price_rejected"] += 1
            return None

    # --------------------------------------------------------
    # EMA20 TOLERANCE
    #
    # Live Futures price must be within 2% of
    # Futures EMA20.
    # --------------------------------------------------------

    ema20_distance = (
        abs(price - ema20)
        / ema20
    )

    if ema20_distance > EMA20_TOLERANCE:
        diagnostics["ema20_rejected"] += 1
        return None

    # --------------------------------------------------------
    # SL
    #
    # MOST RECENT CLOSED FUTURES CANDLE
    # --------------------------------------------------------

    last_closed = candles[-1]

    if long_position:
        direction = "LONG"
        sl = float(last_closed["low"])
        diagnostics["long_valid"] += 1

    else:
        direction = "SHORT"
        sl = float(last_closed["high"])
        diagnostics["short_valid"] += 1

    # --------------------------------------------------------
    # TP1 / TP2
    # --------------------------------------------------------

    if direction == "LONG":

        tp1 = price * (1 + TP1_PERCENT)
        tp2 = price * (1 + TP2_PERCENT)
        tp3 = ema200

        # Make sure TP3 remains above entry
        if tp3 <= price:
            return None

    else:

        tp1 = price * (1 - TP1_PERCENT)
        tp2 = price * (1 - TP2_PERCENT)
        tp3 = ema200

        # Make sure TP3 remains below entry
        if tp3 >= price:
            return None

    if direction == "LONG":
        diagnostics["long_signals"] += 1
    else:
        diagnostics["short_signals"] += 1

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

        "last_candle_timestamp": last_closed[
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
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials missing.")
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
            f"Telegram error "
            f"{response.status_code}: "
            f"{response.text}"
        )

    except Exception as e:
        print(f"Telegram error: {e}")

    return False


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def format_signal(signal):
    emoji = (
        "🟢"
        if signal["direction"] == "LONG"
        else "🔴"
    )

    return (
        f"{emoji} #{signal['symbol'].replace('_USDT', '')} "
        f"{signal['direction']} "
        f"{signal['timeframe']}\n\n"

        f"Entry: ${format_price(signal['entry'])}\n"
        f"SL: ${format_price(signal['sl'])}\n\n"

        f"TP1: ${format_price(signal['tp1'])}\n"
        f"TP2: ${format_price(signal['tp2'])}\n"
        f"TP3: ${format_price(signal['tp3'])}\n\n"

        f"SMA50: ${format_price(signal['sma50'])}\n"
        f"EMA20: ${format_price(signal['ema20'])}\n"
        f"EMA200: ${format_price(signal['ema200'])}\n\n"

        f"Gap: {signal['gap']:.2f}%"
    )


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def print_diagnostics():
    print()
    print("=" * 68)
    print("DIAGNOSTIC REJECTION SUMMARY")
    print("=" * 68)

    print(
        f"TOTAL SCANS              : "
        f"{diagnostics['total_scans']}"
    )

    print(
        f"CANDLE REQUESTS          : "
        f"{diagnostics['candle_requests']}"
    )

    print(
        f"CANDLE DATA FAILED       : "
        f"{diagnostics['candle_failures']}"
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
        f"INVALID INDICATORS       : "
        f"{diagnostics['invalid_indicator']}"
    )

    print()
    print(
        f"REJECTED BY GAP          : "
        f"{diagnostics['gap_rejected']}"
    )

    print(
        f"REJECTED BY PRICE        : "
        f"{diagnostics['price_rejected']}"
    )

    print(
        f"REJECTED BY EMA20        : "
        f"{diagnostics['ema20_rejected']}"
    )

    print()
    print(
        f"VALID LONG SETUPS        : "
        f"{diagnostics['long_valid']}"
    )

    print(
        f"VALID SHORT SETUPS       : "
        f"{diagnostics['short_valid']}"
    )

    print()
    print(
        f"LONG SIGNALS             : "
        f"{diagnostics['long_signals']}"
    )

    print(
        f"SHORT SIGNALS            : "
        f"{diagnostics['short_signals']}"
    )

    print()
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

    print("=" * 68)
    print()


# ============================================================
# TELEGRAM ZERO-SIGNAL REPORT
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
        f"Scans: {diagnostics['total_scans']}\n"
        f"Gap rejected: {diagnostics['gap_rejected']}\n"
        f"Price rejected: {diagnostics['price_rejected']}\n"
        f"EMA20 rejected: {diagnostics['ema20_rejected']}\n"
        f"Candle failures: {diagnostics['candle_failures']}\n\n"
        f"Fresh signals: {diagnostics['fresh_signals']}\n"
        f"Hourly repeats: {diagnostics['hourly_repeats']}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():
    print_header()

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
        print("No Futures symbols found.")
        return

    print(
        f"Scanning {len(symbols)} Futures symbols..."
    )

    # --------------------------------------------------------
    # GET LIVE FUTURES PRICES
    # --------------------------------------------------------

    futures_prices = get_futures_prices()

    if not futures_prices:
        print("No live Futures prices loaded.")
        return

    # --------------------------------------------------------
    # CREATE SCAN JOBS
    # --------------------------------------------------------

    jobs = []

    for symbol in symbols:

        live_price = futures_prices.get(symbol)

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
            for symbol, timeframe, price in jobs
        }

        for future in as_completed(
            future_map
        ):

            completed += 1

            try:
                result = future.result()

                if result:
                    results.append(result)

            except Exception as e:
                symbol, timeframe = future_map[
                    future
                ]

                print(
                    f"ERROR: {symbol} "
                    f"{timeframe} | {e}"
                )

            if (
                completed % 300 == 0
                or completed == len(jobs)
            ):
                elapsed = (
                    time.time() - start_time
                )

                print(
                    f"Progress: "
                    f"{completed}/{len(jobs)} | "
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

        key = signal_key(signal)

        previous = alerts.get(key)

        # ----------------------------------------------------
        # NEW SETUP
        # ----------------------------------------------------

        if previous is None:

            fresh_signals.append(signal)

            alerts[key] = {
                "last_alert": now,
                "direction": signal["direction"],
                "timeframe": signal["timeframe"],
                "entry": signal["entry"],
                "gap": signal["gap"],
            }

            signals_history.append({
                "timestamp": now,
                "type": "fresh",
                **signal,
            })

            continue

        # ----------------------------------------------------
        # SAME SETUP
        # ----------------------------------------------------

        last_alert = float(
            previous.get(
                "last_alert",
                0
            )
        )

        previous_direction = previous.get(
            "direction"
        )

        previous_timeframe = previous.get(
            "timeframe"
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

            alerts[key]["last_alert"] = now

            alerts[key]["entry"] = signal[
                "entry"
            ]

            alerts[key]["gap"] = signal[
                "gap"
            ]

            signals_history.append({
                "timestamp": now,
                "type": "hourly_repeat",
                **signal,
            })

    diagnostics["fresh_signals"] = len(
        fresh_signals
    )

    diagnostics["hourly_repeats"] = len(
        hourly_repeats
    )

    # --------------------------------------------------------
    # SAVE HISTORY
    # --------------------------------------------------------

    save_json(
        ALERTS_FILE,
        alerts
    )

    # Keep history from growing forever
    if len(signals_history) > 5000:
        signals_history = signals_history[-5000:]

    save_json(
        SIGNALS_FILE,
        signals_history
    )

    # --------------------------------------------------------
    # SEND FRESH SIGNALS
    # --------------------------------------------------------

    for signal in fresh_signals:

        message = (
            "🚨 NEW SIGNAL\n\n"
            + format_signal(signal)
        )

        send_telegram(message)

        time.sleep(0.2)

    # --------------------------------------------------------
    # SEND HOURLY REPEATS
    # --------------------------------------------------------

    for signal in hourly_repeats:

        message = (
            "🔄 HOURLY REPEAT\n\n"
            + format_signal(signal)
        )

        send_telegram(message)

        time.sleep(0.2)

    # --------------------------------------------------------
    # ALWAYS SEND REPORT
    # --------------------------------------------------------

    if (
        len(fresh_signals) == 0
        and
        len(hourly_repeats) == 0
    ):
        send_telegram(
            zero_signal_report()
        )

    # --------------------------------------------------------
    # PRINT RESULTS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # PRINT DIAGNOSTICS
    # --------------------------------------------------------

    print_diagnostics()

    # --------------------------------------------------------
    # PRINT ACTUAL SIGNALS TO GITHUB LOG
    # --------------------------------------------------------

    if fresh_signals:

        print()
        print("=" * 68)
        print("FRESH SIGNALS")
        print("=" * 68)

        for signal in fresh_signals:
            print(
                f"{signal['direction']} "
                f"{signal['symbol']} "
                f"{signal['timeframe']} "
                f"Gap={signal['gap']:.2f}% "
                f"Entry={format_price(signal['entry'])}"
            )

    if hourly_repeats:

        print()
        print("=" * 68)
        print("HOURLY REPEATS")
        print("=" * 68)

        for signal in hourly_repeats:
            print(
                f"{signal['direction']} "
                f"{signal['symbol']} "
                f"{signal['timeframe']} "
                f"Gap={signal['gap']:.2f}% "
                f"Entry={format_price(signal['entry'])}"
            )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        run_scan()

    except KeyboardInterrupt:
        print("Stopped.")

    except Exception as e:
        print()
        print("=" * 68)
        print("FATAL ERROR")
        print("=" * 68)
        print(e)
        print("=" * 68)
        raise
