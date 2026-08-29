import os
import time
import threading
import json
import random
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
# ============================================================

SPOT_CANDLE_LIMIT = 1000


# ============================================================
# FUTURES HISTORY
# ============================================================

FUTURES_CANDLE_LIMIT = 20

STRUCTURE_CANDLES = 10


# ============================================================
# EMA20 TOLERANCE
# ============================================================

EMA20_TOLERANCE = 0.02


# ============================================================
# PRICE / GAP RATIO
#
# 20% of the SMA50 -> EMA200 gap.
#
# Example:
# Gap = 40%
# Allowed price distance = 8%
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
# NO FIXED REQUEST PACER.
#
# Gate is allowed to process requests as fast as possible.
# Backoff happens ONLY when Gate actually returns 429.
# ============================================================

MAX_WORKERS = 12

REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/6.0",
    "Accept": "application/json",
    "Connection": "keep-alive",
}


# ============================================================
# RATE LIMIT / RETRY
# ============================================================

MAX_RETRIES = 3

RATE_LIMIT_BASE_WAIT = 1.5

RATE_LIMIT_MAX_WAIT = 8.0

RETRY_JITTER = 0.25


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TIMEOUT = 20

TELEGRAM_DELAY = 0.30


# ============================================================
# REPEAT COOLDOWN
#
# SAME BOS:
# repeat once every 60 minutes.
#
# NEW BOS:
# immediate alert.
# ============================================================

HISTORY_FILE = "alerts.json"

REPEAT_COOLDOWN = 60 * 60


# ============================================================
# THREAD LOCAL SESSION
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

        temp_file = (
            HISTORY_FILE
            + ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                history,
                f,
                indent=2,
            )

        os.replace(
            temp_file,
            HISTORY_FILE,
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
# SIGNAL ID
#
# Same:
# symbol + direction + timeframe + BOS candle
#
# = same BOS
# ============================================================

def get_signal_id(signal):

    return (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['timeframe']}_"
        f"{signal['trigger_time']}"
    )


# ============================================================
# CHECK SIGNAL STATUS
#
# NEW
#     Never alerted before.
#
# REPEAT
#     Same BOS and 60 minutes passed.
#
# BLOCKED
#     Same BOS and less than 60 minutes.
# ============================================================

def get_signal_status(
    signal,
    alert_history,
    print_blocked=True,
):

    signal_id = get_signal_id(
        signal
    )

    record = alert_history.get(
        signal_id
    )

    if not record:

        return "NEW"

    sent_at = record.get(
        "sent_at",
        0,
    )

    try:

        sent_at = float(
            sent_at
        )

    except Exception:

        return "NEW"

    elapsed = (
        time.time()
        - sent_at
    )

    if elapsed >= REPEAT_COOLDOWN:

        return "REPEAT"

    if print_blocked:

        remaining = (
            REPEAT_COOLDOWN
            - elapsed
        )

        remaining_minutes = (
            remaining / 60
        )

        print(
            "SKIPPED SAME BOS:",
            signal["symbol"],
            signal["direction"],
            signal["timeframe"],
            f"| {remaining_minutes:.1f} min remaining",
        )

    return "BLOCKED"


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
            timeout=TELEGRAM_TIMEOUT,
        )

        if response.status_code == 200:

            print(
                "Telegram message sent."
            )

            return True

        print(
            "Telegram error:",
            response.text[:500],
        )

    except Exception as e:

        print(
            "Telegram error:",
            e,
        )

    return False


