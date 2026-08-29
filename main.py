import os
import time
import threading
import json
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

# ============================================================
# SPOT HISTORY
#
# Keep 1000 candles for proper EMA200 warm-up.
# ============================================================

SPOT_CANDLE_LIMIT = 1000

# ============================================================
# FUTURES HISTORY
#
# We only need enough completed candles to determine
# the most recent BOS inside the last 10 completed candles.
#
# 10 structure candles + 3 safety candles.
# ============================================================

FUTURES_CANDLE_LIMIT = 20

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
}


# ============================================================
# TIMEFRAMES
# ============================================================

TIMEFRAMES = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}


# ============================================================
# PERFORMANCE
#
# IMPORTANT:
# Do NOT use 16 workers with nested thread pools.
#
# The old code effectively created up to:
#
# 16 outer workers × 2 inner workers
# = up to 32 HTTP workers
#
# That can cause Gate rate limiting.
# ============================================================

MAX_WORKERS = 12

REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/5.0",
    "Accept": "application/json",
    "Connection": "keep-alive",
}


# ============================================================
# RATE LIMIT / RETRY SETTINGS
# ============================================================

MAX_RETRIES = 3

RATE_LIMIT_BASE_WAIT = 1.5

# Small delay between retries only.
RETRY_JITTER = 0.25


# ============================================================
# REPEATED ALERT PROTECTION
#
# Same:
# symbol + direction + timeframe + BOS candle
#
# can repeat after 60 minutes.
# ============================================================

HISTORY_FILE = "alerts.json"

REPEAT_COOLDOWN = 60 * 60


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

        session.headers.update(
            HEADERS
        )

        _thread_local.session = session

    return session


# ============================================================
# LOAD ALERT HISTORY
# ============================================================

def load_alert_history():

    if not os.path.exists(HISTORY_FILE):
        return {}

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

            if isinstance(data, dict):
                return data

    except Exception as e:

        print(
            "History load error:",
            e,
        )

    return {}


# ============================================================
# SAVE ALERT HISTORY
# ============================================================

def save_alert_history(history):

    try:

        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                history,
                f,
                indent=2,
            )

        print(
            "Alert history saved."
        )

    except Exception as e:

        print(
            "History save error:",
            e,
        )


# ============================================================
# BUILD UNIQUE SIGNAL ID
#
# BOS candle time is included.
#
# SAME BOS = SAME SIGNAL ID
# NEW BOS = NEW SIGNAL ID
# ============================================================

def get_signal_id(signal):

    return (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['timeframe']}_"
        f"{signal['trigger_time']}"
    )


# ============================================================
# CHECK REPEAT COOLDOWN
# ============================================================

def is_repeat_blocked(
    signal,
    alert_history,
):

    signal_id = get_signal_id(
        signal
    )

    record = alert_history.get(
        signal_id
    )

    if not record:
        return False

    sent_at = record.get(
        "sent_at",
        0,
    )

    try:

        sent_at = float(
            sent_at
        )

    except Exception:

        return False

    elapsed = (
        time.time()
        - sent_at
    )

    if elapsed < REPEAT_COOLDOWN:

        remaining = (
            REPEAT_COOLDOWN
            - elapsed
        )

        remaining_minutes = (
            remaining / 60
        )

        print(
            "SKIPPED REPEAT:",
            signal["symbol"],
            signal["direction"],
            signal["timeframe"],
            f"| {remaining_minutes:.1f} min remaining",
        )

        return True

    return False


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        print(
            "Telegram secrets are missing."
        )

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

            print(
                "Telegram message sent."
            )

            return True

        print(
            "Telegram error:",
            response.text,
        )

    except Exception as e:

        print(
            "Telegram error:",
            e,
        )

    return False


# ============================================================
# GENERIC GATE GET
#
# Centralized retry / 429 handling.
# ============================================================

