import os
import time
import json
import subprocess
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

# Slightly looser BOS
STRUCTURE_CANDLES = 10

# EMA20 tolerance
EMA20_TOLERANCE = 0.02

# Price must be close to SMA50
# Maximum distance = 2%
PRICE_SMA50_TOLERANCE = 0.02


# ============================================================
# TIMEFRAME-SPECIFIC GAP REQUIREMENTS
#
# 15M  -> >10%
# 1H   -> >20%
# 4H   -> >35%
# Daily -> >30%
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


CANDLE_LIMIT = 250
MAX_WORKERS = 12

HISTORY_FILE = "signals.json"

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/1.0",
    "Accept": "application/json",
}


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
# SIGNAL HISTORY
# ============================================================

def load_history():

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        return set(data)

    except Exception:

        return set()


def save_history(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            sorted(history),
            f,
            indent=2,
        )


# ============================================================
# SAVE HISTORY TO GITHUB
#
# FIXED:
# If GitHub has a newer commit, pull/rebase
# before pushing.
# ============================================================

def push_history():

    try:

        subprocess.run(
            [
                "git",
                "config",
                "user.name",
                "github-actions[bot]",
            ],
            check=True,
        )

        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ],
            check=True,
        )

        # Add history file
        subprocess.run(
            [
                "git",
                "add",
                HISTORY_FILE,
            ],
            check=True,
        )

        # Check whether there is anything to commit
        changed = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--quiet",
            ]
        )

        if changed.returncode == 0:
            return

        # Commit
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Update signal history",
            ],
            check=True,
        )

        # ----------------------------------------------------
        # Pull latest remote changes and rebase
        # ----------------------------------------------------

        print(
            "Syncing latest GitHub changes..."
        )

        pull_result = subprocess.run(
            [
                "git",
                "pull",
                "--rebase",
                "origin",
                "main",
            ]
        )

        if pull_result.returncode != 0:

            print(
                "Git pull/rebase failed."
            )

            # Try to abort rebase if one started
            subprocess.run(
                [
                    "git",
                    "rebase",
                    "--abort",
                ],
                check=False,
            )

            return

        # ----------------------------------------------------
        # Push
        # ----------------------------------------------------

        print(
            "Pushing signal history..."
        )

        push_result = subprocess.run(
            [
                "git",
                "push",
                "origin",
                "main",
            ]
        )

        if push_result.returncode == 0:

            print(
                "Signal history pushed to GitHub."
            )

        else:

            print(
                "Signal history push failed."
            )

    except Exception as e:

        print(
            "History push error:",
            e,
        )


# ============================================================
# GET GATE.IO SYMBOLS
# ============================================================

def get_symbols():

    url = (
        f"{GATE_URL}/futures/usdt/contracts"
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
        )

        if response.status_code != 200:

            print(
                "Symbol error:",
                response.text,
            )

            return []

        data = response.json()

        symbols = []

        for item in data:

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

                symbols.append(symbol)

        return sorted(set(symbols))

    except Exception as e:

        print(
            "Symbol request error:",
            e,
        )

        return []


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(
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

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20,
            )

            if response.status_code == 200:

                data = response.json()

                if isinstance(data, list):
                    return data

                return []

            if response.status_code == 429:

                time.sleep(
                    2 ** attempt
                )

                continue

            return []

        except Exception as e:

            if attempt == 2:

                print(
                    f"{symbol} {timeframe}: {e}"
                )

                return []

            time.sleep(
                2 ** attempt
            )

    return []


# ============================================================
# PARSE CANDLES
# ============================================================

def parse_candles(data):

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
# REMOVE CURRENT OPEN CANDLE
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
# ============================================================

def calculate_ema(
    values,
    period,
):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    value = (
        sum(values[:period])
        / period
    )

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
        ) + value

    return value


# ============================================================
# SLIGHTLY LOOSER RECENT BOS
#
# Searches latest 10 completed candles.
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
                    "structure_level":
                        swing_high,
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
                    "structure_level":
                        swing_low,
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
# ============================================================

