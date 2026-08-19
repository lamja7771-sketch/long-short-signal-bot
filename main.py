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

STRUCTURE_CANDLES = 20

TIMEFRAMES = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
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

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

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
# SIGNAL HISTORY
# ============================================================

def load_history():

    try:

        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return set(data)

    except Exception:

        return set()


def save_history(history):

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:

        json.dump(
            sorted(history),
            f,
            indent=2,
        )


# ============================================================
# SAVE HISTORY TO GITHUB
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

        subprocess.run(
            ["git", "add", HISTORY_FILE],
            check=True,
        )

        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"]
        )

        if changed.returncode == 0:
            return

        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Update signal history",
            ],
            check=True,
        )

        subprocess.run(
            ["git", "push"],
            check=True,
        )

        print("Signal history pushed to GitHub.")

    except Exception as e:

        print("History push error:", e)


# ============================================================
# GET GATE.IO SYMBOLS
# ============================================================

def get_symbols():

    url = f"{GATE_URL}/futures/usdt/contracts"

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
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
                and not item.get("in_delisting", False)
            ):
                symbols.append(symbol)

        return sorted(set(symbols))

    except Exception as e:

        print("Symbol request error:", e)
        return []


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(symbol, timeframe):

    url = f"{GATE_URL}/futures/usdt/candlesticks"

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

                time.sleep(2 ** attempt)
                continue

            return []

        except Exception as e:

            if attempt == 2:

                print(
                    f"{symbol} {timeframe}: {e}"
                )

                return []

            time.sleep(2 ** attempt)

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
        if candle["time"] + timeframe_seconds <= now
    ]


# ============================================================
# SMA
# ============================================================

def calculate_sma(values, period):

    if len(values) < period:
        return None

    return sum(values[-period:]) / period


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    value = sum(values[:period]) / period

    for price in values[period:]:

        value = (
            (price - value) * multiplier
        ) + value

    return value


# ============================================================
# STRUCTURE BREAK / BOS
# ============================================================

def find_structure_break(candles):

    if len(candles) < STRUCTURE_CANDLES + 5:
        return None

    # --------------------------------------------------------
    # Look at the most recent 20 COMPLETED candles
    # --------------------------------------------------------

    window = candles[-STRUCTURE_CANDLES:]

    long_bos = None
    short_bos = None

    # ========================================================
    # LONG BOS
    # ========================================================

    for i in range(2, len(window) - 1):

        swing_high = window[i]["high"]

        # Confirm swing high
        if not (
            swing_high > window[i - 1]["high"]
            and
            swing_high > window[i + 1]["high"]
        ):
            continue

        # Check candles after the swing high
        for j in range(i + 1, len(window)):

            if window[j]["close"] > swing_high:

                candidate = {
                    "direction": "LONG",
                    "candle": window[j],
                    "structure_level": swing_high,
                }

                # Keep the most recent LONG BOS
                if (
                    long_bos is None
                    or candidate["candle"]["time"]
                    > long_bos["candle"]["time"]
                ):
                    long_bos = candidate

    # ========================================================
    # SHORT BOS
    # ========================================================

    for i in range(2, len(window) - 1):

        swing_low = window[i]["low"]

        # Confirm swing low
        if not (
            swing_low < window[i - 1]["low"]
            and
            swing_low < window[i + 1]["low"]
        ):
            continue

        # Check candles after the swing low
        for j in range(i + 1, len(window)):

            if window[j]["close"] < swing_low:

                candidate = {
                    "direction": "SHORT",
                    "candle": window[j],
                    "structure_level": swing_low,
                }

                # Keep the most recent SHORT BOS
                if (
                    short_bos is None
                    or candidate["candle"]["time"]
                    > short_bos["candle"]["time"]
                ):
                    short_bos = candidate

    # ========================================================
    # SELECT MOST RECENT BOS BETWEEN LONG + SHORT
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
        key=lambda x: x["candle"]["time"],
    )


# ============================================================
# ANALYZE ONE SYMBOL / ONE TIMEFRAME
# ============================================================