def gate_get(
    url,
    params=None,
    label="Gate request",
):

    for attempt in range(
        MAX_RETRIES
    ):

        try:

            session = get_session()

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            # ==================================================
            # SUCCESS
            # ==================================================

            if response.status_code == 200:

                return response.json()

            # ==================================================
            # RATE LIMIT
            # ==================================================

            if response.status_code == 429:

                retry_after = (
                    response.headers.get(
                        "Retry-After"
                    )
                )

                if retry_after:

                    try:
                        wait_time = float(
                            retry_after
                        )
                    except Exception:
                        wait_time = (
                            RATE_LIMIT_BASE_WAIT
                            * (2 ** attempt)
                        )

                else:

                    wait_time = (
                        RATE_LIMIT_BASE_WAIT
                        * (2 ** attempt)
                    )

                wait_time += (
                    RETRY_JITTER
                )

                print(
                    f"RATE LIMITED: "
                    f"{label} | "
                    f"retry {attempt + 1}/"
                    f"{MAX_RETRIES} | "
                    f"waiting "
                    f"{wait_time:.2f}s"
                )

                time.sleep(
                    wait_time
                )

                continue

            # ==================================================
            # OTHER HTTP ERROR
            # ==================================================

            print(
                f"{label}: "
                f"HTTP {response.status_code} | "
                f"{response.text[:200]}"
            )

            return None

        except Exception as e:

            if attempt >= (
                MAX_RETRIES - 1
            ):

                print(
                    f"{label}: "
                    f"request failed: "
                    f"{e}"
                )

                return None

            wait_time = (
                RATE_LIMIT_BASE_WAIT
                * (2 ** attempt)
            )

            print(
                f"{label}: "
                f"retrying after error "
                f"in {wait_time:.2f}s"
            )

            time.sleep(
                wait_time
            )

    return None


# ============================================================
# GET GATE.IO FUTURES SYMBOLS
# ============================================================

def get_symbols():

    url = (
        f"{GATE_URL}/futures/usdt/contracts"
    )

    data = gate_get(
        url,
        label="Futures symbols",
    )

    if not isinstance(
        data,
        list,
    ):

        return []

    symbols = []

    for item in data:

        try:

            symbol = item.get(
                "name",
                "",
            )

            if (
                symbol.endswith("_USDT")
                and not item.get(
                    "in_delisting",
                    False,
                )
            ):

                symbols.append(
                    symbol
                )

        except Exception:

            continue

    return sorted(
        set(symbols)
    )


# ============================================================
# GET ALL LIVE SPOT PRICES
#
# ONE request for all symbols.
# ============================================================

def get_all_spot_prices():

    url = (
        f"{GATE_URL}/spot/tickers"
    )

    data = gate_get(
        url,
        label="Spot tickers",
    )

    if not isinstance(
        data,
        list,
    ):

        return {}

    prices = {}

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


# ============================================================
# GET FUTURES CANDLES
#
# FUTURES = BOS / STRUCTURE ONLY
#
# OPTIMIZED:
# Only request the small amount of history
# actually required for recent BOS.
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
        "limit": FUTURES_CANDLE_LIMIT,
    }

    data = gate_get(
        url,
        params=params,
        label=f"Futures {symbol} {timeframe}",
    )

    if isinstance(
        data,
        list,
    ):

        return data

    return []


# ============================================================
# GET SPOT CANDLES
#
# SPOT = SMA50 / EMA20 / EMA200
#
# IMPORTANT:
# This is called ONLY AFTER a Futures BOS is found.
#
# This is the main request reduction.
# ============================================================

def get_spot_candles(
    symbol,
    timeframe,
):

    url = (
        f"{GATE_URL}/spot/candlesticks"
    )

    params = {
        "currency_pair": symbol,
        "interval": timeframe,
        "limit": SPOT_CANDLE_LIMIT,
    }

    data = gate_get(
        url,
        params=params,
        label=f"Spot {symbol} {timeframe}",
    )

    if isinstance(
        data,
        list,
    ):

        return data

    return []


# ============================================================
# PARSE FUTURES CANDLES
# ============================================================

