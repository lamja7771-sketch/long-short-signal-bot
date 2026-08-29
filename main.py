import os
import time
import threading
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GATE_URL = "https://api.gateio.ws/api/v4"

SMA_PERIOD = 50
EMA_FAST = 20
EMA_SLOW = 200

# Proper EMA200 historical warm-up
CANDLE_LIMIT = 1000

# Recent BOS window
STRUCTURE_CANDLES = 10

# EMA20 tolerance = 2%
EMA20_TOLERANCE = 0.02

# ============================================================
# 2:10 PRICE / GAP RATIO
# ============================================================

PRICE_GAP_RATIO = 0.20

# ============================================================
# TIMEFRAME-SPECIFIC MINIMUM GAP
# ============================================================

GAP_MINIMUM = {
    "15m": 10,
    "1h": 20,
    "4h": 35,
    "1d": 30,
}

# ============================================================
# TIMEFRAMES
# ============================================================

TIMEFRAMES = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# ============================================================
# PERFORMANCE
# ============================================================

MAX_WORKERS = 16

REQUEST_TIMEOUT = 20
SYMBOL_TIMEOUT = 60

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/2.1",
    "Accept": "application/json",
    "Connection": "keep-alive",
}

# ============================================================
# THREAD-LOCAL HTTP SESSION
# ============================================================

_thread_local = threading.local()


def get_session():
    session = getattr(
        _thread_local,
        "session",
        None,
    )

    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session

    return session


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )

        if response.status_code == 200:
            print("Telegram message sent.")
            return True

        print("Telegram error:", response.text)

    except Exception as e:
        print("Telegram error:", e)

    return False


# ============================================================
# GET GATE.IO FUTURES SYMBOLS
# ============================================================

def get_symbols():
    url = f"{GATE_URL}/futures/usdt/contracts"

    try:
        session = get_session()

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print("Symbol error:", response.text)
            return []

        data = response.json()

        symbols = []

        for item in data:
            symbol = item.get("name", "")

            if (
                symbol.endswith("_USDT")
                and not item.get(
                    "in_delisting",
                    False,
                )
            ):
                symbols.append(symbol)

        return sorted(set(symbols))

    except Exception as e:
        print("Symbol request error:", e)
        return []


# ============================================================
# GET ALL LIVE SPOT PRICES
# ============================================================

def get_all_spot_prices():
    url = f"{GATE_URL}/spot/tickers"

    for attempt in range(3):
        try:
            session = get_session()

            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:
                data = response.json()

                prices = {}

                if not isinstance(data, list):
                    return prices

                for item in data:
                    try:
                        symbol = item.get(
                            "currency_pair"
                        )

                        last_price = item.get(
                            "last"
                        )

                        if (
                            symbol
                            and last_price is not None
                        ):
                            prices[symbol] = float(
                                last_price
                            )

                    except Exception:
                        continue

                print(
                    f"Loaded {len(prices)} "
                    f"live Spot prices."
                )

                return prices

            if response.status_code == 429:
                wait_time = 2 ** attempt

                print(
                    "Spot ticker rate limited. "
                    f"Retrying in {wait_time}s..."
                )

                time.sleep(wait_time)
                continue

            print(
                "Spot ticker error:",
                response.text,
            )

            return {}

        except Exception as e:
            if attempt == 2:
                print(
                    "Spot ticker request error:",
                    e,
                )
                return {}

            wait_time = 2 ** attempt
            time.sleep(wait_time)

    return {}


# ============================================================
# GET FUTURES CANDLES
#
# FUTURES = BOS / STRUCTURE ONLY
# ============================================================

def get_futures_candles(
    symbol,
    timeframe,
):
    url = (
        f"{GATE_URL}/futures/usdt/"
        f"candlesticks"
    )

    params = {
        "contract": symbol,
        "interval": timeframe,
        "limit": CANDLE_LIMIT,
    }

    for attempt in range(3):
        try:
            session = get_session()

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:
                data = response.json()

                if isinstance(data, list):
                    return data

                return []

            if response.status_code == 429:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue

            return []

        except Exception as e:
            if attempt == 2:
                print(
                    f"Futures "
                    f"{symbol} "
                    f"{timeframe}: "
                    f"{e}"
                )
                return []

            wait_time = 2 ** attempt
            time.sleep(wait_time)

    return []