# ============================================================
# GATE GET
#
# IMPORTANT OPTIMIZATION:
#
# There is NO fixed sleep between normal requests.
#
# Sleep happens ONLY on:
# - HTTP 429
# - temporary request exception
#
# Retry-After is respected when available.
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

                try:

                    return response.json()

                except Exception as e:

                    print(
                        f"{label}: "
                        f"JSON error: {e}"
                    )

                    return None

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
                            * (
                                2 ** attempt
                            )
                        )

                else:

                    wait_time = (
                        RATE_LIMIT_BASE_WAIT
                        * (
                            2 ** attempt
                        )
                    )

                wait_time = min(
                    wait_time,
                    RATE_LIMIT_MAX_WAIT,
                )

                wait_time += random.uniform(
                    0,
                    RETRY_JITTER,
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
            # TEMPORARY SERVER ERRORS
            # ==================================================

            if response.status_code in (
                500,
                502,
                503,
                504,
            ):

                if attempt >= (
                    MAX_RETRIES - 1
                ):

                    print(
                        f"{label}: "
                        f"HTTP "
                        f"{response.status_code}"
                    )

                    return None

                wait_time = min(
                    RATE_LIMIT_BASE_WAIT
                    * (
                        2 ** attempt
                    ),
                    RATE_LIMIT_MAX_WAIT,
                )

                wait_time += random.uniform(
                    0,
                    RETRY_JITTER,
                )

                print(
                    f"{label}: "
                    f"server HTTP "
                    f"{response.status_code} | "
                    f"retrying in "
                    f"{wait_time:.2f}s"
                )

                time.sleep(
                    wait_time
                )

                continue

            # ==================================================
            # OTHER ERROR
            # ==================================================

            print(
                f"{label}: "
                f"HTTP "
                f"{response.status_code} | "
                f"{response.text[:200]}"
            )

            return None

        except (
            requests.Timeout,
            requests.ConnectionError,
        ) as e:

            if attempt >= (
                MAX_RETRIES - 1
            ):

                print(
                    f"{label}: "
                    f"request failed: "
                    f"{e}"
                )

                return None

            wait_time = min(
                RATE_LIMIT_BASE_WAIT
                * (
                    2 ** attempt
                ),
                RATE_LIMIT_MAX_WAIT,
            )

            wait_time += random.uniform(
                0,
                RETRY_JITTER,
            )

            print(
                f"{label}: "
                f"temporary error | "
                f"retrying in "
                f"{wait_time:.2f}s"
            )

            time.sleep(
                wait_time
            )

        except Exception as e:

            print(
                f"{label}: "
                f"unexpected error: "
                f"{e}"
            )

            return None

    return None


# ============================================================
# GET FUTURES SYMBOLS
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
# ALL LIVE SPOT PRICES
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
# FUTURES CANDLES
#
# FUTURES = BOS / STRUCTURE
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
        label=(
            f"Futures "
            f"{symbol} "
            f"{timeframe}"
        ),
    )

    if isinstance(
        data,
        list,
    ):

        return data

    return []


# ============================================================
# SPOT CANDLES
#
# SPOT = SMA50 / EMA20 / EMA200
#
# ONLY requested AFTER BOS.
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
        label=(
            f"Spot "
            f"{symbol} "
            f"{timeframe}"
        ),
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

            # ------------------------------------------------
            # Dictionary response
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Array response
            # ------------------------------------------------

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
#
# Indicators and BOS use CLOSED candles only.
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
#
# Starts from SMA of the first 200 historical values
# and then applies the EMA formula across all remaining
# values.
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
# Uses LAST 10 COMPLETED FUTURES CANDLES.
#
# Most recent valid LONG or SHORT BOS is selected.
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
# OPTIMIZATION ORDER:
#
# 1. Futures request
# 2. Remove open candle
# 3. Find BOS
# 4. If no BOS -> STOP
# 5. Spot request
# 6. Calculate indicators
# 7. Gap
# 8. Price
# 9. EMA20
# 10. SL / structure
# 11. Signal
# ============================================================