def analyze_timeframe(
    symbol,
    timeframe,
):

    raw = get_candles(
        symbol,
        timeframe,
    )

    candles = parse_candles(raw)

    candles = remove_open_candle(
        candles,
        TIMEFRAMES[timeframe],
    )

    if len(candles) < (
        EMA_SLOW
        + STRUCTURE_CANDLES
        + 5
    ):

        return None

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    structure = find_structure_break(
        candles
    )

    if not structure:
        return None

    direction = structure[
        "direction"
    ]

    trigger = structure[
        "candle"
    ]

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

    closes = [
        candle["close"]
        for candle in candles
    ]

    price = closes[-1]

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
    # TIMEFRAME-SPECIFIC GAP
    # ========================================================

    if ema200 == 0:
        return None

    gap = (
        abs(sma50 - ema200)
        / abs(ema200)
    ) * 100

    minimum_gap = GAP_MINIMUM[
        timeframe
    ]

    if gap <= minimum_gap:
        return None

    # ========================================================
    # LONG
    #
    # Price:
    #   Above SMA50
    #   Maximum 2% above SMA50
    #
    # EMA20:
    #   Within 2% tolerance of SMA50
    # ========================================================

    if direction == "LONG":

        if not (
            sma50 < price
            <= sma50
            * (1 + PRICE_SMA50_TOLERANCE)
        ):

            return None

        if not (
            ema20
            <= sma50
            * (1 + EMA20_TOLERANCE)
        ):

            return None

        sl = trigger["low"]

        if not (
            sl < ema20
            <= sma50
            * (1 + EMA20_TOLERANCE)
        ):

            return None

        return {
            "direction": "LONG",
            "symbol":
                symbol.replace(
                    "_USDT",
                    "",
                ),
            "timeframe":
                timeframe,
            "entry":
                price,
            "sl":
                sl,
            "tp":
                ema200,
            "gap":
                gap,
            "trigger_time":
                trigger["time"],
        }

    # ========================================================
    # SHORT
    #
    # Price:
    #   Below SMA50
    #   Maximum 2% below SMA50
    #
    # EMA20:
    #   Within 2% tolerance of SMA50
    # ========================================================

    if direction == "SHORT":

        if not (
            sma50
            * (1 - PRICE_SMA50_TOLERANCE)
            <= price
            < sma50
        ):

            return None

        if not (
            ema20
            >= sma50
            * (1 - EMA20_TOLERANCE)
        ):

            return None

        sl = trigger["high"]

        if not (
            ema200
            < ema20
            >= sma50
            * (1 - EMA20_TOLERANCE)
            and sl > ema20
        ):

            return None

        return {
            "direction": "SHORT",
            "symbol":
                symbol.replace(
                    "_USDT",
                    "",
                ),
            "timeframe":
                timeframe,
            "entry":
                price,
            "sl":
                sl,
            "tp":
                ema200,
            "gap":
                gap,
            "trigger_time":
                trigger["time"],
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

    entry = format_price(
        entry_price
    )

    sl = format_price(
        sl_price
    )

    # ========================================================
    # TP1 = 5%
    # TP2 = 10%
    # TP3 = 200 EMA
    # ========================================================

    if direction == "LONG":

        tp1_price = (
            entry_price * 1.05
        )

        tp2_price = (
            entry_price * 1.10
        )

    else:

        tp1_price = (
            entry_price * 0.95
        )

        tp2_price = (
            entry_price * 0.90
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

        f"Gap: {gap}%"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "LONG + SHORT SIGNAL BOT"
    )

    print(
        "15M / 1H / 4H / DAILY"
    )

    print(
        "20 EMA / 50 SMA / 200 EMA"
    )

    print(
        "TIMEFRAME-SPECIFIC GAP"
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
        "DAILY GAP > 30%"
    )

    print(
        "PRICE MAX 2% FROM SMA50"
    )

    print(
        "EMA20 TOLERANCE: 2%"
    )

    print(
        "SLIGHTLY LOOSER RECENT BOS"
    )

    print(
        "RECENT BOS: LAST 10 COMPLETED CANDLES"
    )

    print(
        "MOST RECENT BOS SELECTED"
    )

    print(
        "TP1 5% / TP2 10% / TP3 EMA-BASED"
    )

    print(
        "NO RSI"
    )

    print("=" * 60)

    history = load_history()

    print(
        f"Previously sent signals: "
        f"{len(history)}"
    )

    symbols = get_symbols()

    if not symbols:

        print(
            "No symbols found."
        )

        send_telegram(
            "⚠️ No symbols found.\n\n"
            "Bot scan failed."
        )

        return

    print(
        f"Scanning "
        f"{len(symbols)} symbols..."
    )

    jobs = []

    for symbol in symbols:

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

    new_signals = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_timeframe,
                symbol,
                timeframe,
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

            try:

                signal = (
                    future.result()
                )

                if signal:

                    key = (
                        f"{signal['symbol']}|"
                        f"{signal['direction']}|"
                        f"{signal['timeframe']}|"
                        f"{signal['trigger_time']}"
                    )

                    signal["key"] = key

                    if key not in history:

                        new_signals.append(
                            signal
                        )

                        print(
                            "NEW:",
                            key,
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

                print(
                    f"Progress: "
                    f"{completed}/"
                    f"{len(jobs)}"
                )

    # ========================================================
    # SORT BY GAP
    # ========================================================

    new_signals.sort(
        key=lambda x: x["gap"],
        reverse=True,
    )

    # ========================================================
    # NO NEW SIGNAL
    # ========================================================

    if not new_signals:

        print(
            "No NEW signals."
        )

        send_telegram(
            "🔍 No signal found.\n\n"
            "Next scan: 5 minutes"
        )

        return

    # ========================================================
    # SEND NEW SIGNALS
    # ========================================================

    print(
        f"NEW SIGNALS: "
        f"{len(new_signals)}"
    )

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

            history.add(
                signal["key"]
            )

            save_history(
                history
            )

            push_history()

        time.sleep(0.5)

    print(
        "Finished."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