# ============================================================
# GET SPOT CANDLES
#
# SPOT = SMA50 / EMA20 / EMA200
# ============================================================

def get_spot_candles(
    symbol,
    timeframe,
):
    url = f"{GATE_URL}/spot/candlesticks"

    params = {
        "currency_pair": symbol,
        "interval": timeframe,
        "limit": CANDLE_LIMIT,
    }

    for attempt in range(3):
        try:
            session = get_session()

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:
                data = response.json()

                if isinstance(data, list):
                    return data

                return []

            if response.status_code == 429:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue

            return []

        except Exception as e:
            if attempt == 2:
                print(
                    f"Spot "
                    f"{symbol} "
                    f"{timeframe}: "
                    f"{e}"
                )
                return []

            wait_time = 2 ** attempt
            time.sleep(wait_time)

    return []


# ============================================================
# PARSE FUTURES CANDLES
# ============================================================

def parse_futures_candles(data):
    candles = []

    for item in data:
        try:
            if isinstance(item, dict):
                candle = {
                    "time": int(item["t"]),
                    "open": float(item["o"]),
                    "high": float(item["h"]),
                    "low": float(item["l"]),
                    "close": float(item["c"]),
                }

            else:
                candle = {
                    "time": int(item[0]),
                    "open": float(item[5]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                    "close": float(item[2]),
                }

            candles.append(candle)

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


# ============================================================
# PARSE SPOT CANDLES
#
# Gate Spot:
#
# [timestamp, volume, close, high, low, open, amount]
# ============================================================

def parse_spot_candles(data):
    candles = []

    for item in data:
        try:
            candle = {
                "time": int(item[0]),
                "open": float(item[5]),
                "high": float(item[3]),
                "low": float(item[4]),
                "close": float(item[2]),
            }

            candles.append(candle)

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


# ============================================================
# REMOVE CURRENT OPEN CANDLE
#
# ONLY CLOSED CANDLES ARE USED FOR:
# - BOS
# - SMA50
# - EMA20
# - EMA200
# ============================================================

def remove_open_candle(
    candles,
    timeframe_seconds,
):
    now = int(time.time())

    return [
        candle
        for candle in candles
        if (
            candle["time"]
            + timeframe_seconds
            <= now
        )
    ]


# ============================================================
# SMA
# ============================================================

def calculate_sma(
    values,
    period,
):
    if len(values) < period:
        return None

    return (
        sum(values[-period:])
        / period
    )


# ============================================================
# EMA
#
# Proper historical warm-up.
# ============================================================

def calculate_ema(
    values,
    period,
):
    if len(values) < period:
        return None

    multiplier = (
        2 / (period + 1)
    )

    value = (
        sum(values[:period])
        / period
    )

    for price in values[period:]:
        value = (
            (
                price
                - value
            )
            * multiplier
        ) + value

    return value


# ============================================================
# FIND RECENT BOS
#
# Searches latest 10 completed FUTURES candles.
#
# Selects the MOST RECENT LONG or SHORT BOS.
# ============================================================

def find_structure_break(candles):
    if len(candles) < (
        STRUCTURE_CANDLES + 3
    ):
        return None

    window = candles[
        -STRUCTURE_CANDLES:
    ]

    long_bos = None
    short_bos = None

    # ========================================================
    # LONG BOS
    # ========================================================

    for i in range(
        1,
        len(window) - 1,
    ):
        swing_high = window[i]["high"]

        if (
            swing_high
            <= window[i - 1]["high"]
        ):
            continue

        for j in range(
            i + 1,
            len(window),
        ):
            if (
                window[j]["close"]
                > swing_high
            ):
                candidate = {
                    "direction": "LONG",
                    "candle": window[j],
                    "structure_level": swing_high,
                }

                if (
                    long_bos is None
                    or candidate["candle"]["time"]
                    > long_bos["candle"]["time"]
                ):
                    long_bos = candidate

    # ========================================================
    # SHORT BOS
    # ========================================================

    for i in range(
        1,
        len(window) - 1,
    ):
        swing_low = window[i]["low"]

        if (
            swing_low
            >= window[i - 1]["low"]
        ):
            continue

        for j in range(
            i + 1,
            len(window),
        ):
            if (
                window[j]["close"]
                < swing_low
            ):
                candidate = {
                    "direction": "SHORT",
                    "candle": window[j],
                    "structure_level": swing_low,
                }

                if (
                    short_bos is None
                    or candidate["candle"]["time"]
                    > short_bos["candle"]["time"]
                ):
                    short_bos = candidate

    # ========================================================
    # MOST RECENT BOS
    # ========================================================

    candidates = [
        bos
        for bos in (
            long_bos,
            short_bos,
        )
        if bos is not None
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda x:
            x["candle"]["time"],
    )


# ============================================================
# ANALYZE ONE SYMBOL / ONE TIMEFRAME
#
# FUTURES + SPOT candles are requested simultaneously.
#
# SIGNAL CALCULATION IS UNCHANGED.
# ============================================================

def analyze_timeframe(
    symbol,
    timeframe,
    spot_prices,
):
    # ========================================================
    # FUTURES + SPOT CANDLES IN PARALLEL
    # ========================================================

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:

        futures_request = executor.submit(
            get_futures_candles,
            symbol,
            timeframe,
        )

        spot_request = executor.submit(
            get_spot_candles,
            symbol,
            timeframe,
        )

        futures_raw = futures_request.result()
        spot_raw = spot_request.result()

    # ========================================================
    # FUTURES = BOS / STRUCTURE
    # ========================================================

    futures_candles = parse_futures_candles(
        futures_raw
    )

    futures_candles = remove_open_candle(
        futures_candles,
        TIMEFRAMES[timeframe],
    )

    if len(futures_candles) < (
        STRUCTURE_CANDLES + 3
    ):
        return None

    structure = find_structure_break(
        futures_candles
    )

    if not structure:
        return None

    direction = structure["direction"]
    trigger = structure["candle"]

    # ========================================================
    # SPOT = SMA50 / EMA20 / EMA200
    # ========================================================

    spot_candles = parse_spot_candles(
        spot_raw
    )

    spot_candles = remove_open_candle(
        spot_candles,
        TIMEFRAMES[timeframe],
    )

    if len(spot_candles) < (
        EMA_SLOW + 50
    ):
        return None

    closes = [
        candle["close"]
        for candle in spot_candles
    ]

    # ========================================================
    # SPOT INDICATORS
    # ========================================================

    sma50 = calculate_sma(
        closes,
        SMA_PERIOD,
    )

    ema20 = calculate_ema(
        closes,
        EMA_FAST,
    )

    ema200 = calculate_ema(
        closes,
        EMA_SLOW,
    )

    if None in (
        sma50,
        ema20,
        ema200,
    ):
        return None

    # ========================================================
    # LIVE SPOT PRICE
    # ========================================================

    price = spot_prices.get(symbol)

    if price is None:
        return None

    if price <= 0:
        return None

    # ========================================================
    # SMA50 / EMA200 GAP
    # ========================================================

    if ema200 == 0:
        return None

    gap = (
        abs(
            sma50 - ema200
        )
        / abs(ema200)
    ) * 100

    # ========================================================
    # MINIMUM GAP
    # ========================================================

    minimum_gap = GAP_MINIMUM[timeframe]

    if gap <= minimum_gap:
        return None

    # ========================================================
    # DYNAMIC PRICE TOLERANCE
    #
    # Actual gap * 20%
    # ========================================================

    price_tolerance = (
        gap * PRICE_GAP_RATIO
    )

    price_tolerance_decimal = (
        price_tolerance / 100
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        maximum_price = (
            sma50
            * (
                1
                + price_tolerance_decimal
            )
        )

        if not (
            sma50
            < price
            <= maximum_price
        ):
            return None

        if not (
            ema20
            <= sma50
            * (
                1
                + EMA20_TOLERANCE
            )
        ):
            return None

        # SL = FUTURES BOS candle low
        sl = trigger["low"]

        if not (
            sl
            < ema20
            <= sma50
            * (
                1
                + EMA20_TOLERANCE
            )
        ):
            return None

        return {
            "direction": "LONG",
            "symbol": symbol.replace(
                "_USDT",
                "",
            ),
            "timeframe": timeframe,
            "entry": price,
            "sl": sl,
            "tp": ema200,
            "gap": gap,
            "price_tolerance": price_tolerance,
            "trigger_time": trigger["time"],
        }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        minimum_price = (
            sma50
            * (
                1
                - price_tolerance_decimal
            )
        )

        if not (
            minimum_price
            <= price
            < sma50
        ):
            return None

        if not (
            ema20
            >= sma50
            * (
                1
                - EMA20_TOLERANCE
            )
        ):
            return None

        # SL = FUTURES BOS candle high
        sl = trigger["high"]

        if not (
            ema200
            < ema20
            >= sma50
            * (
                1
                - EMA20_TOLERANCE
            )
            and sl > ema20
        ):
            return None

        return {
            "direction": "SHORT",
            "symbol": symbol.replace(
                "_USDT",
                "",
            ),
            "timeframe": timeframe,
            "entry": price,
            "sl": sl,
            "tp": ema200,
            "gap": gap,
            "price_tolerance": price_tolerance,
            "trigger_time": trigger["time"],
        }

    return None


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(price):
    if price >= 100:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    if price >= 0.0001:
        return f"{price:.8f}"

    return f"{price:.10f}"


# ============================================================
# FORMAT TELEGRAM MESSAGE
# ============================================================

def format_signal(signal):
    symbol = signal["symbol"]
    direction = signal["direction"]
    timeframe = signal["timeframe"]

    entry_price = signal["entry"]
    sl_price = signal["sl"]
    ema_tp_price = signal["tp"]

    entry = format_price(entry_price)
    sl = format_price(sl_price)

    # ========================================================
    # TP1 = 5%
    # TP2 = 10%
    # TP3 = 200 EMA
    # ========================================================

    if direction == "LONG":
        tp1_price = entry_price * 1.05
        tp2_price = entry_price * 1.10

    else:
        tp1_price = entry_price * 0.95
        tp2_price = entry_price * 0.90

    tp1 = format_price(tp1_price)
    tp2 = format_price(tp2_price)
    tp3 = format_price(ema_tp_price)

    gap = round(signal["gap"])

    price_tolerance = round(
        signal["price_tolerance"],
        2,
    )

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return (
        f"#{symbol} "
        f"{direction} "
        f"{timeframe} "
        f"{emoji}\n\n"
        f"Entry: ${entry}\n"
        f"SL: ${sl}\n\n"
        f"**TP1: ${tp1}**\n"
        f"**TP2: ${tp2}**\n"
        f"**TP3: ${tp3}**\n\n"
        f"Gap: {gap}%\n"
        f"SMA50 Distance Allowed: "
        f"{price_tolerance}%"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    scan_start = time.time()

    print("=" * 60)

    print("LONG + SHORT SIGNAL BOT")
    print("15M / 1H / 4H / DAILY")
    print("SPOT SMA50 / SPOT EMA20 / SPOT EMA200")
    print("FUTURES BOS / STRUCTURE")
    print("LIVE SPOT ENTRY PRICE")
    print("PROPER EMA200 WARM-UP")
    print("TIMEFRAME-SPECIFIC MINIMUM GAP")
    print("15M GAP > 10%")
    print("1H GAP > 20%")
    print("4H GAP > 35%")
    print("DAILY GAP > 30%")
    print("PRICE / GAP RATIO = 2:10")
    print("PRICE TOLERANCE = ACTUAL GAP x 20%")
    print("EMA20 TOLERANCE: 2%")
    print("RECENT BOS: LAST 10 COMPLETED FUTURES CANDLES")
    print("MOST RECENT BOS SELECTED")
    print("TP1 5% / TP2 10% / TP3 EMA200")
    print("OPTIMIZED API SCANNING")

    print("=" * 60)

    # ========================================================
    # GET SYMBOLS
    # ========================================================

    symbols = get_symbols()

    if not symbols:

        print("No symbols found.")

        send_telegram(
            "⚠️ No symbols found.\n\n"
            "Bot scan failed."
        )

        return

    print(
        f"Scanning {len(symbols)} symbols..."
    )

    # ========================================================
    # GET ALL LIVE SPOT PRICES ONCE
    # ========================================================

    price_start = time.time()

    spot_prices = get_all_spot_prices()

    price_time = (
        time.time()
        - price_start
    )

    print(
        f"Live Spot prices loaded in "
        f"{price_time:.2f}s"
    )

    if not spot_prices:

        print("No Spot prices found.")

        send_telegram(
            "⚠️ Spot ticker request failed.\n\n"
            "Bot scan failed."
        )

        return

    # ========================================================
    # BUILD JOBS
    # ========================================================

    jobs = []

    for symbol in symbols:

        # Only scan symbols that have a live
        # Spot price.

        if symbol not in spot_prices:
            continue

        for timeframe in TIMEFRAMES:

            jobs.append(
                (
                    symbol,
                    timeframe,
                )
            )

    print(
        f"Total scans: {len(jobs)}"
    )

    # ========================================================
    # RUN SCAN
    # ========================================================

    scan_api_start = time.time()

    new_signals = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_timeframe,
                symbol,
                timeframe,
                spot_prices,
            ): (
                symbol,
                timeframe,
            )
            for symbol, timeframe in jobs
        }

        completed = 0

        for future in as_completed(
            futures
        ):

            completed += 1

            try:

                signal = future.result()

                if signal:

                    new_signals.append(
                        signal
                    )

                    print(
                        "SIGNAL:",
                        signal["symbol"],
                        signal["direction"],
                        signal["timeframe"],
                    )

            except Exception as e:

                symbol, timeframe = (
                    futures[future]
                )

                print(
                    f"{symbol} "
                    f"{timeframe}: "
                    f"{e}"
                )

            if (
                completed % 300 == 0
                or completed == len(jobs)
            ):

                elapsed = (
                    time.time()
                    - scan_api_start
                )

                print(
                    f"Progress: "
                    f"{completed}/"
                    f"{len(jobs)} "
                    f"| "
                    f"{elapsed:.1f}s"
                )

    scan_api_time = (
        time.time()
        - scan_api_start
    )

    # ========================================================
    # SORT BY GAP
    # ========================================================

    new_signals.sort(
        key=lambda x: x["gap"],
        reverse=True,
    )

    # ========================================================
    # NO SIGNAL
    # ========================================================

    if not new_signals:

        total_time = (
            time.time()
            - scan_start
        )

        print("No signals found.")

        print(
            f"API scan time: "
            f"{scan_api_time:.2f}s"
        )

        print(
            f"Total runtime: "
            f"{total_time:.2f}s"
        )

        send_telegram(
            "🔍 No signal found.\n\n"
            "Next scan: 5 minutes"
        )

        return

    # ========================================================
    # SEND SIGNALS
    # ========================================================

    print(
        f"SIGNALS FOUND: "
        f"{len(new_signals)}"
    )

    for signal in new_signals:

        message = format_signal(
            signal
        )

        print()
        print(message)
        print()

        send_telegram(message)

        time.sleep(0.5)

    # ========================================================
    # PERFORMANCE SUMMARY
    # ========================================================

    total_time = (
        time.time()
        - scan_start
    )

    print("=" * 60)

    print(
        f"API SCAN TIME: "
        f"{scan_api_time:.2f}s"
    )

    print(
        f"TOTAL RUNTIME: "
        f"{total_time:.2f}s"
    )

    print(
        f"SIGNALS: "
        f"{len(new_signals)}"
    )

    print("=" * 60)

    print("Finished.")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