def parse_futures_candles(data):

    candles = []

    for item in data:

        try:

            if isinstance(
                item,
                dict,
            ):

                candle = {
                    "time": int(
                        item["t"]
                    ),
                    "open": float(
                        item["o"]
                    ),
                    "high": float(
                        item["h"]
                    ),
                    "low": float(
                        item["l"]
                    ),
                    "close": float(
                        item["c"]
                    ),
                }

            else:

                candle = {
                    "time": int(
                        item[0]
                    ),
                    "open": float(
                        item[5]
                    ),
                    "high": float(
                        item[3]
                    ),
                    "low": float(
                        item[4]
                    ),
                    "close": float(
                        item[2]
                    ),
                }

            candles.append(
                candle
            )

        except Exception:

            continue

    candles.sort(
        key=lambda x:
            x["time"]
    )

    return candles


# ============================================================
# PARSE SPOT CANDLES
# ============================================================

def parse_spot_candles(data):

    candles = []

    for item in data:

        try:

            candle = {
                "time": int(
                    item[0]
                ),
                "open": float(
                    item[5]
                ),
                "high": float(
                    item[3]
                ),
                "low": float(
                    item[4]
                ),
                "close": float(
                    item[2]
                ),
            }

            candles.append(
                candle
            )

        except Exception:

            continue

    candles.sort(
        key=lambda x:
            x["time"]
    )

    return candles


# ============================================================
# REMOVE CURRENT OPEN CANDLE
# ============================================================