def analyze_timeframe(
    symbol,
    timeframe,
    spot_prices,
):

    # ========================================================
    # FUTURES FIRST
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
    # BOS
    # ========================================================

    structure = find_structure_break(
        futures_candles
    )

    if not structure:

        return None, "no_bos"

    direction = structure[
        "direction"
    ]

    trigger = structure[
        "candle"
    ]

    # ========================================================
    # SPOT ONLY AFTER BOS
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

    # Need enough history for proper EMA200 warm-up.
    if len(spot_candles) < (
        EMA_SLOW + 50
    ):

        return None, "data_error"

    closes = [
        candle["close"]
        for candle in spot_candles
    ]

    # ========================================================
    # INDICATORS
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
    # GAP
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
    # PRICE / GAP ALLOWANCE
    #
    # 20% of the calculated gap.
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
    #
    # PRICE:
    # SMA50 < PRICE < EMA200
    # ========================================================

    if direction == "LONG":

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

        # ----------------------------------------------------
        # EMA20 tolerance
        # ----------------------------------------------------

        if not (
            ema20
            <= sma50
            * (
                1
                + EMA20_TOLERANCE
            )
        ):

            return None, "ema20"

        # ----------------------------------------------------
        # SL = LOW OF BOS CANDLE
        # ----------------------------------------------------

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
    #
    # PRICE:
    # EMA200 < PRICE < SMA50
    # ========================================================

    if direction == "SHORT":

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

        # ----------------------------------------------------
        # EMA20 tolerance
        # ----------------------------------------------------

        if not (
            ema20
            >= sma50
            * (
                1
                - EMA20_TOLERANCE
            )
        ):

            return None, "ema20"

        # ----------------------------------------------------
        # SL = HIGH OF BOS CANDLE
        # ----------------------------------------------------

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
    # TP1 / TP2
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
# TIMEFRAME NAME
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
# NO FRESH SIGNAL REPORT
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
        f"📊 {name} FRESH SIGNAL SCAN\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"

        f"❌ *ZERO FRESH SIGNALS FOUND*\n\n"

        f"🪙 Coins scanned: {scanned}\n"
        f"🚫 Rejected: {rejected}\n\n"

        f"🔎 No BOS: {no_bos}\n"
        f"📉 Gap rejected: {gap}\n"
        f"💰 Price rejected: {price}\n"
        f"📏 EMA20 rejected: {ema20}\n"
        f"🛑 SL/structure rejected: "
        f"{sl_structure}\n"
        f"⚠️ Data/API errors: "
        f"{data_error}\n\n"

        f"✅ Fresh qualifying setups: 0"
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
        "FRESH SCAN = EVERY 5 MINUTES"
    )

    print(
        "ZERO FRESH SIGNALS = TELEGRAM REPORT"
    )

    print(
        "OPTIMIZED: FUTURES BOS FIRST"
    )

    print(
        "OPTIMIZED: SPOT REQUEST ONLY AFTER BOS"
    )

    print(
        "NO FIXED REQUEST PACER"
    )

    print(
        "RATE LIMIT BACKOFF = ONLY ON HTTP 429"
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
    # LOAD HISTORY
    # ========================================================

    alert_history = load_alert_history()

    print(
        f"Previously recorded alerts: "
        f"{len(alert_history)}"
    )


    # ========================================================
    # SYMBOLS
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
    # LIVE SPOT PRICES
    #
    # ONE REQUEST FOR ALL SPOT PRICES.
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
    # JOBS
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
    # STATISTICS
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
    # SCAN
    # ========================================================

    scan_api_start = time.time()

    fresh_signals = []

    repeat_signals = []

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

                # =================================================
                # SIGNAL / QUALIFYING SETUP
                # =================================================

                if signal:

                    stats[timeframe][
                        "bos_found"
                    ] += 1

                    status = get_signal_status(
                        signal,
                        alert_history,
                        print_blocked=True,
                    )

                    # =============================================
                    # NEW BOS
                    # =============================================

                    if status == "NEW":

                        fresh_signals.append(
                            signal
                        )

                        print(
                            "NEW FRESH SIGNAL:",
                            signal["symbol"],
                            signal["direction"],
                            signal["timeframe"],
                        )

                    # =============================================
                    # SAME BOS AFTER 60 MINUTES
                    # =============================================

                    elif status == "REPEAT":

                        repeat_signals.append(
                            signal
                        )

                        print(
                            "HOURLY REPEAT:",
                            signal["symbol"],
                            signal["direction"],
                            signal["timeframe"],
                        )

                    continue


                # =================================================
                # NO SIGNAL
                # =================================================

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

            # =================================================
            # PROGRESS
            # =================================================

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
    # SORT BY LARGEST GAP
    # ========================================================

    fresh_signals.sort(
        key=lambda x:
            x["gap"],
        reverse=True,
    )

    repeat_signals.sort(
        key=lambda x:
            x["gap"],
        reverse=True,
    )


    # ========================================================
    # COMBINE
    #
    # Fresh signals first.
    # Hourly repeats after fresh signals.
    # ========================================================

    signals_to_send = (
        fresh_signals
        + repeat_signals
    )


    # ========================================================
    # SEND SIGNALS
    # ========================================================

    print(
        f"FRESH SIGNALS: "
        f"{len(fresh_signals)}"
    )

    print(
        f"HOURLY REPEATS: "
        f"{len(repeat_signals)}"
    )

    print(
        f"TOTAL SIGNAL ALERTS: "
        f"{len(signals_to_send)}"
    )


    sent_count_by_tf = {
        "15m": 0,
        "1h": 0,
        "4h": 0,
    }

    fresh_sent_by_tf = {
        "15m": 0,
        "1h": 0,
        "4h": 0,
    }

    repeat_sent_by_tf = {
        "15m": 0,
        "1h": 0,
        "4h": 0,
    }


    # ========================================================
    # SEND
    # ========================================================

    for signal in signals_to_send:

        message = format_signal(
            signal
        )

        signal_id = get_signal_id(
            signal
        )

        # =====================================================
        # IMPORTANT:
        #
        # Determine status ONCE.
        # Do not call get_signal_status twice.
        # =====================================================

        if signal in fresh_signals:

            status = "NEW"

        else:

            status = "REPEAT"

        # =====================================================
        # ALERT TYPE
        # =====================================================

        if status == "REPEAT":

            message = (
                "🔄 *HOURLY REPEAT*\n\n"
                + message
            )

        else:

            message = (
                "🆕 *FRESH SIGNAL*\n\n"
                + message
            )

        print()

        print(message)

        print()

        sent = send_telegram(
            message
        )

        if sent:

            now = int(
                time.time()
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
                    now,
            }

            save_alert_history(
                alert_history
            )

            sent_count_by_tf[
                signal["timeframe"]
            ] += 1

            if status == "REPEAT":

                repeat_sent_by_tf[
                    signal["timeframe"]
                ] += 1

            else:

                fresh_sent_by_tf[
                    signal["timeframe"]
                ] += 1

        time.sleep(
            TELEGRAM_DELAY
        )


    # ========================================================
    # ZERO FRESH SIGNAL REPORT
    #
    # IMPORTANT:
    #
    # ONLY NEW/FRESH SIGNALS count here.
    #
    # A repeat does NOT prevent the zero-fresh report.
    # ========================================================

    for timeframe in TIMEFRAMES:

        if (
            fresh_sent_by_tf[
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

            time.sleep(
                TELEGRAM_DELAY
            )


    # ========================================================
    # PERFORMANCE
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
        f"FRESH SIGNALS SENT: "
        f"{sum(fresh_sent_by_tf.values())}"
    )

    print(
        f"HOURLY REPEATS SENT: "
        f"{sum(repeat_sent_by_tf.values())}"
    )


    # ========================================================
    # STATISTICS
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
            f"Qualifying BOS/SETUP: "
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
            f"Fresh alerts sent: "
            f"{fresh_sent_by_tf[timeframe]}"
        )

        print(
            f"Hourly repeats sent: "
            f"{repeat_sent_by_tf[timeframe]}"
        )


    print()

    print(
        "FRESH SCAN = EVERY 5 MINUTES"
    )

    print(
        "NEW BOS = IMMEDIATE ALERT"
    )

    print(
        "SAME BOS = REPEAT EVERY 60 MINUTES"
    )

    print(
        "ZERO FRESH SIGNALS = TELEGRAM REPORT"
    )

    print(
        "NO FIXED REQUEST PACER"
    )

    print(
        "429 ONLY = EXPONENTIAL BACKOFF"
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
