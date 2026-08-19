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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

GATE_URL = "https://api.gateio.ws/api/v4"

SMA_PERIOD = 50
EMA_FAST = 20
EMA_SLOW = 200

STRUCTURE_CANDLES = 20

TIMEFRAMES = ["15m", "1h", "4h"]

CANDLE_LIMIT = 300

MAX_WORKERS = 10

HISTORY_FILE = "signals.json"

HEADERS = {
    "User-Agent": "Long-Short-Signal-Bot/1.0",
    "Accept": "application/json"
}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets missing.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:

        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=20
        )

        if response.status_code == 200:
            print("Telegram message sent.")
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text
        )

    except Exception as e:
        print("Telegram error:", e)

    return False


# ============================================================
# HISTORY
# ============================================================

def load_history():

    if not os.path.exists(HISTORY_FILE):
        return set()

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        return set(data)

    except Exception as e:

        print("History read error:", e)
        return set()


def save_history(history):

    try:

        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                sorted(history),
                file,
                indent=2
            )

        return True

    except Exception as e:

        print("History save error:", e)
        return False


# ============================================================
# GITHUB SAVE
# ============================================================

def push_history():

    try:

        subprocess.run(
            [
                "git",
                "config",
                "user.name",
                "github-actions[bot]"
            ],
            check=True
        )

        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com"
            ],
            check=True
        )

        subprocess.run(
            [
                "git",
                "add",
                HISTORY_FILE
            ],
            check=True
        )

        result = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--quiet"
            ]
        )

        # Nothing changed
        if result.returncode == 0:
            return

        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Update signal history"
            ],
            check=True
        )

        subprocess.run(
            [
                "git",
                "push"
            ],
            check=True
        )

        print("Signal history saved to GitHub.")

    except Exception as e:

        print(
            "GitHub history update error:",
            e
        )


# ============================================================
# GATE SYMBOLS
# ============================================================

def get_symbols():

    url = f"{GATE_URL}/futures/usdt/contracts"

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code != 200:
            print(response.text)
            return []

        data = response.json()

        symbols = []

        for item in data:

            if not isinstance(item, dict):
                continue

            symbol = item.get("name", "")

            if (
                symbol.endswith("_USDT")
                and not item.get(
                    "in_delisting",
                    False
                )
            ):

                symbols.append(symbol)

        return sorted(set(symbols))

    except Exception as e:

        print("Symbol error:", e)
        return []


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    interval,
    limit=CANDLE_LIMIT
):

    url = (
        f"{GATE_URL}/futures/usdt/"
        "candlesticks"
    )

    params = {
        "contract": symbol,
        "interval": interval,
        "limit": limit
    }

    for attempt in range(3):

        try:

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20
            )

            if response.status_code == 200:

                data = response.json()

                return (
                    data
                    if isinstance(data, list)
                    else []
                )

            if response.status_code == 429:

                time.sleep(
                    2 ** attempt
                )

                continue

            return []

        except Exception as e:

            if attempt == 2:

                print(
                    f"{symbol} {interval}: {e}"
                )

                return []

            time.sleep(
                2 ** attempt
            )

    return []


# ============================================================
# PARSE
# ============================================================

def parse_candles(data):

    candles = []

    for item in data:

        try:

            if isinstance(item, dict):

                timestamp = int(item["t"])
                open_price = float(item["o"])
                high = float(item["h"])
                low = float(item["l"])
                close = float(item["c"])
                volume = float(
                    item.get("v", 0)
                )

            else:

                timestamp = int(item[0])
                open_price = float(item[5])
                high = float(item[3])
                low = float(item[4])
                close = float(item[2])
                volume = float(item[6])

            candles.append(
                {
                    "time": timestamp,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume
                }
            )

        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError
        ):

            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


# ============================================================
# REMOVE OPEN CANDLE
# ============================================================

def remove_open_candle(
    candles,
    interval_seconds
):

    now = int(time.time())

    return [
        candle
        for candle in candles
        if candle["time"]
        + interval_seconds
        <= now
    ]


# ============================================================
# SMA
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return sum(
        values[-period:]
    ) / period


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = (
        2 / (period + 1)
    )

    value = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
        ) + value

    return value


# ============================================================
# STRUCTURE BREAKOUT
# ============================================================

def bullish_breakout(candles):

    if len(candles) < STRUCTURE_CANDLES + 1:
        return None

    current = candles[-1]

    previous = candles[
        -(STRUCTURE_CANDLES + 1):-1
    ]

    structure_high = max(
        candle["high"]
        for candle in previous
    )

    if current["close"] > structure_high:

        return current

    return None


# ============================================================
# STRUCTURE BREAKDOWN
# ============================================================

def bearish_breakdown(candles):

    if len(candles) < STRUCTURE_CANDLES + 1:
        return None

    current = candles[-1]

    previous = candles[
        -(STRUCTURE_CANDLES + 1):-1
    ]

    structure_low = min(
        candle["low"]
        for candle in previous
    )

    if current["close"] < structure_low:

        return current

    return None


# ============================================================
# LONG / SHORT ANALYSIS
# ============================================================