def remove_open_candle(
    candles,
    timeframe_seconds,
):

    now = int(
        time.time()
    )

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
        sum(
            values[-period:]
        )
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
        2
        / (
            period
            + 1
        )
    )

    value = (
        sum(
            values[:period]
        )
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
# LAST 10 COMPLETED FUTURES CANDLES
# ============================================================

def find_structure_break(
    candles
):

    if len(candles) < (
        STRUCTURE_CANDLES
        + 3
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
                    "structure_level":
                        swing_high,
                }

                if (
                    long_bos is None
                    or
                    candidate[
                        "candle"
                    ]["time"]
                    >
                    long_bos[
                        "candle"
                    ]["time"]
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
                    "structure_level":
                        swing_low,
                }

                if (
                    short_bos is None
                    or
                    candidate[
                        "candle"
                    ]["time"]
                    >
                    short_bos[
                        "candle"
                    ]["time"]
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
# IMPORTANT OPTIMIZATION:
#
# 1. Futures first.
# 2. Find BOS.
# 3. If no BOS -> STOP.
# 4. Only then request Spot candles.
#
# Signal rules remain unchanged.
# ============================================================

def analyze_timeframe(
    symbol,
    timeframe,
    spot_prices,
):

    # ========================================================
    # STEP 1: FUTURES
    # ========================================================

    futures_raw = get_futures_candles(
        symbol,
        timeframe,
    )

    futures_candles = parse_futures_candles(
        futures_raw
    )

    futures_candles = remove_open_candle(
        futures_candles,
        TIMEFRAMES[timeframe],
    )

    if len(futures_candles) < (
        STRUCTURE_CANDLES
        + 3
    ):

        return None, "data_error"

    # ========================================================
    # STEP 2: FIND BOS
    # ========================================================

    structure = find_structure_break(
        futures_candles
    )

    if not structure:

        # IMPORTANT:
        # No BOS means no reason to request Spot candles.
        return None, "no_bos"

    direction = structure[
        "direction"
    ]

    trigger = structure[
        "candle"
    ]

    # ========================================================
    # STEP 3: SPOT CANDLES
    #
    # Only requested if BOS exists.
    # ========================================================

    spot_raw = get_spot_candles(
        symbol,
        timeframe,
    )

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

        return None, "data_error"

    closes = [
        candle["close"]
        for candle in spot_candles
    ]

    # ========================================================
    # SPOT INDICATORS
    #
    # CLOSED CANDLES ONLY
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

        return None, "data_error"

    # ========================================================
    # LIVE SPOT PRICE
    # ========================================================

    price = spot_prices.get(
        symbol
    )

    if price is None:

        return None, "data_error"

    if price <= 0:

        return None, "data_error"

    # ========================================================
    # SMA50 / EMA200 GAP
    # ========================================================

    if ema200 == 0:

        return None, "data_error"

    gap = (
        abs(
            sma50
            - ema200
        )
        / abs(ema200)
    ) * 100

    # ========================================================
    # MINIMUM GAP
    # ========================================================

    minimum_gap = GAP_MINIMUM[
        timeframe
    ]

    if gap <= minimum_gap:

        return None, "gap"

    # ========================================================
    # PRICE TOLERANCE
    # ========================================================

    price_tolerance = (
        gap
        * PRICE_GAP_RATIO
    )

    price_tolerance_decimal = (
        price_tolerance
        / 100
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # SMA50 < CURRENT PRICE < EMA200

        if not (
            sma50
            < price
            < ema200
        ):

            return None, "price"

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

            return None, "price"

        if not (
            ema20
            <= sma50
            * (
                1
                + EMA20_TOLERANCE
            )
        ):

            return None, "ema20"

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

            return None, "sl_structure"

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
            "price_tolerance":
                price_tolerance,
            "trigger_time":
                trigger["time"],
        }, "signal"

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        # EMA200 < CURRENT PRICE < SMA50

        if not (
            ema200
            < price
            < sma50
        ):

            return None, "price"

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

            return None, "price"

        if not (
            ema20
            >= sma50
            * (
                1
                - EMA20_TOLERANCE
            )
        ):

            return None, "ema20"

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

            return None, "sl_structure"

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
            "price_tolerance":
                price_tolerance,
            "trigger_time":
                trigger["time"],
        }, "signal"

    return None, "ema20"


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
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    symbol = signal["symbol"]
    direction = signal["direction"]
    timeframe = signal["timeframe"]

    entry_price = signal["entry"]
    sl_price = signal["sl"]
    ema_tp_price = signal["tp"]

    entry = format_price(
        entry_price
    )

    sl = format_price(
        sl_price
    )

    # ========================================================
    # TP1 = 5%
    # TP2 = 10%
    # TP3 = EMA200
    # ========================================================

    if direction == "LONG":

        tp1_price = (
            entry_price
            * 1.05
        )

        tp2_price = (
            entry_price
            * 1.10
        )

    else:

        tp1_price = (
            entry_price
            * 0.95
        )

        tp2_price = (
            entry_price
            * 0.90
        )

    tp1 = format_price(
        tp1_price
    )

    tp2 = format_price(
        tp2_price
    )

    tp3 = format_price(
        ema_tp_price
    )

    gap = round(
        signal["gap"]
    )

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
# TIMEFRAME DISPLAY NAME
# ============================================================

def timeframe_name(timeframe):

    names = {
        "15m": "15M",
        "1h": "1H",
        "4h": "4H",
    }

    return names.get(
        timeframe,
        timeframe.upper(),
    )


# ============================================================
# FORMAT NO-SETUP REPORT
# ============================================================

def format_no_setup_report(
    timeframe,
    stats,
):

    name = timeframe_name(
        timeframe
    )

    scanned = stats["scanned"]

    no_bos = stats["no_bos"]
    gap = stats["gap"]
    price = stats["price"]
    ema20 = stats["ema20"]
    sl_structure = stats[
        "sl_structure"
    ]
    data_error = stats[
        "data_error"
    ]

    rejected = (
        no_bos
        + gap
        + price
        + ema20
        + sl_structure
        + data_error
    )

    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {name} SIGNAL SCAN\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"

        f"❌ *NO QUALIFYING SETUP*\n\n"

        f"🪙 Coins scanned: {scanned}\n"
        f"🚫 Rejected: {rejected}\n\n"

        f"🔎 No BOS: {no_bos}\n"
        f"📉 Gap rejected: {gap}\n"
        f"💰 Price rejected: {price}\n"
        f"📏 EMA20 rejected: {ema20}\n"
        f"🛑 SL/structure rejected: "
        f"{sl_structure}\n"
        f"⚠️ Data/API errors: {data_error}\n\n"

        f"✅ Qualifying setups: 0"
    )


# ============================================================
# FORMAT TIMEFRAME SUMMARY
# ============================================================