def analyze_timeframe(symbol, timeframe):

    raw = get_candles(
        symbol,
        timeframe,
    )

    candles = parse_candles(raw)

    candles = remove_open_candle(
        candles,
        TIMEFRAMES[timeframe],
    )

    if len(candles) < EMA_SLOW + STRUCTURE_CANDLES + 5:
        return None

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    structure = find_structure_break(candles)

    if not structure:
        return None

    direction = structure["direction"]
    trigger = structure["candle"]

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

    # --------------------------------------------------------
    # GAP > 10%
    # --------------------------------------------------------

    if ema200 == 0:
        return None

    gap = (
        abs(sma50 - ema200)
        / abs(ema200)
    ) * 100

    if gap <= 10:
        return None

    # ========================================================
    # LONG
    #
    # 50 SMA < PRICE < 200 EMA
    # 20 EMA < 50 SMA
    # SL < 20 EMA
    # ========================================================

    if direction == "LONG":

        if not (
            sma50 < price < ema200
        ):
            return None

        if not (
            ema20 < sma50
        ):
            return None

        sl = trigger["low"]

        if not (
            sl < ema20 < sma50
        ):
            return None

        return {
            "direction": "LONG",
            "symbol": symbol.replace("_USDT", ""),
            "timeframe": timeframe,
            "entry": price,
            "sl": sl,
            "tp": ema200,
            "gap": gap,
            "trigger_time": trigger["time"],
        }

    # ========================================================
    # SHORT
    #
    # 200 EMA < PRICE < 50 SMA
    # 20 EMA > 50 SMA
    # 20 EMA < SL
    # ========================================================

    if direction == "SHORT":

        if not (
            ema200 < price < sma50
        ):
            return None

        if not (
            ema20 > sma50
        ):
            return None

        sl = trigger["high"]

        if not (
            ema200 < ema20 < sl
        ):
            return None

        return {
            "direction": "SHORT",
            "symbol": symbol.replace("_USDT", ""),
            "timeframe": timeframe,
            "entry": price,
            "sl": sl,
            "tp": ema200,
            "gap": gap,
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

    # --------------------------------------------------------
    # TP1 = 5%
    # TP2 = 10%
    # TP3 = 200 EMA
    # --------------------------------------------------------

    if direction == "LONG":

        tp1_price = entry_price * 1.05
        tp2_price = entry_price * 1.10

    else:

        tp1_price = entry_price * 0.95
        tp2_price = entry_price * 0.90

    tp1 = format_price(tp1_price)
    tp2 = format_price(tp2_price)
    tp3 = format_price(ema_tp_price)

    gap = round(
        signal["gap"]
    )

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return (
        f"#{symbol} {direction} {timeframe} {emoji}\n\n"
        f"Entry: ${entry}\n"
        f"SL: ${sl}\n\n"
        f"**TP1: ${tp1} — 5%**\n"
        f"**TP2: ${tp2} — 10%**\n"
        f"**TP3: ${tp3}**\n\n"
        f"Gap: {gap}%"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 50)
    print("LONG + SHORT SIGNAL BOT")
    print("15M / 1H / 4H INDEPENDENT STRUCTURE")
    print("20 EMA / 50 SMA / 200 EMA")
    print("GAP > 10%")
    print("BOS SCANNED INSIDE 20-CANDLE STRUCTURE WINDOW")
    print("MOST RECENT BOS SELECTED: LONG vs SHORT")
    print("=" * 50)

    history = load_history()

    print(
        f"Previously sent signals: {len(history)}"
    )

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
        f"Total scans: {len(jobs)}"
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
            for symbol, timeframe in jobs
        }

        completed = 0

        for future in as_completed(futures):

            completed += 1

            try:

                signal = future.result()

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

                symbol, timeframe = futures[future]

                print(
                    f"{symbol} {timeframe}: {e}"
                )

            if (
                completed % 300 == 0
                or completed == len(jobs)
            ):

                print(
                    f"Progress: "
                    f"{completed}/{len(jobs)}"
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

        print("No NEW signals.")

        send_telegram(
            "🔍 No signal found.\n\n"
            "Next scan: 15 minutes"
        )

        return

    # ========================================================
    # SEND NEW SIGNALS
    # ========================================================

    print(
        f"NEW SIGNALS: {len(new_signals)}"
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

    print("Finished.")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