def analyze_symbol(symbol):

    data = {}

    intervals = {
        "15m": 900,
        "1h": 3600,
        "4h": 14400
    }

    for timeframe in TIMEFRAMES:

        raw = get_candles(
            symbol,
            timeframe
        )

        candles = parse_candles(raw)

        candles = remove_open_candle(
            candles,
            intervals[timeframe]
        )

        if len(candles) < EMA_SLOW + 30:

            return None

        data[timeframe] = candles

    # ========================================================
    # 15M STRUCTURE
    # ========================================================

    long_15m = bullish_breakout(
        data["15m"]
    )

    short_15m = bearish_breakdown(
        data["15m"]
    )

    # ========================================================
    # 1H STRUCTURE
    # ========================================================

    long_1h = bullish_breakout(
        data["1h"]
    )

    short_1h = bearish_breakdown(
        data["1h"]
    )

    # ========================================================
    # 4H STRUCTURE
    # ========================================================

    long_4h = bullish_breakout(
        data["4h"]
    )

    short_4h = bearish_breakdown(
        data["4h"]
    )

    # ========================================================
    # 4H INDICATORS
    # ========================================================

    candles_4h = data["4h"]

    closes = [
        candle["close"]
        for candle in candles_4h
    ]

    current_price = closes[-1]

    sma50 = sma(
        closes,
        SMA_PERIOD
    )

    ema20 = ema(
        closes,
        EMA_FAST
    )

    ema200 = ema(
        closes,
        EMA_SLOW
    )

    if None in (
        sma50,
        ema20,
        ema200
    ):
        return None

    # ========================================================
    # GAP
    # ========================================================

    if ema200 == 0:
        return None

    gap = (
        abs(sma50 - ema200)
        / abs(ema200)
    ) * 100

    # STRICTLY GREATER THAN 10%
    if gap <= 10:
        return None

    # ========================================================
    # LONG
    #
    # SL < 20 EMA < PRICE
    # 50 SMA < PRICE < 200 EMA
    #
    # 20 EMA must be between SL and 200 EMA.
    # ========================================================

    if (
        long_15m
        and long_1h
        and long_4h
        and sma50 < current_price < ema200
        and ema20 < sma50
    ):

        # SL from the 15m breakout candle
        sl = long_15m["low"]

        # 20 EMA must be above SL
        # and below 200 EMA.
        if not (
            sl < ema20 < ema200
        ):
            return None

        entry = current_price

        tp = ema200

        signal_key = (
            f"{symbol}|LONG|"
            f"{long_4h['time']}"
        )

        return {
            "type": "LONG",
            "symbol": symbol.replace(
                "_USDT",
                ""
            ),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "gap": gap,
            "key": signal_key,
            "trigger_time": long_4h["time"]
        }

    # ========================================================
    # SHORT
    #
    # 200 EMA < PRICE < 50 SMA < 20 EMA < SL
    #
    # 20 EMA must be between 200 EMA and SL.
    # ========================================================

    if (
        short_15m
        and short_1h
        and short_4h
        and ema200 < current_price < sma50
        and ema20 > sma50
    ):

        # SL from the 15m breakdown candle
        sl = short_15m["high"]

        # 20 EMA must be below SL
        # and above 200 EMA.
        if not (
            ema200 < ema20 < sl
        ):
            return None

        entry = current_price

        tp = ema200

        signal_key = (
            f"{symbol}|SHORT|"
            f"{short_4h['time']}"
        )

        return {
            "type": "SHORT",
            "symbol": symbol.replace(
                "_USDT",
                ""
            ),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "gap": gap,
            "key": signal_key,
            "trigger_time": short_4h["time"]
        }

    return None


# ============================================================
# PRICE FORMAT
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
# TELEGRAM FORMAT
# ============================================================

def format_signal(signal):

    symbol = signal["symbol"]

    entry = format_price(
        signal["entry"]
    )

    sl = format_price(
        signal["sl"]
    )

    tp = format_price(
        signal["tp"]
    )

    gap = round(
        signal["gap"]
    )

    if signal["type"] == "LONG":

        return (
            f"#{symbol} LONG 🟢\n\n"
            f"Entry: ${entry}\n"
            f"SL: ${sl}\n\n"
            f"**TP: ${tp} — 200 EMA**\n\n"
            f"Gap: {gap}%"
        )

    return (
        f"#{symbol} SHORT 🔴\n\n"
        f"Entry: ${entry}\n"
        f"SL: ${sl}\n\n"
        f"**TP: ${tp} — 200 EMA**\n\n"
        f"Gap: {gap}%"
    )


# ============================================================
# MAIN SCANNER
# ============================================================

def main():

    print(
        "========================================"
    )

    print(
        "LONG + SHORT SIGNAL BOT"
    )

    print(
        "15M + 1H + 4H STRUCTURE"
    )

    print(
        "20 EMA / 50 SMA / 200 EMA"
    )

    print(
        "GAP > 10%"
    )

    print(
        "========================================"
    )

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

        return

    print(
        f"Scanning {len(symbols)} symbols..."
    )

    signals = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_symbol,
                symbol
            ): symbol

            for symbol in symbols
        }

        completed = 0

        for future in as_completed(
            futures
        ):

            symbol = futures[future]

            completed += 1

            try:

                signal = future.result()

                if signal:

                    print(
                        f"FOUND: "
                        f"{signal['type']} "
                        f"{signal['symbol']} "
                        f"Gap "
                        f"{signal['gap']:.2f}%"
                    )

                    if signal["key"] not in history:

                        signals.append(
                            signal
                        )

            except Exception as e:

                print(
                    f"{symbol}: {e}"
                )

            if (
                completed % 100 == 0
                or completed == len(symbols)
            ):

                print(
                    f"Progress: "
                    f"{completed}/"
                    f"{len(symbols)}"
                )

    # ========================================================
    # SORT
    # ========================================================

    signals.sort(
        key=lambda x: x["gap"],
        reverse=True
    )

    # ========================================================
    # SEND NEW SIGNALS
    # ========================================================

    if not signals:

        print(
            "No NEW signals."
        )

        return

    for signal in signals:

        message = format_signal(
            signal
        )

        print()
        print(message)
        print()

        if send_telegram(message):

            history.add(
                signal["key"]
            )

            save_history(
                history
            )

            push_history()

        time.sleep(0.5)

    print(
        f"New signals sent: "
        f"{len(signals)}"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