def format_timeframe_summary(
    timeframe,
    stats,
    signal_count,
):

    name = timeframe_name(
        timeframe
    )

    return (
        f"📊 *{name}*\n"
        f"🪙 Scanned: {stats['scanned']}\n"
        f"🔎 BOS found: {stats['bos_found']}\n"
        f"🆕 Alerts sent: {signal_count}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    scan_start = time.time()

    print("=" * 60)

    print(
        "LONG + SHORT SIGNAL BOT"
    )

    print(
        "15M / 1H / 4H"
    )

    print(
        "DAILY REMOVED"
    )

    print(
        "SPOT SMA50 / SPOT EMA20 / SPOT EMA200"
    )

    print(
        "FUTURES BOS / STRUCTURE"
    )

    print(
        "LIVE SPOT ENTRY PRICE"
    )

    print(
        "PROPER EMA200 WARM-UP"
    )

    print(
        "TIMEFRAME-SPECIFIC MINIMUM GAP"
    )

    print(
        "15M GAP > 10%"
    )

    print(
        "1H GAP > 20%"
    )

    print(
        "4H GAP > 35%"
    )

    print(
        "PRICE / GAP RATIO = 2:10"
    )

    print(
        "EMA20 TOLERANCE = 2%"
    )

    print(
        "RECENT BOS = LAST 10 COMPLETED FUTURES CANDLES"
    )

    print(
        "MOST RECENT BOS SELECTED"
    )

    print(
        "TP1 5% / TP2 10% / TP3 EMA200"
    )

    print(
        "EMA200 PRICE POSITION FILTER ENABLED"
    )

    print(
        "LONG = SMA50 < PRICE < EMA200"
    )

    print(
        "SHORT = EMA200 < PRICE < SMA50"
    )

    print(
        "NEW BOS = IMMEDIATE ALERT"
    )

    print(
        "SAME BOS = REPEAT EVERY 60 MINUTES"
    )

    print(
        "NO SETUP = TELEGRAM REPORT"
    )

    print(
        "OPTIMIZED: FUTURES BOS FIRST"
    )

    print(
        "OPTIMIZED: SPOT REQUEST ONLY AFTER BOS"
    )

    print(
        f"FUTURES CANDLE LIMIT = "
        f"{FUTURES_CANDLE_LIMIT}"
    )

    print(
        f"SPOT CANDLE LIMIT = "
        f"{SPOT_CANDLE_LIMIT}"
    )

    print(
        f"MAX WORKERS = "
        f"{MAX_WORKERS}"
    )

    print("=" * 60)

    # ========================================================
    # LOAD ALERT HISTORY
    # ========================================================

    alert_history = load_alert_history()

    print(
        f"Previously recorded alerts: "
        f"{len(alert_history)}"
    )

    # ========================================================
    # GET SYMBOLS
    # ========================================================

    symbols = get_symbols()

    if not symbols:

        print(
            "No symbols found."
        )

        send_telegram(
            "⚠️ *No symbols found.*\n\n"
            "Bot scan failed."
        )

        return

    print(
        f"Scanning "
        f"{len(symbols)} symbols..."
    )

    # ========================================================
    # GET ALL LIVE SPOT PRICES
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

        print(
            "No Spot prices found."
        )

        send_telegram(
            "⚠️ *Spot ticker request failed.*\n\n"
            "Bot scan failed."
        )

        return

    # ========================================================
    # BUILD JOBS
    # ========================================================

    jobs = []

    for symbol in symbols:

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
        f"Total scans: "
        f"{len(jobs)}"
    )

    # ========================================================
    # INITIALIZE STATISTICS
    # ========================================================

    stats = {}

    for timeframe in TIMEFRAMES:

        stats[timeframe] = {
            "scanned": 0,
            "bos_found": 0,
            "no_bos": 0,
            "gap": 0,
            "price": 0,
            "ema20": 0,
            "sl_structure": 0,
            "data_error": 0,
        }

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
            for symbol, timeframe
            in jobs
        }

        completed = 0

        for future in as_completed(
            futures
        ):

            completed += 1

            symbol, timeframe = (
                futures[future]
            )

            stats[timeframe][
                "scanned"
            ] += 1

            try:

                signal, reason = (
                    future.result()
                )

                # ====================================================
                # SIGNAL FOUND
                # ====================================================

                if signal:

                    stats[timeframe][
                        "bos_found"
                    ] += 1

                    if is_repeat_blocked(
                        signal,
                        alert_history,
                    ):

                        pass

                    else:

                        new_signals.append(
                            signal
                        )

                        print(
                            "NEW SIGNAL:",
                            signal["symbol"],
                            signal["direction"],
                            signal["timeframe"],
                        )

                    continue

                # ====================================================
                # NO SIGNAL
                # ====================================================

                if reason == "no_bos":

                    stats[timeframe][
                        "no_bos"
                    ] += 1

                elif reason == "gap":

                    stats[timeframe][
                        "gap"
                    ] += 1

                elif reason == "price":

                    stats[timeframe][
                        "price"
                    ] += 1

                elif reason == "ema20":

                    stats[timeframe][
                        "ema20"
                    ] += 1

                elif reason == "sl_structure":

                    stats[timeframe][
                        "sl_structure"
                    ] += 1

                else:

                    stats[timeframe][
                        "data_error"
                    ] += 1

            except Exception as e:

                stats[timeframe][
                    "data_error"
                ] += 1

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
        key=lambda x:
            x["gap"],
        reverse=True,
    )

    # ========================================================
    # SEND NEW SIGNALS
    # ========================================================

    print(
        f"NEW SIGNALS READY: "
        f"{len(new_signals)}"
    )

    sent_count_by_tf = {
        "15m": 0,
        "1h": 0,
        "4h": 0,
    }

    for signal in new_signals:

        message = format_signal(
            signal
        )

        print()
        print(message)
        print()

        sent = send_telegram(
            message
        )

        if sent:

            signal_id = get_signal_id(
                signal
            )

            alert_history[
                signal_id
            ] = {
                "symbol":
                    signal["symbol"],
                "direction":
                    signal["direction"],
                "timeframe":
                    signal["timeframe"],
                "trigger_time":
                    signal["trigger_time"],
                "sent_at":
                    int(time.time()),
            }

            save_alert_history(
                alert_history
            )

            sent_count_by_tf[
                signal["timeframe"]
            ] += 1

        time.sleep(0.5)

    # ========================================================
    # NO-SETUP REPORTS
    #
    # Only send NO SETUP when that timeframe
    # produced zero qualifying alerts.
    # ========================================================

    for timeframe in TIMEFRAMES:

        if (
            sent_count_by_tf[
                timeframe
            ] == 0
        ):

            report = (
                format_no_setup_report(
                    timeframe,
                    stats[timeframe],
                )
            )

            print()
            print(report)
            print()

            send_telegram(
                report
            )

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
        f"NEW SIGNALS SENT: "
        f"{len(new_signals)}"
    )

    # ========================================================
    # PRINT TIMEFRAME STATISTICS
    # ========================================================

    for timeframe in TIMEFRAMES:

        s = stats[timeframe]

        print()
        print(
            f"===== "
            f"{timeframe_name(timeframe)} "
            f"STATISTICS ====="
        )

        print(
            f"Scanned: "
            f"{s['scanned']}"
        )

        print(
            f"BOS found: "
            f"{s['bos_found']}"
        )

        print(
            f"No BOS: "
            f"{s['no_bos']}"
        )

        print(
            f"Gap rejected: "
            f"{s['gap']}"
        )

        print(
            f"Price rejected: "
            f"{s['price']}"
        )

        print(
            f"EMA20 rejected: "
            f"{s['ema20']}"
        )

        print(
            f"SL/structure rejected: "
            f"{s['sl_structure']}"
        )

        print(
            f"Data/API errors: "
            f"{s['data_error']}"
        )

        print(
            f"Alerts sent: "
            f"{sent_count_by_tf[timeframe]}"
        )

    print()

    print(
        "NEW BOS = IMMEDIATE ALERT"
    )

    print(
        "SAME BOS = REPEAT EVERY 60 MINUTES"
    )

    print(
        "NO SETUP = TELEGRAM REPORT"
    )

    print("=" * 60)

    print(
        "Finished."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
