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

# RECENT BOS WINDOW
BOS_LOOKBACK = 10

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
            [
                "git",
                "push",
            ],
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
# FIND MOST RECENT BOS
#
# IMPORTANT:
# BOS is searched only inside the MOST RECENT
# 10 COMPLETED CANDLES.
#
# We find both LONG and SHORT BOS candidates
# and select the most recent one.
# ============================================================

def find_structure_break(candles):

    if len(candles) < BOS_LOOKBACK + 5:
        return None

    window = candles[-BOS_LOOKBACK:]

    long_bos = None
    short_bos = None

    # ========================================================
    # LONG BOS
    # ========================================================

    for i in range(2, len(window) - 1):

        swing_high = window[i]["high"]

        if not (
            swing_high > window[i - 1]["high"]
            and
            swing_high > window[i + 1]["high"]
        ):
            continue

        for j in range(i + 1, len(window)):

            if window[j]["close"] > swing_high:

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

    for i in range(2, len(window) - 1):

        swing_low = window[i]["low"]

        if not (
            swing_low < window[i - 1]["low"]
            and
            swing_low < window[i + 1]["low"]
        ):
            continue

        for j in range(i + 1, len(window)):

            if window[j]["close"] < swing_low:

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
#
# Returns:
#   signal
#   or diagnostic information
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

    if len(candles) < EMA_SLOW + BOS_LOOKBACK + 5:

        return {
            "signal": None,
            "diagnostic": None,
        }

    # ========================================================
    # STRUCTURE
    # ========================================================

    structure = find_structure_break(candles)

    if not structure:

        return {
            "signal": None,
            "diagnostic": None,
        }

    direction = structure["direction"]
    trigger = structure["candle"]

    # ========================================================
    # INDICATORS
    # ========================================================

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

        return {
            "signal": None,
            "diagnostic": None,
        }

    # ========================================================
    # GAP
    # ========================================================

    if ema200 == 0:

        return {
            "signal": None,
            "diagnostic": None,
        }

    gap = (
        abs(sma50 - ema200)
        / abs(ema200)
    ) * 100

    symbol_name = symbol.replace(
        "_USDT",
        "",
    )

    # ========================================================
    # DIAGNOSTIC
    # ========================================================

    diagnostic = {
        "symbol": symbol_name,
        "timeframe": timeframe,
        "direction": direction,
        "price": price,
        "sma50": sma50,
        "ema20": ema20,
        "ema200": ema200,
        "gap": gap,
        "trigger_time": trigger["time"],
    }

    # ========================================================
    # GAP CHECK
    # ========================================================

    if gap <= 10:

        diagnostic["reason"] = (
            f"GAP {gap:.2f}% <= 10%"
        )

        return {
            "signal": None,
            "diagnostic": diagnostic,
        }

    # ========================================================
    # LONG
    #
    # 50 SMA < PRICE < 200 EMA
    # 20 EMA < 50 SMA
    # SL < 20 EMA < 50 SMA
    # ========================================================

    if direction == "LONG":

        if not (
            sma50 < price < ema200
        ):

            diagnostic["reason"] = (
                "LONG price condition failed"
            )

            return {
                "signal": None,
                "diagnostic": diagnostic,
            }

        if not (
            ema20 < sma50
        ):

            diagnostic["reason"] = (
                "LONG EMA20 < SMA50 condition failed"
            )

            return {
                "signal": None,
                "diagnostic": diagnostic,
            }

        sl = trigger["low"]

        if not (
            sl < ema20 < sma50
        ):

            diagnostic["reason"] = (
                "LONG SL / EMA20 / SMA50 condition failed"
            )

            return {
                "signal": None,
                "diagnostic": diagnostic,
            }

        signal = {
            "direction": "LONG",
            "symbol": symbol_name,
            "timeframe": timeframe,
            "entry": price,
            "sl": sl,
            "tp": ema200,
            "gap": gap,
            "trigger_time": trigger["time"],
        }

        diagnostic["reason"] = "QUALIFIED"

        return {
            "signal": signal,
            "diagnostic": diagnostic,
        }

    # ========================================================
    # SHORT
    #
    # 200 EMA < PRICE < 50 SMA
    # 20 EMA > 50 SMA
    # 200 EMA < 20 EMA < SL
    # ========================================================

    if direction == "SHORT":

        if not (
            ema200 < price < sma50
        ):

            diagnostic["reason"] = (
                "SHORT price condition failed"
            )

            return {
                "signal": None,
                "diagnostic": diagnostic,
            }

        if not (
            ema20 > sma50
        ):

            diagnostic["reason"] = (
                "SHORT EMA20 > SMA50 condition failed"
            )

            return {
                "signal": None,
                "diagnostic": diagnostic,
            }

        sl = trigger["high"]

        if not (
            ema200 < ema20 < sl
        ):

            diagnostic["reason"] = (
                "SHORT EMA200 / EMA20 / SL condition failed"
            )

            return {
                "signal": None,
                "diagnostic": diagnostic,
            }

        signal = {
            "direction": "SHORT",
            "symbol": symbol_name,
            "timeframe": timeframe,
            "entry": price,
            "sl": sl,
            "tp": ema200,
            "gap": gap,
            "trigger_time": trigger["time"],
        }

        diagnostic["reason"] = "QUALIFIED"

        return {
            "signal": signal,
            "diagnostic": diagnostic,
        }

    return {
        "signal": None,
        "diagnostic": None,
    }


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
#
# TP1 = 5%
# TP2 = 10%
# TP3 = 200 EMA
#
# EMA IS NOT NAMED IN ALERT.
# ============================================================

def format_signal(signal):

    symbol = signal["symbol"]
    direction = signal["direction"]
    timeframe = signal["timeframe"]

    entry_price = signal["entry"]
    sl_price = signal["sl"]
    tp3_price = signal["tp"]

    entry = format_price(
        entry_price
    )

    sl = format_price(
        sl_price
    )

    if direction == "LONG":

        tp1_price = entry_price * 1.05
        tp2_price = entry_price * 1.10

    else:

        tp1_price = entry_price * 0.95
        tp2_price = entry_price * 0.90

    tp1 = format_price(
        tp1_price
    )

    tp2 = format_price(
        tp2_price
    )

    tp3 = format_price(
        tp3_price
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
        f"#{symbol} {direction} {timeframe} {emoji}\n\n"
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

    print("=" * 50)
    print("LONG + SHORT SIGNAL BOT")
    print("15M / 1H / 4H INDEPENDENT STRUCTURE")
    print("20 EMA / 50 SMA / 200 EMA")
    print("GAP > 10%")
    print("RECENT BOS: LAST 10 COMPLETED CANDLES")
    print("MOST RECENT BOS SELECTED")
    print("TP1 5% / TP2 10% / TP3 EMA-BASED")
    print("NO RSI")
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

    # ========================================================
    # DIAGNOSTIC COUNTERS
    # ========================================================

    bos_found = 0

    long_bos = 0
    short_bos = 0

    gap_failed = 0
    price_failed = 0
    ema_failed = 0
    sl_failed = 0

    qualified = 0

    diagnostic_examples = []

    # ========================================================
    # SCAN
    # ========================================================

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

                result = future.result()

                signal = result["signal"]
                diagnostic = result["diagnostic"]

                # =================================================
                # DIAGNOSTIC
                # =================================================

                if diagnostic:

                    bos_found += 1

                    direction = diagnostic["direction"]

                    if direction == "LONG":
                        long_bos += 1
                    else:
                        short_bos += 1

                    reason = diagnostic["reason"]

                    if reason == "QUALIFIED":

                        qualified += 1

                    elif "GAP" in reason:

                        gap_failed += 1

                    elif "price condition" in reason:

                        price_failed += 1

                    elif "EMA20" in reason:

                        ema_failed += 1

                    elif "SL" in reason:

                        sl_failed += 1

                    # Keep a few examples only
                    if len(diagnostic_examples) < 30:

                        diagnostic_examples.append(
                            diagnostic
                        )

                # =================================================
                # SIGNAL
                # =================================================

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
    # DIAGNOSTIC SUMMARY
    # ========================================================

    print()
    print("=" * 50)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 50)

    print(
        f"BOS found: {bos_found}"
    )

    print(
        f"LONG BOS: {long_bos}"
    )

    print(
        f"SHORT BOS: {short_bos}"
    )

    print(
        f"Rejected by GAP: {gap_failed}"
    )

    print(
        f"Rejected by PRICE: {price_failed}"
    )

    print(
        f"Rejected by EMA/SMA: {ema_failed}"
    )

    print(
        f"Rejected by SL: {sl_failed}"
    )

    print(
        f"Qualified: {qualified}"
    )

    print(
        f"NEW signals: {len(new_signals)}"
    )

    # ========================================================
    # SHOW EXAMPLES
    # ========================================================

    if diagnostic_examples:

        print()
        print(
            "RECENT BOS DIAGNOSTIC EXAMPLES:"
        )

        for item in diagnostic_examples[:30]:

            print(
                f"{item['symbol']} "
                f"{item['timeframe']} "
                f"{item['direction']} | "
                f"Price={format_price(item['price'])} | "
                f"SMA50={format_price(item['sma50'])} | "
                f"EMA20={format_price(item['ema20'])} | "
                f"EMA200={format_price(item['ema200'])} | "
                f"Gap={item['gap']:.2f}% | "
                f"{item['reason']}"
            )

    print(
        "=" * 50
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

    print(
        "Finished."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
